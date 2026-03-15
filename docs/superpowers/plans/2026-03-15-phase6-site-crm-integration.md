# Phase 6: Site-CRM Integration — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire calmmoney.ca lead capture forms to the CRM bot so website leads auto-enter the pipeline, and approved drafts for website leads auto-send via Resend.

**Architecture:** Two independent repos (pineault-wealth + calm-money-bot) on Railway, connected by HTTP webhooks over internal networking. Site fires POST to CRM `/api/intake` after each form submission. CRM creates/updates prospects in SQLite, routes approved drafts through Resend or Outlook based on `send_channel`.

**Tech Stack:** Python/Flask (CRM), Next.js/TypeScript (site), SQLite, Resend API, Railway internal networking

**Spec:** `docs/superpowers/specs/2026-03-15-phase6-site-crm-integration-design.md`

---

## File Structure

### calm-money-bot (CRM) — this repo

| File | Action | Responsibility |
|------|--------|----------------|
| `db.py` | Modify | Add `send_channel` column migration, `get_prospect_by_email()`, update `add_prospect()` + `update_prospect()` |
| `analytics.py` | Modify | Add `resend_email_id` column migration, update `record_outcome()` |
| `resend_sender.py` | Create | Thin Resend API wrapper — `send_email(to, subject, body)` returns `resend_email_id` |
| `intake.py` | Modify | New business logic: `process_website_contact()`, `process_website_quiz()`, `process_website_tool()`, `process_email_event()` |
| `webhook_intake.py` | Modify | Route new intake types to `intake.py` functions |
| `bot.py` | Modify | Approval flow: auto-send via Resend when `send_channel = 'resend'` |
| `tests/test_website_intake.py` | Create | Tests for website lead intake + dedup |
| `tests/test_resend_sender.py` | Create | Tests for Resend sender module |
| `tests/test_send_channel_routing.py` | Create | Tests for approval flow routing |
| `tests/test_email_event_intake.py` | Create | Tests for engagement event processing |

### pineault-wealth (site) — separate repo at `/Users/map98/Desktop/pineault-wealth`

| File | Action | Responsibility |
|------|--------|----------------|
| `src/lib/crm.ts` | Create | Fire-and-forget POST helper for CRM webhook |
| `src/app/api/contact/route.ts` | Modify | Add `crmNotify()` after Resend send |
| `src/app/api/quiz/route.ts` | Modify | Add `crmNotify()` after Resend send |
| `src/app/api/lead/route.ts` | Modify | Add `crmNotify()` after Resend send |
| `src/app/api/webhooks/resend/route.ts` | Modify | Forward engagement events to CRM |

---

## Chunk 1: CRM Database + Resend Sender

### Task 1: Add `send_channel` to prospects and `get_prospect_by_email()`

**Files:**
- Modify: `db.py:97-301` (schema + functions)
- Test: `tests/test_website_intake.py` (new)

- [ ] **Step 1: Write failing tests for new DB features**

Create `tests/test_website_intake.py`:

```python
"""Tests for website lead intake — DB layer."""
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_website"
os.environ.setdefault("INTAKE_WEBHOOK_SECRET", "test-secret-123")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_add_prospect_with_send_channel():
    """add_prospect() should accept and store send_channel."""
    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "priority": "Hot",
        "send_channel": "resend",
    })
    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect is not None
    assert prospect["send_channel"] == "resend"


def test_add_prospect_defaults_send_channel_to_outlook():
    """add_prospect() without send_channel should default to 'outlook'."""
    db.add_prospect({
        "name": "Bob Lee",
        "email": "bob@example.com",
        "source": "Outlook Booking",
        "priority": "Warm",
    })
    prospect = db.get_prospect_by_name("Bob Lee")
    assert prospect is not None
    assert prospect["send_channel"] == "outlook"


def test_get_prospect_by_email():
    """get_prospect_by_email() should find prospect by exact email match."""
    db.add_prospect({
        "name": "Alice Wong",
        "email": "alice@example.com",
        "source": "website",
        "priority": "Warm",
    })
    prospect = db.get_prospect_by_email("alice@example.com")
    assert prospect is not None
    assert prospect["name"] == "Alice Wong"


def test_get_prospect_by_email_case_insensitive():
    """get_prospect_by_email() should be case-insensitive."""
    db.add_prospect({
        "name": "Alice Wong",
        "email": "Alice@Example.COM",
        "source": "website",
        "priority": "Warm",
    })
    prospect = db.get_prospect_by_email("alice@example.com")
    assert prospect is not None
    assert prospect["name"] == "Alice Wong"


def test_get_prospect_by_email_returns_none():
    """get_prospect_by_email() should return None for unknown emails."""
    result = db.get_prospect_by_email("nobody@example.com")
    assert result is None


def test_update_prospect_send_channel():
    """update_prospect() should allow updating send_channel."""
    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    db.update_prospect("Sarah Chen", {"send_channel": "outlook"})
    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect["send_channel"] == "outlook"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_website_intake.py -v`
Expected: FAIL — `send_channel` not in INSERT, `get_prospect_by_email` not defined

- [ ] **Step 3: Add `send_channel` column to schema in `init_db()`**

In `db.py`, modify the `CREATE TABLE IF NOT EXISTS prospects` statement to add `send_channel` after `notes`:

```python
                notes TEXT DEFAULT '',
                send_channel TEXT DEFAULT 'outlook',
                created_at TEXT DEFAULT (datetime('now')),
```

- [ ] **Step 4: Add `send_channel` to `add_prospect()` INSERT SQL**

In `db.py:324-344`, change the INSERT to include `send_channel`:

