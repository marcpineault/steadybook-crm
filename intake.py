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


def process_calendar_event(data: dict) -> str:
    """Process an Outlook calendar event from Power Automate.

    Handles multiple attendees, location, categories, and meeting type detection.
    Expected data fields:
      subject, start_time, end_time, location, body, categories,
      attendees: [{"name": "...", "email": "..."}]
      is_online (bool), online_meeting_url
    """
    subject = (data.get("subject") or "").strip()
    if not subject:
        return "No subject in calendar event."

    attendees = data.get("attendees") or []
    # If attendees came as a single dict (one attendee), wrap it
    if isinstance(attendees, dict):
        attendees = [attendees]

    start_time = data.get("start_time") or data.get("date") or ""
    end_time = data.get("end_time") or ""
    location = data.get("location") or ""
    body = data.get("body") or data.get("notes") or ""
    categories = data.get("categories") or ""
    is_online = data.get("is_online", False)
    online_url = data.get("online_meeting_url") or ""

    # Build meeting context
    meeting_type = _classify_meeting(subject, location, is_online)
    meeting_details = f"{meeting_type}: {subject}"
    if location:
        meeting_details += f" @ {location}"
    if is_online and online_url:
        meeting_details += " (virtual)"
    if body:
        meeting_details += f" | {body[:200]}"

    results = []

    if not attendees:
        # No attendees — just log the meeting for Marc's schedule
        if start_time:
            meeting_date, meeting_time = _parse_datetime(start_time)
            db.add_meeting({
                "date": meeting_date, "time": meeting_time,
                "prospect": "", "type": meeting_type,
                "prep_notes": meeting_details,
            })
        results.append(f"Calendar event logged: {subject}")
    else:
        for att in attendees:
            att_name = (att.get("name") or "").strip()
            att_email = (att.get("email") or "").strip()

            if not att_name and not att_email:
                continue
            if not att_name:
                att_name = att_email.split("@")[0].replace(".", " ").title()

            # Skip Marc's own email
            if att_email and "marcpineault" in att_email.lower():
                continue
            if att_email and "pineault" in att_email.lower() and "cooperators" in att_email.lower():
                continue

            existing = db.get_prospect_by_name(att_name)
            if existing:
                old_notes = existing.get("notes", "")
                combined = f"{old_notes} | [Calendar] {meeting_details}" if old_notes else f"[Calendar] {meeting_details}"
                updates = {"notes": combined}
                if att_email and not existing.get("email"):
                    updates["email"] = att_email
                db.update_prospect(att_name, updates)
                action = f"Updated {existing['name']}"
            else:
                product = _guess_product(subject, body)
                db.add_prospect({
                    "name": att_name, "email": att_email, "phone": "",
                    "source": "Calendar Event", "stage": "New Lead",
                    "priority": "Warm", "product": product,
                    "notes": f"[Calendar] {meeting_details}",
                })
                _score_and_schedule(att_name)
                action = f"New prospect: {att_name}"

            # Add meeting entry
            if start_time:
                meeting_date, meeting_time = _parse_datetime(start_time)
                db.add_meeting({
                    "date": meeting_date, "time": meeting_time,
                    "prospect": att_name, "type": meeting_type,
                    "prep_notes": meeting_details,
                })

            db.add_interaction({
                "prospect": att_name, "source": "calendar_event",
                "raw_text": json.dumps(data)[:2000],
                "summary": meeting_details,
            })
            db.add_activity({
                "prospect": att_name, "action": f"Calendar event: {meeting_type}",
                "outcome": subject, "next_step": "Prepare for meeting",
            })
            results.append(action)

    if not results:
        return f"Calendar event logged: {subject} (no attendees to track)"

    return f"Calendar event processed:\n" + "\n".join(f"  {r}" for r in results)


def _classify_meeting(subject: str, location: str, is_online: bool) -> str:
    """Classify a calendar event into a meeting type."""
    import re
    text = f" {subject} {location} ".lower()

    def _has_word(*words):
        return any(re.search(rf"\b{w}\b", text) for w in words)

    if _has_word("discovery", "intro", "initial", "first"):
        return "Discovery Call"
    if _has_word("review", "annual", "check-in", "checkin"):
        return "Review Meeting"
    if _has_word("presentation", "proposal") or "plan pres" in text:
        return "Plan Presentation"
    if _has_word("sign", "closing", "paperwork"):
        return "Closing"
    if _has_word("needs", "analysis") or "fact find" in text:
        return "Needs Analysis"
    if is_online or _has_word("teams", "zoom", "virtual", "call", "phone"):
        return "Call"
    return "Meeting"


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
            model="gpt-4.1-nano",
            max_completion_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()

        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rstrip()
            if raw.endswith("```"):
                raw = raw[:-3].rstrip()
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
        updates = {"notes": combined}
        if prospect.get("email") and not existing.get("email"):
            updates["email"] = prospect["email"]
        if prospect.get("phone") and not existing.get("phone"):
            updates["phone"] = prospect["phone"]
        db.update_prospect(name, updates)
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
