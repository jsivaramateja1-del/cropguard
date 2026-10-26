"""Authentication, authorization and rate limiting.

Design decision worth stating up front: CropGuard's farmer flow is **anonymous by
design**. A farmer photographing a leaf in a field should not have to create an account,
and the brief explicitly permits "appropriately designed, unguessable, scoped
report-access tokens where anonymous reports are intentionally supported".

So the model is:

* **Farmer (anonymous)** — submitting a photo returns a one-time `access_token`. Only a
  hash is stored. That token, not the report id, is what grants access to that report and
  to its follow-ups. Report ids are sequential and therefore public reference numbers;
  they are never sufficient to read private data.
* **Expert / administrator** — identified by a bearer token configured in the
  environment. The name an expert types in a request body establishes nothing; the
  token does. This is prototype-grade identity (a shared secret per role) rather than a
  full user database, and that limitation is documented rather than hidden.
* **Audit** — every verification decision writes an `AuditEvent` row recording who acted,
  what changed, and when.

Why not passwords and sessions: there is no user table, no email delivery, and no
password reset channel available to this prototype. Adding a password system without
those would be a worse security posture than a scoped token, not a better one. The
upgrade path is stated in docs/security.md.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from fastapi import Header, HTTPException, Request, status

from config import settings

logger = logging.getLogger("cropguard.security")

ROLE_EXPERT = "expert"
ROLE_ADMIN = "admin"


# ---------------------------------------------------------------------------
# Report access tokens (farmer side)
# ---------------------------------------------------------------------------
def new_report_token() -> str:
    """A fresh unguessable token. 32 bytes of entropy, URL-safe."""
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    """Hash a token for storage.

    SHA-256 without a salt is appropriate here and not for passwords: the input is 256
    bits of randomness, so there is no dictionary to attack. Passwords would need
    bcrypt/argon2; tokens do not.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_matches(raw: Optional[str], stored_hash: Optional[str]) -> bool:
    """Constant-time comparison, so a wrong token leaks no timing information."""
    if not raw or not stored_hash:
        return False
    return hmac.compare_digest(hash_token(raw), stored_hash)


# ---------------------------------------------------------------------------
# Staff identity (expert / admin)
# ---------------------------------------------------------------------------
def _configured_tokens() -> Dict[str, str]:
    tokens: Dict[str, str] = {}
    if settings.expert_token.strip():
        tokens[ROLE_EXPERT] = settings.expert_token.strip()
    if settings.admin_token.strip():
        tokens[ROLE_ADMIN] = settings.admin_token.strip()
    return tokens


_legacy_warned = False


def _warn_legacy_once() -> None:
    """Log the deprecation once per process, not once per request."""
    global _legacy_warned
    if _legacy_warned:
        return
    _legacy_warned = True
    logger.warning(
        "a request authenticated with the legacy shared staff token. It is deprecated: "
        "create expert accounts through the approval flow and set "
        "ALLOW_LEGACY_STAFF_TOKEN=false, then remove EXPERT_TOKEN."
    )


def staff_identity(authorization: Optional[str]) -> Optional[Tuple[str, str]]:
    """Return (role, display_name) for a valid bearer token, else None.

    Display names come from configuration, not from the request, so an expert cannot
    choose what the audit log says about them.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    supplied = authorization.split(" ", 1)[1].strip()
    if not supplied:
        return None

    if not settings.allow_legacy_staff_token:
        # The migration is complete: only account tokens are accepted.
        return None

    for role, expected in _configured_tokens().items():
        if hmac.compare_digest(supplied, expected):
            _warn_legacy_once()
            name = (
                settings.expert_name if role == ROLE_EXPERT else settings.admin_name
            ).strip() or role
            return role, name
    return None


def require_staff(authorization: Optional[str] = Header(default=None)) -> Tuple[str, str]:
    """Dependency: any authenticated staff member (expert or administrator)."""
    identity = staff_identity(authorization)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint requires an authenticated staff token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return identity


def require_expert(authorization: Optional[str] = Header(default=None)) -> Tuple[str, str]:
    """Dependency: an agricultural expert (administrators may also act)."""
    identity = staff_identity(authorization)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This endpoint requires an authenticated expert token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    role, name = identity
    if role not in (ROLE_EXPERT, ROLE_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an agricultural expert may perform this action.",
        )
    return role, name


def staff_auth_configured() -> bool:
    return bool(_configured_tokens())


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class SlidingWindowLimiter:
    """In-memory per-client limiter for the costly analysis endpoint.

    Deliberately simple: a deque of timestamps per client key. It is per-process, so
    with several workers the effective limit is multiplied — acceptable for a prototype
    and documented as such. A shared store (Redis) is the production answer.
    """

    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = max(1, limit)
        self.window = max(1, window_seconds)
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> Tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > self.window:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = int(self.window - (now - bucket[0])) + 1
                return False, max(1, retry_after)
            bucket.append(now)
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    Behind Render's proxy the client address arrives in X-Forwarded-For; the first entry
    is the originating client.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


_analyze_limiter: Optional[SlidingWindowLimiter] = None


def analyze_limiter() -> SlidingWindowLimiter:
    global _analyze_limiter
    if _analyze_limiter is None:
        _analyze_limiter = SlidingWindowLimiter(
            limit=settings.analyze_rate_limit, window_seconds=settings.analyze_rate_window
        )
    return _analyze_limiter