```python
        conn.execute(
            """INSERT INTO prospects
               (name, phone, email, source, priority, stage, product,
                aum, revenue, first_contact, next_followup, notes, send_channel)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                data.get("phone", ""),
                data.get("email", ""),
                data.get("source", ""),
                data.get("priority", ""),
                stage,
                data.get("product", ""),
                aum,
                revenue,
                first_contact,
                data.get("next_followup", ""),
                data.get("notes", ""),
                data.get("send_channel", "outlook"),
            ),
        )
```

- [ ] **Step 5: Add `send_channel` to `update_prospect()` allowlist**

In `db.py:363-366`, add `"send_channel"` to the allowed set:

```python
        allowed = {
            "name", "phone", "email", "source", "priority", "stage",
            "product", "aum", "revenue", "first_contact", "next_followup", "notes",
            "send_channel",
        }
```

- [ ] **Step 6: Add `get_prospect_by_email()` function**

In `db.py`, add after `get_prospect_by_name()` (after line 426):

```python
def get_prospect_by_email(email: str):
    """Lookup prospect by exact email match (case-insensitive). Returns dict or None."""
    if not email:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM prospects WHERE LOWER(email) = ? LIMIT 1",
            (email.lower().strip(),),
        ).fetchone()
    return _row_to_dict(row)
```

- [ ] **Step 7: Add migration for existing databases**

In `db.py`, after the `init_db()` function's trust config seed (after line 301), add migration:

```python
    # Phase 6 migrations — add send_channel to prospects, resend_email_id to outcomes
    _migrate_phase6()


def _migrate_phase6():
    """Add Phase 6 columns if they don't exist (safe to run repeatedly)."""
    with get_db() as conn:
        # Check if send_channel exists on prospects
        cols = [row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()]
        if "send_channel" not in cols:
            conn.execute("ALTER TABLE prospects ADD COLUMN send_channel TEXT DEFAULT 'outlook'")
            logger.info("Migration: added send_channel to prospects")
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_website_intake.py -v`
Expected: All 6 tests PASS

- [ ] **Step 9: Run full test suite to verify no regressions**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 10: Commit**

```bash
git add db.py tests/test_website_intake.py
git commit -m "feat: add send_channel to prospects, get_prospect_by_email() for Phase 6"
```

---

### Task 2: Add `resend_email_id` to outcomes

**Files:**
- Modify: `db.py:97-301` (schema migration)
- Modify: `analytics.py:39-48` (`record_outcome()`)
- Test: `tests/test_email_event_intake.py` (new, partial)

- [ ] **Step 1: Write failing test for `record_outcome()` with `resend_email_id`**

Create `tests/test_email_event_intake.py`:

```python
"""Tests for email event intake and outcome matching."""
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_email_events"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import analytics


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_record_outcome_with_resend_email_id():
    """record_outcome() should store resend_email_id when provided."""
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Sarah Chen",
        sent_at="2026-03-15",
        resend_email_id="re_abc123xyz",
    )
    assert outcome["resend_email_id"] == "re_abc123xyz"


def test_record_outcome_without_resend_email_id():
    """record_outcome() should work without resend_email_id (backwards compat)."""
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Bob Lee",
        sent_at="2026-03-15",
    )
    assert outcome["resend_email_id"] is None


def test_find_outcome_by_resend_email_id():
    """Should be able to find an outcome by resend_email_id."""
    analytics.record_outcome(
        action_type="follow_up",
        target="Sarah Chen",
        sent_at="2026-03-15",
        resend_email_id="re_findme123",
    )
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM outcomes WHERE resend_email_id = ?",
            ("re_findme123",),
        ).fetchone()
    assert row is not None
    assert row["target"] == "Sarah Chen"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_email_event_intake.py -v`
Expected: FAIL — `resend_email_id` not a column, `record_outcome()` doesn't accept it

- [ ] **Step 3: Add `resend_email_id` to outcomes schema**

In `db.py`, modify `CREATE TABLE IF NOT EXISTS outcomes` to add the column:

```python
        CREATE TABLE IF NOT EXISTS outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id INTEGER,
            action_type TEXT NOT NULL,
            target TEXT,
            sent_at TEXT,
            response_received INTEGER DEFAULT 0,
            response_at TEXT,
            response_type TEXT,
            converted INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            resend_email_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (action_id) REFERENCES audit_log(id)
        );
```

- [ ] **Step 4: Add `resend_email_id` migration to `_migrate_phase6()`**

Extend the migration function in `db.py`:

```python
def _migrate_phase6():
    """Add Phase 6 columns if they don't exist (safe to run repeatedly)."""
    with get_db() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()]
        if "send_channel" not in cols:
            conn.execute("ALTER TABLE prospects ADD COLUMN send_channel TEXT DEFAULT 'outlook'")
            logger.info("Migration: added send_channel to prospects")

        outcome_cols = [row[1] for row in conn.execute("PRAGMA table_info(outcomes)").fetchall()]
        if "resend_email_id" not in outcome_cols:
            conn.execute("ALTER TABLE outcomes ADD COLUMN resend_email_id TEXT")
            logger.info("Migration: added resend_email_id to outcomes")
```

- [ ] **Step 5: Update `record_outcome()` in `analytics.py`**

Modify `analytics.py:39-48`:

```python
def record_outcome(action_type, target, sent_at, action_id=None, notes="", resend_email_id=None):
    """Record an outcome for an AI-generated action. Returns dict."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO outcomes (action_id, action_type, target, sent_at, notes, resend_email_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action_id, action_type, target, sent_at, notes, resend_email_id),
        )
        row = conn.execute("SELECT * FROM outcomes WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_email_event_intake.py -v`
Expected: All 3 tests PASS

- [ ] **Step 7: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests pass (existing `record_outcome()` callers use positional/keyword args without `resend_email_id` — new param has default `None`)

- [ ] **Step 8: Commit**

```bash
git add db.py analytics.py tests/test_email_event_intake.py
git commit -m "feat: add resend_email_id to outcomes for email engagement tracking"
```

