# Phase 1: "The Brain" — Intelligence Foundation

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a client memory engine, database-backed approval queue, compliance/audit layer, and strategic morning briefing to transform calm-money-bot from a reactive CRM into an intelligent business brain.

**Architecture:** Four new modules (`memory_engine.py`, `compliance.py`, `briefing.py`, `approval_queue.py`) plus schema additions to `db.py`. The memory engine extracts client facts from every interaction via GPT-4.1-mini. The approval queue persists all AI-generated drafts for Marc's review. The compliance layer filters outgoing messages. The briefing replaces the current morning message with a strategic daily brief powered by GPT-4.1.

**Tech Stack:** Python 3.13, SQLite (WAL mode), OpenAI GPT-4.1/GPT-4.1-mini, python-telegram-bot 21.10, APScheduler 3.10.4

**Spec:** `docs/superpowers/specs/2026-03-13-calm-money-ai-design.md`

**Note on bot.py refactoring:** The spec lists decomposing `bot.py` (2,387 lines) into `tools.py`, `handlers.py`, and `bot.py` as a prerequisite. This is deferred to a separate plan between Phase 1 and Phase 2 to keep this plan focused on new functionality. Phase 1 adds ~50 lines to `bot.py` (1 tool + 3 commands), which is manageable. The refactoring becomes critical before Phase 2 adds more.

---

## Chunk 1: Database Schema & Approval Queue

### File Structure (Chunk 1)

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `db.py` | Add 3 new tables: `client_memory`, `approval_queue`, `audit_log` |
| Create | `approval_queue.py` | CRUD operations for the approval queue |
| Create | `tests/test_approval_queue.py` | Tests for approval queue operations |
| Create | `tests/test_schema_additions.py` | Tests for new table schemas |

---

### Task 1: Add New Tables to Database Schema

**Files:**
- Modify: `db.py:97-185` (inside `init_db()`)
- Test: `tests/test_schema_additions.py`

- [ ] **Step 1: Write the failing test for new tables**

Create `tests/test_schema_additions.py`:

```python
import os
import sys
import sqlite3

# Setup test environment
os.environ["DATA_DIR"] = "/tmp/test_calm_bot_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_client_memory_table_exists():
    with db.get_db() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='client_memory'"
        )
        assert cursor.fetchone() is not None


def test_client_memory_columns():
    with db.get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(client_memory)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {"id", "prospect_id", "category", "fact", "source", "needs_review", "extracted_at"}
        assert expected == columns


def test_approval_queue_table_exists():
    with db.get_db() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_queue'"
        )
        assert cursor.fetchone() is not None


def test_approval_queue_columns():
    with db.get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(approval_queue)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "type", "prospect_id", "channel", "content", "context",
            "status", "created_at", "acted_on_at", "telegram_message_id",
        }
        assert expected == columns


def test_audit_log_table_exists():
    with db.get_db() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        )
        assert cursor.fetchone() is not None


def test_audit_log_columns():
    with db.get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(audit_log)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "timestamp", "action_type", "target", "content",
            "compliance_check", "approved_by", "outcome",
        }
        assert expected == columns
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_schema_additions.py -v`
Expected: FAIL — tables don't exist yet

- [ ] **Step 3: Add CREATE TABLE statements to db.py init_db()**

In `db.py`, append the following three CREATE TABLE statements **inside the existing `conn.executescript("""...""")` block** in `init_db()`, just before the closing `""");` (after the `tasks` table, around line 183):

```sql
            CREATE TABLE IF NOT EXISTS client_memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER REFERENCES prospects(id),
                category    TEXT NOT NULL,
                fact        TEXT NOT NULL,
                source      TEXT,
                needs_review INTEGER DEFAULT 0,
                extracted_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS approval_queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                type                TEXT NOT NULL,
                prospect_id         INTEGER REFERENCES prospects(id),
                channel             TEXT NOT NULL,
                content             TEXT NOT NULL,
                context             TEXT,
                status              TEXT DEFAULT 'pending',
                created_at          TEXT DEFAULT (datetime('now')),
                acted_on_at         TEXT,
                telegram_message_id TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT DEFAULT (datetime('now')),
                action_type      TEXT NOT NULL,
                target           TEXT,
                content          TEXT,
                compliance_check TEXT,
                approved_by      TEXT,
                outcome          TEXT
            );
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_schema_additions.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_schema_additions.py
git commit -m "feat: add client_memory, approval_queue, audit_log tables"
```

---

### Task 2: Approval Queue CRUD Module

**Files:**
- Create: `approval_queue.py`
- Test: `tests/test_approval_queue.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_approval_queue.py`:

```python
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_aq"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import approval_queue as aq


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_add_draft():
    draft = aq.add_draft(
        draft_type="follow_up",
        channel="email_draft",
        content="Hi Sarah, great meeting today...",
        context="post-call follow-up for discovery call",
        prospect_id=None,
    )
    assert draft["id"] is not None
    assert draft["status"] == "pending"
    assert draft["content"] == "Hi Sarah, great meeting today..."


def test_get_pending_drafts():
    aq.add_draft("follow_up", "email_draft", "Draft 1", "ctx1")
    aq.add_draft("outreach", "sms", "Draft 2", "ctx2")
    aq.add_draft("follow_up", "email_draft", "Draft 3", "ctx3")
    pending = aq.get_pending_drafts()
    assert len(pending) == 3


def test_get_pending_drafts_by_type():
    aq.add_draft("follow_up", "email_draft", "Draft 1", "ctx1")
    aq.add_draft("outreach", "sms", "Draft 2", "ctx2")
    pending = aq.get_pending_drafts(draft_type="follow_up")
    assert len(pending) == 1
    assert pending[0]["type"] == "follow_up"


def test_approve_draft():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    updated = aq.update_draft_status(draft["id"], "approved")
    assert updated["status"] == "approved"
    assert updated["acted_on_at"] is not None


def test_dismiss_draft():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    updated = aq.update_draft_status(draft["id"], "dismissed")
    assert updated["status"] == "dismissed"


def test_set_telegram_message_id():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    aq.set_telegram_message_id(draft["id"], "12345")
    pending = aq.get_pending_drafts()
    assert pending[0]["telegram_message_id"] == "12345"


def test_get_draft_by_id():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    fetched = aq.get_draft_by_id(draft["id"])
    assert fetched is not None
    assert fetched["content"] == "content"


def test_get_draft_by_id_not_found():
    result = aq.get_draft_by_id(9999)
    assert result is None


def test_pending_count():
    aq.add_draft("follow_up", "email_draft", "d1", "c1")
    aq.add_draft("follow_up", "email_draft", "d2", "c2")
    draft3 = aq.add_draft("follow_up", "email_draft", "d3", "c3")
    aq.update_draft_status(draft3["id"], "approved")
    assert aq.get_pending_count() == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_approval_queue.py -v`
Expected: FAIL — `approval_queue` module doesn't exist

- [ ] **Step 3: Implement approval_queue.py**

Create `approval_queue.py`:

```python
"""Database-backed approval queue for AI-generated drafts.

All drafted messages (follow-ups, outreach, content) are persisted here
so nothing is lost if the bot restarts or Marc misses a Telegram notification.
"""

from datetime import datetime, timezone
import db


def add_draft(draft_type, channel, content, context, prospect_id=None):
    """Add a new draft to the approval queue. Returns the created draft dict."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO approval_queue (type, prospect_id, channel, content, context)
               VALUES (?, ?, ?, ?, ?)""",
            (draft_type, prospect_id, channel, content, context),
        )
        return _row_to_dict(
            conn.execute("SELECT * FROM approval_queue WHERE id = ?", (cursor.lastrowid,)).fetchone()
        )


def get_pending_drafts(draft_type=None, limit=50):
    """Get pending drafts, optionally filtered by type."""
    with db.get_db() as conn:
        if draft_type:
            rows = conn.execute(
                "SELECT * FROM approval_queue WHERE status = 'pending' AND type = ? ORDER BY created_at ASC LIMIT ?",
                (draft_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM approval_queue WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_draft_by_id(draft_id):
    """Get a single draft by ID. Returns None if not found."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (draft_id,)).fetchone()
        return _row_to_dict(row) if row else None


def update_draft_status(draft_id, status):
    """Update draft status (approved, edited, dismissed, snoozed, sent). Returns updated draft."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE approval_queue SET status = ?, acted_on_at = ? WHERE id = ?",
            (status, now, draft_id),
        )
        row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (draft_id,)).fetchone()
        return _row_to_dict(row) if row else None


def set_telegram_message_id(draft_id, message_id):
    """Link a draft to its Telegram notification message."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE approval_queue SET telegram_message_id = ? WHERE id = ?",
            (str(message_id), draft_id),
        )


def get_pending_count():
    """Return count of pending drafts."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'"
        ).fetchone()
        return row[0] if row else 0


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_approval_queue.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add approval_queue.py tests/test_approval_queue.py
git commit -m "feat: add database-backed approval queue module"
```

---

## Chunk 2: Memory Engine

### File Structure (Chunk 2)

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `memory_engine.py` | Extract, store, retrieve, and manage client facts |
| Create | `tests/test_memory_engine.py` | Tests for memory extraction and CRUD |

---

### Task 3: Memory Engine — Storage & Retrieval

**Files:**
- Create: `memory_engine.py`
- Test: `tests/test_memory_engine.py`

- [ ] **Step 1: Write the failing tests for CRUD operations**

Create `tests/test_memory_engine.py`:

```python
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_mem"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import memory_engine


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()
    # Add a test prospect
    db.add_prospect({"name": "Sarah Chen", "product": "Life Insurance", "stage": "Discovery Call"})


def _get_prospect_id(name="Sarah Chen"):
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None


def test_add_fact():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(
        prospect_id=pid,
        category="life_context",
        fact="Daughter starts university Sept 2027",
        source="voice_note_2026-03-10",
    )
    assert fact["id"] is not None
    assert fact["category"] == "life_context"
    assert fact["needs_review"] == 0


def test_add_fact_needs_review():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(
        prospect_id=pid,
        category="financial_context",
        fact="Risk tolerance seems low",
        source="meeting_transcript",
        needs_review=True,
    )
    assert fact["needs_review"] == 1


def test_get_client_profile():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Has two kids", "voice_note")
    memory_engine.add_fact(pid, "financial_context", "Owns home in London", "chat")
    memory_engine.add_fact(pid, "key_dates", "Birthday March 15", "chat")
    profile = memory_engine.get_client_profile(pid)
    assert "life_context" in profile
    assert len(profile["life_context"]) == 1
    assert len(profile["financial_context"]) == 1
    assert len(profile["key_dates"]) == 1


def test_get_client_profile_empty():
    pid = _get_prospect_id()
    profile = memory_engine.get_client_profile(pid)
    assert profile == {}


def test_get_facts_needing_review():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Fact 1", "src", needs_review=False)
    memory_engine.add_fact(pid, "life_context", "Fact 2", "src", needs_review=True)
    memory_engine.add_fact(pid, "financial_context", "Fact 3", "src", needs_review=True)
    review = memory_engine.get_facts_needing_review()
    assert len(review) == 2


def test_confirm_fact():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(pid, "life_context", "Maybe has a dog", "chat", needs_review=True)
    memory_engine.confirm_fact(fact["id"])
    review = memory_engine.get_facts_needing_review()
    assert len(review) == 0


def test_delete_fact():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(pid, "life_context", "Wrong fact", "chat")
    memory_engine.delete_fact(fact["id"])
    profile = memory_engine.get_client_profile(pid)
    assert profile == {}


def test_get_profile_summary_text():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Has two kids aged 8 and 12", "voice_note")
    memory_engine.add_fact(pid, "financial_context", "Risk-averse investor", "meeting")
    memory_engine.add_fact(pid, "communication_prefs", "Prefers text over email", "chat")
    summary = memory_engine.get_profile_summary_text(pid)
    assert "life_context" in summary.lower() or "Life" in summary
    assert "two kids" in summary
    assert "Risk-averse" in summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_engine.py -v`
Expected: FAIL — `memory_engine` module doesn't exist

- [ ] **Step 3: Implement memory_engine.py — CRUD functions**

Create `memory_engine.py`:

```python
"""Client Memory Engine — extracts, stores, and retrieves relationship intelligence.

Transforms flat CRM records into rich client profiles by extracting facts
from every interaction (voice notes, chat, transcripts) via GPT.
"""

import json
import logging
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {
    "life_context",
    "financial_context",
    "communication_prefs",
    "relationship_signals",
    "conversation_history",
    "key_dates",
}


def add_fact(prospect_id, category, fact, source, needs_review=False):
    """Add a single fact to a prospect's memory profile. Returns the created fact dict."""
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}")
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO client_memory (prospect_id, category, fact, source, needs_review)
               VALUES (?, ?, ?, ?, ?)""",
            (prospect_id, category, fact, source, 1 if needs_review else 0),
        )
        row = conn.execute("SELECT * FROM client_memory WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_client_profile(prospect_id):
    """Get all facts for a prospect, organized by category.

    Returns dict like: {"life_context": [fact1, fact2], "financial_context": [fact3]}
    Empty categories are omitted.
    """
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM client_memory WHERE prospect_id = ? ORDER BY extracted_at ASC",
            (prospect_id,),
        ).fetchall()
    profile = {}
    for row in rows:
        cat = row["category"]
        if cat not in profile:
            profile[cat] = []
        profile[cat].append(dict(row))
    return profile


def get_profile_summary_text(prospect_id):
    """Get a human-readable summary of a prospect's memory profile.

    Used for including in GPT prompts (meeting prep, follow-ups, briefings).
    """
    profile = get_client_profile(prospect_id)
    if not profile:
        return "No additional client intelligence available."
    lines = []
    category_labels = {
        "life_context": "Life & Family",
        "financial_context": "Financial Situation",
        "communication_prefs": "Communication Preferences",
        "relationship_signals": "Relationship Notes",
        "conversation_history": "Key Conversations",
        "key_dates": "Important Dates",
    }
    for cat in VALID_CATEGORIES:
        if cat in profile:
            label = category_labels.get(cat, cat)
            facts = [f["fact"] for f in profile[cat]]
            lines.append(f"{label}: {'; '.join(facts)}")
    return "\n".join(lines)


def get_facts_needing_review():
    """Get all facts marked needs_review across all prospects."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT cm.*, p.name as prospect_name
               FROM client_memory cm
               JOIN prospects p ON cm.prospect_id = p.id
               WHERE cm.needs_review = 1
               ORDER BY cm.extracted_at ASC""",
        ).fetchall()
        return [dict(r) for r in rows]


def confirm_fact(fact_id):
    """Mark a fact as confirmed (clears needs_review flag)."""
    with db.get_db() as conn:
        conn.execute("UPDATE client_memory SET needs_review = 0 WHERE id = ?", (fact_id,))


def delete_fact(fact_id):
    """Delete a fact from client memory."""
    with db.get_db() as conn:
        conn.execute("DELETE FROM client_memory WHERE id = ?", (fact_id,))


def get_all_facts_for_prospect(prospect_id):
    """Get flat list of all facts for a prospect (used in extraction to check for duplicates)."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM client_memory WHERE prospect_id = ? ORDER BY extracted_at ASC",
            (prospect_id,),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_engine.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py tests/test_memory_engine.py
git commit -m "feat: add memory engine CRUD for client intelligence profiles"
```

