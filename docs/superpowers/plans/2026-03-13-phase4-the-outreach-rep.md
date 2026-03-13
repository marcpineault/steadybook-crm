# Phase 4: "The Outreach Rep" Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a trust-gated outreach system with campaign management and lead nurture sequences, where all client-facing messages are compliance-checked and queued for Marc's approval.

**Architecture:** A trust ladder (`trust_config` table) controls AI autonomy. A `campaigns.py` module handles batch outreach against the insurance book + prospects tables. A `nurture.py` module manages multi-touch nurture sequences for cold/warm leads. All outreach flows through the existing `approval_queue` and compliance pipeline. The existing `FOLLOW_UP_SEQUENCES` in bot.py is replaced by the dynamic nurture system.

**Tech Stack:** Python 3.13, OpenAI GPT-4.1 (message drafting), GPT-4.1-mini (segmentation), python-telegram-bot 21.10 (inline keyboards), APScheduler 3.10.4, SQLite WAL mode.

**Important codebase notes:**
- `db.py` uses `conn.executescript()` for schema in `init_db()` — append new tables there
- OpenAI calls use `max_completion_tokens` (NOT `max_tokens`)
- Use `.replace()` for prompt templating (NOT `.format()`) — EXCEPTION: `briefing.py` uses `.format()` with `_escape_braces()`
- Bot uses `ADMIN_CHAT_ID` module-level constant and `_is_admin()` / `_require_admin()` helpers
- `approval_queue.py` has: `add_draft()`, `get_pending_drafts()`, `get_draft_by_id()`, `update_draft_status()`, `set_telegram_message_id()`, `get_pending_count()`
- `compliance.py` has: `check_compliance()`, `log_action()`, `get_audit_log()`, `update_audit_outcome()`
- `insurance_book` table has: id, name, phone, address, policy_start, status, last_called, notes, retry_date
- `db.read_insurance_book()` returns all entries, `db.read_pipeline()` returns all prospects
- `memory_engine.get_profile_summary_text(prospect_id)` returns client intelligence
- `scoring.score_prospect(prospect)` returns dict with score, reasons, action
- Inline keyboard pattern: `_draft_keyboard(queue_id)` + `handle_draft_callback()` in bot.py
- Brand voice evolution: approved `content_post` drafts are saved back to `brand_voice` via `handle_draft_callback`

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `campaigns.py` | Campaign creation, segmentation, batch message generation |
| Create | `nurture.py` | Lead nurture sequences — multi-touch personalized outreach |
| Create | `tests/test_campaigns.py` | Tests for campaigns module |
| Create | `tests/test_nurture.py` | Tests for nurture module |
| Modify | `db.py` | Add `trust_config`, `campaigns`, `nurture_sequences` tables |
| Modify | `bot.py` | Add `/trust`, `/campaign`, `/nurture` commands |
| Modify | `scheduler.py` | Add daily nurture check job |

---

## Chunk 1: Trust Ladder + Database Schema

### Task 1: Database Schema — trust_config, campaigns, nurture_sequences Tables

**Files:**
- Modify: `db.py` (inside `init_db()` executescript block)
- Create: `tests/test_outreach_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_outreach_schema.py`:

```python
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_outreach_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_trust_config_table_exists():
    with db.get_db() as conn:
        conn.execute("SELECT id, trust_level, changed_at, changed_by FROM trust_config LIMIT 1")


def test_campaigns_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, name, description, segment_query, status, channel, wave_count, created_at FROM campaigns LIMIT 1"
        )


def test_campaign_messages_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, campaign_id, prospect_name, content, status, queue_id, wave, created_at FROM campaign_messages LIMIT 1"
        )


def test_nurture_sequences_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, prospect_id, prospect_name, status, current_touch, total_touches, next_touch_date, created_at FROM nurture_sequences LIMIT 1"
        )


def test_trust_config_default():
    with db.get_db() as conn:
        row = conn.execute("SELECT trust_level FROM trust_config ORDER BY id DESC LIMIT 1").fetchone()
    # Should have a default row with level 1
    assert row is not None
    assert row["trust_level"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_outreach_schema.py -v`
Expected: FAIL — tables don't exist

- [ ] **Step 3: Add tables to db.py**

In `db.py`, inside the `init_db()` function's `conn.executescript(...)` block, append after the `market_calendar` table:

```sql
        CREATE TABLE IF NOT EXISTS trust_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trust_level INTEGER NOT NULL DEFAULT 1,
            changed_at TEXT DEFAULT (datetime('now')),
            changed_by TEXT DEFAULT 'system'
        );

        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            segment_query TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            channel TEXT DEFAULT 'email_draft',
            wave_count INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS campaign_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            prospect_name TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            queue_id INTEGER,
            wave INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS nurture_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER,
            prospect_name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            current_touch INTEGER DEFAULT 0,
            total_touches INTEGER DEFAULT 4,
            next_touch_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (prospect_id) REFERENCES prospects(id)
        );
```

Also, after the executescript block in `init_db()`, add a trust_config seed:

```python
    # Seed default trust level if empty
    with get_db() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM trust_config").fetchone()[0]
        if existing == 0:
            conn.execute("INSERT INTO trust_config (trust_level, changed_by) VALUES (1, 'system')")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_outreach_schema.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_outreach_schema.py
git commit -m "feat: add trust_config, campaigns, campaign_messages, nurture_sequences tables"
```

---

### Task 2: Trust Ladder — /trust Command + Helper Functions

**Files:**
- Modify: `bot.py` (add `/trust` command and `get_trust_level()` helper)

- [ ] **Step 1: Add trust level helper to bot.py**

Add near other helper functions in `bot.py`:

```python
def get_trust_level():
    """Get the current trust level (1-3). Defaults to 1."""
    try:
        with db.get_db() as conn:
            row = conn.execute("SELECT trust_level FROM trust_config ORDER BY id DESC LIMIT 1").fetchone()
            return row["trust_level"] if row else 1
    except Exception:
        return 1


def set_trust_level(level, changed_by="marc"):
    """Set the trust level (1-3)."""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO trust_config (trust_level, changed_by) VALUES (?, ?)",
            (level, changed_by),
        )
```

- [ ] **Step 2: Add /trust command to bot.py**

```python
async def cmd_trust(update, context):
    """View or set the AI trust level: /trust or /trust 2"""
    if not await _require_admin(update):
        return

    args = context.args
    current = get_trust_level()

    LEVEL_DESCRIPTIONS = {
        1: "Training wheels — I draft everything, you approve each message",
        2: "Trusted on routine — I send standard reminders autonomously, you review first-contact only",
        3: "Full autonomy — I handle all routine outreach, escalate exceptions only",
    }

    if not args:
        desc = LEVEL_DESCRIPTIONS.get(current, "Unknown")
        await update.message.reply_text(
            f"Current trust level: {current}\n{desc}\n\n"
            "Set with: /trust 1, /trust 2, or /trust 3"
        )
        return

    try:
        new_level = int(args[0])
    except ValueError:
        await update.message.reply_text("Usage: /trust 1, /trust 2, or /trust 3")
        return

    if new_level not in (1, 2, 3):
        await update.message.reply_text("Trust level must be 1, 2, or 3.")
        return

    set_trust_level(new_level)
    desc = LEVEL_DESCRIPTIONS.get(new_level, "")
    await update.message.reply_text(f"Trust level set to {new_level}.\n{desc}")
```

- [ ] **Step 3: Register handler in build_application()**

```python
    app.add_handler(CommandHandler("trust", cmd_trust))
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: add trust ladder with /trust command and trust level helpers"
```

---

## Chunk 2: Campaign System

### Task 3: Campaign Module — Core

**Files:**
- Create: `campaigns.py`
- Create: `tests/test_campaigns.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_campaigns.py`:

```python
import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_campaigns"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import campaigns


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_insurance_book():
    entries = [
        {"name": "Alice Johnson", "phone": "555-0101", "notes": "Life insurance only, no disability"},
        {"name": "Bob Smith", "phone": "555-0102", "notes": "Home and auto, no life insurance"},
        {"name": "Carol White", "phone": "555-0103", "notes": "Full coverage — life, disability, home"},
    ]
    for e in entries:
        db.add_insurance_entry(e)


def _seed_prospects():
    for p in [
        {"name": "Alice Johnson", "stage": "Client", "product": "Life Insurance", "priority": "Warm"},
        {"name": "Dave Brown", "stage": "Discovery Call", "product": "Disability Insurance", "priority": "Hot"},
    ]:
        db.add_prospect(p)


def test_create_campaign():
    camp = campaigns.create_campaign(
        name="Disability cross-sell",
        description="Reach out to life insurance clients who don't have disability",
        channel="email_draft",
    )
    assert camp is not None
    assert camp["id"] > 0
    assert camp["status"] == "draft"


def test_get_campaign():
    camp = campaigns.create_campaign(name="Test campaign", description="test")
    fetched = campaigns.get_campaign(camp["id"])
    assert fetched is not None
    assert fetched["name"] == "Test campaign"


def test_get_campaign_not_found():
    result = campaigns.get_campaign(9999)
    assert result is None


def test_list_campaigns():
    campaigns.create_campaign(name="Camp 1", description="test1")
    campaigns.create_campaign(name="Camp 2", description="test2")
    all_camps = campaigns.list_campaigns()
    assert len(all_camps) >= 2


@patch("campaigns.openai_client")
def test_segment_audience(mock_client):
    _seed_insurance_book()
    _seed_prospects()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(["Alice Johnson"])
    mock_client.chat.completions.create.return_value = mock_response

    matches = campaigns.segment_audience(
        criteria="life insurance clients who don't have disability coverage",
    )
    assert isinstance(matches, list)
    assert len(matches) >= 1


@patch("campaigns.openai_client")
@patch("campaigns.compliance")
def test_generate_campaign_message(mock_compliance, mock_client):
    _seed_insurance_book()
    _seed_prospects()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Alice, I noticed you have great life insurance coverage. Have you considered disability protection?"
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    msg = campaigns.generate_campaign_message(
        prospect_name="Alice Johnson",
        campaign_context="Disability cross-sell for existing life insurance clients",
        channel="email_draft",
    )
    assert msg is not None
    assert "content" in msg
    assert msg["compliance_passed"] is True


def test_update_campaign_status():
    camp = campaigns.create_campaign(name="Status test", description="test")
    campaigns.update_campaign_status(camp["id"], "active")
    updated = campaigns.get_campaign(camp["id"])
    assert updated["status"] == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_campaigns.py -v`
Expected: FAIL — `campaigns` module doesn't exist

- [ ] **Step 3: Implement campaigns.py**

Create `campaigns.py`:

```python
"""Campaign management for batch outreach.

Creates and manages targeted outreach campaigns against the insurance book
and prospect pipeline. Each campaign segments an audience, generates
personalized messages, and queues them for Marc's approval.
"""

import json
import logging
import os

from openai import OpenAI

import approval_queue
import compliance
import db
import memory_engine

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

SEGMENT_PROMPT = """You are helping Marc Pereira, a financial advisor at Co-operators in London, Ontario, segment his client base for a targeted outreach campaign.

CRITERIA: {criteria}

Here are Marc's current clients from his insurance book:
{insurance_book_summary}

And his active prospects:
{prospects_summary}

Return a JSON array of names that match the criteria. Include ONLY names that clearly match.
Return ONLY the JSON array, no explanation. Example: ["Alice Johnson", "Bob Smith"]"""

MESSAGE_PROMPT = """You are drafting a personalized outreach message for Marc Pereira, a financial advisor at Co-operators in London, Ontario.

CAMPAIGN: {campaign_context}
RECIPIENT: {prospect_name}
CHANNEL: {channel}

CLIENT INTELLIGENCE:
{client_intel}

GUIDELINES:
1. Sound like Marc — warm, professional, never salesy
2. Reference something specific about the client (shows you know them)
3. Keep it concise: email 100-150 words, SMS 50-80 words, LinkedIn DM 80-120 words
4. Include a clear, low-pressure call to action
5. NEVER make specific return promises or misleading claims
6. For existing clients: acknowledge the relationship, don't sell from scratch

Write ONLY the message text. No subject lines, no meta-commentary."""


def create_campaign(name, description, channel="email_draft"):
    """Create a new campaign. Returns dict with campaign data."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO campaigns (name, description, channel)
               VALUES (?, ?, ?)""",
            (name, description, channel),
        )
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_campaign(campaign_id):
    """Get a campaign by ID. Returns dict or None."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return dict(row) if row else None


def list_campaigns(status=None):
    """List all campaigns, optionally filtered by status."""
    with db.get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_campaign_status(campaign_id, status):
    """Update campaign status (draft, active, paused, completed)."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE campaigns SET status = ? WHERE id = ?",
            (status, campaign_id),
        )


def segment_audience(criteria):
    """Use AI to segment the audience based on natural language criteria.

    Returns list of matching client names.
    """
    # Gather data
    insurance_entries = db.read_insurance_book()
    prospects = db.read_pipeline()

    book_lines = []
    for e in insurance_entries[:50]:
        book_lines.append(f"- {e['name']}: {e.get('notes', '')[:100]}")
    book_text = "\n".join(book_lines) if book_lines else "No insurance book entries."

    prospect_lines = []
    for p in prospects[:50]:
        prospect_lines.append(f"- {p['name']}: {p.get('product', '?')} ({p.get('stage', '?')}), notes: {p.get('notes', '')[:80]}")
    prospect_text = "\n".join(prospect_lines) if prospect_lines else "No prospects."

    try:
        prompt = SEGMENT_PROMPT.replace("{criteria}", criteria)
        prompt = prompt.replace("{insurance_book_summary}", book_text)
        prompt = prompt.replace("{prospects_summary}", prospect_text)

        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rstrip()
            if raw.endswith("```"):
                raw = raw[:-3].rstrip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        names = json.loads(raw)
        return names if isinstance(names, list) else []
    except Exception:
        logger.exception("Audience segmentation failed")
        return []