---

### Task 3: Create `resend_sender.py`

**Files:**
- Create: `resend_sender.py`
- Create: `tests/test_resend_sender.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_resend_sender.py`:

```python
"""Tests for Resend email sender module."""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@patch.dict(os.environ, {
    "RESEND_API_KEY": "re_test_key",
    "RESEND_FROM_EMAIL": "marc@info.calmmoney.ca",
    "RESEND_REPLY_TO": "mpineault1@gmail.com",
})
@patch("resend_sender.requests")
def test_send_email_success(mock_requests):
    """send_email() should POST to Resend API and return the email ID."""
    import resend_sender

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "re_abc123xyz"}
    mock_requests.post.return_value = mock_response

    result = resend_sender.send_email(
        to="sarah@example.com",
        subject="Following up on our chat",
        body="Hi Sarah, great talking with you about life insurance.",
    )

    assert result == "re_abc123xyz"
    mock_requests.post.assert_called_once()
    call_kwargs = mock_requests.post.call_args
    payload = call_kwargs[1]["json"]
    assert payload["to"] == ["sarah@example.com"]
    assert payload["subject"] == "Following up on our chat"
    assert payload["from"] == "marc@info.calmmoney.ca"
    assert payload["reply_to"] == "mpineault1@gmail.com"


@patch.dict(os.environ, {
    "RESEND_API_KEY": "re_test_key",
    "RESEND_FROM_EMAIL": "marc@info.calmmoney.ca",
    "RESEND_REPLY_TO": "mpineault1@gmail.com",
})
@patch("resend_sender.requests")
def test_send_email_api_failure(mock_requests):
    """send_email() should return None on API error."""
    import resend_sender

    mock_requests.post.side_effect = Exception("Network error")

    result = resend_sender.send_email(
        to="sarah@example.com",
        subject="Test",
        body="Test body",
    )

    assert result is None


@patch.dict(os.environ, {"RESEND_API_KEY": ""})
def test_send_email_no_api_key():
    """send_email() should return None if RESEND_API_KEY is not set."""
    # Force reimport to pick up empty env var
    import importlib
    import resend_sender
    importlib.reload(resend_sender)

    result = resend_sender.send_email(
        to="sarah@example.com",
        subject="Test",
        body="Test body",
    )

    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_resend_sender.py -v`
Expected: FAIL — `resend_sender` module not found

- [ ] **Step 3: Implement `resend_sender.py`**

Create `resend_sender.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_resend_sender.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add resend_sender.py tests/test_resend_sender.py
git commit -m "feat: add resend_sender.py for auto-sending approved drafts to website leads"
```

---

## Chunk 2: Website Lead Intake Logic

### Task 4: Website lead processing functions in `intake.py`

**Files:**
- Modify: `intake.py` (add 4 new functions)
- Modify: `webhook_intake.py:56-64` (add routing)
- Extend: `tests/test_website_intake.py`

- [ ] **Step 1: Write failing tests for website_contact intake**

Append to `tests/test_website_intake.py`:

```python
from unittest.mock import patch, MagicMock
import intake


def test_process_website_contact_creates_hot_prospect():
    """website_contact should create a Hot prospect with send_channel=resend."""
    result = intake.process_website_contact({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "phone": "519-555-0100",
        "service": "Life Insurance",
        "message": "I'd like to learn about life insurance options.",
    })
    assert "Sarah Chen" in result

    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect is not None
    assert prospect["priority"] == "Hot"
    assert prospect["send_channel"] == "resend"
    assert prospect["source"] == "website"
    assert prospect["product"] == "Life Insurance"
    assert prospect["email"] == "sarah@example.com"


def test_process_website_contact_dedup_updates_existing():
    """website_contact should update existing prospect found by email."""
    # Create a prospect via quiz (no name, email only)
    db.add_prospect({
        "name": "sarah",
        "email": "sarah@example.com",
        "source": "website",
        "priority": "Warm",
        "send_channel": "resend",
    })

    result = intake.process_website_contact({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "phone": "519-555-0100",
        "service": "Life Insurance",
        "message": "Following up on my quiz results.",
    })

    # Should update the existing prospect, not create a new one
    prospect = db.get_prospect_by_email("sarah@example.com")
    assert prospect["name"] == "Sarah Chen"  # Name upgraded
    assert prospect["priority"] == "Hot"     # Priority bumped
    assert prospect["phone"] == "519-555-0100"


def test_process_website_quiz_creates_warm_prospect():
    """website_quiz should create a Warm prospect with score in notes."""
    result = intake.process_website_quiz({
        "email": "bob@example.com",
        "score": 72,
        "answers": [
            {"questionId": 1, "optionLabel": "No plan", "points": 5},
            {"questionId": 2, "optionLabel": "Some savings", "points": 15},
        ],
        "tier": "Needs Attention",
    })
    assert "bob" in result.lower()

    prospect = db.get_prospect_by_email("bob@example.com")
    assert prospect is not None
    assert prospect["priority"] == "Warm"
    assert prospect["send_channel"] == "resend"
    assert prospect["source"] == "website"
    # Name derived from email local part
    assert prospect["name"] == "bob"
    assert "72" in prospect["notes"]


def test_process_website_tool_creates_cool_prospect():
    """website_tool should create a Cool prospect with minimal data."""
    result = intake.process_website_tool({
        "email": "jane@example.com",
        "toolName": "Life Insurance Calculator",
    })

    prospect = db.get_prospect_by_email("jane@example.com")
    assert prospect is not None
    assert prospect["priority"] == "Cool"
    assert prospect["send_channel"] == "resend"
    assert prospect["name"] == "jane"
    assert "Life Insurance Calculator" in prospect["notes"]


def test_process_website_contact_no_email_still_works():
    """website_contact with no email should still create prospect by name."""
    result = intake.process_website_contact({
        "name": "Marc Test",
        "email": "",
        "phone": "519-555-0000",
        "service": "Auto Insurance",
        "message": "Quick question.",
    })
    prospect = db.get_prospect_by_name("Marc Test")
    assert prospect is not None
    assert prospect["send_channel"] == "resend"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_website_intake.py::test_process_website_contact_creates_hot_prospect -v`
