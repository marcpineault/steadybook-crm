"""Twilio SMS wrapper for sending pre-call texts to prospects."""
import logging
import os
import re

from twilio.rest import Client

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN  = os.environ.get("TWILIO_AUTH_TOKEN", "")
FROM_NUMBER        = os.environ.get("TWILIO_FROM_NUMBER", "")


def _normalize_phone(phone: str) -> str:
    """Normalize phone to E.164 format (+1XXXXXXXXXX for North American numbers)."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return f"+{digits}"


def send_sms(to: str, body: str) -> str | None:
    """Send SMS via Twilio. Returns message SID or None on failure."""
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not FROM_NUMBER:
        logger.warning("Twilio credentials not set")
        return None
    normalized = _normalize_phone(to)
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            body=body,
            from_=FROM_NUMBER,
            to=normalized,
        )
        from pii import redact_phone
        logger.info("SMS sent to %s sid=%s", redact_phone(to), message.sid)
        return message.sid
    except Exception:
        from pii import redact_phone
        logger.exception("SMS send failed to %s", redact_phone(to))
        return None