---

### Task 4: Memory Engine — GPT Extraction Pipeline

**Files:**
- Modify: `memory_engine.py` (add extraction functions)
- Test: `tests/test_memory_engine.py` (add extraction tests)

- [ ] **Step 1: Write the failing tests for extraction**

Append to `tests/test_memory_engine.py`:

```python
from unittest.mock import patch, MagicMock


def test_build_extraction_prompt():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Has two kids", "voice_note")
    prompt = memory_engine.build_extraction_prompt(
        prospect_name="Sarah Chen",
        prospect_id=pid,
        interaction_text="Sarah mentioned her husband runs a landscaping business in Byron",
        source="voice_note_2026-03-13",
    )
    assert "Sarah Chen" in prompt
    assert "Has two kids" in prompt  # existing facts included
    assert "husband runs a landscaping business" in prompt
    assert "life_context" in prompt  # category definitions included


def test_parse_extraction_response_valid():
    response = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband runs a landscaping business in Byron", "needs_review": False},
            {"category": "relationship_signals", "fact": "Referred by colleague at work", "needs_review": False},
        ]
    })
    facts = memory_engine.parse_extraction_response(response)
    assert len(facts) == 2
    assert facts[0]["category"] == "life_context"


def test_parse_extraction_response_with_backticks():
    response = '```json\n{"facts": [{"category": "life_context", "fact": "Has a dog", "needs_review": false}]}\n```'
    facts = memory_engine.parse_extraction_response(response)
    assert len(facts) == 1


def test_parse_extraction_response_invalid():
    facts = memory_engine.parse_extraction_response("not json at all")
    assert facts == []


def test_parse_extraction_response_empty_facts():
    response = json.dumps({"facts": []})
    facts = memory_engine.parse_extraction_response(response)
    assert facts == []


@patch("memory_engine.openai_client")
def test_extract_facts_from_interaction(mock_client):
    pid = _get_prospect_id()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband owns landscaping business", "needs_review": False},
        ]
    })
    mock_client.chat.completions.create.return_value = mock_response

    new_facts = memory_engine.extract_facts_from_interaction(
        prospect_name="Sarah Chen",
        prospect_id=pid,
        interaction_text="Sarah mentioned her husband runs a landscaping business",
        source="voice_note_2026-03-13",
    )
    assert len(new_facts) == 1
    # Verify fact was stored
    profile = memory_engine.get_client_profile(pid)
    assert "life_context" in profile


@patch("memory_engine.openai_client")
def test_extract_facts_api_failure(mock_client):
    pid = _get_prospect_id()
    mock_client.chat.completions.create.side_effect = Exception("API error")
    new_facts = memory_engine.extract_facts_from_interaction(
        prospect_name="Sarah Chen",
        prospect_id=pid,
        interaction_text="Some interaction",
        source="chat",
    )
    assert new_facts == []  # Graceful failure, no facts stored
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_engine.py -v -k "extraction or parse or build_extraction"`
Expected: FAIL — functions don't exist yet

- [ ] **Step 3: Add extraction functions to memory_engine.py**

Add to `memory_engine.py` (after the existing CRUD functions):

```python
import os
import re
from openai import OpenAI

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def build_extraction_prompt(prospect_name, prospect_id, interaction_text, source):
    """Build the GPT prompt for extracting facts from an interaction.

    Includes existing facts to help GPT avoid duplicates and flag contradictions.
    """
    existing_facts = get_all_facts_for_prospect(prospect_id)
    existing_section = ""
    if existing_facts:
        fact_lines = [f"- [{f['category']}] {f['fact']}" for f in existing_facts]
        existing_section = f"\n\nEXISTING FACTS about {prospect_name}:\n" + "\n".join(fact_lines)

    return f"""Extract factual information about {prospect_name} from this interaction.

CATEGORIES (use exactly these):
- life_context: family, kids, career, hobbies, living situation, life events
- financial_context: risk tolerance, income bracket, assets, debts, retirement timeline, coverage gaps
- communication_prefs: preferred contact method, best times, response patterns, tone preferences
- relationship_signals: how they found us, referral source, warmth level, trust indicators
- conversation_history: key things said, objections raised, questions asked, promises made
- key_dates: birthdays, anniversaries, policy renewals, kid milestones, retirement dates
{existing_section}

RULES:
- Only extract what is explicitly stated or strongly implied. Do not speculate.
- If new information contradicts an existing fact, set needs_review to true.
- Do not duplicate existing facts. Only add genuinely new information.
- Each fact should be a single, specific, self-contained statement.

INTERACTION ({source}):
{interaction_text}

Respond with JSON only:
{{"facts": [{{"category": "...", "fact": "...", "needs_review": false}}]}}

If no new facts can be extracted, return: {{"facts": []}}"""


def parse_extraction_response(raw):
    """Parse GPT extraction response into list of fact dicts.

    Handles JSON wrapped in backticks or markdown.
    Returns empty list on parse error.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Strip markdown code block wrapper
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "facts" in data:
            return data["facts"]
        return []
    except (json.JSONDecodeError, KeyError):
        logger.warning("Failed to parse memory extraction response: %s", raw[:200])
        return []


def extract_facts_from_interaction(prospect_name, prospect_id, interaction_text, source):
    """Run GPT extraction on an interaction and store new facts.

    Returns list of newly created fact dicts. Returns empty list on API failure.
    """
    try:
        prompt = build_extraction_prompt(prospect_name, prospect_id, interaction_text, source)
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024,
            temperature=0.2,
        )
        raw = response.choices[0].message.content
        parsed_facts = parse_extraction_response(raw)

        created = []
        needs_review_facts = []
        for f in parsed_facts:
            cat = f.get("category", "")
            fact_text = f.get("fact", "")
            needs_rev = f.get("needs_review", False)
            if cat in VALID_CATEGORIES and fact_text:
                stored = add_fact(prospect_id, cat, fact_text, source, needs_review=needs_rev)
                created.append(stored)
                if needs_rev:
                    needs_review_facts.append((prospect_name, fact_text))

        # Proactively notify Marc about facts needing review
        if needs_review_facts:
            _notify_needs_review(needs_review_facts)

        return created

    except Exception:
        logger.exception("Memory extraction failed for %s", prospect_name)
        return []


def _notify_needs_review(facts):
    """Send Telegram notification to Marc about facts that need confirmation.

    Non-blocking — logs errors but doesn't raise.
    """
    try:
        from bot import notify_admin
        lines = ["I learned some things I'm not sure about:\n"]
        for name, fact in facts:
            lines.append(f"- {name}: {fact}")
        lines.append("\nUse /memory review to confirm or forget these.")
        notify_admin("\n".join(lines))
    except Exception:
        logger.debug("Could not send needs_review notification (non-blocking)")
```