Expected: FAIL — `process_website_contact` not defined

- [ ] **Step 3: Implement website lead processing functions in `intake.py`**

Add to the end of `intake.py` (before the closing):

```python
# ── Website lead intake (Phase 6) ──

PRIORITY_RANK = {"Hot": 3, "Warm": 2, "Cool": 1, "Cold": 0}


def _dedup_or_create(email: str, name: str, data: dict) -> tuple:
    """Check for existing prospect by email. Returns (prospect_dict, is_new)."""
    existing = db.get_prospect_by_email(email) if email else None

    if existing:
        # Merge new data onto existing prospect
        updates = {}
        if name and (not existing.get("name") or existing["name"] == email.split("@")[0]):
            updates["name"] = name
        if data.get("phone") and not existing.get("phone"):
            updates["phone"] = data["phone"]
        if data.get("product") and not existing.get("product"):
            updates["product"] = data["product"]
        # Bump priority if new source is higher
        new_priority = data.get("priority", "")
        if PRIORITY_RANK.get(new_priority, 0) > PRIORITY_RANK.get(existing.get("priority", ""), 0):
            updates["priority"] = new_priority
        # Append to notes
        note_addition = data.get("note_addition", "")
        if note_addition:
            old_notes = existing.get("notes", "")
            updates["notes"] = f"{old_notes} | {note_addition}" if old_notes else note_addition

        if updates:
            db.update_prospect(existing["name"], updates)

        # Re-fetch with updates applied
        updated = db.get_prospect_by_email(email) or existing
        return updated, False
    else:
        # Create new prospect
        prospect_name = name or (email.split("@")[0] if email else "Unknown")
        db.add_prospect({
            "name": prospect_name,
            "email": email,
            "phone": data.get("phone", ""),
            "source": "website",
            "priority": data.get("priority", "Warm"),
            "stage": "New Lead",
            "product": data.get("product", ""),
            "notes": data.get("note_addition", ""),
            "send_channel": "resend",
        })
        new_prospect = db.get_prospect_by_email(email) or db.get_prospect_by_name(prospect_name)
        return new_prospect, True


def process_website_contact(data: dict) -> str:
    """Process a contact form submission from calmmoney.ca."""
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    service = (data.get("service") or "").strip()
    message = (data.get("message") or "").strip()

    note = f"[Website Contact] Service: {service}"
    if message:
        note += f" | {message[:200]}"

    prospect, is_new = _dedup_or_create(email, name, {
        "phone": phone,
        "product": service,
        "priority": "Hot",
        "note_addition": note,
    })

    prospect_name = prospect["name"] if prospect else name or email
    prospect_id = prospect["id"] if prospect else None

    db.add_interaction({
        "prospect": prospect_name,
        "source": "website_contact",
        "raw_text": f"Service: {service}\nMessage: {message}",
        "summary": note,
    })

    if is_new:
        db.add_activity({
            "prospect": prospect_name,
            "action": "Website contact form submitted",
            "outcome": note,
            "next_step": "Follow up within 24 hours",
        })
        _score_and_schedule(prospect_name)

    action = "New website lead" if is_new else "Updated"
    return f"{action}: {prospect_name} — {service} (Hot, website contact)"


def process_website_quiz(data: dict) -> str:
    """Process a retirement quiz submission from calmmoney.ca."""
    email = (data.get("email") or "").strip()
    score = data.get("score", 0)
    tier = data.get("tier", "")
    answers = data.get("answers", [])

    note = f"[Website Quiz] Score: {score}/100, Tier: {tier}"
    if answers:
        # Identify weak areas (low-point answers)
        weak = [a["optionLabel"] for a in answers if a.get("points", 0) <= 10]
        if weak:
            note += f" | Weak areas: {', '.join(weak[:3])}"

    prospect, is_new = _dedup_or_create(email, "", {
        "priority": "Warm",
        "note_addition": note,
    })

    prospect_name = prospect["name"] if prospect else email.split("@")[0]
    prospect_id = prospect["id"] if prospect else None

    # Store score in client memory
    if prospect_id:
        try:
            import memory_engine
            memory_engine.extract_facts_from_interaction(
                prospect_name=prospect_name,
                prospect_id=prospect_id,
                interaction_text=f"Retirement quiz: scored {score}/100 ({tier}). {note}",
                source="website_quiz",
            )
        except Exception:
            logger.exception("Memory extraction failed for quiz (non-blocking)")

    # Start nurture sequence for new quiz leads
    if is_new and prospect_id:
        try:
            import nurture
            nurture.create_sequence(prospect_name=prospect_name, prospect_id=prospect_id)
        except Exception:
            logger.exception("Nurture sequence creation failed (non-blocking)")

    action = "New quiz lead" if is_new else "Updated"
    return f"{action}: {prospect_name} — Score {score}/100 (Warm, website quiz)"


def process_website_tool(data: dict) -> str:
    """Process a tool capture form from calmmoney.ca."""
    email = (data.get("email") or "").strip()
    tool_name = (data.get("toolName") or data.get("tool_name") or "").strip()

    note = f"[Website Tool] Used: {tool_name}"

    prospect, is_new = _dedup_or_create(email, "", {
        "priority": "Cool",
        "note_addition": note,
    })

    prospect_name = prospect["name"] if prospect else email.split("@")[0]

    action = "New tool lead" if is_new else "Updated"
    return f"{action}: {prospect_name} — {tool_name} (Cool, website tool)"


def process_email_event(data: dict) -> str:
    """Process a Resend engagement event (open/click/bounce/complaint)."""
    event_type = (data.get("event_type") or "").strip()
    email = (data.get("email") or "").strip()
    resend_email_id = (data.get("resend_email_id") or "").strip()

    if not event_type or not email:
        return "Missing event_type or email in email_event payload."

    import analytics

    # Try to find the matching outcome by resend_email_id first
    outcome = None
    if resend_email_id:
        with db.get_db() as conn:
            row = conn.execute(
                "SELECT * FROM outcomes WHERE resend_email_id = ?",
                (resend_email_id,),
            ).fetchone()
            if row:
                outcome = dict(row)

    # Fallback: match by prospect email + recent sent_at
    if not outcome:
        prospect = db.get_prospect_by_email(email)
        if prospect:
            with db.get_db() as conn:
                row = conn.execute(
                    """SELECT o.* FROM outcomes o
                       WHERE o.target = ? AND o.sent_at >= date('now', '-2 days')
                       ORDER BY o.created_at DESC LIMIT 1""",
                    (prospect["name"],),
                ).fetchone()
                if row:
                    outcome = dict(row)

    if event_type == "email.opened" and outcome:
        analytics.update_outcome(outcome["id"], response_received=True)
        return f"Email opened by {email} — outcome #{outcome['id']} updated"

    elif event_type == "email.clicked" and outcome:
        analytics.update_outcome(outcome["id"], response_received=True, response_type="clicked")
        return f"Email link clicked by {email} — outcome #{outcome['id']} updated"

    elif event_type == "email.bounced":
        prospect = db.get_prospect_by_email(email)
        if prospect:
            db.update_prospect(prospect["name"], {"notes": f"{prospect.get('notes', '')} | [BOUNCED] Email bounced"})
            # Pause nurture
            try:
                import nurture
                with db.get_db() as conn:
                    conn.execute(
                        "UPDATE nurture_sequences SET status = 'paused' WHERE prospect_name = ? AND status = 'active'",
                        (prospect["name"],),
                    )
            except Exception:
                logger.exception("Failed to pause nurture for bounced email")
        return f"Email bounced for {email}"

    elif event_type == "email.complained":
        prospect = db.get_prospect_by_email(email)
        if prospect:
            db.update_prospect(prospect["name"], {
                "notes": f"{prospect.get('notes', '')} | [COMPLAINT] Spam complaint — all outreach paused",
                "stage": "Do Not Contact",
            })
            try:
                with db.get_db() as conn:
                    conn.execute(
                        "UPDATE nurture_sequences SET status = 'paused' WHERE prospect_name = ? AND status = 'active'",
                        (prospect["name"],),
                    )
            except Exception:
                logger.exception("Failed to pause nurture for complaint")
        return f"Spam complaint from {email} — outreach paused"

    if outcome:
        return f"Event {event_type} for {email} — outcome #{outcome['id']} (no action taken)"
    return f"Event {event_type} for {email} — no matching outcome found (ignored)"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_website_intake.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add intake.py tests/test_website_intake.py
git commit -m "feat: website lead intake — contact, quiz, tool, email event processing"
```