def generate_campaign_message(prospect_name, campaign_context, channel="email_draft"):
    """Generate a single personalized campaign message.

    Returns dict with: prospect_name, content, compliance_passed, compliance_issues, queue_id.
    Returns None on failure.
    """
    # Gather client intelligence
    prospect = db.get_prospect_by_name(prospect_name)
    if prospect:
        client_intel = memory_engine.get_profile_summary_text(prospect["id"])
        if not client_intel or "No additional" in client_intel:
            client_intel = f"Product: {prospect.get('product', '?')}. Stage: {prospect.get('stage', '?')}. Notes: {prospect.get('notes', '')[:200]}"
    else:
        # Check insurance book
        book_entries = db.read_insurance_book()
        entry = next((e for e in book_entries if e["name"].lower() == prospect_name.lower()), None)
        client_intel = f"Insurance book client. Notes: {entry.get('notes', '')[:200]}" if entry else "No client data on file."

    try:
        # Static replacements first, user-sourced last
        prompt = MESSAGE_PROMPT.replace("{channel}", channel)
        prompt = prompt.replace("{campaign_context}", campaign_context)
        prompt = prompt.replace("{prospect_name}", prospect_name)
        prompt = prompt.replace("{client_intel}", client_intel)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            temperature=0.7,
        )
        content = response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Campaign message generation failed for %s", prospect_name)
        return None

    # Compliance check
    comp_result = compliance.check_compliance(content)
    compliance.log_action(
        action_type="campaign_message",
        target=prospect_name,
        content=content,
        compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
    )

    # Queue for approval
    draft = approval_queue.add_draft(
        draft_type="campaign",
        channel=channel,
        content=content,
        context=f"Campaign: {campaign_context}",
        prospect_id=prospect["id"] if prospect else None,
    )

    # Track in campaign_messages
    return {
        "prospect_name": prospect_name,
        "content": content,
        "compliance_passed": comp_result["passed"],
        "compliance_issues": comp_result.get("issues", []),
        "queue_id": draft["id"],
    }


def format_campaign_summary(campaign):
    """Format a campaign for Telegram display."""
    lines = [
        f"CAMPAIGN #{campaign['id']}: {campaign['name']}",
        f"Status: {campaign['status']} | Channel: {campaign['channel']}",
        f"Description: {campaign['description'][:200]}",
    ]

    # Count messages
    with db.get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = ?", (campaign["id"],)
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = ? AND status = 'pending'",
            (campaign["id"],),
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = ? AND status = 'approved'",
            (campaign["id"],),
        ).fetchone()[0]

    lines.append(f"Messages: {total} total, {pending} pending, {approved} approved")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_campaigns.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add campaigns.py tests/test_campaigns.py
