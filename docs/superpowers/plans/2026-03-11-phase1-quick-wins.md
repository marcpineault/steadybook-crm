# Phase 1: Quick Wins — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add voice note transcription, lead intake via email/Telegram, and auto-intake from Outlook Bookings to the existing Telegram bot.

**Architecture:** Extend the existing Flask app with a new `/api/intake` webhook endpoint that accepts payloads from Zapier and Power Automate. Add a Telegram voice message handler that transcribes via OpenAI Whisper and uses GPT to extract prospect data. All new features use the existing `db.py` CRUD operations and `bot.py` tool-calling pattern.

**Tech Stack:** Python, Flask, python-telegram-bot, OpenAI Whisper API, OpenAI GPT-4.1, SQLite, Zapier (external), Power Automate (external)

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `voice_handler.py` | Create | Voice message download, Whisper transcription, AI extraction, pipeline update |
| `webhook_intake.py` | Create | Flask blueprint for `/api/intake` endpoint — receives Zapier/Power Automate payloads, validates auth, routes to intake logic |
| `intake.py` | Create | Shared lead intake logic — AI parsing of email text / booking data, prospect creation, scoring, activity logging |
| `bot.py` | Modify | Register voice handler, import webhook blueprint |
| `dashboard.py` | Modify | Register webhook_intake blueprint |
| `db.py` | Modify | Add `interactions` table for logging all touchpoints with source tracking |
| `requirements.txt` | Modify | No new deps needed (openai already included, Flask already included). Note: `pytest` must be installed separately for tests (`pip install pytest`). |
| `tests/__init__.py` | Create | Empty file to make tests a package |
| `tests/test_voice_handler.py` | Create | Tests for transcription + extraction logic |
| `tests/test_intake.py` | Create | Tests for lead intake parsing (booking tests run without API, email tests mock OpenAI) |
| `tests/test_webhook_intake.py` | Create | Tests for webhook endpoint auth + routing |

---

## Chunk 0: Project Setup

### Task 0: Create tests directory

- [ ] **Step 1: Create tests directory and __init__.py**

```bash
mkdir -p /Users/map98/Desktop/calm-money-bot/tests
touch /Users/map98/Desktop/calm-money-bot/tests/__init__.py
```

- [ ] **Step 2: Verify pytest is available**

Run: `pip install pytest` (if not already installed)

---

## Chunk 1: Database — Interactions Table

### Task 1: Add interactions table to db.py

**Files:**
- Modify: `db.py:97-160` (init_db function)
- Create: `tests/test_db_interactions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_interactions.py`:

```python
import os
import sys
import sqlite3

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    """Reset test database before each test."""
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_add_interaction():
    result = db.add_interaction({
        "prospect": "John Smith",
        "source": "voice_note",
        "raw_text": "Just had coffee with John Smith, interested in life insurance",
        "summary": "Met for coffee, interested in life insurance",
        "action_items": "Send quote by Friday",
    })
    assert "Logged interaction" in result


def test_read_interactions():
    db.add_interaction({
        "prospect": "John Smith",
        "source": "voice_note",
        "raw_text": "test transcript",
    })
    db.add_interaction({
        "prospect": "Sarah Chen",
        "source": "otter_transcript",
        "raw_text": "test transcript 2",
    })
    interactions = db.read_interactions()
    assert len(interactions) == 2
    assert interactions[0]["prospect"] == "Sarah Chen"  # newest first


def test_read_interactions_by_prospect():
    db.add_interaction({"prospect": "John Smith", "source": "voice_note", "raw_text": "a"})
    db.add_interaction({"prospect": "Sarah Chen", "source": "voice_note", "raw_text": "b"})
    interactions = db.read_interactions(prospect="John")
    assert len(interactions) == 1
    assert interactions[0]["prospect"] == "John Smith"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_db_interactions.py -v`
Expected: FAIL with `AttributeError: module 'db' has no attribute 'add_interaction'`

- [ ] **Step 3: Add interactions table to init_db and CRUD functions**

In `db.py`, add the `interactions` table to the `init_db()` executescript, after the `win_loss_log` table:

```python
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT DEFAULT (datetime('now')),
                prospect TEXT DEFAULT '',
                source TEXT DEFAULT '',
                raw_text TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                action_items TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );
```

Then add these functions after the `get_win_loss_stats()` function in `db.py`:

```python
# ── Interactions CRUD ──

def add_interaction(data: dict) -> str:
    """Log an interaction (voice note, transcript, email, booking)."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO interactions (date, prospect, source, raw_text, summary, action_items)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                data.get("prospect", ""),
                data.get("source", ""),
                data.get("raw_text", ""),
                data.get("summary", ""),
                data.get("action_items", ""),
            ),
        )
    return f"Logged interaction for {data.get('prospect', 'unknown')} via {data.get('source', '?')}."


def read_interactions(limit: int = 50, prospect: str = ""):
    """Return recent interactions, newest first. Optionally filter by prospect."""
    with get_db() as conn:
        if prospect:
            rows = conn.execute(
                "SELECT * FROM interactions WHERE LOWER(prospect) LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{prospect.lower()}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM interactions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return _rows_to_dicts(rows)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_db_interactions.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db_interactions.py
git commit -m "feat: add interactions table for tracking all touchpoints"
```

---

## Chunk 2: Voice Note Handler

### Task 2: Create voice_handler.py — transcription + AI extraction

**Files:**
- Create: `voice_handler.py`
- Create: `tests/test_voice_handler.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_voice_handler.py`:

```python
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_extract_prospect_data_from_transcript():
    """Test that AI extraction prompt returns valid structured JSON."""
    from voice_handler import build_extraction_prompt

    transcript = "Just had coffee with John Smith, he's interested in life insurance for his wife, currently has auto and home with us, wants a quote by Friday"
    prompt = build_extraction_prompt(transcript)

    assert "John Smith" in prompt or "transcript" in prompt.lower()
    assert "prospect" in prompt.lower()
    assert "action_items" in prompt.lower()


def test_parse_extraction_response_valid():
    from voice_handler import parse_extraction_response

    raw = json.dumps({
        "prospects": [{
            "name": "John Smith",
            "product": "Life Insurance",
            "notes": "Interested in life insurance for wife",
            "action_items": "Send quote by Friday",
            "source": "voice_note",
        }]
    })
    result = parse_extraction_response(raw)
    assert len(result) == 1
    assert result[0]["name"] == "John Smith"
    assert result[0]["product"] == "Life Insurance"


def test_parse_extraction_response_with_referral():
    from voice_handler import parse_extraction_response

    raw = json.dumps({
        "prospects": [
            {"name": "John Smith", "product": "Life Insurance", "notes": "Wants quote", "action_items": "Quote by Friday", "source": "voice_note"},
            {"name": "Mike Smith", "product": "Commercial Insurance", "notes": "John's brother, plumbing business", "action_items": "Initial contact", "source": "referral"},
        ]
    })
    result = parse_extraction_response(raw)
    assert len(result) == 2
    assert result[1]["name"] == "Mike Smith"


def test_parse_extraction_response_invalid_json():
    from voice_handler import parse_extraction_response

    result = parse_extraction_response("this is not json at all")
    assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_voice_handler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'voice_handler'`

- [ ] **Step 3: Create voice_handler.py**