---

### Task 5: Route new intake types in `webhook_intake.py`

**Files:**
- Modify: `webhook_intake.py:56-64`
- Extend: `tests/test_webhook_intake.py` (or use `tests/test_website_intake.py`)

- [ ] **Step 1: Write failing integration test**

Append to `tests/test_website_intake.py`:

```python
from flask import Flask
from webhook_intake import intake_bp


def _create_app():
    app = Flask(__name__)
    app.register_blueprint(intake_bp)
    return app


def test_webhook_website_contact_integration():
    """Full webhook → intake flow for website_contact."""
    app = _create_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "website_contact",
                "data": {
                    "name": "Integration Test",
                    "email": "integration@example.com",
                    "phone": "519-555-9999",
                    "service": "Home Insurance",
                    "message": "Testing the integration.",
                },
            },
            headers={"X-Webhook-Secret": os.environ.get("INTAKE_WEBHOOK_SECRET", "test-secret-123")},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True

        prospect = db.get_prospect_by_email("integration@example.com")
        assert prospect is not None
        assert prospect["send_channel"] == "resend"


def test_webhook_website_quiz_integration():
    """Full webhook → intake flow for website_quiz."""
    app = _create_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "website_quiz",
                "data": {
                    "email": "quiz@example.com",
                    "score": 65,
                    "answers": [{"questionId": 1, "optionLabel": "Somewhat", "points": 12}],
                    "tier": "Fair",
                },
            },
            headers={"X-Webhook-Secret": os.environ.get("INTAKE_WEBHOOK_SECRET", "test-secret-123")},
        )
        assert resp.status_code == 200


def test_webhook_website_tool_integration():
    """Full webhook → intake flow for website_tool."""
    app = _create_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "website_tool",
                "data": {
                    "email": "tool@example.com",
                    "toolName": "Life Insurance Calculator",
                },
            },
            headers={"X-Webhook-Secret": os.environ.get("INTAKE_WEBHOOK_SECRET", "test-secret-123")},
        )
        assert resp.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_website_intake.py::test_webhook_website_contact_integration -v`
Expected: FAIL — `Unknown intake type: website_contact` (400 response)

- [ ] **Step 3: Add routing in `webhook_intake.py`**

In `webhook_intake.py`, update the import at line 16:

```python
from intake import (
    process_booking, process_calendar_event, process_email_lead,
    process_website_contact, process_website_quiz, process_website_tool,
    process_email_event,
)
```

In `webhook_intake.py`, update the type dispatch (lines 56-64) to add new cases before the `else`:

```python
        if intake_type == "booking":
            result = process_booking(data)
        elif intake_type == "calendar_event":
            result = process_calendar_event(data)
        elif intake_type == "email_lead":
            result = process_email_lead(data)
        elif intake_type == "website_contact":
            result = process_website_contact(data)
        elif intake_type == "website_quiz":
            result = process_website_quiz(data)
        elif intake_type == "website_tool":
            result = process_website_tool(data)
        elif intake_type == "email_event":
            result = process_email_event(data)
        else:
            return jsonify({"error": f"Unknown intake type: {intake_type}"}), 400
```

Note: For `website_contact` and `website_quiz`, we want the Telegram alert. For `website_tool` and `email_event`, we don't (too noisy / not actionable). Update the notification logic after the try block:

```python
        logger.info(f"Intake webhook ({intake_type}): {result}")
        # Telegram alert for high-signal intake types only
        if intake_type not in ("website_tool", "email_event"):
            _notify_telegram(result)
        return jsonify({"ok": True, "message": result})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_website_intake.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add webhook_intake.py intake.py tests/test_website_intake.py
git commit -m "feat: route website_contact, website_quiz, website_tool, email_event in webhook intake"
```

---

## Chunk 3: Approval Flow + Site Integration

### Task 6: Resend auto-send in approval flow (`bot.py`)

**Files:**
- Modify: `bot.py:2475-2534` (approval flow)
- Create: `tests/test_send_channel_routing.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_send_channel_routing.py`:

```python
"""Tests for send_channel routing in approval flow.

Since bot.py requires TELEGRAM_BOT_TOKEN at import time, we test
the routing logic extracted from the approval flow.
"""
import os
import sys
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_routing"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _get_send_channel_for_prospect(prospect_name: str) -> str:
    """Mirror of the routing logic in bot.py approval flow."""
    prospect = db.get_prospect_by_name(prospect_name)
    if prospect and prospect.get("send_channel") == "resend":
        return "resend"
    return "outlook"


def test_website_lead_routes_to_resend():
    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    assert _get_send_channel_for_prospect("Sarah Chen") == "resend"


def test_outlook_lead_routes_to_outlook():
    db.add_prospect({
        "name": "Bob Lee",
        "email": "bob@example.com",
        "source": "Outlook Booking",
    })
    assert _get_send_channel_for_prospect("Bob Lee") == "outlook"


def test_unknown_prospect_defaults_to_outlook():
    assert _get_send_channel_for_prospect("Nobody") == "outlook"


@patch("resend_sender.send_email")
def test_resend_send_email_called_for_resend_channel(mock_send):
    """When channel is resend, send_email should be called."""
    import resend_sender

    mock_send.return_value = "re_abc123"

    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })

    # Simulate what bot.py approval does for resend channel
    prospect = db.get_prospect_by_name("Sarah Chen")
    if prospect and prospect.get("send_channel") == "resend" and prospect.get("email"):
        result = resend_sender.send_email(
            to=prospect["email"],
            subject="Following up",
            body="Hi Sarah, thanks for reaching out.",
        )
        assert result == "re_abc123"
        mock_send.assert_called_once_with(
            to="sarah@example.com",
            subject="Following up",
            body="Hi Sarah, thanks for reaching out.",
        )
```

- [ ] **Step 2: Run tests to verify they pass (these test routing logic, not bot.py directly)**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_send_channel_routing.py -v`
Expected: All 4 tests PASS (routing logic is self-contained)

- [ ] **Step 3: Modify approval flow in `bot.py`**

In `bot.py`, modify the approve block (around line 2497-2504). Replace the current copy-paste output with channel-aware routing:

Find this block (approximately lines 2497-2504):
```python
        content = draft.get("content", "")
        if len(content) > 3800:
            content = content[:3800] + "\n...(truncated)"
        copy_target = "Publer" if draft.get("type") == "content_post" else "Outlook"
        await query.edit_message_text(
            f"APPROVED — {draft.get('type', 'draft')} for queue #{queue_id}\n\n"
            f"{content}\n\n"
            f"Copy-paste the above into {copy_target}."
        )
```

Replace with:
```python
        content = draft.get("content", "")
        if len(content) > 3800:
            content = content[:3800] + "\n...(truncated)"

        # Route based on send_channel: auto-send via Resend for website leads
        resend_id = None
        send_via_resend = False
        prospect_email = ""
        if draft.get("type") != "content_post" and draft.get("prospect_id"):
            with db.get_db() as _conn:
                _prow = _conn.execute(
                    "SELECT send_channel, email FROM prospects WHERE id = ?",
                    (draft["prospect_id"],),
                ).fetchone()
                if _prow and _prow["send_channel"] == "resend" and _prow["email"]:
                    send_via_resend = True
                    prospect_email = _prow["email"]

        if send_via_resend:
            import resend_sender
            # Use a standard subject — draft content is the email body
            # (AI-generated drafts start with greetings, not subject lines)
            subject = f"Following up — Marc Pineault"
            body = content
            resend_id = resend_sender.send_email(to=prospect_email, subject=subject, body=body)
            if resend_id:
                await query.edit_message_text(
                    f"APPROVED & SENT via Resend — {draft.get('type', 'draft')} #{queue_id}\n\n"
                    f"Sent to: {prospect_email}\n"
                    f"Resend ID: {resend_id}"
                )
            else:
                await query.edit_message_text(
                    f"APPROVED but Resend send FAILED — {draft.get('type', 'draft')} #{queue_id}\n\n"
                    f"{content}\n\n"
                    f"Copy-paste the above and send manually to {prospect_email}."
                )
        else:
            copy_target = "Publer" if draft.get("type") == "content_post" else "Outlook"
            await query.edit_message_text(
                f"APPROVED — {draft.get('type', 'draft')} for queue #{queue_id}\n\n"
                f"{content}\n\n"
                f"Copy-paste the above into {copy_target}."
            )