- [ ] **Step 4: Run all memory engine tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_engine.py -v`
Expected: All 15 tests PASS

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py tests/test_memory_engine.py
git commit -m "feat: add GPT extraction pipeline to memory engine"
```

---

## Chunk 3: Compliance & Audit Layer

### File Structure (Chunk 3)

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `compliance.py` | Compliance filter + audit logging |
| Create | `tests/test_compliance.py` | Tests for compliance module |

---

### Task 5: Compliance Filter & Audit Logging

**Files:**
- Create: `compliance.py`
- Test: `tests/test_compliance.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compliance.py`:

```python
import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_compliance"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import compliance


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_log_action():
    entry = compliance.log_action(
        action_type="email_draft",
        target="Sarah Chen",
        content="Hi Sarah, following up on our meeting...",
    )
    assert entry["id"] is not None
    assert entry["action_type"] == "email_draft"
    assert entry["target"] == "Sarah Chen"


def test_log_action_with_compliance_result():
    entry = compliance.log_action(
        action_type="email_draft",
        target="Mike Johnson",
        content="Your returns are guaranteed at 8%!",
        compliance_check="FAIL: contains return guarantee",
    )
    assert "FAIL" in entry["compliance_check"]


def test_get_audit_log():
    compliance.log_action("email_draft", "Person A", "content A")
    compliance.log_action("content_generated", "LinkedIn", "post content")
    compliance.log_action("prospect_updated", "Person B", "stage changed")
    log = compliance.get_audit_log(limit=10)
    assert len(log) == 3


def test_get_audit_log_by_type():
    compliance.log_action("email_draft", "Person A", "content")
    compliance.log_action("content_generated", "LinkedIn", "post")
    log = compliance.get_audit_log(action_type="email_draft")
    assert len(log) == 1


def test_update_audit_outcome():
    entry = compliance.log_action("email_draft", "Sarah", "content")
    compliance.update_audit_outcome(entry["id"], outcome="sent", approved_by="marc")
    updated = compliance.get_audit_log(limit=1)[0]
    assert updated["outcome"] == "sent"
    assert updated["approved_by"] == "marc"


@patch("compliance.openai_client")
def test_check_compliance_pass(mock_client):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "passed": True,
        "issues": [],
    })
    mock_client.chat.completions.create.return_value = mock_response

    result = compliance.check_compliance("Hi Sarah, great talking today. Let's schedule a follow-up next week.")
    assert result["passed"] is True
    assert result["issues"] == []


@patch("compliance.openai_client")
def test_check_compliance_fail(mock_client):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "passed": False,
        "issues": ["Contains guarantee of returns"],
    })
    mock_client.chat.completions.create.return_value = mock_response

    result = compliance.check_compliance("I guarantee you'll see 8% returns on this investment!")
    assert result["passed"] is False
    assert len(result["issues"]) > 0


@patch("compliance.openai_client")
def test_check_compliance_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    result = compliance.check_compliance("Some message")
    assert result["passed"] is False
    assert "API failure" in result["issues"][0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_compliance.py -v`
Expected: FAIL — `compliance` module doesn't exist

- [ ] **Step 3: Implement compliance.py**

Create `compliance.py`:

```python
"""Compliance filter and audit logging for financial services.

Every AI-generated client-facing message passes through the compliance filter.
Every AI action is logged to the audit trail.
"""

import json
import logging
import os
import re

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

COMPLIANCE_PROMPT = """You are a compliance reviewer for a Canadian financial advisor (insurance and wealth management at Co-operators).

Review the following message that will be sent to a client or posted publicly. Check for:
1. Promises of specific returns or guaranteed outcomes
2. Misleading claims about products or coverage
3. Missing disclaimers where they would be required
4. Sharing of other clients' personal information
5. Unprofessional tone inappropriate for financial services
6. Any language that could be construed as financial advice without proper qualification

MESSAGE:
{message}

Respond with JSON only:
{{"passed": true/false, "issues": ["issue description 1", "issue description 2"]}}

If the message is compliant, return: {{"passed": true, "issues": []}}"""


def check_compliance(message):
    """Run compliance check on a message. Returns {"passed": bool, "issues": [str]}.

    On API failure, returns failed with explanation (fail-safe: never send unchecked).
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": COMPLIANCE_PROMPT.format(message=message)}],
            max_completion_tokens=512,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return {
            "passed": result.get("passed", False),
            "issues": result.get("issues", []),
        }
    except Exception as e:
        logger.exception("Compliance check failed")
        return {
            "passed": False,
            "issues": [f"API failure — compliance check could not complete: {e}"],
        }


def log_action(action_type, target, content, compliance_check=None, approved_by=None, outcome=None):
    """Log an AI action to the audit trail. Returns the created log entry."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO audit_log (action_type, target, content, compliance_check, approved_by, outcome)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action_type, target, content, compliance_check, approved_by, outcome),
        )
        row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_audit_log(action_type=None, target=None, limit=50):
    """Get audit log entries, optionally filtered."""
    with db.get_db() as conn:
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if target:
            query += " AND target = ?"
            params.append(target)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_audit_outcome(log_id, outcome, approved_by=None):
    """Update the outcome and approval status of an audit log entry."""
    with db.get_db() as conn:
        if approved_by:
            conn.execute(
                "UPDATE audit_log SET outcome = ?, approved_by = ? WHERE id = ?",
                (outcome, approved_by, log_id),
            )
        else:
            conn.execute(
                "UPDATE audit_log SET outcome = ? WHERE id = ?",
                (outcome, log_id),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_compliance.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add compliance.py tests/test_compliance.py
git commit -m "feat: add compliance filter and audit logging module"
```

---

## Chunk 4: Strategic Morning Briefing

### File Structure (Chunk 4)

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `briefing.py` | Generate strategic morning briefing content |
| Modify | `scheduler.py:143-242` | Replace `_morning_briefing_inner()` to use new briefing module |
| Create | `tests/test_briefing.py` | Tests for briefing generation |

---

### Task 6: Briefing Data Assembly

**Files:**
- Create: `briefing.py`
- Test: `tests/test_briefing.py`

- [ ] **Step 1: Write the failing tests for data assembly**

Create `tests/test_briefing.py`:

```python
import os
import sys
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_briefing"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import briefing


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_data():
    """Create test prospects, activities, and tasks."""
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    overdue = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    db.add_prospect({
        "name": "Sarah Chen", "stage": "Discovery Call", "priority": "Hot",
        "revenue": "5000", "aum": "200000", "next_followup": today,
    })
    db.add_prospect({
        "name": "Mike Johnson", "stage": "Needs Analysis", "priority": "Warm",
        "revenue": "3000", "next_followup": overdue,
    })
    db.add_prospect({
        "name": "Lisa Park", "stage": "Closed-Won", "priority": "Hot",
        "revenue": "8000",
    })
    db.add_activity({
        "date": yesterday, "prospect": "Sarah Chen",
        "action": "Phone call", "outcome": "Booked discovery call",
    })
    db.add_task({
        "title": "Send brochure to Mike", "prospect": "Mike Johnson",
        "due_date": overdue, "assigned_to": "123", "created_by": "123",
    })
    db.add_task({
        "title": "Prep for Sarah meeting", "prospect": "Sarah Chen",
        "due_date": today, "assigned_to": "123", "created_by": "123",
    })
    db.add_meeting({
        "date": today, "time": "14:00", "prospect": "Sarah Chen",
        "type": "Discovery Call",
    })


def test_assemble_briefing_data():
    _seed_data()
    data = briefing.assemble_briefing_data()
    assert "prospects" in data
    assert "activities_recent" in data
    assert "tasks_due_today" in data
    assert "tasks_overdue" in data
    assert "meetings_today" in data
    assert "pipeline_stats" in data
    assert "call_list" in data


def test_pipeline_stats():
    _seed_data()
    data = briefing.assemble_briefing_data()
    stats = data["pipeline_stats"]
    assert stats["active_count"] == 2  # excludes Closed-Won
    assert stats["total_revenue"] > 0
    assert "weighted_forecast" in stats


def test_pipeline_stats_empty():
    data = briefing.assemble_briefing_data()
    stats = data["pipeline_stats"]
    assert stats["active_count"] == 0


def test_call_list_ranked():
    _seed_data()
    data = briefing.assemble_briefing_data()
    assert len(data["call_list"]) > 0
    # Should be ranked by score descending
    if len(data["call_list"]) > 1:
        assert data["call_list"][0]["score"] >= data["call_list"][1]["score"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_briefing.py -v`
Expected: FAIL — `briefing` module doesn't exist

- [ ] **Step 3: Implement briefing.py — data assembly**

Create `briefing.py`:

```python
"""Strategic morning briefing generator.

Replaces the simple morning briefing with a CEO-level daily brief:
pipeline health, revenue forecast, priority moves, risk/opportunity alerts,
ranked call list, and queued actions.
"""

import logging
import os
import json
import re
from datetime import datetime, timedelta

import db
import scoring
import memory_engine

logger = logging.getLogger(__name__)

ACTIVE_STAGES = {
    "New Lead", "Contacted", "Discovery Call", "Needs Analysis",
    "Plan Presentation", "Proposal Sent", "Negotiation",
}


def assemble_briefing_data():
    """Gather all data needed for the morning briefing. Returns a dict."""
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    prospects = db.read_pipeline()
    active = [p for p in prospects if p.get("stage") in ACTIVE_STAGES]
    activities = db.read_activities(limit=50)
    recent_activities = [a for a in activities if a.get("date", "") >= week_ago]
    tasks_all = db.get_tasks(status="pending")
    tasks_due_today = db.get_due_tasks(today)
    tasks_overdue = db.get_overdue_tasks()
    meetings_today = [
        m for m in db.read_meetings()
        if m.get("date") == today and m.get("status") == "Scheduled"
    ]

    # Pipeline stats
    total_revenue = sum(float(p.get("revenue") or 0) for p in active)
    weighted_forecast = sum(
        float(p.get("revenue") or 0) * scoring.STAGE_PROBABILITY.get(p.get("stage", ""), 0.05)
        for p in active
    )

    # Ranked call list
    call_list = scoring.get_ranked_call_list(10)

    # Pending approval count (import here to avoid circular)
    try:
        import approval_queue
        pending_approvals = approval_queue.get_pending_count()
    except Exception:
        pending_approvals = 0

    return {
        "date": today,
        "prospects": active,
        "all_prospects": prospects,
        "activities_recent": recent_activities,
        "tasks_due_today": tasks_due_today,
        "tasks_overdue": tasks_overdue,
        "tasks_pending_count": len(tasks_all),
        "meetings_today": meetings_today,
        "call_list": call_list,
        "pending_approvals": pending_approvals,
        "pipeline_stats": {
            "active_count": len(active),
            "total_revenue": total_revenue,
            "weighted_forecast": round(weighted_forecast, 2),
            "hot_count": sum(1 for p in active if p.get("priority") == "Hot"),
            "stages": _stage_distribution(active),
        },
    }


def _stage_distribution(active_prospects):
    """Count prospects per stage."""
    dist = {}
    for p in active_prospects:
        stage = p.get("stage", "Unknown")
        dist[stage] = dist.get(stage, 0) + 1
    return dist
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_briefing.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add briefing.py tests/test_briefing.py
git commit -m "feat: add briefing data assembly for strategic morning brief"
```

---

### Task 7: Briefing GPT Generation & Scheduler Integration

**Files:**
- Modify: `briefing.py` (add GPT generation function)
- Modify: `scheduler.py:143-242` (replace morning briefing)
- Test: `tests/test_briefing.py` (add generation tests)

- [ ] **Step 1: Write failing tests for GPT briefing generation**

Append to `tests/test_briefing.py`:

```python
from unittest.mock import patch, MagicMock


@patch("briefing.openai_client")
def test_generate_briefing_text(mock_client):
    _seed_data()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Good morning Marc! Pipeline health: 75/100..."
    mock_client.chat.completions.create.return_value = mock_response

    text = briefing.generate_briefing_text()
    assert "Pipeline" in text or "pipeline" in text or "Marc" in text


@patch("briefing.openai_client")
def test_generate_briefing_text_api_failure(mock_client):
    _seed_data()
    mock_client.chat.completions.create.side_effect = Exception("API down")
    text = briefing.generate_briefing_text()
    # Should fall back to simple format
    assert text is not None
    assert len(text) > 0
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_briefing.py::test_generate_briefing_text -v`
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Add generate_briefing_text() to briefing.py**

Add to `briefing.py`:

```python
from openai import OpenAI

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

BRIEFING_PROMPT = """You are Marc's AI business partner for his financial planning practice at Co-operators in London, Ontario. Generate his morning briefing.

Write in plain text, no markdown, no emojis. Write like a sharp chief of staff texting the boss — concise, direct, actionable.

DATA:
Date: {date}

PIPELINE ({active_count} active deals):
{prospect_summary}

REVENUE:
- Total pipeline revenue: ${total_revenue:,.0f}
- Weighted forecast this month: ${weighted_forecast:,.0f}

TODAY'S MEETINGS:
{meetings_summary}

TASKS DUE TODAY:
{tasks_today_summary}

OVERDUE TASKS:
{tasks_overdue_summary}

CALL LIST (ranked by impact):
{call_list_summary}

RECENT ACTIVITY (last 7 days):
{activity_summary}

PENDING APPROVALS: {pending_approvals} items in queue

INSTRUCTIONS:
1. Start with a pipeline health score (0-100) and one-line trend assessment
2. Revenue forecast for the month
3. Top 2-3 priority moves for today with reasoning
4. Risk alerts (deals going cold, overdue follow-ups)
5. Today's call list with brief talking points for each
6. Mention pending approvals if any

Keep it under 2000 characters. Be specific — use names, numbers, and days."""


def generate_briefing_text():
    """Generate the full strategic morning briefing. Falls back to simple format on failure."""
    try:
        data = assemble_briefing_data()
        prompt = _build_briefing_prompt(data)
        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=2048,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Strategic briefing generation failed, falling back to simple format")
        return _fallback_briefing()


def _build_briefing_prompt(data):
    """Format the briefing prompt with assembled data."""
    stats = data["pipeline_stats"]

    # Prospect summary
    prospect_lines = []
    for p in data["prospects"][:15]:
        days_since = ""
        if p.get("updated_at"):
            try:
                updated = datetime.strptime(p["updated_at"][:10], "%Y-%m-%d")
                days = (datetime.now() - updated).days
                days_since = f" ({days}d ago)"
            except (ValueError, TypeError):
                pass
        prospect_lines.append(
            f"- {p.get('name')}: {p.get('stage')} | {p.get('priority', 'N/A')} | ${float(p.get('revenue') or 0):,.0f}{days_since}"
        )
    prospect_summary = "\n".join(prospect_lines) if prospect_lines else "No active prospects"

    # Meetings
    meeting_lines = [
        f"- {m.get('time')} — {m.get('prospect')} ({m.get('type', 'Meeting')})"
        for m in data["meetings_today"]
    ]
    meetings_summary = "\n".join(meeting_lines) if meeting_lines else "No meetings today"

    # Tasks
    today_lines = [f"- {t.get('title')} (prospect: {t.get('prospect', 'N/A')})" for t in data["tasks_due_today"]]
    tasks_today_summary = "\n".join(today_lines) if today_lines else "None"

    overdue_lines = [f"- {t.get('title')} — due {t.get('due_date')} (prospect: {t.get('prospect', 'N/A')})" for t in data["tasks_overdue"]]
    tasks_overdue_summary = "\n".join(overdue_lines) if overdue_lines else "None"

    # Call list (get_ranked_call_list returns flat merged dicts: {**prospect, **score_data})
    call_lines = []
    for entry in data["call_list"][:5]:
        call_lines.append(
            f"- {entry.get('name', 'Unknown')} (score: {entry.get('score', 0)}) — {entry.get('action', 'Follow up')}"
        )
    call_list_summary = "\n".join(call_lines) if call_lines else "No calls recommended"

    # Recent activity
    act_lines = [
        f"- {a.get('date')}: {a.get('prospect')} — {a.get('action')} → {a.get('outcome', 'N/A')}"
        for a in data["activities_recent"][:10]
    ]
    activity_summary = "\n".join(act_lines) if act_lines else "No recent activity"

    return BRIEFING_PROMPT.format(
        date=data["date"],
        active_count=stats["active_count"],
        prospect_summary=prospect_summary,
        total_revenue=stats["total_revenue"],
        weighted_forecast=stats["weighted_forecast"],
        meetings_summary=meetings_summary,
        tasks_today_summary=tasks_today_summary,
        tasks_overdue_summary=tasks_overdue_summary,
        call_list_summary=call_list_summary,
        activity_summary=activity_summary,
        pending_approvals=data["pending_approvals"],
    )


def _fallback_briefing():
    """Simple fallback briefing when GPT is unavailable (matches current morning briefing style)."""
    try:
        data = assemble_briefing_data()
        stats = data["pipeline_stats"]
        lines = [
            f"MORNING BRIEFING — {data['date']}",
            f"Pipeline: {stats['active_count']} active | ${stats['total_revenue']:,.0f} revenue | {stats['hot_count']} hot",
            "",
        ]
        if data["tasks_overdue"]:
            lines.append(f"OVERDUE TASKS ({len(data['tasks_overdue'])}):")
            for t in data["tasks_overdue"]:
                lines.append(f"  - {t.get('title')} (due {t.get('due_date')})")
            lines.append("")
        if data["tasks_due_today"]:
            lines.append(f"DUE TODAY ({len(data['tasks_due_today'])}):")
            for t in data["tasks_due_today"]:
                lines.append(f"  - {t.get('title')}")
            lines.append("")
        if data["meetings_today"]:
            lines.append("TODAY'S MEETINGS:")
            for m in data["meetings_today"]:
                lines.append(f"  - {m.get('time')} — {m.get('prospect')} ({m.get('type', 'Meeting')})")
        return "\n".join(lines)
    except Exception:
        logger.exception("Fallback briefing also failed")
        return "Morning briefing unavailable — check bot logs."
```

- [ ] **Step 4: Run all briefing tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_briefing.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Update scheduler.py to use new briefing module**

In `scheduler.py`, replace the body of `_morning_briefing_inner()` (lines ~158-242). Keep the outer function and Telegram sending logic, but replace the briefing content generation:

Find the section inside `_morning_briefing_inner()` that builds the briefing text (the long string construction). Replace it with:

```python
    import briefing as briefing_module
    text = briefing_module.generate_briefing_text()
```

Keep the existing `await bot.send_message(chat_id=CHAT_ID, text=text)` call at the end.

- [ ] **Step 6: Run existing scheduler tests (if any) to verify no regression**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add briefing.py scheduler.py tests/test_briefing.py
git commit -m "feat: strategic morning briefing with GPT-powered analysis"
```

---

## Chunk 5: Integration — Wire Memory Engine Into Existing Flows

### File Structure (Chunk 5)

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `voice_handler.py:102-186` | Add memory extraction after prospect processing |
| Modify | `bot.py:1317-1342` | Add memory extraction in tool dispatch after `add_activity`/`update_prospect` |
| Modify | `intake.py:20-85,221-318` | Add memory extraction on booking and email lead intake |
| Create | `tests/test_memory_integration.py` | Integration tests for memory extraction in existing flows |

---

### Task 8: Wire Memory Engine Into Voice Handler

**Files:**
- Modify: `voice_handler.py:102-186` (`extract_and_update` function)
- Test: `tests/test_memory_integration.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_memory_integration.py`:

```python
import os
import sys
from unittest.mock import patch, MagicMock
import json

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_memint"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import memory_engine


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _get_prospect_id(name):
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None


@patch("memory_engine.openai_client")
def test_voice_handler_triggers_memory_extraction(mock_me_client):
    """After voice_handler processes a transcript, memory extraction should run."""
    # Setup: create a prospect that voice_handler would find
    db.add_prospect({"name": "Sarah Chen", "product": "Life Insurance", "stage": "Discovery Call"})
    pid = _get_prospect_id("Sarah Chen")

    # Mock memory extraction response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband runs landscaping business", "needs_review": False},
        ]
    })
    mock_me_client.chat.completions.create.return_value = mock_response

    # Directly call memory extraction as voice_handler would
    new_facts = memory_engine.extract_facts_from_interaction(
        prospect_name="Sarah Chen",
        prospect_id=pid,
        interaction_text="Sarah mentioned her husband runs a landscaping business in Byron. She wants to protect her family income.",
        source="voice_note_2026-03-13",
    )

    assert len(new_facts) == 1
    profile = memory_engine.get_client_profile(pid)
    assert "life_context" in profile
