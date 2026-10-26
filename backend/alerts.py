"""Outbreak alerts by email.

The pitch promised that nearby farmers get an early warning when disease appears. This is
that mechanism, built around three rules:

1. **Consent is explicit.** Subscribing sends a confirmation email; nothing is delivered
   until the link is clicked. An address cannot be signed up by somebody else.
2. **Every message can be stopped.** Each alert carries an unsubscribe link, and the
   footer says who to contact otherwise.
3. **Alerts are rare by construction.** A subscriber is told about a given disease in
   their area at most once per cooldown window, so a busy district cannot flood an inbox.

Distance is straight-line (haversine) from the coordinates the farmer supplied. It is not
a travel distance and does not account for terrain — it is a radius, and the email says so.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

import email_templates
import emailer
from config import settings
from models import AlertSubscription, Report

logger = logging.getLogger("cropguard.alerts")

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


@dataclass
class AlertOutcome:
    considered: int
    matched: int
    sent: int
    skipped_cooldown: int
    skipped_duplicate: int

    def as_dict(self) -> dict:
        return {
            "considered": self.considered,
            "matched": self.matched,
            "sent": self.sent,
            "skipped_cooldown": self.skipped_cooldown,
            "skipped_duplicate": self.skipped_duplicate,
        }


def _crop_of(label: str) -> str:
    return label.split("___")[0].replace("_", " ").strip()


def _condition_of(label: str) -> str:
    return label.split("___")[-1].replace("_", " ").strip()


def subscribers_for(
    db: Session, *, latitude: float, longitude: float, crop: str
) -> List[tuple]:
    """Confirmed subscribers within their own radius who asked about this crop."""
    rows = db.execute(
        select(AlertSubscription).where(AlertSubscription.confirmed_at.is_not(None))
    ).scalars()

    matched = []
    for subscription in rows:
        if subscription.crops and crop not in subscription.crops:
            continue
        distance = haversine_km(latitude, longitude, subscription.latitude, subscription.longitude)
        if distance <= (subscription.radius_km or 0):
            matched.append((subscription, distance))
    return matched


def notify_neighbours(db: Session, report: Report) -> AlertOutcome:
    """Email everyone who asked to hear about this kind of report nearby.

    Best effort throughout: a failure here must never fail the farmer's analysis.
    """
    outcome = AlertOutcome(considered=0, matched=0, sent=0, skipped_cooldown=0, skipped_duplicate=0)

    if not settings.alerts_enabled:
        return outcome
    if report.predicted_disease.endswith("healthy"):
        # Telling a district that something is healthy is not an alert.
        return outcome

    crop = _crop_of(report.predicted_disease)
    condition = _condition_of(report.predicted_disease)
    cooldown = timedelta(hours=settings.alert_cooldown_hours)
    now = datetime.now(timezone.utc)

    try:
        matched = subscribers_for(
            db, latitude=report.latitude, longitude=report.longitude, crop=crop
        )
    except Exception:  # noqa: BLE001
        logger.exception("could not look up alert subscribers")
        return outcome

    outcome.considered = len(matched)
    seen: set = set()

    for subscription, distance in matched:
        if subscription.email in seen:
            outcome.skipped_duplicate += 1
            continue
        seen.add(subscription.email)

        last = subscription.last_notified_at
        if last is not None:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < cooldown:
                outcome.skipped_cooldown += 1
                continue

        outcome.matched += 1

        app_url = settings.public_app_url.strip().rstrip("/") or "https://cropguard.app"
        unsubscribe_url = f"{app_url}/alerts/unsubscribe?token={subscription.unsubscribe_token}"
        subject, html, text = email_templates.outbreak_alert_email(
            condition=condition,
            crop=crop,
            region_name=report.region_name,
            distance_km=round(distance, 1),
            reference=report.reference or "",
            unsubscribe_url=unsubscribe_url,
            app_url=app_url,
            language=subscription.language or "en",
        )

        # Called through the module so a test can substitute the transport.
        result = emailer.send_email(subscription.email, subject, html, text)
        if result.delivered:
            subscription.last_notified_at = now
            outcome.sent += 1
        else:
            logger.warning("alert not delivered: %s", result.detail)

    if outcome.sent:
        db.commit()
        logger.info(
            "outbreak alert sent to %s subscriber(s) for report %s", outcome.sent, report.id
        )
    return outcome


def confirm_subscription(db: Session, token_hash: str) -> Optional[AlertSubscription]:
    subscription = db.execute(
        select(AlertSubscription).where(AlertSubscription.confirm_token_hash == token_hash)
    ).scalar_one_or_none()
    if subscription is None or subscription.confirmed_at is not None:
        return None
    subscription.confirmed_at = datetime.now(timezone.utc)
    db.commit()
    return subscription


def unsubscribe(db: Session, token: str) -> bool:
    subscription = db.execute(
        select(AlertSubscription).where(AlertSubscription.unsubscribe_token == token)
    ).scalar_one_or_none()
    if subscription is None:
        return False
    db.delete(subscription)
    db.commit()
    return True


def nearby_counts(db: Session, latitude: float, longitude: float, radius_km: float) -> int:
    """How many reports sit inside a radius — used to phrase the alert honestly."""
    rows = db.execute(select(Report.latitude, Report.longitude)).all()
    return sum(
        1
        for lat, lon in rows
        if haversine_km(latitude, longitude, lat, lon) <= radius_km
    )