```

Also update the outcome recording block (around lines 2507-2534) to pass `resend_email_id`:

Find:
```python
            outcome = analytics.record_outcome(
                action_type=draft.get("type", "unknown"),
                target=_target,
                sent_at=datetime.now().strftime("%Y-%m-%d"),
                action_id=None,
            )
```

Replace with:
```python
            outcome = analytics.record_outcome(
                action_type=draft.get("type", "unknown"),
                target=_target,
                sent_at=datetime.now().strftime("%Y-%m-%d"),
                action_id=None,
                resend_email_id=resend_id if send_via_resend else None,
            )
```

Note: `resend_id = None` is initialized before the if/else block so it's always defined here.

- [ ] **Step 4: Update draft notification to show channel**

In `bot.py`, find `_draft_keyboard()` (line 44) and wherever drafts are sent to Telegram for approval, add the channel indicator. Search for where `_draft_keyboard(queue_id)` is called and the message is composed — typically it includes the draft content. Add at the end of the message text:

Find usages of `_draft_keyboard` to locate where the draft notification message is built. Before the `reply_markup=_draft_keyboard(queue_id)` call, modify the message to include channel info:

```python
# Add this logic where draft notifications are sent (search for _draft_keyboard usage):
# Determine channel label
channel_label = ""
if prospect_id:
    with db.get_db() as _conn:
        _ch_row = _conn.execute(
            "SELECT send_channel FROM prospects WHERE id = ?", (prospect_id,)
        ).fetchone()
        if _ch_row:
            channel_label = " — send via Resend" if _ch_row["send_channel"] == "resend" else " — copy to Outlook"
```

This is context-dependent and should be added where draft messages are sent. The implementer should search for all calls to `_draft_keyboard` and add channel info to the message text.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add bot.py tests/test_send_channel_routing.py
git commit -m "feat: auto-send approved drafts via Resend for website leads"
```

---

### Task 7: Email event intake tests

**Files:**
- Extend: `tests/test_email_event_intake.py`

- [ ] **Step 1: Add tests for email event processing**

Append to `tests/test_email_event_intake.py`:

```python
import intake


def test_email_opened_updates_outcome():
    """email.opened event should mark outcome as response_received."""
    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Sarah Chen",
        sent_at="2026-03-15",
        resend_email_id="re_open_test",
    )

    result = intake.process_email_event({
        "event_type": "email.opened",
        "email": "sarah@example.com",
        "resend_email_id": "re_open_test",
    })

    updated = analytics.get_outcome(outcome["id"])
    assert updated["response_received"] == 1
    assert "opened" in result.lower()


def test_email_clicked_updates_outcome():
    """email.clicked event should set response_type to 'clicked'."""
    db.add_prospect({
        "name": "Bob Lee",
        "email": "bob@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Bob Lee",
        sent_at="2026-03-15",
        resend_email_id="re_click_test",
    )

    intake.process_email_event({
        "event_type": "email.clicked",
        "email": "bob@example.com",
        "resend_email_id": "re_click_test",
    })

    updated = analytics.get_outcome(outcome["id"])
    assert updated["response_received"] == 1
    assert updated["response_type"] == "clicked"


def test_email_bounced_pauses_nurture():
    """email.bounced event should mark prospect and pause nurture."""
    db.add_prospect({
        "name": "Jane Doe",
        "email": "jane@example.com",
        "source": "website",
        "send_channel": "resend",
    })

    result = intake.process_email_event({
        "event_type": "email.bounced",
        "email": "jane@example.com",
    })

    prospect = db.get_prospect_by_email("jane@example.com")
    assert "BOUNCED" in prospect["notes"]
    assert "bounced" in result.lower()


def test_email_complained_stops_outreach():
    """email.complained should set stage to 'Do Not Contact'."""
    db.add_prospect({
        "name": "Spam Reporter",
        "email": "spam@example.com",
        "source": "website",
        "send_channel": "resend",
    })

    intake.process_email_event({
        "event_type": "email.complained",
        "email": "spam@example.com",
    })

    prospect = db.get_prospect_by_email("spam@example.com")
    assert prospect["stage"] == "Do Not Contact"
    assert "COMPLAINT" in prospect["notes"]


def test_email_event_no_match_ignored():
    """Events with no matching outcome should be silently ignored."""
    result = intake.process_email_event({
        "event_type": "email.opened",
        "email": "unknown@example.com",
    })
    assert "ignored" in result.lower() or "no matching" in result.lower()
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_email_event_intake.py -v`
Expected: All 8 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_email_event_intake.py
git commit -m "test: comprehensive email event intake tests (open/click/bounce/complaint)"
```

---

### Task 8: Site-side CRM webhook helper (`pineault-wealth` repo)

**Files:**
- Create: `/Users/map98/Desktop/pineault-wealth/src/lib/crm.ts`

- [ ] **Step 1: Create `src/lib/crm.ts`**

```typescript
/**
 * Fire-and-forget CRM webhook helper.
 * POSTs lead data to calm-money-bot's /api/intake endpoint
 * over Railway internal networking. Errors are caught silently
 * so form submissions never fail due to CRM issues.
 */

const CRM_URL = process.env.CRM_INTERNAL_URL;
const CRM_SECRET = process.env.CRM_WEBHOOK_SECRET;

interface CrmPayload {
  type: string;
  data: Record<string, unknown>;
}