git commit -m "feat: add campaign system for batch outreach with segmentation"
```

---

### Task 4: Wire Campaigns to Bot — /campaign Command

**Files:**
- Modify: `bot.py` (add `/campaign` command)

- [ ] **Step 1: Add /campaign command**

Add to `bot.py`:

```python
async def cmd_campaign(update, context):
    """Manage campaigns: /campaign new, /campaign list, /campaign <id> run"""
    if not await _require_admin(update):
        return

    import campaigns as camp

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "/campaign new <name> — Create a new campaign\n"
            "/campaign list — List all campaigns\n"
            "/campaign <id> segment <criteria> — Find matching clients\n"
            "/campaign <id> run — Generate messages for segmented audience\n"
            "/campaign <id> status — View campaign status"
        )
        return

    action = args[0].lower()

    if action == "new":
        if len(args) < 2:
            await update.message.reply_text("Usage: /campaign new <name>")
            return
        name = " ".join(args[1:])
        campaign = camp.create_campaign(name=name, description=name)
        await update.message.reply_text(
            f"Campaign #{campaign['id']} created: {name}\n\n"
            f"Next: /campaign {campaign['id']} segment <criteria>\n"
            f"Example: /campaign {campaign['id']} segment life insurance clients without disability"
        )

    elif action == "list":
        all_campaigns = camp.list_campaigns()
        if not all_campaigns:
            await update.message.reply_text("No campaigns yet. Create one with /campaign new <name>")
            return
        lines = ["YOUR CAMPAIGNS:\n"]
        for c in all_campaigns[:10]:
            lines.append(f"  #{c['id']} — {c['name']} ({c['status']})")
        await update.message.reply_text("\n".join(lines))

    elif args[0].isdigit():
        campaign_id = int(args[0])
        campaign = camp.get_campaign(campaign_id)
        if not campaign:
            await update.message.reply_text(f"Campaign #{campaign_id} not found.")
            return

        if len(args) < 2:
            text = camp.format_campaign_summary(campaign)
            await update.message.reply_text(text)
            return

        sub_action = args[1].lower()

        if sub_action == "segment":
            if len(args) < 3:
                await update.message.reply_text(f"Usage: /campaign {campaign_id} segment <criteria>")
                return
            criteria = " ".join(args[2:])
            await update.message.reply_text(f"Segmenting audience for: {criteria}...")
            matches = camp.segment_audience(criteria)
            if not matches:
                await update.message.reply_text("No matching clients found.")
                return

            # Store segment in campaign description
            with db.get_db() as conn:
                conn.execute(
                    "UPDATE campaigns SET description = ?, segment_query = ? WHERE id = ?",
                    (f"{campaign['name']} — {criteria}", criteria, campaign_id),
                )

            await update.message.reply_text(
                f"Found {len(matches)} matching clients:\n"
                + "\n".join(f"  - {n}" for n in matches[:20])
                + f"\n\nRun: /campaign {campaign_id} run to generate messages"
            )

        elif sub_action == "run":
            segment = campaign.get("segment_query", "")
            if not segment:
                await update.message.reply_text(f"Segment first: /campaign {campaign_id} segment <criteria>")
                return

            await update.message.reply_text("Generating campaign messages...")
            matches = camp.segment_audience(segment)
            generated = 0
            for name in matches[:20]:
                msg = camp.generate_campaign_message(
                    prospect_name=name,
                    campaign_context=campaign["description"],
                    channel=campaign["channel"],
                )
                if msg:
                    with db.get_db() as conn:
                        conn.execute(
                            "INSERT INTO campaign_messages (campaign_id, prospect_name, content, queue_id, wave) VALUES (?, ?, ?, ?, 1)",
                            (campaign_id, name, msg["content"], msg["queue_id"]),
                        )
                    generated += 1

            camp.update_campaign_status(campaign_id, "active")
            await update.message.reply_text(
                f"Generated {generated} messages for campaign #{campaign_id}.\n"
                f"Use /drafts to review and approve them."
            )

        elif sub_action == "status":
            text = camp.format_campaign_summary(campaign)
            await update.message.reply_text(text)

    else:
        await update.message.reply_text("Unknown campaign action. Use /campaign for help.")
```

- [ ] **Step 2: Register handler in build_application()**

```python
    app.add_handler(CommandHandler("campaign", cmd_campaign))
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add /campaign command for batch outreach management"
```

---

## Chunk 3: Nurture Sequences

### Task 5: Nurture Module — Core

**Files:**
- Create: `nurture.py`
- Create: `tests/test_nurture.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_nurture.py`:

```python
import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_nurture"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import nurture


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect():
    db.add_prospect({
        "name": "Sarah Chen", "stage": "New Lead", "priority": "Warm",
        "product": "Life Insurance", "email": "sarah@example.com",
        "notes": "Referred by existing client. Has two kids.",
    })
    with db.get_db() as conn:
        return conn.execute("SELECT id FROM prospects WHERE name = 'Sarah Chen'").fetchone()[0]


def test_create_sequence():
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    assert seq is not None
    assert seq["status"] == "active"
    assert seq["total_touches"] == 4
    assert seq["current_touch"] == 0


def test_create_sequence_no_duplicate():
    pid = _seed_prospect()
    seq1 = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    seq2 = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    # Should return existing active sequence, not create duplicate
    assert seq2["id"] == seq1["id"]