```python
"""
Voice note handler for the Telegram bot.

Handles: voice message download, Whisper transcription, AI extraction
of prospect data, and pipeline updates.
"""

import json
import logging
import os
import tempfile

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def build_extraction_prompt(transcript: str) -> str:
    """Build the prompt for extracting prospect data from a voice note transcript."""
    return f"""You are a sales assistant for Marc, a financial advisor at Co-operators in London, Ontario.

Analyze this voice note transcript and extract ALL prospects mentioned (including referrals).

TRANSCRIPT:
{transcript}

Return a JSON object with this exact structure:
{{
  "prospects": [
    {{
      "name": "Full Name",
      "product": "Life Insurance / Disability Insurance / Wealth Management / Commercial Insurance / Auto Insurance / Home Insurance / etc.",
      "notes": "Key details from the conversation",
      "action_items": "Specific next steps with dates if mentioned",
      "source": "voice_note or referral (if this person was mentioned as a referral)",
      "phone": "",
      "email": "",
      "priority": "Hot / Warm / Cold (based on interest level)",
      "stage": "New Lead / Contacted / Discovery Call / Needs Analysis (based on context)"
    }}
  ]
}}

Rules:
- Extract ALL people mentioned, including referrals ("his brother", "her friend", etc.)
- For referrals, set source to "referral" and include who referred them in notes
- If no specific name is given for a referral, use a placeholder like "John's Brother"
- Guess stage from context: just met = "Discovery Call", wants quote = "Needs Analysis", initial mention = "New Lead"
- Return ONLY valid JSON, no other text"""


def parse_extraction_response(raw: str) -> list[dict]:
    """Parse the AI extraction response into a list of prospect dicts."""
    try:
        # Handle markdown code blocks
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            if text.startswith("json"):
                text = text[4:].strip()

        data = json.loads(text)
        prospects = data.get("prospects", [])
        if not isinstance(prospects, list):
            return []
        return prospects
    except (json.JSONDecodeError, AttributeError, KeyError):
        logger.warning(f"Failed to parse extraction response: {raw[:200]}")
        return []


async def transcribe_voice(file_path: str) -> str:
    """Transcribe a voice note file using OpenAI Whisper API."""
    with open(file_path, "rb") as audio_file:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
    return transcript.text


async def extract_and_update(transcript: str, bot=None) -> str:
    """Extract prospect data from transcript, update pipeline, return summary."""
    prompt = build_extraction_prompt(transcript)

    response = client.chat.completions.create(
        model="gpt-4.1",
        max_completion_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content
    prospects = parse_extraction_response(raw)

    if not prospects:
        return f"Could not extract prospect data from your voice note. Here's what I heard:\n\n{transcript}\n\nTry again or add manually with /add."

    results = []
    for p in prospects:
        name = p.get("name", "").strip()
        if not name:
            continue

        # Check if prospect already exists
        existing = db.get_prospect_by_name(name)
        if existing:
            # Update existing prospect with new notes
            old_notes = existing.get("notes", "")
            new_notes = p.get("notes", "")
            action_items = p.get("action_items", "")
            combined = f"{old_notes} | [Voice] {new_notes}"
            if action_items:
                combined += f" | Action: {action_items}"

            updates = {"notes": combined.strip(" |")}
            if p.get("stage") and p["stage"] != "New Lead":
                updates["stage"] = p["stage"]
            if p.get("priority"):
                updates["priority"] = p["priority"]

            db.update_prospect(name, updates)
            results.append(f"Updated {existing['name']} — added voice note details")
        else:
            # Create new prospect
            db.add_prospect({
                "name": name,
                "phone": p.get("phone", ""),
                "email": p.get("email", ""),
                "source": p.get("source", "voice_note"),
                "priority": p.get("priority", "Warm"),
                "stage": p.get("stage", "New Lead"),
                "product": p.get("product", ""),
                "notes": p.get("notes", ""),
            })
            results.append(f"New prospect: {name} — {p.get('product', '?')}")

        # Log interaction
        db.add_interaction({
            "prospect": name,
            "source": "voice_note",
            "raw_text": transcript,
            "summary": p.get("notes", ""),
            "action_items": p.get("action_items", ""),
        })

        # Log activity
        db.add_activity({
            "prospect": name,
            "action": "Voice note processed",
            "outcome": p.get("notes", ""),
            "next_step": p.get("action_items", ""),
        })

    summary = "Voice note processed:\n" + "\n".join(f"  {r}" for r in results)
    return summary


async def handle_voice_message(update, context):
    """Telegram handler for voice messages."""
    voice = update.message.voice or update.message.audio
    if not voice:
        return

    chat_id = update.effective_chat.id
    logger.info(f"Voice message received, duration: {voice.duration}s")

    await update.message.reply_text("Got your voice note, processing...")

    try:
        # Download the voice file
        file = await voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

        # Transcribe
        transcript = await transcribe_voice(tmp_path)
        logger.info(f"Transcription: {transcript[:200]}")

        # Clean up temp file
        os.unlink(tmp_path)

        if not transcript.strip():
            await update.message.reply_text("Couldn't make out what you said. Try again?")
            return

        # Extract and update pipeline
        result = await extract_and_update(transcript)
        await update.message.reply_text(result)

    except Exception as e:
        logger.error(f"Voice handler error: {e}")
        await update.message.reply_text(f"Error processing voice note: {str(e)[:200]}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_voice_handler.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add voice_handler.py tests/test_voice_handler.py
git commit -m "feat: add voice note handler with Whisper transcription and AI extraction"
```

### Task 3: Register voice handler in bot.py

**Files:**
- Modify: `bot.py:1434-1451` (build_application function)

