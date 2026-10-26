"""Account authentication: password hashing and signed access tokens.

Replaces the earlier prototype of one shared secret per role with real accounts, while
keeping that shared secret working so nothing already deployed breaks.

Design:

* **Passwords** are hashed with bcrypt (cost 12). Never stored or logged in plaintext.
* **Sessions** are signed JWTs carrying the user id, role and a `ver` claim. Bumping
  `users.token_version` invalidates every token issued before it — that is the revocation
  mechanism, so a password change or a forced logout takes effect immediately without a
  server-side session table.
* **Roles** are farmer, expert and admin. Registration can only ever create a farmer; an
  expert or admin account must be created by an admin.
* **Brute force** is limited per email and per client address on the login route.

The signing key comes from `SECRET_KEY`. If it is unset a random key is generated at
startup, which means tokens do not survive a restart — loud in development, safe by
default in the sense that nothing is guessable.
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Dict, Optional, Tuple

import bcrypt
import jwt

from config import settings

logger = logging.getLogger("cropguard.auth")

ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 24  # 24 hours

ROLE_FARMER = "farmer"
ROLE_EXPERT = "expert"
ROLE_ADMIN = "admin"
STAFF_ROLES = (ROLE_EXPERT, ROLE_ADMIN)
ALL_ROLES = (ROLE_FARMER, ROLE_EXPERT, ROLE_ADMIN)

# Password policy: length is the only rule that matters much, so enforce it plainly
# instead of demanding character classes that push people towards "Password1!".
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200  # bcrypt ignores anything past 72 bytes; reject rather than truncate

_BCRYPT_ROUNDS = 12


# ---------------------------------------------------------------------------
# Signing key
# ---------------------------------------------------------------------------
def _signing_key() -> str:
    configured = settings.secret_key.strip()
    if configured:
        return configured
    global _EPHEMERAL_KEY
    try:
        return _EPHEMERAL_KEY
    except NameError:
        _EPHEMERAL_KEY = secrets.token_urlsafe(48)
        logger.warning(
            "SECRET_KEY is not set: using a random key generated at startup. Access "
            "tokens will be invalidated on every restart. Set SECRET_KEY in production."
        )
        return _EPHEMERAL_KEY


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    """Constant-time check that never raises on malformed input."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_problem(password: str) -> Optional[str]:
    """Return a human-readable reason the password is unacceptable, else None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password.encode("utf-8")) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} bytes."
    if password.strip() != password:
        return "Remove leading or trailing spaces."
    return None


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def create_access_token(user_id: int, role: str, token_version: int) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "role": role,
        "ver": int(token_version),
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def decode_access_token(token: Optional[str]) -> Optional[Dict]:
    """Decode and verify a token, returning its claims or None.

    Expiry is enforced by PyJWT. Signature failures, malformed tokens and expiry all
    return None so callers cannot accidentally treat them as valid.
    """
    if not token:
        return None
    try:
        claims = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    if not claims.get("sub") or claims.get("role") not in ALL_ROLES:
        return None
    return claims


def bearer_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    value = authorization.split(" ", 1)[1].strip()
    return value or None


# ---------------------------------------------------------------------------
# Login throttling
# ---------------------------------------------------------------------------
class LoginThrottle:
    """Counts recent failures per key and refuses further attempts past a limit.

    In-memory and therefore per-process; documented as such in docs/security.md. The
    point is to make online guessing impractical, not to be a distributed rate limiter.
    """

    def __init__(self, limit: int = 8, window_seconds: int = 900) -> None:
        self.limit = limit
        self.window = window_seconds
        self._failures: Dict[str, list] = {}

    def _recent(self, key: str) -> list:
        now = time.monotonic()
        recent = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> Tuple[bool, int]:
        recent = self._recent(key)
        if len(recent) >= self.limit:
            retry_after = int(self.window - (time.monotonic() - recent[0])) + 1
            return True, max(1, retry_after)
        return False, 0

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)

    def clear(self) -> None:
        self._failures.clear()


_login_throttle: Optional[LoginThrottle] = None


def login_throttle() -> LoginThrottle:
    global _login_throttle
    if _login_throttle is None:
        _login_throttle = LoginThrottle(
            limit=settings.login_max_failures, window_seconds=settings.login_failure_window
        )
    return _login_throttle


def normalise_email(email: str) -> str:
    return email.strip().lower()