export async function crmNotify(payload: CrmPayload): Promise<void> {
  if (!CRM_URL || !CRM_SECRET) {
    console.warn("CRM_INTERNAL_URL or CRM_WEBHOOK_SECRET not set — skipping CRM notification");
    return;
  }

  try {
    const resp = await fetch(`${CRM_URL}/api/intake`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Webhook-Secret": CRM_SECRET,
      },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(5000),
    });

    if (!resp.ok) {
      console.error(`CRM webhook failed: ${resp.status} ${resp.statusText}`);
    }
  } catch (error) {
    // Fire-and-forget: log but don't throw
    console.error("CRM webhook error (non-blocking):", error);
  }
}
```

- [ ] **Step 2: Commit**

```bash
cd /Users/map98/Desktop/pineault-wealth
git add src/lib/crm.ts
git commit -m "feat: CRM webhook helper for Phase 6 site-CRM integration"
```

---

### Task 9: Wire site form handlers to CRM

**Files:**
- Modify: `/Users/map98/Desktop/pineault-wealth/src/app/api/contact/route.ts`
- Modify: `/Users/map98/Desktop/pineault-wealth/src/app/api/quiz/route.ts`
- Modify: `/Users/map98/Desktop/pineault-wealth/src/app/api/lead/route.ts`

- [ ] **Step 1: Add `crmNotify()` to contact form handler**

In `/Users/map98/Desktop/pineault-wealth/src/app/api/contact/route.ts`, add import at top:

```typescript
import { crmNotify } from "@/lib/crm";
```

After the `await resend.emails.send(...)` call (line 70), before `return NextResponse.json({ success: true })`, add:

```typescript
    // Notify CRM (fire-and-forget)
    crmNotify({
      type: "website_contact",
      data: {
        name: body.name.trim(),
        email: body.email.trim().toLowerCase(),
        phone: body.phone?.trim() || "",
        service: body.service,
        message: body.message.trim(),
      },
    });
```

Note: No `await` — fire-and-forget. The CRM call happens in the background; the user gets their response immediately.

- [ ] **Step 2: Add `crmNotify()` to quiz form handler**

In `/Users/map98/Desktop/pineault-wealth/src/app/api/quiz/route.ts`, add import at top:

```typescript
import { crmNotify } from "@/lib/crm";
```

After `await resend.emails.send(...)` (line 91), before the return:

```typescript
    // Notify CRM (fire-and-forget)
    crmNotify({
      type: "website_quiz",
      data: {
        email: body.email.trim().toLowerCase(),
        score: body.score,
        answers: body.answers,
        tier: tier.label,
      },
    });
```

- [ ] **Step 3: Add `crmNotify()` to lead/tool form handler**

In `/Users/map98/Desktop/pineault-wealth/src/app/api/lead/route.ts`, add import at top:

```typescript
import { crmNotify } from "@/lib/crm";
```

After `await resend.emails.send(...)` (line 56), before the return:

```typescript
    // Notify CRM (fire-and-forget)
    crmNotify({
      type: "website_tool",
      data: {
        email: body.email.trim().toLowerCase(),
        toolName: body.toolName.trim(),
      },
    });
```

- [ ] **Step 4: Commit**

```bash
cd /Users/map98/Desktop/pineault-wealth
git add src/app/api/contact/route.ts src/app/api/quiz/route.ts src/app/api/lead/route.ts
git commit -m "feat: wire contact, quiz, and lead forms to CRM via webhook"
```

---

### Task 10: Forward Resend engagement events to CRM

**Files:**
- Modify: `/Users/map98/Desktop/pineault-wealth/src/app/api/webhooks/resend/route.ts`

- [ ] **Step 1: Add CRM forwarding to Resend webhook handler**

In `/Users/map98/Desktop/pineault-wealth/src/app/api/webhooks/resend/route.ts`, add import:

```typescript
import { crmNotify } from "@/lib/crm";
```

After the svix verification and before the try/catch block (after line 43, before line 45), add the CRM forwarding logic. The key is that this must happen OUTSIDE the subscriber-specific switch block. Parse event from the already-consumed body string:

After the `event = wh.verify(...)` block (line 40), and BEFORE the existing try block (line 45), add:

```typescript
  // Forward engagement events to CRM (fire-and-forget, outside subscriber switch)
  const CRM_EVENTS = ["email.opened", "email.clicked", "email.bounced", "email.complained"];
  if (CRM_EVENTS.includes(event.type)) {
    crmNotify({
      type: "email_event",
      data: {
        event_type: event.type,
        email: event.data.to?.[0]?.toLowerCase() || "",
        resend_email_id: event.data.email_id || "",
      },
    });
  }
```

- [ ] **Step 2: Commit**

```bash
cd /Users/map98/Desktop/pineault-wealth
git add src/app/api/webhooks/resend/route.ts
git commit -m "feat: forward Resend engagement events to CRM for outcome tracking"
```

---

### Task 11: Final integration verification

- [ ] **Step 1: Run full CRM test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests pass (existing + new)

- [ ] **Step 2: Verify site builds**

Run: `cd /Users/map98/Desktop/pineault-wealth && npm run build`
Expected: Build succeeds with no TypeScript errors

- [ ] **Step 3: Commit any final fixes**

If tests or build reveal issues, fix and commit.

- [ ] **Step 4: Set environment variables (manual)**

On Railway, set these env vars:

**calm-money-bot service:**
- `RESEND_API_KEY` — same key as pineault-wealth uses
- `RESEND_FROM_EMAIL` — `marc@info.calmmoney.ca`
- `RESEND_REPLY_TO` — `mpineault1@gmail.com`

**pineault-wealth service:**
- `CRM_INTERNAL_URL` — `http://calm-money-bot.railway.internal:8080`
- `CRM_WEBHOOK_SECRET` — must match calm-money-bot's `INTAKE_WEBHOOK_SECRET`

- [ ] **Step 5: Deploy and smoke test**

Deploy both services. Test by submitting the contact form on calmmoney.ca and verifying:
1. Resend notification email still arrives (existing behavior)
2. CRM creates a Hot prospect with `send_channel=resend`
3. Telegram alert fires to Marc
4. Follow-up draft appears for approval
5. Approving sends via Resend (not copy-paste)
