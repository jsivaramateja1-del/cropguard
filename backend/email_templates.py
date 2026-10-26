"""Email templates.

One template only — the password reset — because that is the only message CropGuard sends.
It is built with tables and inline styles: email clients strip <style> blocks and ignore
modern layout, so flexbox and external CSS do not survive. Everything is inlined and the
layout is a single 600px table, which renders consistently in Gmail, Outlook and Apple Mail.
"""

from __future__ import annotations

from typing import Tuple

BRAND_GREEN = "#2f6b3f"
BRAND_DARK = "#1d3f27"
INK = "#1f2420"
MUTED = "#5c6b60"
RULE = "#e2e6e2"
CANVAS = "#f4f6f3"


def password_reset_email(
    reset_url: str,
    expires_minutes: int,
    user_name: str = "",
    app_url: str = "",
) -> Tuple[str, str, str]:
    """Return (subject, html, plain_text) for a password reset message."""
    subject = "Reset your CropGuard password"

    greeting = f"Hello {user_name.strip()}," if user_name.strip() else "Hello,"

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:{CANVAS};">
  <!-- Preheader: shown in the inbox list, hidden in the body. -->
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    Use this link within {expires_minutes} minutes to choose a new password.
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:{CANVAS};padding:28px 12px;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
               style="width:100%;max-width:600px;background:#ffffff;border:1px solid {RULE};border-radius:14px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

          <!-- Brand bar -->
          <tr>
            <td style="background:{BRAND_GREEN};padding:22px 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td style="font-size:19px;font-weight:700;color:#ffffff;letter-spacing:-0.2px;">
                    CropGuard
                  </td>
                  <td align="right" style="font-size:10px;font-weight:600;color:#d8e8db;letter-spacing:1.4px;text-transform:uppercase;">
                    Crop disease screening
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:30px 28px 8px 28px;">
              <h1 style="margin:0 0 12px 0;font-size:21px;line-height:1.3;font-weight:700;color:{INK};">
                Reset your password
              </h1>
              <p style="margin:0 0 14px 0;font-size:14px;line-height:1.65;color:{INK};">
                {greeting}
              </p>
              <p style="margin:0 0 14px 0;font-size:14px;line-height:1.65;color:{INK};">
                Someone asked to reset the password for this CropGuard account. If that was
                you, choose a new password using the button below.
              </p>
            </td>
          </tr>

          <!-- Button -->
          <tr>
            <td align="center" style="padding:6px 28px 4px 28px;">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td align="center" bgcolor="{BRAND_GREEN}"
                      style="border-radius:9px;">
                    <a href="{reset_url}"
                       style="display:inline-block;padding:13px 26px;font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:9px;">
                      Choose a new password
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td style="padding:16px 28px 0 28px;">
              <p style="margin:0;font-size:12px;line-height:1.6;color:{MUTED};text-align:center;">
                This link expires in {expires_minutes} minutes and can be used once.
              </p>
            </td>
          </tr>

          <!-- Fallback link -->
          <tr>
            <td style="padding:22px 28px 0 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="background:{CANVAS};border-radius:10px;">
                <tr>
                  <td style="padding:14px 16px;">
                    <p style="margin:0 0 6px 0;font-size:11px;font-weight:700;letter-spacing:0.6px;text-transform:uppercase;color:{MUTED};">
                      If the button does not work
                    </p>
                    <p style="margin:0;font-size:11px;line-height:1.6;color:{BRAND_DARK};word-break:break-all;font-family:Menlo,Consolas,monospace;">
                      {reset_url}
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- What CropGuard is -->
          <tr>
            <td style="padding:26px 28px 0 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
                <tr><td style="border-top:1px solid {RULE};padding-top:18px;"></td></tr>
              </table>
              <p style="margin:0 0 6px 0;font-size:12px;font-weight:700;color:{INK};">
                What is CropGuard?
              </p>
              <p style="margin:0;font-size:12px;line-height:1.65;color:{MUTED};">
                A field tool for farmers: photograph a diseased leaf, get a screening result
                checked against local weather, and follow the case through expert review.
                Results are a potential diagnosis and an early-screening aid, not a
                replacement for an agricultural expert.
              </p>
            </td>
          </tr>

          <!-- Security note -->
          <tr>
            <td style="padding:20px 28px 0 28px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
                     style="border-left:3px solid {BRAND_GREEN};">
                <tr>
                  <td style="padding:2px 0 2px 12px;">
                    <p style="margin:0;font-size:12px;line-height:1.65;color:{MUTED};">
                      <strong style="color:{INK};">Did not request this?</strong>
                      You can ignore this email — your password will not change. Nobody can
                      reset it without access to this inbox.
                    </p>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr><td style="padding:26px 28px 0 28px;"></td></tr>

          <!-- Footer -->
          <tr>
            <td style="background:{CANVAS};padding:18px 28px;border-top:1px solid {RULE};">
              <p style="margin:0 0 4px 0;font-size:11px;line-height:1.6;color:{MUTED};">
                CropGuard · early detection and management of crop diseases
                {"· " + app_url if app_url else ""}
              </p>
              <p style="margin:0;font-size:10px;line-height:1.6;color:{MUTED};">
                Weather data by Open-Meteo.com (CC BY 4.0). You received this because someone
                requested a password reset for this address. If it was not you, no action is
                needed.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

    text = f"""\
CropGuard — reset your password

{greeting}

Someone asked to reset the password for this CropGuard account. If that was you, open
this link to choose a new password:

{reset_url}

This link expires in {expires_minutes} minutes and can be used once.

Did not request this? You can ignore this email — your password will not change. Nobody
can reset it without access to this inbox.

About CropGuard: a field tool for farmers. Photograph a diseased leaf, get a screening
result checked against local weather, and follow the case through expert review. Results
are a potential diagnosis and an early-screening aid, not a replacement for an
agricultural expert.

Weather data by Open-Meteo.com (CC BY 4.0).
"""
    return subject, html, text