def test_get_active_sequences():
    pid = _seed_prospect()
    nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    active = nurture.get_active_sequences()
    assert len(active) >= 1


def test_get_due_touches():
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    # Set next_touch_date to yesterday so it's due
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE nurture_sequences SET next_touch_date = ? WHERE id = ?",
            (yesterday, seq["id"]),
        )
    due = nurture.get_due_touches()
    assert len(due) >= 1


@patch("nurture.openai_client")
@patch("nurture.compliance")
def test_generate_touch(mock_compliance, mock_client):
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Sarah, I came across an article about life insurance for young families that I thought you might find helpful."
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    touch = nurture.generate_touch(seq["id"])
    assert touch is not None
    assert "content" in touch


def test_complete_sequence():
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    nurture.complete_sequence(seq["id"], reason="booked_meeting")
    updated = nurture.get_sequence(seq["id"])
    assert updated["status"] == "completed"


def test_get_sequence_not_found():
    result = nurture.get_sequence(9999)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_nurture.py -v`
Expected: FAIL — `nurture` module doesn't exist

- [ ] **Step 3: Implement nurture.py**

Create `nurture.py`:

```python
"""Lead nurture sequences — personalized multi-touch outreach.

For prospects who enter the pipeline but aren't meeting-ready, this module
builds and executes 3-5 value touches over 2-4 weeks:
  Touch 1: Relevant educational content
  Touch 2: Specific insight related to their situation
  Touch 3: Soft ask (booking link)
  Touch 4+: Additional value or re-engagement
"""

import logging
import os
from datetime import datetime, timedelta

from openai import OpenAI

import approval_queue
import compliance
import db
import memory_engine

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

TOUCH_TYPES = {
    1: {"type": "educational", "description": "Share relevant educational content about their product interest"},
    2: {"type": "insight", "description": "Share a specific insight related to their situation"},
    3: {"type": "soft_ask", "description": "Soft ask — invite them to book a chat, include booking link"},
    4: {"type": "value_add", "description": "Additional value — a different angle or follow-up on earlier touches"},
}

TOUCH_SPACING_DAYS = [3, 5, 7, 10]  # Days between touches 1→2, 2→3, 3→4, 4→end

NURTURE_PROMPT = """You are writing a nurture message for Marc Pereira, a financial advisor at Co-operators in London, Ontario.

This is touch {touch_number} of {total_touches} in a nurture sequence.
TOUCH TYPE: {touch_type} — {touch_description}

PROSPECT: {prospect_name}
PRODUCT INTEREST: {product}
STAGE: {stage}

CLIENT INTELLIGENCE:
{client_intel}

CHANNEL: email

GUIDELINES:
1. Sound like Marc — warm, approachable, not salesy
2. This is a nurture message, not a hard sell
3. Keep it concise (100-150 words for email)
4. Reference their specific situation when possible
5. Touch 3 should include Marc's booking link: https://outlook.office365.com/book/MarcPereira
6. NEVER make return promises or misleading claims

Write ONLY the message text."""