```

- [ ] **Step 2: Run test to verify it passes** (this tests the memory_engine directly, should pass)

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_integration.py -v`
Expected: PASS

- [ ] **Step 3: Add memory extraction call to voice_handler.py**

In `voice_handler.py`, inside `extract_and_update()`, after the prospect update/creation loop (around line 183, after activities and interactions are logged), add:

```python
        # Extract client intelligence into Memory Engine
        try:
            import memory_engine
            prospect_obj = db.get_prospect_by_name(name)
            if prospect_obj:
                memory_engine.extract_facts_from_interaction(
                    prospect_name=name,
                    prospect_id=prospect_obj["id"],
                    interaction_text=transcript,
                    source=f"{source}_{datetime.now().strftime('%Y-%m-%d')}",
                )
        except Exception:
            logger.exception("Memory extraction failed for %s (non-blocking)", name)
```

Add at top of `voice_handler.py` (after existing imports, around line 9):

```python
from datetime import datetime
```

Note: `logging` and `logger` are already present in `voice_handler.py` (lines 7, 15).

- [ ] **Step 4: Run all tests to verify no regression**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add voice_handler.py tests/test_memory_integration.py
git commit -m "feat: wire memory extraction into voice handler pipeline"
```

---

### Task 9: Wire Memory Engine Into Intake & Chat

**Files:**
- Modify: `intake.py:20-85` (add memory extraction to `process_booking`)
- Modify: `intake.py:221-318` (add memory extraction to `process_email_lead`)
- Modify: `bot.py` (add memory extraction when GPT tool calls update a prospect)

- [ ] **Step 1: Add memory extraction to intake.py process_booking**

In `intake.py`, inside `process_booking()`, after the prospect is created/updated and the interaction is logged, add:

```python
    # Extract client intelligence
    try:
        import memory_engine
        prospect_obj = db.get_prospect_by_name(data.get("name", ""))
        if prospect_obj and data.get("notes"):
            memory_engine.extract_facts_from_interaction(
                prospect_name=prospect_obj["name"],
                prospect_id=prospect_obj["id"],
                interaction_text=f"Booking: {data.get('service', '')}. Notes: {data.get('notes', '')}",
                source="booking",
            )
    except Exception:
        logger.exception("Memory extraction failed for booking (non-blocking)")
```

- [ ] **Step 2: Add memory extraction to intake.py process_email_lead**

In `intake.py`, inside `process_email_lead()`, after the prospect is created/updated, add similar code:

```python
    # Extract client intelligence
    # Note: the local variable is `name` (line 275) and email body is in `body` (line 231)
    try:
        import memory_engine
        prospect_obj = db.get_prospect_by_name(name)
        if prospect_obj:
            memory_engine.extract_facts_from_interaction(
                prospect_name=prospect_obj["name"],
                prospect_id=prospect_obj["id"],
                interaction_text=body,
                source="email_lead",
            )
    except Exception:
        logger.exception("Memory extraction failed for email lead (non-blocking)")
```

- [ ] **Step 3: Add memory extraction to bot.py tool dispatch**

In `bot.py`, inside `_llm_respond()` around line 1319, after a tool call to `add_activity` or `update_prospect` succeeds, add memory extraction. Find the tool dispatch section and add after the tool result is collected:

```python
            # Trigger memory extraction for activity-related tools
            if tool_name in ("add_activity", "update_prospect") and "prospect" in tool_input:
                try:
                    import memory_engine as me
                    prospect_name = tool_input.get("prospect", tool_input.get("name", ""))
                    prospect_obj = db.get_prospect_by_name(prospect_name)
                    if prospect_obj:
                        # Use the full conversation context for extraction
                        context_text = " ".join(
                            m.get("content", "") for m in messages
                            if isinstance(m.get("content"), str) and m.get("role") == "user"
                        )
                        if context_text.strip():
                            me.extract_facts_from_interaction(
                                prospect_name=prospect_obj["name"],
                                prospect_id=prospect_obj["id"],
                                interaction_text=context_text,
                                source="chat",
                            )
                except Exception:
                    pass  # Non-blocking
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add intake.py bot.py
git commit -m "feat: wire memory extraction into intake webhooks and chat tool dispatch"
```

---

### Task 10: Data Migration — Backfill Memory Engine From Existing Data

**Files:**
- Modify: `memory_engine.py` (add migration function)
- Test: `tests/test_memory_engine.py` (add migration test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_memory_engine.py`:

```python
@patch("memory_engine.openai_client")
def test_backfill_from_existing_data(mock_client):
    pid = _get_prospect_id()
    # Add notes to the prospect
    db.update_prospect("Sarah Chen", {"notes": "Husband is a teacher. Two kids aged 8 and 12. Looking at term life for mortgage protection."})
    # Add an interaction
    db.add_interaction({
        "prospect": "Sarah Chen",
        "source": "voice_note",
        "raw_text": "Sarah called about her RRSP contributions and retirement planning timeline",
        "summary": "RRSP discussion",
    })

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband is a teacher", "needs_review": False},
            {"category": "life_context", "fact": "Two kids aged 8 and 12", "needs_review": False},
        ]
    })
    mock_client.chat.completions.create.return_value = mock_response

    count = memory_engine.backfill_prospect(pid, "Sarah Chen")
    assert count > 0  # At least one extraction ran
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_engine.py::test_backfill_from_existing_data -v`
Expected: FAIL — function doesn't exist

- [ ] **Step 3: Add backfill functions to memory_engine.py**

Add to `memory_engine.py`:

```python
def backfill_prospect(prospect_id, prospect_name):
    """Backfill memory for a single prospect from their notes and interactions.

    Returns count of extraction runs performed.
    """
    runs = 0

    # Extract from prospect notes
    with db.get_db() as conn:
        row = conn.execute("SELECT notes FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
        if row and row["notes"] and row["notes"].strip():
            extract_facts_from_interaction(prospect_name, prospect_id, row["notes"], "backfill_notes")
            runs += 1

    # Extract from interactions
    interactions = db.read_interactions(limit=100, prospect=prospect_name)
    for interaction in interactions:
        text = interaction.get("raw_text") or interaction.get("summary") or ""
        if text.strip():
            source = f"backfill_{interaction.get('source', 'unknown')}"
            extract_facts_from_interaction(prospect_name, prospect_id, text, source)
            runs += 1

    return runs


def backfill_all():
    """Backfill memory for all prospects. Returns total extraction runs."""
    total = 0
    prospects = db.read_pipeline()
    for p in prospects:
        if p.get("id") and p.get("name"):
            total += backfill_prospect(p["id"], p["name"])
    return total
```

