"""Resend API wrapper for sending approved drafts to website leads.

Sends plain-text emails via Resend API. Used when a prospect's
send_channel is 'resend' (website-originated leads only).
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "marc@info.calmmoney.ca")
REPLY_TO = os.environ.get("RESEND_REPLY_TO", "mpineault1@gmail.com")
API_URL = "https://api.resend.com/emails"


def send_email(to: str, subject: str, body: str) -> str | None:
    """Send a plain-text email via Resend. Returns resend_email_id or None on failure."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — cannot send email")
        return None

    try:
        resp = requests.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": [to],
                "reply_to": REPLY_TO,
                "subject": subject,
                "text": body,
            },
            timeout=10,
        )
        resp.raise_for_status()
        email_id = resp.json().get("id")
        logger.info("Resend email sent to %s — id=%s", to, email_id)
        return email_id
    except Exception:
        logger.exception("Failed to send email via Resend to %s", to)
        return None