def create_sequence(prospect_name, prospect_id=None, total_touches=4):
    """Create a nurture sequence for a prospect. Returns existing if already active."""
    with db.get_db() as conn:
        # Check for existing active sequence
        existing = conn.execute(
            "SELECT * FROM nurture_sequences WHERE prospect_name = ? AND status = 'active'",
            (prospect_name,),
        ).fetchone()
        if existing:
            return dict(existing)

        next_date = (datetime.now() + timedelta(days=TOUCH_SPACING_DAYS[0])).strftime("%Y-%m-%d")
        cursor = conn.execute(
            """INSERT INTO nurture_sequences (prospect_id, prospect_name, total_touches, next_touch_date)
               VALUES (?, ?, ?, ?)""",
            (prospect_id, prospect_name, total_touches, next_date),
        )
        row = conn.execute("SELECT * FROM nurture_sequences WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_sequence(sequence_id):
    """Get a nurture sequence by ID. Returns dict or None."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM nurture_sequences WHERE id = ?", (sequence_id,)).fetchone()
        return dict(row) if row else None


def get_active_sequences():
    """Get all active nurture sequences."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM nurture_sequences WHERE status = 'active' ORDER BY next_touch_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_due_touches():
    """Get nurture sequences with touches due today or earlier."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM nurture_sequences WHERE status = 'active' AND next_touch_date <= ? ORDER BY next_touch_date ASC",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


def generate_touch(sequence_id):
    """Generate the next nurture touch for a sequence.

    Returns dict with: prospect_name, content, touch_number, queue_id. Returns None on failure.
    """
    seq = get_sequence(sequence_id)
    if not seq or seq["status"] != "active":
        return None

    next_touch = seq["current_touch"] + 1
    if next_touch > seq["total_touches"]:
        complete_sequence(sequence_id, reason="all_touches_sent")
        return None

    touch_info = TOUCH_TYPES.get(next_touch, TOUCH_TYPES[4])

    # Gather context
    prospect = db.get_prospect_by_name(seq["prospect_name"])
    if prospect:
        client_intel = memory_engine.get_profile_summary_text(prospect["id"])
        if not client_intel or "No additional" in client_intel:
            client_intel = f"Notes: {prospect.get('notes', '')[:200]}"
        product = prospect.get("product", "Not specified")
        stage = prospect.get("stage", "Unknown")
    else:
        client_intel = "No client data on file."
        product = "Not specified"
        stage = "New Lead"

    try:
        # Static replacements first, user-sourced last
        prompt = NURTURE_PROMPT.replace("{touch_number}", str(next_touch))
        prompt = prompt.replace("{total_touches}", str(seq["total_touches"]))
        prompt = prompt.replace("{touch_type}", touch_info["type"])
        prompt = prompt.replace("{touch_description}", touch_info["description"])
        prompt = prompt.replace("{product}", product)
        prompt = prompt.replace("{stage}", stage)
        prompt = prompt.replace("{prospect_name}", seq["prospect_name"])
        prompt = prompt.replace("{client_intel}", client_intel)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            temperature=0.7,
        )
        content = response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Nurture touch generation failed for %s", seq["prospect_name"])
        return None

    # Compliance
    comp_result = compliance.check_compliance(content)
    compliance.log_action(
        action_type="nurture_touch",
        target=seq["prospect_name"],
        content=content,
        compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
    )

    # Queue for approval
    draft = approval_queue.add_draft(
        draft_type="nurture",
        channel="email_draft",
        content=content,
        context=f"Nurture touch {next_touch}/{seq['total_touches']} — {touch_info['type']}",
        prospect_id=seq.get("prospect_id"),
    )

    # Advance sequence
    with db.get_db() as conn:
        spacing_idx = min(next_touch, len(TOUCH_SPACING_DAYS)) - 1
        next_date = (datetime.now() + timedelta(days=TOUCH_SPACING_DAYS[spacing_idx])).strftime("%Y-%m-%d")
        conn.execute(
            "UPDATE nurture_sequences SET current_touch = ?, next_touch_date = ? WHERE id = ?",
            (next_touch, next_date, sequence_id),
        )

    return {
        "prospect_name": seq["prospect_name"],
        "content": content,
        "touch_number": next_touch,
        "total_touches": seq["total_touches"],
        "queue_id": draft["id"],
    }


def complete_sequence(sequence_id, reason="manual"):
    """Mark a nurture sequence as completed."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE nurture_sequences SET status = 'completed' WHERE id = ?",
            (sequence_id,),
        )
    logger.info("Nurture sequence #%s completed: %s", sequence_id, reason)


