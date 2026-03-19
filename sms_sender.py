"""Sendblue API wrapper for sending iMessage/SMS to prospects."""
import logging
import os
import re

import requests

logger = logging.getLogger(__name__)

SENDBLUE_API_KEY    = os.environ.get("SENDBLUE_API_KEY", "")
SENDBLUE_API_SECRET = os.environ.get("SENDBLUE_API_SECRET", "")
FROM_NUMBER         = os.environ.get("SENDBLUE_FROM_NUMBER", "")
API_URL = "https://api.sendblue.co/api/send-message"

def _normalize_phone(phone: str) -> str:
    """Normalize phone to E.164 format (+1XXXXXXXXXX for North American numbers)."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10: return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1": return f"+{digits}"
    return f"+{digits}"

def send_sms(to: str, body: str) -> str | None:
    """Send iMessage/SMS via Sendblue. Returns message_handle or None."""
    if not SENDBLUE_API_KEY or not SENDBLUE_API_SECRET:
        logger.warning("Sendblue credentials not set")
        return None
    normalized = _normalize_phone(to)
    try:
        resp = requests.post(API_URL,
            headers={"sb-api-key-id": SENDBLUE_API_KEY,
                     "sb-api-secret-key": SENDBLUE_API_SECRET,
                     "Content-Type": "application/json"},
            json={"number": normalized, "content": body}, timeout=10)
        resp.raise_for_status()
        handle = resp.json().get("message_handle")
        from pii import redact_phone
        logger.info("SMS sent to %s handle=%s", redact_phone(to), handle)
        return handle
    except Exception:
        from pii import redact_phone
        logger.exception("SMS send failed to %s", redact_phone(to))
        return None