- [ ] **Step 1: Add voice handler import and registration**

At the top of `bot.py`, add import:
```python
from voice_handler import handle_voice_message
```

In the `build_application()` function, add the voice message handler BEFORE the text message handler (line ~1451):

```python
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))
```

This line goes right before:
```python
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
```

- [ ] **Step 2: Verify bot still loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -c "from bot import build_application; print('OK')"`
Expected: `OK` (no import errors)

- [ ] **Step 3: Commit**

```bash
git add bot.py
git commit -m "feat: register voice message handler in bot"
```

---

## Chunk 3: Lead Intake Logic

### Task 4: Create intake.py — shared lead intake parsing

**Files:**
- Create: `intake.py`
- Create: `tests/test_intake.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_intake.py`:

```python
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_process_booking_payload():
    from intake import process_booking

    result = process_booking({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "phone": "519-555-1234",
        "service": "Financial Planning Consultation",
        "start_time": "2026-03-15T14:00:00",
        "notes": "Interested in home insurance",
    })

    assert "Sarah Chen" in result
    # Verify prospect was created
    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect is not None
    assert prospect["email"] == "sarah@example.com"
    assert prospect["source"] == "Outlook Booking"


def test_process_booking_duplicate():
    from intake import process_booking

    db.add_prospect({"name": "Sarah Chen", "source": "Manual", "stage": "Contacted"})

    result = process_booking({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "service": "Review Meeting",
        "start_time": "2026-03-15T14:00:00",
    })

    assert "Updated" in result or "already" in result.lower() or "Sarah Chen" in result


def test_process_email_lead(monkeypatch):
    from intake import process_email_lead
    import intake

    # Mock the OpenAI client response
    class MockMessage:
        content = '{"name": "Mike Johnson", "phone": "519-555-5678", "email": "", "product": "Life Insurance", "notes": "35, married, tech company, referred by neighbor", "priority": "Warm", "source": "Referral", "stage": "New Lead"}'

    class MockChoice:
        message = MockMessage()

    class MockResponse:
        choices = [MockChoice()]

    class MockCompletions:
        def create(self, **kwargs):
            return MockResponse()

    class MockChat:
        completions = MockCompletions()

    class MockClient:
        chat = MockChat()

    monkeypatch.setattr(intake, "client", MockClient())

    result = process_email_lead({
        "from": "colleague@cooperators.ca",
        "subject": "Referral: Mike Johnson",
        "body": "Hi Marc, Mike Johnson is looking for life insurance. He's 35, married, works at a tech company. His number is 519-555-5678. He was referred by his neighbor.",
    })

    assert "Mike Johnson" in result
    prospect = db.get_prospect_by_name("Mike Johnson")
    assert prospect is not None
    assert prospect["source"] == "Referral"


def test_process_email_lead_minimal(monkeypatch):
    from intake import process_email_lead
    import intake

    class MockMessage:
        content = '{"name": "Jane Doe", "phone": "", "email": "", "product": "Auto Insurance", "notes": "Wants auto insurance quote", "priority": "Warm", "source": "Email Lead", "stage": "New Lead"}'

    class MockChoice:
        message = MockMessage()

    class MockResponse:
        choices = [MockChoice()]

    class MockCompletions:
        def create(self, **kwargs):
            return MockResponse()

    class MockChat:
        completions = MockCompletions()

    class MockClient:
        chat = MockChat()

    monkeypatch.setattr(intake, "client", MockClient())

    result = process_email_lead({
        "body": "New lead: Jane Doe, wants auto insurance quote",
    })

    assert "Jane" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_intake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'intake'`

- [ ] **Step 3: Create intake.py**

```python
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
    """Process an Outlook Bookings payload from Power Automate.

    Expected fields: name, email, phone, service, start_time, notes (all optional except name).
    """
    name = (data.get("name") or "").strip()
    if not name:
        return "No name in booking payload."

    email = data.get("email", "")
    phone = data.get("phone", "")
    service = data.get("service", "")
    start_time = data.get("start_time", "")
    notes = data.get("notes", "")

    # Build notes from booking context
    booking_notes = f"Booked: {service}" if service else "Booked via Outlook"
    if notes:
        booking_notes += f" | {notes}"

    # Check for existing prospect
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

        # Add meeting
        if start_time:
            meeting_date, meeting_time = _parse_datetime(start_time)
            db.add_meeting({
                "date": meeting_date,
                "time": meeting_time,
                "prospect": existing["name"],
                "type": service or "Consultation",
                "prep_notes": booking_notes,
            })

        db.add_interaction({
            "prospect": existing["name"],
            "source": "outlook_booking",
            "raw_text": json.dumps(data),
            "summary": booking_notes,
        })

        return f"Updated {existing['name']} with new booking. Meeting added."
    else:
        # Create new prospect
        db.add_prospect({
            "name": name,
            "email": email,
            "phone": phone,
            "source": "Outlook Booking",
            "stage": "New Lead",
            "priority": "Warm",
            "product": _guess_product(service, notes),
            "notes": booking_notes,
        })

        # Add meeting
        if start_time:
            meeting_date, meeting_time = _parse_datetime(start_time)
            db.add_meeting({
                "date": meeting_date,
                "time": meeting_time,
                "prospect": name,
                "type": service or "Consultation",
                "prep_notes": booking_notes,
            })

        db.add_interaction({
            "prospect": name,
            "source": "outlook_booking",
            "raw_text": json.dumps(data),
            "summary": booking_notes,
        })

        db.add_activity({
            "prospect": name,
            "action": "Outlook Booking received",
            "outcome": booking_notes,
            "next_step": "Prepare for meeting",
        })

        # Score the new prospect and set follow-up
        _score_and_schedule(name)

        return f"New prospect: {name} — {booking_notes}. Meeting added."


