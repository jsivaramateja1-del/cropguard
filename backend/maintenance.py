"""Housekeeping that keeps the deployment inside its storage budget.

The problem this solves: every analysis stores the leaf photo in Postgres. Reports are kept
indefinitely because they are the outbreak map's history, but the photo itself stops being
useful long before that — and the free Supabase tier is 500 MB.

The policy is deliberately conservative and reversible:

* images older than ``IMAGE_RETENTION_DAYS`` (default 90) are deleted
* if the stored total exceeds ``IMAGE_MAX_TOTAL_MB``, the oldest are deleted until it fits
* **the report survives in both cases** — only the bytes go, and ``reports.image_url`` is
  cleared so the UI shows its "no photo" placeholder rather than a broken image

Nothing here deletes a report, a follow-up or an audit row.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from config import settings
from models import Report, ReportImage

logger = logging.getLogger("cropguard.maintenance")

_last_sweep: Optional[float] = None


@dataclass
class RetentionResult:
    deleted_images: int
    freed_bytes: int
    remaining_images: int
    remaining_bytes: int
    reason: str

    def as_dict(self) -> dict:
        return {
            "deleted_images": self.deleted_images,
            "freed_bytes": self.freed_bytes,
            "remaining_images": self.remaining_images,
            "remaining_bytes": self.remaining_bytes,
            "reason": self.reason,
        }


def storage_usage(db: Session) -> dict:
    """How much image data is stored right now."""
    count, total = db.execute(
        select(func.count(ReportImage.id), func.coalesce(func.sum(ReportImage.size_bytes), 0))
    ).one()
    return {
        "images": int(count or 0),
        "bytes": int(total or 0),
        "megabytes": round(int(total or 0) / (1024 * 1024), 2),
        "retention_days": settings.image_retention_days,
        "max_total_mb": settings.image_max_total_mb,
    }


def _detach_images_from_reports(db: Session, tokens: list) -> None:
    """Clear image_url on reports whose bytes are gone, so the UI shows a placeholder."""
    if not tokens:
        return
    db.execute(
        update(Report)
        .where(Report.image_url.in_([f"/api/images/{token}" for token in tokens]))
        .values(image_url=None)
    )


def purge_expired_images(db: Session, days: Optional[int] = None) -> RetentionResult:
    """Delete images past the retention window, then enforce the size cap."""
    retention_days = settings.image_retention_days if days is None else days
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    expired = db.execute(
        select(ReportImage.token, ReportImage.size_bytes).where(ReportImage.created_at < cutoff)
    ).all()

    freed = 0
    deleted = 0
    tokens = []
    if expired:
        tokens = [row[0] for row in expired]
        freed += sum(int(row[1] or 0) for row in expired)
        deleted += len(tokens)
        db.execute(ReportImage.__table__.delete().where(ReportImage.token.in_(tokens)))
        _detach_images_from_reports(db, tokens)
        db.flush()

    # Size cap: drop the oldest until the total fits.
    cap_bytes = settings.image_max_total_mb * 1024 * 1024
    total = int(
        db.execute(select(func.coalesce(func.sum(ReportImage.size_bytes), 0))).scalar() or 0
    )
    reason = "retention window" if deleted else "nothing to do"

    if total > cap_bytes:
        reason = "size cap"
        overflow = total - cap_bytes
        rows = db.execute(
            select(ReportImage.token, ReportImage.size_bytes).order_by(ReportImage.created_at.asc())
        ).all()
        evicted = []
        for token, size in rows:
            if overflow <= 0:
                break
            evicted.append(token)
            overflow -= int(size or 0)
            freed += int(size or 0)
            deleted += 1
        if evicted:
            db.execute(ReportImage.__table__.delete().where(ReportImage.token.in_(evicted)))
            _detach_images_from_reports(db, evicted)
            tokens.extend(evicted)

    db.commit()

    remaining = storage_usage(db)
    if deleted:
        logger.info(
            "retention: removed %s images (%s MB), %s remain",
            deleted,
            round(freed / (1024 * 1024), 2),
            remaining["images"],
        )

    return RetentionResult(
        deleted_images=deleted,
        freed_bytes=freed,
        remaining_images=remaining["images"],
        remaining_bytes=remaining["bytes"],
        reason=reason,
    )


def maybe_run_retention(db: Session) -> Optional[RetentionResult]:
    """Run at most once per interval, so it can be called from a request path cheaply."""
    global _last_sweep
    now = time.monotonic()
    if _last_sweep is not None and now - _last_sweep < settings.image_cleanup_interval:
        return None
    _last_sweep = now
    try:
        return purge_expired_images(db)
    except Exception:  # noqa: BLE001 - housekeeping must never fail a request
        logger.exception("retention sweep failed")
        return None


def reset_sweep_clock() -> None:
    """Testing hook: forget when the last sweep ran."""
    global _last_sweep
    _last_sweep = None