- [ ] **Step 4: Run all memory engine tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/test_memory_engine.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add memory_engine.py tests/test_memory_engine.py
git commit -m "feat: add data migration backfill for memory engine"
```

---

## Chunk 6: Memory Engine GPT Tool + Briefing Profile Enrichment

### Task 11: Add Memory Profile to GPT System Prompts

**Files:**
- Modify: `bot.py` (update PROMPT_GENERAL and tool dispatch to include memory context)
- Modify: `briefing.py` (include memory profiles in briefing data)

- [ ] **Step 1: Add get_client_memory tool to bot.py**

In `bot.py`, add a new tool to the `TOOLS` list (around line 1150):

```python
    _tool("get_client_memory", "Get detailed client intelligence profile — life context, financial situation, communication preferences, key dates, relationship notes. Use this before drafting emails, preparing for meetings, or when you need deeper context about a prospect.", {
        "prospect_name": {"type": "string", "description": "Name of the prospect to look up"},
    }, ["prospect_name"]),
```

Add to `TOOL_FUNCTIONS` dict (around line 1190):

```python
    "get_client_memory": lambda args: _get_client_memory(args["prospect_name"]),
```

Add the handler function (near other tool handler functions):

```python
def _get_client_memory(prospect_name):
    """Look up client memory profile for a prospect."""
    import memory_engine
    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect:
        return f"No prospect found matching '{prospect_name}'"
    profile_text = memory_engine.get_profile_summary_text(prospect["id"])
    return f"Client Intelligence for {prospect['name']}:\n{profile_text}"
```

- [ ] **Step 2: Add memory profiles to briefing call list**

In `briefing.py`, update `_build_briefing_prompt()` to include memory summaries for the top call list entries. After building the call_list_summary, add:

```python
    # Enrich call list with memory context
    # Note: get_ranked_call_list returns flat merged dicts {**prospect, **score_data}
    enriched_calls = []
    for entry in data["call_list"][:5]:
        name = entry.get("name", "Unknown")
        line = f"- {name} (score: {entry.get('score', 0)}) — {entry.get('action', 'Follow up')}"
        if entry.get("id"):
            try:
                profile = memory_engine.get_profile_summary_text(entry["id"])
                if profile and "No additional" not in profile:
                    line += f"\n  Context: {profile[:200]}"
            except Exception:
                pass
        enriched_calls.append(line)
    call_list_summary = "\n".join(enriched_calls) if enriched_calls else "No calls recommended"
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add bot.py briefing.py
git commit -m "feat: expose memory engine as GPT tool and enrich briefing with client profiles"
```

---

### Task 12: Add /memory Command for Marc

**Files:**
- Modify: `bot.py` (add `/memory` command handler and registration)

- [ ] **Step 1: Add the command handler**

Add to `bot.py` (near the other command handlers):

```python
async def cmd_memory(update, context):
    """Show memory profile for a prospect, or list facts needing review."""
    if not _is_admin(update):
        return
    text = " ".join(context.args) if context.args else ""

    import memory_engine

    if not text or text.strip().lower() == "review":
        # Show facts needing review
        facts = memory_engine.get_facts_needing_review()
        if not facts:
            await update.message.reply_text("No facts needing review.")
            return
        lines = ["FACTS NEEDING REVIEW:\n"]
        for f in facts[:10]:
            lines.append(f"[{f['id']}] {f.get('prospect_name', '?')}: {f['fact']}")
            lines.append(f"  Category: {f['category']} | Source: {f.get('source', '?')}")
            lines.append(f"  /confirm {f['id']}  or  /forget {f['id']}")
            lines.append("")
        await update.message.reply_text("\n".join(lines))
        return

    # Look up prospect memory
    prospect = db.get_prospect_by_name(text)
    if not prospect:
        await update.message.reply_text(f"No prospect found matching '{text}'")
        return

    profile = memory_engine.get_profile_summary_text(prospect["id"])
    await update.message.reply_text(f"MEMORY: {prospect['name']}\n\n{profile}")
```

- [ ] **Step 2: Add /confirm and /forget command handlers**

```python
async def cmd_confirm(update, context):
    """Confirm a memory fact."""
    if not _is_admin(update):
        return
    import memory_engine
    if not context.args:
        await update.message.reply_text("Usage: /confirm <fact_id>")
        return
    try:
        fact_id = int(context.args[0])
        memory_engine.confirm_fact(fact_id)
        await update.message.reply_text(f"Fact #{fact_id} confirmed.")
    except (ValueError, Exception) as e:
        await update.message.reply_text(f"Error: {e}")


async def cmd_forget(update, context):
    """Delete a memory fact."""
    if not _is_admin(update):
        return
    import memory_engine
    if not context.args:
        await update.message.reply_text("Usage: /forget <fact_id>")
        return
    try:
        fact_id = int(context.args[0])
        memory_engine.delete_fact(fact_id)
        await update.message.reply_text(f"Fact #{fact_id} forgotten.")
    except (ValueError, Exception) as e:
        await update.message.reply_text(f"Error: {e}")
```

- [ ] **Step 3: Register commands in build_application()**

In `build_application()` (around line 2248), add:

```python
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CommandHandler("confirm", cmd_confirm))
    app.add_handler(CommandHandler("forget", cmd_forget))
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: add /memory, /confirm, /forget commands for client intelligence"
```

---

## Chunk 7: Final Integration & Smoke Test

### Task 13: Run Full Test Suite and Manual Verification

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify database migrations work on existing DB**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -c "import db; db.init_db(); print('Schema OK')"`
Expected: "Schema OK" — new tables created alongside existing data

- [ ] **Step 3: Verify all new modules import cleanly**

Run: `cd /Users/map98/Desktop/calm-money-bot && python -c "import memory_engine; import compliance; import briefing; import approval_queue; print('All modules OK')"`
Expected: "All modules OK"

- [ ] **Step 4: Verify bot starts without errors**

Run: `cd /Users/map98/Desktop/calm-money-bot && timeout 5 python -c "import bot; print('Bot module loads OK')" 2>&1 || true`
Expected: "Bot module loads OK" (may show warnings about missing env vars, that's fine)

- [ ] **Step 5: Final commit with any fixups**

```bash
git add -A
git commit -m "chore: Phase 1 complete — The Brain intelligence foundation"
```

---

## Summary of New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `memory_engine.py` | ~250 | Client fact extraction, storage, retrieval, backfill |
| `compliance.py` | ~120 | Compliance filter, audit logging |
| `briefing.py` | ~220 | Strategic morning briefing data assembly + GPT generation |
| `approval_queue.py` | ~90 | Database-backed draft queue CRUD |
| `tests/test_schema_additions.py` | ~60 | Schema validation tests |
| `tests/test_approval_queue.py` | ~90 | Approval queue CRUD tests |
| `tests/test_memory_engine.py` | ~180 | Memory engine CRUD + extraction tests |
| `tests/test_compliance.py` | ~90 | Compliance filter + audit log tests |
| `tests/test_briefing.py` | ~100 | Briefing data assembly + generation tests |
| `tests/test_memory_integration.py` | ~50 | Integration test for memory in voice handler |

## Modified Files

| File | Changes |
|------|---------|
| `db.py` | +3 CREATE TABLE statements in `init_db()` |
| `voice_handler.py` | +memory extraction call in `extract_and_update()` |
| `intake.py` | +memory extraction in `process_booking()` and `process_email_lead()` |
| `bot.py` | +`get_client_memory` tool, +`/memory`, `/confirm`, `/forget` commands, +memory extraction in tool dispatch |
| `scheduler.py` | Replace `_morning_briefing_inner()` body with `briefing.generate_briefing_text()` call |