def process_email_lead(data: dict) -> str:
    """Process a forwarded lead email from Zapier.

    Expected fields: from, subject, body.
    Uses AI to extract prospect info from the email body.
    """
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

        # Strip markdown code blocks
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

    # Check for existing prospect
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
        "prospect": name,
        "source": "email_lead",
        "raw_text": email_text[:2000],
        "summary": prospect.get("notes", ""),
    })

    db.add_activity({
        "prospect": name,
        "action": "Lead intake (email)",
        "outcome": prospect.get("notes", ""),
        "next_step": "Initial contact",
    })

    # Score new prospects and set follow-up
    if not existing:
        _score_and_schedule(name)

    return result


def _score_and_schedule(name: str):
    """Score a newly created prospect and set their first follow-up date."""
    import scoring

    prospect = db.get_prospect_by_name(name)
    if not prospect:
        return

    score_data = scoring.score_prospect(prospect)
    score = score_data.get("score", 0)

    # Set priority based on score (0-100 scale)
    if score >= 70:
        priority = "Hot"
    elif score >= 40:
        priority = "Warm"
    else:
        priority = "Cold"

    # Set first follow-up: hot=1 day, warm=2 days, cold=5 days
    from datetime import timedelta
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_intake.py -v`
Expected: All 4 tests PASS (email tests use mocked OpenAI client)

- [ ] **Step 5: Commit**

```bash
git add intake.py tests/test_intake.py
git commit -m "feat: add lead intake logic for bookings and email leads"
```

---

## Chunk 4: Webhook Endpoint

### Task 5: Create webhook_intake.py — Flask blueprint for external intake

**Files:**
- Create: `webhook_intake.py`
- Create: `tests/test_webhook_intake.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_webhook_intake.py`:

```python
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INTAKE_WEBHOOK_SECRET", "test-secret-123")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db
from webhook_intake import intake_bp
from flask import Flask


def create_test_app():
    app = Flask(__name__)
    app.register_blueprint(intake_bp)
    return app


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_webhook_rejects_missing_auth():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post("/api/intake", json={"type": "booking", "data": {"name": "Test"}})
        assert resp.status_code == 401


def test_webhook_rejects_bad_secret():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={"type": "booking", "data": {"name": "Test"}},
            headers={"X-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401


def test_webhook_accepts_valid_booking():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "booking",
                "data": {
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "service": "Life Insurance Consultation",
                    "start_time": "2026-03-20T10:00:00",
                },
            },
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "Jane Doe" in body["message"]


def test_webhook_rejects_unknown_type():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={"type": "unknown", "data": {}},
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status_code == 400


def test_webhook_rejects_missing_payload():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            data="not json",
            content_type="text/plain",
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_webhook_intake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'webhook_intake'`

- [ ] **Step 3: Create webhook_intake.py**

```python
"""
Webhook endpoint for external lead intake.

Flask blueprint that receives payloads from:
- Power Automate (Outlook Bookings)
- Zapier (email forwarding, Otter transcripts in Phase 2)

All requests must include X-Webhook-Secret header matching INTAKE_WEBHOOK_SECRET env var.
"""

