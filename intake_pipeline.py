"""
Unified intake pipeline.
Normalizes all incoming lead events from any channel into a consistent
prospect record. Handles deduplication, intent classification, and
initial tagging.

Usage:
    ev = IntakeEvent(channel="instagram_dm", name="Sarah Chen", ...)
    process_intake_event(ev, tenant_id=1)
"""

import logging
from dataclasses import dataclass, field

import db

logger = logging.getLogger(__name__)

# Channels that indicate a scheduled meeting (booking intent)
BOOKING_CHANNELS = {"calendly", "cal_com", "google_calendar", "outlook_calendar"}

# Channel → source tag mapping
CHANNEL_TAGS = {
    "instagram_dm": "source_instagram",
    "instagram_ad": "source_instagram_ad",
    "linkedin_ad": "source_linkedin",
    "whatsapp": "source_whatsapp",
    "gmail": "source_email",
    "outlook": "source_email",
    "calendly": "source_calendly",
    "cal_com": "source_cal_com",
    "google_calendar": "source_calendar",
    "outlook_calendar": "source_calendar",
    "qr_code": "source_qr",
    "business_card": "source_card",
    "telegram": "source_telegram",
}


@dataclass
class IntakeEvent:
    """Normalized representation of an incoming lead event."""
    channel: str
    name: str
    email: str = ""
    phone: str = ""
    company: str = ""
    message: str = ""
    raw: dict = field(default_factory=dict)


def classify_intent(event: dict) -> str:
    """
    Classify the intent of an incoming event.
    Returns 'booking' or 'lead'.
    event: raw dict with 'type' and 'data' keys.
    """
    channel = event.get("type", "")
    if channel in BOOKING_CHANNELS:
        return "booking"
    return "lead"


class EntityResolver:
    """Deduplicates incoming leads against existing prospects."""

    @staticmethod
    def resolve(event: "IntakeEvent", tenant_id: int) -> dict | None:
        """
        Look up existing prospect by email → phone → name.
        Returns prospect dict if found, None if new.
        """
        if event.email:
            existing = db.get_prospect_by_email(event.email, tenant_id=tenant_id)
            if existing:
                return existing
        if event.phone:
            existing = db.get_prospect_by_phone(event.phone, tenant_id=tenant_id)
            if existing:
                return existing
        if event.name:
            existing = db.get_prospect_by_name(event.name, tenant_id=tenant_id)
            if existing:
                return existing
        return None


class ActionExecutor:
    """Executes the appropriate action based on intent and entity resolution."""

    @staticmethod
    def execute(event: "IntakeEvent", intent: str, existing: dict | None, tenant_id: int) -> None:
        """
        Create or update a prospect based on the intake event.
        Applies source tag and queues enrichment for new prospects.
        """
        if existing:
            ActionExecutor._handle_existing(event, existing, intent)
        else:
            ActionExecutor._handle_new(event, intent, tenant_id)

    @staticmethod
    def _handle_new(event: "IntakeEvent", intent: str, tenant_id: int) -> None:
        """Create a new prospect and apply initial tags."""
        stage = "Meeting Booked" if intent == "booking" else "New Lead"
        data = {
            "name": event.name,
            "email": event.email,
            "phone": event.phone,
            "company": event.company,
            "source": event.channel,
            "stage": stage,
            "notes": event.message,
        }
        db.add_prospect(data, tenant_id=tenant_id)

        # Look up the created prospect to get ID for tagging
        prospect = db.get_prospect_by_name(event.name, tenant_id=tenant_id)
        if not prospect:
            logger.warning("Could not retrieve prospect after add: %s", event.name)
            return

        pid = prospect.get("id")
        if not pid:
            return

        db.apply_tag(pid, "new_lead")
        source_tag = CHANNEL_TAGS.get(event.channel, "source_other")
        db.apply_tag(pid, source_tag)
        if intent == "booking":
            db.apply_tag(pid, "booked_meeting")
        db.queue_enrichment(pid)
        logger.info("New prospect created: %s (id=%d, channel=%s)", event.name, pid, event.channel)

    @staticmethod
    def _handle_existing(event: "IntakeEvent", existing: dict, intent: str) -> None:
        """Update existing prospect with new interaction data."""
        pid = existing.get("id")
        if not pid:
            return

        source_tag = CHANNEL_TAGS.get(event.channel, "source_other")
        db.apply_tag(pid, source_tag)
        if intent == "booking":
            db.apply_tag(pid, "booked_meeting")
        logger.info("Existing prospect updated: %s (id=%d, channel=%s)", existing.get("name"), pid, event.channel)


def process_intake_event(event: IntakeEvent, tenant_id: int = 1) -> None:
    """
    Main entry point for processing a normalized intake event.
    Called by social_intake.py and Telegram handlers for all new leads.
    """
    try:
        intent = classify_intent({"type": event.channel, "data": {}})
        existing = EntityResolver.resolve(event, tenant_id)
        ActionExecutor.execute(event, intent, existing, tenant_id)
    except Exception as e:
        logger.error("process_intake_event failed for %s: %s", event.name, e)
