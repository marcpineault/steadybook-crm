"""
Social intake webhook handler.
Receives normalized payloads from n8n for all social/calendar channels.
Validates HMAC signature, parses payload, and creates/updates prospects
via the unified intake pipeline.

Registered as a Flask Blueprint in dashboard.py.
"""

import hashlib
import hmac
import logging
import os

from flask import Blueprint, request, jsonify

from intake_pipeline import IntakeEvent, process_intake_event

logger = logging.getLogger(__name__)

social_intake_bp = Blueprint("social_intake", __name__)

WEBHOOK_SECRET = os.environ.get("STEADYBOOK_WEBHOOK_SECRET", "")

# Channel type → field mappings for building IntakeEvent from data dict
CHANNEL_FIELD_MAP = {
    "instagram_dm":      {"name": "name", "email": None, "phone": None, "company": None, "message": "message"},
    "instagram_ad":      {"name": "name", "email": "email", "phone": "phone", "company": None, "message": None},
    "linkedin_ad":       {"name": "name", "email": "email", "phone": "phone", "company": "company", "message": None},
    "whatsapp":          {"name": "name", "email": None, "phone": "phone", "company": None, "message": "message"},
    "gmail":             {"name": "name", "email": "email", "phone": None, "company": None, "message": "body_preview"},
    "outlook":           {"name": "name", "email": "email", "phone": None, "company": None, "message": "body_preview"},
    "calendly":          {"name": "name", "email": "email", "phone": "phone", "company": None, "message": None},
    "cal_com":           {"name": "name", "email": "email", "phone": "phone", "company": None, "message": None},
    "google_calendar":   {"name": "name", "email": "email", "phone": None, "company": None, "message": "event_title"},
    "outlook_calendar":  {"name": "name", "email": "email", "phone": None, "company": None, "message": "event_title"},
}


def _validate_signature(body: bytes, header: str) -> bool:
    """Validate X-SteadyBook-Signature HMAC header."""
    if not WEBHOOK_SECRET or not header:
        return False
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)


def _build_intake_event(channel: str, data: dict) -> IntakeEvent | None:
    """Map a channel data dict to an IntakeEvent. Returns None if name is missing."""
    field_map = CHANNEL_FIELD_MAP.get(channel, {
        "name": "name", "email": "email", "phone": "phone", "company": "company", "message": "message"
    })

    def get(key):
        src = field_map.get(key)
        return str(data.get(src, "") or "") if src else ""

    name = get("name")
    if not name:
        return None

    return IntakeEvent(
        channel=channel,
        name=name,
        email=get("email"),
        phone=get("phone"),
        company=get("company"),
        message=get("message"),
        raw=data,
    )


@social_intake_bp.route("/api/social-intake", methods=["POST"])
def social_intake():
    """Receive normalized webhook payload from n8n."""
    body = request.get_data()
    sig_header = request.headers.get("X-SteadyBook-Signature", "")

    if not _validate_signature(body, sig_header):
        logger.warning("Social intake: invalid or missing signature")
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    channel = str(payload.get("type", "")).strip()
    tenant_id_raw = payload.get("tenant_id", 1)
    try:
        tenant_id = int(tenant_id_raw)
    except (TypeError, ValueError):
        tenant_id = 1

    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}

    event = _build_intake_event(channel, data)
    if event is None:
        return jsonify({"error": "name is required"}), 400

    try:
        process_intake_event(event, tenant_id=tenant_id)
    except Exception as e:
        logger.error("social_intake process error: %s", e)
        return jsonify({"error": "processing failed"}), 500

    return jsonify({"status": "ok"}), 200