import logging
import os

from flask import Blueprint, jsonify, request

from intake import process_booking, process_email_lead

logger = logging.getLogger(__name__)

WEBHOOK_SECRET = os.environ.get("INTAKE_WEBHOOK_SECRET", "")

intake_bp = Blueprint("intake", __name__)


def _check_auth() -> bool:
    """Validate the webhook secret header."""
    if not WEBHOOK_SECRET:
        logger.warning("INTAKE_WEBHOOK_SECRET not set — rejecting all intake webhooks")
        return False
    token = request.headers.get("X-Webhook-Secret", "")
    return token == WEBHOOK_SECRET


@intake_bp.route("/api/intake", methods=["POST"])
def intake_webhook():
    """Main intake webhook endpoint.

    Expects JSON body:
    {
        "type": "booking" | "email_lead",
        "data": { ... payload ... }
    }
    """
    if not _check_auth():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "Invalid JSON payload"}), 400

    intake_type = payload.get("type", "")
    data = payload.get("data", {})

    if not data:
        return jsonify({"error": "Missing 'data' field"}), 400

    try:
        if intake_type == "booking":
            result = process_booking(data)
        elif intake_type == "email_lead":
            result = process_email_lead(data)
        else:
            return jsonify({"error": f"Unknown intake type: {intake_type}"}), 400

        logger.info(f"Intake webhook ({intake_type}): {result}")

        # Notify via Telegram if bot is available
        _notify_telegram(result)

        return jsonify({"ok": True, "message": result})

    except Exception as e:
        logger.error(f"Intake webhook error: {e}")
        return jsonify({"error": str(e)[:200]}), 500


