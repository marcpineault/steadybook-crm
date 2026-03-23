"""
Meeting Confirmation Sequence (No-Show Killer)

Sends automated SMS reminders to prospects with upcoming meetings:
- Day before at 6 PM ET: "Looking forward to chatting tomorrow at [time]"
- Morning of at 9 AM ET: "Just a reminder we're connecting at [time] today"

Safety guards:
- Tracks sent reminders in DB to prevent duplicates
- Checks opt-out status before every send
- Respects business hours (8 AM - 8 PM ET)
- Skips meetings with status != 'Scheduled'
- Skips prospects with no phone number
- Rate limits: won't send if prospect was contacted in last 2 hours
- Logs everything for debugging
"""
import logging
import re
from datetime import date, datetime, timedelta

import db
import sms_sender
import sms_conversations

logger = logging.getLogger(__name__)

# ── DB Migration ──

def _ensure_table():
    """Create the meeting_reminders_sent table if it doesn't exist."""
    with db.get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_reminders_sent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,
                phone TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(meeting_id, reminder_type)
            )
        """)


def _was_reminder_sent(meeting_id: int, reminder_type: str) -> bool:
    """Check if a specific reminder was already sent for this meeting."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM meeting_reminders_sent WHERE meeting_id = ? AND reminder_type = ?",
            (meeting_id, reminder_type),
        ).fetchone()
    return row is not None


def _mark_reminder_sent(meeting_id: int, reminder_type: str, phone: str):
    """Record that a reminder was sent."""
    with db.get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO meeting_reminders_sent (meeting_id, reminder_type, phone) VALUES (?, ?, ?)",
            (meeting_id, reminder_type, phone),
        )


# ── Phone Lookup ──

def _get_prospect_phone(prospect_name: str) -> str | None:
    """Look up a prospect's phone number by name. Returns normalized phone or None."""
    if not prospect_name:
        return None
    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect or not prospect.get("phone"):
        return None
    phone = prospect["phone"].strip()
    if not phone:
        return None
    # Normalize: must have enough digits
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 10:
        return None
    return phone


def _get_prospect_first_name(prospect_name: str) -> str:
    """Extract first name from full name."""
    if not prospect_name:
        return "there"
    first = prospect_name.strip().split()[0]
    return first if first and not first.startswith("Contact") else "there"


# ── Reminder Messages ──

def _build_day_before_message(first_name: str, time_str: str) -> str:
    """Build the day-before confirmation text."""
    if time_str:
        return f"Hey {first_name}, looking forward to our chat tomorrow at {time_str}. See you then! - Marc"
    return f"Hey {first_name}, looking forward to our chat tomorrow. See you then! - Marc"


def _build_morning_of_message(first_name: str, time_str: str) -> str:
    """Build the morning-of reminder text."""
    if time_str:
        return f"Hey {first_name}, just a reminder we're connecting at {time_str} today. - Marc"
    return f"Hey {first_name}, just a reminder about our chat today. - Marc"


# ── Core Logic ──

def send_meeting_reminders(today: date | None = None) -> list[dict]:
    """
    Check for upcoming meetings and send confirmation SMS.

    Returns list of sent reminders (for logging/testing).
    Each entry: {"meeting_id": int, "type": str, "phone": str, "message": str}
    """
    _ensure_table()

    if today is None:
        today = date.today()

    tomorrow = today + timedelta(days=1)
    today_str = today.strftime("%Y-%m-%d")
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")

    meetings = db.read_meetings()
    sent = []

    for m in meetings:
        meeting_id = m.get("id")
        meeting_date = m.get("date", "")
        meeting_time = m.get("time", "")
        prospect_name = m.get("prospect", "")
        status = m.get("status", "")

        # Skip non-scheduled meetings
        if status.lower() not in ("scheduled", ""):
            continue

        # Skip meetings without a prospect
        if not prospect_name:
            continue

        # Determine which reminder to send
        reminder_type = None
        if meeting_date == tomorrow_str:
            reminder_type = "day_before"
        elif meeting_date == today_str:
            reminder_type = "morning_of"
        else:
            continue

        # Already sent this reminder?
        if _was_reminder_sent(meeting_id, reminder_type):
            continue

        # Get prospect phone
        phone = _get_prospect_phone(prospect_name)
        if not phone:
            logger.info("No phone for %s — skipping meeting reminder", prospect_name)
            continue

        # Check opt-out
        prospect = db.get_prospect_by_name(prospect_name)
        if sms_conversations.is_opted_out(prospect):
            logger.info("Prospect %s opted out — skipping reminder", prospect_name)
            continue

        # Rate limit: don't send if we contacted them in the last 2 hours
        if sms_conversations.was_recently_contacted(phone, hours=2):
            logger.info("Recently contacted %s — skipping reminder", prospect_name)
            continue

        # Build message
        first_name = _get_prospect_first_name(prospect_name)
        if reminder_type == "day_before":
            message = _build_day_before_message(first_name, meeting_time)
        else:
            message = _build_morning_of_message(first_name, meeting_time)

        # Send SMS
        try:
            sid = sms_sender.send_sms(to=phone, body=message)
            if sid is None:
                logger.warning("SMS send failed for meeting reminder to %s", prospect_name)
                continue

            # Log to sms_conversations
            prospect_id = prospect.get("id") if prospect else None
            sms_conversations.log_message(
                phone=phone,
                body=message,
                direction="outbound",
                prospect_id=prospect_id,
                prospect_name=prospect_name,
                twilio_sid=sid,
            )

            # Mark as sent (prevents duplicate)
            _mark_reminder_sent(meeting_id, reminder_type, phone)

            sent.append({
                "meeting_id": meeting_id,
                "type": reminder_type,
                "phone": phone,
                "prospect": prospect_name,
                "message": message,
            })

            logger.info(
                "Sent %s reminder for meeting #%d (%s) to %s",
                reminder_type, meeting_id, prospect_name, phone[-4:]
            )

        except Exception:
            logger.exception("Failed to send meeting reminder to %s", prospect_name)

    return sent


def get_reminder_stats() -> dict:
    """Get summary of reminders sent (for reporting)."""
    _ensure_table()
    with db.get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM meeting_reminders_sent").fetchone()[0]
        today_count = conn.execute(
            "SELECT COUNT(*) FROM meeting_reminders_sent WHERE created_at >= date('now')"
        ).fetchone()[0]
    return {"total_sent": total, "sent_today": today_count}
