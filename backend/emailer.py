"""Transactional email delivery through Brevo.

Why Brevo: it offers a free tier with a plain HTTPS API and no SDK, so this is one POST
request with an API key — no extra dependency beyond `httpx`, which the weather client
already uses.

The rule this module follows: **sending is never assumed to have worked.** `send_email`
returns an explicit result with `delivered: False` and a reason when the API key is not
configured or Brevo rejects the request. A caller must not tell a user "check your inbox"
on the strength of a function that swallowed an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("cropguard.email")

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"
TIMEOUT_SECONDS = 15.0


@dataclass
class EmailResult:
    delivered: bool
    detail: str
    message_id: Optional[str] = None

    def as_dict(self) -> dict:
        return {"delivered": self.delivered, "detail": self.detail}


def email_configured() -> bool:
    return bool(settings.brevo_api_key.strip() and settings.email_from.strip())


def send_email(to_address: str, subject: str, html: str, text: str) -> EmailResult:
    """Send one transactional email. Never raises."""
    if not settings.brevo_api_key.strip():
        logger.warning("BREVO_API_KEY is not set; the email was not sent")
        return EmailResult(
            delivered=False,
            detail="Email delivery is not configured (BREVO_API_KEY is unset).",
        )
    if not settings.email_from.strip():
        logger.warning("EMAIL_FROM is not set; the email was not sent")
        return EmailResult(delivered=False, detail="Email delivery is not configured (EMAIL_FROM is unset).")

    payload = {
        "sender": {"email": settings.email_from.strip(), "name": settings.email_from_name.strip()},
        "to": [{"email": to_address}],
        "subject": subject,
        "htmlContent": html,
        "textContent": text,
    }

    try:
        response = httpx.post(
            BREVO_ENDPOINT,
            json=payload,
            headers={
                "api-key": settings.brevo_api_key.strip(),
                "content-type": "application/json",
                "accept": "application/json",
            },
            timeout=TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as error:
        # Network failure. Log the type, never the payload — it contains a reset link.
        logger.error("Brevo request failed: %s", type(error).__name__)
        return EmailResult(delivered=False, detail="The email service could not be reached.")

    if response.status_code in (200, 201, 202):
        message_id = None
        try:
            message_id = response.json().get("messageId")
        except ValueError:
            pass
        logger.info("reset email accepted by Brevo")
        return EmailResult(delivered=True, detail="accepted", message_id=message_id)

    # 401/403 mean a bad key; 400 usually a rejected sender that has not been verified.
    reason = {
        400: "Brevo rejected the request. Check that EMAIL_FROM is a verified sender.",
        401: "Brevo rejected the API key.",
        403: "The Brevo API key is not permitted to send.",
        429: "Brevo rate limit reached. Try again shortly.",
    }.get(response.status_code, f"Brevo returned HTTP {response.status_code}.")

    logger.error("Brevo rejected the email: HTTP %s", response.status_code)
    return EmailResult(delivered=False, detail=reason)


def email_status() -> dict:
    """Non-secret summary for /health."""
    return {
        "configured": email_configured(),
        "provider": "brevo",
        "sender_configured": bool(settings.email_from.strip()),
    }