def _notify_telegram(message: str):
    """Send a notification to the Telegram bot chat. Best-effort, non-blocking."""
    try:
        from bot import telegram_app, bot_event_loop
        import asyncio

        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not telegram_app or not bot_event_loop or not chat_id:
            return

        async def send():
            await telegram_app.bot.send_message(chat_id=chat_id, text=f"New lead intake:\n{message}")

        asyncio.run_coroutine_threadsafe(send(), bot_event_loop)
    except Exception as e:
        logger.warning(f"Could not notify Telegram: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_webhook_intake.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add webhook_intake.py tests/test_webhook_intake.py
git commit -m "feat: add webhook intake endpoint with auth for external lead sources"
```

### Task 6: Register webhook blueprint in dashboard.py

**Files:**
- Modify: `dashboard.py:1382-1389` (register_webhook function)

Note: The existing codebase does not use Flask blueprints — routes are defined directly on `app` and the Telegram webhook is registered lazily via `register_webhook()`. We follow the same lazy pattern to avoid import issues at module load time.

- [ ] **Step 1: Register the intake blueprint inside register_webhook**

In `dashboard.py`, modify the `register_webhook` function (line 1382) to also register the intake blueprint:

```python
def register_webhook(flask_app):
    """Register the Telegram webhook route and intake webhook on the Flask app."""
    from webhook_intake import intake_bp
    flask_app.register_blueprint(intake_bp)

    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        from bot import process_webhook_update
        update_data = request.get_json(force=True)
        process_webhook_update(update_data)
        return "ok"
```

- [ ] **Step 2: Verify app still loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -c "from dashboard import app; from webhook_intake import intake_bp; app.register_blueprint(intake_bp); print('Routes:', [r.rule for r in app.url_map.iter_rules() if 'intake' in r.rule])"`
Expected: `Routes: ['/api/intake']`

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: register intake webhook blueprint in Flask app"
```

---

## Chunk 5: Telegram Paste-In Lead Intake

### Task 7: Add /lead command for pasting lead info into Telegram

The spec says leads can be "pasted into Telegram" in addition to the webhook. Add a `/lead` command that accepts pasted email/lead text and routes it through `process_email_lead()`.

**Files:**
- Modify: `bot.py` (add /lead command handler)

- [ ] **Step 1: Add /lead command handler to bot.py**

Add this function in `bot.py` after the `cmd_priority` function (~line 1224):

```python
async def cmd_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /lead command — paste in a lead email or referral info."""
    user_msg = update.message.text.replace("/lead", "", 1).strip()
    if not user_msg:
        await update.message.reply_text(
            "Paste a lead email or referral info after /lead:\n"
            "/lead Mike Johnson, 35, looking for life insurance, referred by his neighbor. 519-555-5678"
        )
        return

    logger.info(f"/lead: {user_msg[:100]}")
    await update.message.reply_text("Processing lead...")

    try:
        from intake import process_email_lead
        result = process_email_lead({
            "from": "Telegram paste",
            "subject": "",
            "body": user_msg,
        })
        await update.message.reply_text(result)
    except Exception as e:
        logger.error(f"/lead error: {e}")
        await update.message.reply_text(f"Error processing lead: {str(e)[:200]}")
```

- [ ] **Step 2: Register the /lead command in build_application()**

In `build_application()`, add after the `/priority` handler:

```python
    app.add_handler(CommandHandler("lead", cmd_lead))
```

- [ ] **Step 3: Verify bot loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -c "from bot import build_application; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add /lead command for pasting lead info into Telegram"
```

---

## Chunk 6: Integration + Environment Setup

### Task 8: Add INTAKE_WEBHOOK_SECRET to environment and update /start command

**Files:**
- Modify: `bot.py:1410-1431` (/start command)

- [ ] **Step 1: Update /start command to mention voice notes**

In `bot.py`, update the `start()` function to add voice note instructions. Add this line after the existing help text:

```python
        "Send a voice message after any call/meeting and I'll auto-update your pipeline.\n"
        "/lead — paste in a referral or lead email to auto-create a prospect\n\n"
```

Add it right before `"Let's close some deals."`.

- [ ] **Step 2: Generate and set INTAKE_WEBHOOK_SECRET on Railway**

Run: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` to generate a secret.

Then set it on Railway:
Run: `railway variables set INTAKE_WEBHOOK_SECRET=<generated-secret>`

Note: Save this secret — you'll need it when configuring Zapier and Power Automate.

- [ ] **Step 3: Add .superpowers to .gitignore**

Run: `echo ".superpowers/" >> /Users/map98/Desktop/calm-money-bot/.gitignore` (if .gitignore exists, append; if not, create)

- [ ] **Step 4: Commit**

```bash
git add bot.py .gitignore
git commit -m "feat: update /start help text for voice notes, add .superpowers to gitignore"
```

### Task 9: Run all tests and verify

**Files:** All test files

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v --tb=short`
Expected: All tests pass (email lead tests may skip if no API key — that's fine)

- [ ] **Step 2: Verify bot loads cleanly**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -c "from bot import build_application; from dashboard import app; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Final commit with all changes**

If any uncommitted changes remain:
```bash
git add -A
git commit -m "chore: phase 1 complete — voice notes, lead intake, webhook endpoint"
```

---

## Post-Implementation: External Service Setup

These are manual steps (not code) to complete after deployment:

### Power Automate Setup (Outlook Bookings → Bot)
1. Create a new Power Automate flow
2. Trigger: "When a new event is created" in your Bookings calendar
3. Action: HTTP POST to `https://<your-railway-domain>/api/intake`
4. Headers: `X-Webhook-Secret: <your-secret>`, `Content-Type: application/json`
5. Body:
```json
{
  "type": "booking",
  "data": {
    "name": "@{triggerOutputs()?['body/customer/name']}",
    "email": "@{triggerOutputs()?['body/customer/email']}",
    "phone": "@{triggerOutputs()?['body/customer/phone']}",
    "service": "@{triggerOutputs()?['body/subject']}",
    "start_time": "@{triggerOutputs()?['body/start/dateTime']}",
    "notes": "@{triggerOutputs()?['body/body/content']}"
  }
}
```

### Zapier Setup (Email Lead Forwarding)
1. Create a new Zap
2. Trigger: "New Email" matching a filter (e.g., from Co-operators lead system, or forwarded to a specific label)
3. Action: Webhooks by Zapier → POST to `https://<your-railway-domain>/api/intake`
4. Headers: `X-Webhook-Secret: <your-secret>`
5. Body:
```json
{
  "type": "email_lead",
  "data": {
    "from": "{{sender}}",
    "subject": "{{subject}}",
    "body": "{{body_plain}}"
  }
}
```

### Railway Deploy
After all code is committed and pushed, the bot will auto-deploy on Railway with the new features.