def format_sequence_for_telegram(seq):
    """Format a nurture sequence for Telegram display."""
    return (
        f"NURTURE: {seq['prospect_name']}\n"
        f"Progress: {seq['current_touch']}/{seq['total_touches']} touches\n"
        f"Status: {seq['status']}\n"
        f"Next touch: {seq.get('next_touch_date', 'N/A')}"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_nurture.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add nurture.py tests/test_nurture.py
git commit -m "feat: add lead nurture sequence module with multi-touch outreach"
```

---

## Chunk 4: Bot Commands + Scheduler + Integration

### Task 6: Wire Nurture to Bot — /nurture Command + Scheduler Job

**Files:**
- Modify: `bot.py` (add `/nurture` command)
- Modify: `scheduler.py` (add daily nurture check job)

- [ ] **Step 1: Add /nurture command to bot.py**

```python
async def cmd_nurture(update, context):
    """Manage nurture sequences: /nurture, /nurture start <name>, /nurture stop <id>"""
    if not await _require_admin(update):
        return

    import nurture

    args = context.args
    if not args:
        active = nurture.get_active_sequences()
        if not active:
            await update.message.reply_text(
                "No active nurture sequences.\n"
                "Start one: /nurture start <prospect name>"
            )
            return
        lines = [f"ACTIVE NURTURE SEQUENCES ({len(active)}):\n"]
        for seq in active[:10]:
            lines.append(nurture.format_sequence_for_telegram(seq))
            lines.append("")
        await update.message.reply_text("\n".join(lines))
        return

    action = args[0].lower()

    if action == "start":
        if len(args) < 2:
            await update.message.reply_text("Usage: /nurture start <prospect name>")
            return
        name = " ".join(args[1:])
        prospect = db.get_prospect_by_name(name)
        pid = prospect["id"] if prospect else None
        seq = nurture.create_sequence(prospect_name=name, prospect_id=pid)
        await update.message.reply_text(
            f"Nurture sequence started for {name}.\n"
            f"Sequence #{seq['id']} — {seq['total_touches']} touches over ~3 weeks.\n"
            f"First touch: {seq.get('next_touch_date', 'soon')}"
        )

    elif action == "stop":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("Usage: /nurture stop <sequence_id>")
            return
        seq_id = int(args[1])
        nurture.complete_sequence(seq_id, reason="manual_stop")
        await update.message.reply_text(f"Nurture sequence #{seq_id} stopped.")

    else:
        await update.message.reply_text("Usage: /nurture, /nurture start <name>, /nurture stop <id>")
```

- [ ] **Step 2: Register handler in build_application()**

```python
    app.add_handler(CommandHandler("nurture", cmd_nurture))
```

- [ ] **Step 3: Add daily nurture check to scheduler.py**

Add to `scheduler.py` BEFORE `start_scheduler()`:

```python
async def check_nurture_sequences():
    """Check for due nurture touches and generate them."""
    if not _bot or not CHAT_ID:
        return

    try:
        import nurture

        due = nurture.get_due_touches()
        if not due:
            return

        generated = 0
        for seq in due:
            try:
                touch = nurture.generate_touch(seq["id"])
                if touch:
                    # Send notification to Marc
                    text = (
                        f"NURTURE TOUCH — {touch['prospect_name']}\n"
                        f"Touch {touch['touch_number']}/{touch['total_touches']}\n\n"
                        f"{touch['content'][:500]}\n\n"
                        f"Queue #{touch['queue_id']} — /drafts to review"
                    )
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    keyboard = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("Approve", callback_data=f"draft_approve_{touch['queue_id']}"),
                            InlineKeyboardButton("Skip", callback_data=f"draft_dismiss_{touch['queue_id']}"),
                        ],
                    ])
                    await _bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=keyboard)
                    generated += 1
            except Exception:
                logger.exception("Nurture touch failed for sequence #%s", seq["id"])

        if generated:
            logger.info("Generated %d nurture touches", generated)

    except Exception:
        logger.exception("Nurture sequence check failed")
```

Register the job in `start_scheduler()` BEFORE `scheduler.start()`:

```python
    # Daily nurture check — 9AM ET weekdays
    scheduler.add_job(
        check_nurture_sequences,
        "cron",
        day_of_week="mon-fri",
        hour=9,
        minute=0,
        id="check_nurture_sequences",
        name="Nurture Sequence Check",
    )
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py scheduler.py
git commit -m "feat: add /nurture command and daily nurture scheduler job"
```

---

### Task 7: Final Integration — Smoke Test

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify all new modules import cleanly**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -c "import campaigns; import nurture; print('Phase 4 modules OK')"`
Expected: "Phase 4 modules OK"

- [ ] **Step 3: Verify bot loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && TELEGRAM_BOT_TOKEN=test OPENAI_API_KEY=test TELEGRAM_CHAT_ID=123 WEBHOOK_SECRET=test python3 -c "import bot; print('Bot loads OK')"`
Expected: "Bot loads OK"

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: Phase 4 complete — The Outreach Rep with trust ladder, campaigns, and nurture"
```

---

## Summary of New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `campaigns.py` | ~200 | Campaign creation, audience segmentation, message generation |
| `nurture.py` | ~200 | Lead nurture sequences with multi-touch outreach |
| `tests/test_campaigns.py` | ~100 | Tests for campaigns module |
| `tests/test_nurture.py` | ~90 | Tests for nurture module |
| `tests/test_outreach_schema.py` | ~45 | Tests for new DB tables |

## Modified Files

| File | Changes |
|------|---------|
| `db.py` | +trust_config, campaigns, campaign_messages, nurture_sequences tables |
| `bot.py` | +/trust, /campaign, /nurture commands, trust level helpers |
| `scheduler.py` | +daily nurture sequence check job (9AM weekdays) |
