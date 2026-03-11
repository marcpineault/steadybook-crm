"""
Lead intake logic — processes bookings, referral emails, and forwarded leads.
Shared by webhook_intake.py (HTTP payloads) and bot.py (Telegram messages).
"""

import json
import logging
import os
from datetime import datetime

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def process_booking(data: dict) -> str:
    """Process an Outlook Bookings payload from Power Automate."""
    name = (data.get("name") or "").strip()
    if not name:
        return "No name in booking payload."

    email = data.get("email", "")
    phone = data.get("phone", "")
    service = data.get("service", "")
    start_time = data.get("start_time", "")
    notes = data.get("notes", "")

    booking_notes = f"Booked: {service}" if service else "Booked via Outlook"
    if notes:
        booking_notes += f" | {notes}"

    existing = db.get_prospect_by_name(name)
    if existing:
        old_notes = existing.get("notes", "")
        combined = f"{old_notes} | [Booking] {booking_notes}" if old_notes else f"[Booking] {booking_notes}"
        updates = {"notes": combined}
        if email and not existing.get("email"):
            updates["email"] = email
        if phone and not existing.get("phone"):
            updates["phone"] = phone
        db.update_prospect(name, updates)

        if start_time:
            meeting_date, meeting_time = _parse_datetime(start_time)
            db.add_meeting({
                "date": meeting_date, "time": meeting_time,
                "prospect": existing["name"], "type": service or "Consultation",
                "prep_notes": booking_notes,
            })

        db.add_interaction({
            "prospect": existing["name"], "source": "outlook_booking",
            "raw_text": json.dumps(data), "summary": booking_notes,
        })
        return f"Updated {existing['name']} with new booking. Meeting added."
    else:
        db.add_prospect({
            "name": name, "email": email, "phone": phone,
            "source": "Outlook Booking", "stage": "New Lead",
            "priority": "Warm", "product": _guess_product(service, notes),
            "notes": booking_notes,
        })

        if start_time:
            meeting_date, meeting_time = _parse_datetime(start_time)
            db.add_meeting({
                "date": meeting_date, "time": meeting_time,
                "prospect": name, "type": service or "Consultation",
                "prep_notes": booking_notes,
            })

        db.add_interaction({
            "prospect": name, "source": "outlook_booking",
            "raw_text": json.dumps(data), "summary": booking_notes,
        })
        db.add_activity({
            "prospect": name, "action": "Outlook Booking received",
            "outcome": booking_notes, "next_step": "Prepare for meeting",
        })
        _score_and_schedule(name)
        return f"New prospect: {name} — {booking_notes}. Meeting added."


def process_email_lead(data: dict) -> str:
    """Process a forwarded lead email from Zapier."""
    body = data.get("body", "")
    subject = data.get("subject", "")
    sender = data.get("from", "")

    if not body and not subject:
        return "Empty email payload — nothing to process."

    email_text = f"Subject: {subject}\nFrom: {sender}\n\n{body}"

    prompt = f"""You are a sales assistant for Marc, a financial advisor at Co-operators in London, Ontario.

Extract prospect information from this forwarded email/lead notification.

EMAIL:
{email_text[:3000]}

Return a JSON object:
{{
  "name": "Full Name",
  "phone": "phone number if mentioned",
  "email": "email if mentioned",
  "product": "Insurance type or financial product they need",
  "notes": "Key details about the prospect",
  "priority": "Hot / Warm / Cold",
  "source": "Referral from [name]" or "Co-operators Lead" or "Email Lead",
  "stage": "New Lead"
}}

Return ONLY valid JSON."""

    try:
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_completion_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        prospect = json.loads(raw)
    except Exception as e:
        logger.error(f"AI extraction failed for email lead: {e}")
        return f"Could not parse lead email. Raw text saved for manual review.\n\nSubject: {subject}"

    name = prospect.get("name", "").strip()
    if not name:
        return "Could not extract a prospect name from the email."

    existing = db.get_prospect_by_name(name)
    if existing:
        old_notes = existing.get("notes", "")
        new_notes = prospect.get("notes", "")
        combined = f"{old_notes} | [Email Lead] {new_notes}" if old_notes else f"[Email Lead] {new_notes}"
        db.update_prospect(name, {"notes": combined})
        result = f"Updated {existing['name']} with email lead details."
    else:
        db.add_prospect({
            "name": name,
            "phone": prospect.get("phone", ""),
            "email": prospect.get("email", ""),
            "source": prospect.get("source", "Email Lead"),
            "priority": prospect.get("priority", "Warm"),
            "stage": prospect.get("stage", "New Lead"),
            "product": prospect.get("product", ""),
            "notes": prospect.get("notes", ""),
        })
        result = f"New prospect: {name} — {prospect.get('product', '?')} ({prospect.get('source', 'Email Lead')})"

    db.add_interaction({
        "prospect": name, "source": "email_lead",
        "raw_text": email_text[:2000],
        "summary": prospect.get("notes", ""),
    })
    db.add_activity({
        "prospect": name, "action": "Lead intake (email)",
        "outcome": prospect.get("notes", ""),
        "next_step": "Initial contact",
    })

    if not existing:
        _score_and_schedule(name)

    return result


def _score_and_schedule(name: str):
    """Score a newly created prospect and set their first follow-up date."""
    import scoring
    from datetime import timedelta

    prospect = db.get_prospect_by_name(name)
    if not prospect:
        return

    score_data = scoring.score_prospect(prospect)
    score = score_data.get("score", 0)

    if score >= 70:
        priority = "Hot"
    elif score >= 40:
        priority = "Warm"
    else:
        priority = "Cold"

    days = {"Hot": 1, "Warm": 2, "Cold": 5}.get(priority, 3)
    next_followup = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    db.update_prospect(name, {"priority": priority, "next_followup": next_followup})
    logger.info(f"Scored {name}: {score}/100 ({priority}), follow-up {next_followup}")


def _parse_datetime(dt_str: str) -> tuple[str, str]:
    """Parse an ISO datetime string into (date, time) strings."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d"), dt.strftime("%I:%M %p")
    except (ValueError, AttributeError):
        return dt_str[:10] if len(dt_str) >= 10 else "", ""


def _guess_product(service: str, notes: str) -> str:
    """Guess the insurance product from booking service name and notes."""
    text = f"{service} {notes}".lower()
    if "life" in text:
        return "Life Insurance"
    if "disab" in text:
        return "Disability Insurance"
    if "home" in text or "house" in text or "property" in text:
        return "Home Insurance"
    if "auto" in text or "car" in text or "vehicle" in text:
        return "Auto Insurance"
    if "commercial" in text or "business" in text:
        return "Commercial Insurance"
    if "wealth" in text or "invest" in text or "rrsp" in text or "tfsa" in text:
        return "Wealth Management"
    return ""
