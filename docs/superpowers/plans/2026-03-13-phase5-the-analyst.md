# Phase 5: "The Analyst" Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an outcome tracking and learning loop system that measures what works, generates weekly insights, and feeds learnings back into content/outreach prompts.

**Architecture:** An `outcomes` table tracks whether AI-generated actions (emails, content, outreach) got responses or conversions. An `analytics.py` module aggregates this data. The existing `weekly_report` in scheduler.py is enhanced into a "Weekly Insights Digest" that includes AI-powered analysis of what worked and what didn't. Outcome tracking buttons are added to the draft approval flow so Marc can log results with minimal friction.

**Tech Stack:** Python 3.13, OpenAI GPT-4.1 (insights generation), SQLite WAL mode, python-telegram-bot 21.10 (inline keyboards), APScheduler 3.10.4.

**Important codebase notes:**
- `db.py` uses `conn.executescript()` for schema in `init_db()` — append new tables there
- OpenAI calls use `max_completion_tokens` (NOT `max_tokens`)
- Use `.replace()` for prompt templating (NOT `.format()`) — EXCEPTION: `briefing.py` uses `.format()` with `_escape_braces()`
- `compliance.py` has: `log_action()`, `get_audit_log()`, `update_audit_outcome()`
- `approval_queue.py` has: `add_draft()`, `get_pending_drafts()`, `update_draft_status()`
- Inline keyboard pattern: `_draft_keyboard(queue_id)` + `handle_draft_callback()` in bot.py
- Existing `weekly_report()` in scheduler.py runs Sunday 7PM ET — the spec says to replace/enhance it
- `db.read_pipeline()` for all prospects, `db.read_activities()` for activity log, `db.get_win_loss_stats()` for wins/losses
- `ADMIN_CHAT_ID` module-level constant, `_is_admin()` / `_require_admin()` helpers

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `analytics.py` | Outcome tracking, aggregation, insights generation |
| Create | `tests/test_analytics.py` | Tests for analytics module |
| Create | `tests/test_outcomes_schema.py` | Tests for outcomes DB table |
| Modify | `db.py` | Add `outcomes` table |
| Modify | `bot.py` | Add `/outcomes` command, outcome tracking buttons on approved drafts |
| Modify | `scheduler.py` | Enhance `weekly_report` with AI insights digest, reschedule to 6PM |
| Modify | `briefing.py` | Add "what's working" learning context to daily briefing prompt |

---

## Chunk 1: Database Schema + Analytics Module Core

### Task 1: Database Schema — outcomes Table

**Files:**
- Modify: `db.py` (inside `init_db()` executescript block)
- Create: `tests/test_outcomes_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_outcomes_schema.py`:

```python
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_outcomes_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_outcomes_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, action_id, action_type, target, sent_at, response_received, "
            "response_at, response_type, converted, notes, created_at FROM outcomes LIMIT 1"
        )


def test_outcomes_insert_and_read():
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO outcomes (action_type, target, sent_at)
               VALUES ('email_draft', 'Alice Johnson', '2026-03-10')"""
        )
        row = conn.execute("SELECT * FROM outcomes WHERE target = 'Alice Johnson'").fetchone()
    assert row is not None
    assert row["action_type"] == "email_draft"
    assert row["response_received"] == 0
    assert row["converted"] == 0


def test_outcomes_response_tracking():
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type, converted)
               VALUES ('follow_up', 'Bob Smith', '2026-03-08', 1, 'positive', 1)"""
        )
        row = conn.execute("SELECT * FROM outcomes WHERE target = 'Bob Smith'").fetchone()
    assert row["response_received"] == 1
    assert row["response_type"] == "positive"
    assert row["converted"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_outcomes_schema.py -v`
Expected: FAIL — table doesn't exist

- [ ] **Step 3: Add outcomes table to db.py**

In `db.py`, inside the `init_db()` function's `conn.executescript(...)` block, append after the `nurture_sequences` table:

```sql
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
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (action_id) REFERENCES audit_log(id)
        );
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_outcomes_schema.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_outcomes_schema.py
git commit -m "feat: add outcomes table for tracking AI action results"
```

---

### Task 2: Analytics Module — Core Functions

**Files:**
- Create: `analytics.py`
- Create: `tests/test_analytics.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analytics.py`:

```python
import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_analytics"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import analytics


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_outcomes():
    with db.get_db() as conn:
        # 3 email drafts: 2 got responses, 1 converted
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type, converted) "
            "VALUES ('email_draft', 'Alice', '2026-03-07', 1, 'positive', 1)"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type) "
            "VALUES ('email_draft', 'Bob', '2026-03-08', 1, 'neutral')"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received) "
            "VALUES ('email_draft', 'Carol', '2026-03-09', 0)"
        )
        # 2 content posts
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type) "
            "VALUES ('content_post', 'linkedin', '2026-03-06', 1, 'positive')"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received) "
            "VALUES ('content_post', 'facebook', '2026-03-07', 0)"
        )


def test_record_outcome():
    outcome = analytics.record_outcome(
        action_type="email_draft",
        target="Dave",
        sent_at="2026-03-10",
    )
    assert outcome is not None
    assert outcome["id"] > 0
    assert outcome["response_received"] == 0


def test_update_outcome_response():
    outcome = analytics.record_outcome(
        action_type="email_draft",
        target="Eve",
        sent_at="2026-03-10",
    )
    updated = analytics.update_outcome(
        outcome["id"],
        response_received=True,
        response_type="positive",
        converted=True,
    )
    assert updated["response_received"] == 1
    assert updated["response_type"] == "positive"
    assert updated["converted"] == 1


def test_get_weekly_stats():
    _seed_outcomes()
    stats = analytics.get_weekly_stats(reference_date="2026-03-13")
    assert stats["total_actions"] >= 5
    assert stats["response_rate"] > 0
    assert "by_type" in stats


def test_get_weekly_stats_by_type():
    _seed_outcomes()
    stats = analytics.get_weekly_stats(reference_date="2026-03-13")
    assert "email_draft" in stats["by_type"]
    assert stats["by_type"]["email_draft"]["total"] >= 3
    assert stats["by_type"]["email_draft"]["responses"] >= 2


def test_get_outcome_by_id():
    outcome = analytics.record_outcome(
        action_type="campaign", target="Test", sent_at="2026-03-10"
    )
    fetched = analytics.get_outcome(outcome["id"])
    assert fetched is not None
    assert fetched["target"] == "Test"


def test_get_outcome_not_found():
    result = analytics.get_outcome(9999)
    assert result is None


def test_get_recent_outcomes():
    _seed_outcomes()
    recent = analytics.get_recent_outcomes(limit=3)
    assert len(recent) <= 3


@patch("analytics.openai_client")
def test_generate_insights(mock_client):
    _seed_outcomes()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "INSIGHTS:\n- Email response rate was 67%\n- Content on LinkedIn outperformed Facebook"
    mock_client.chat.completions.create.return_value = mock_response

    insights = analytics.generate_insights(reference_date="2026-03-13")
    assert insights is not None
    assert len(insights) > 0


def test_get_learning_context():
    _seed_outcomes()
    context = analytics.get_learning_context(reference_date="2026-03-13")
    assert isinstance(context, str)
    assert "email_draft" in context or "response" in context.lower()


def test_get_learning_context_empty():
    context = analytics.get_learning_context(reference_date="2026-03-13")
    assert context == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_analytics.py -v`
Expected: FAIL — `analytics` module doesn't exist

- [ ] **Step 3: Implement analytics.py**

Create `analytics.py`:

```python
"""Analytics and outcome tracking — the learning loop.

Tracks results of AI-generated actions (emails, content, outreach) and
generates insights about what's working and what isn't. Feeds learnings
back into content and outreach strategies.
"""

import logging
import os
from datetime import datetime, timedelta

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

# Note: {placeholders} below are .replace() tokens, NOT .format() tokens
INSIGHTS_PROMPT = """You are analyzing Marc Pereira's outreach and content performance for the past week. Marc is a financial advisor at Co-operators in London, Ontario.

WEEKLY STATS:
{stats_summary}

PIPELINE CONTEXT:
{pipeline_context}

Generate a concise weekly insights digest covering:
1. WHAT WORKED: Top-performing actions, best response rates, successful conversions
2. WHAT DIDN'T: Low response rates, underperforming content types or channels
3. PATTERNS: Timing patterns, messaging patterns, prospect type patterns
4. RECOMMENDATIONS: 2-3 specific, actionable adjustments for next week

Keep it concise — this goes into a Telegram message. Use plain language.
Focus on actionable insights, not just restating numbers."""


def record_outcome(action_type, target, sent_at, action_id=None, notes=""):
    """Record an outcome for an AI-generated action. Returns dict."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO outcomes (action_id, action_type, target, sent_at, notes)
               VALUES (?, ?, ?, ?, ?)""",
            (action_id, action_type, target, sent_at, notes),
        )
        row = conn.execute("SELECT * FROM outcomes WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_outcome(outcome_id):
    """Get an outcome by ID. Returns dict or None."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM outcomes WHERE id = ?", (outcome_id,)).fetchone()
        return dict(row) if row else None


def update_outcome(outcome_id, response_received=None, response_type=None, converted=None, notes=None):
    """Update an outcome with response data. Returns updated dict."""
    with db.get_db() as conn:
        updates = []
        params = []
        if response_received is not None:
            updates.append("response_received = ?")
            params.append(1 if response_received else 0)
            if response_received:
                updates.append("response_at = datetime('now')")
        if response_type is not None:
            updates.append("response_type = ?")
            params.append(response_type)
        if converted is not None:
            updates.append("converted = ?")
            params.append(1 if converted else 0)
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        if not updates:
            return get_outcome(outcome_id)
        params.append(outcome_id)
        conn.execute(
            f"UPDATE outcomes SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        row = conn.execute("SELECT * FROM outcomes WHERE id = ?", (outcome_id,)).fetchone()
        return dict(row) if row else None


def get_recent_outcomes(limit=20):
    """Get recent outcomes ordered by creation date."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM outcomes ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_weekly_stats(reference_date=None):
    """Aggregate outcome stats for the past 7 days.

    Returns dict with: total_actions, responses, response_rate, conversions,
    conversion_rate, by_type (breakdown by action_type).
    """
    if reference_date:
        ref = datetime.strptime(reference_date, "%Y-%m-%d")
    else:
        ref = datetime.now()
    week_start = (ref - timedelta(days=7)).strftime("%Y-%m-%d")
    ref_str = ref.strftime("%Y-%m-%d")

    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE sent_at >= ? AND sent_at <= ? ORDER BY sent_at",
            (week_start, ref_str),
        ).fetchall()

    outcomes = [dict(r) for r in rows]
    total = len(outcomes)
    responses = sum(1 for o in outcomes if o["response_received"])
    conversions = sum(1 for o in outcomes if o["converted"])

    # Breakdown by type
    by_type = {}
    for o in outcomes:
        t = o["action_type"]
        if t not in by_type:
            by_type[t] = {"total": 0, "responses": 0, "conversions": 0}
        by_type[t]["total"] += 1
        if o["response_received"]:
            by_type[t]["responses"] += 1
        if o["converted"]:
            by_type[t]["conversions"] += 1

    return {
        "total_actions": total,
        "responses": responses,
        "response_rate": round(responses / total * 100, 1) if total > 0 else 0,
        "conversions": conversions,
        "conversion_rate": round(conversions / total * 100, 1) if total > 0 else 0,
        "by_type": by_type,
    }


def _format_stats_for_prompt(stats):
    """Format weekly stats into a text block for the insights prompt."""
    lines = [
        f"Total AI actions: {stats['total_actions']}",
        f"Response rate: {stats['response_rate']}% ({stats['responses']}/{stats['total_actions']})",
        f"Conversion rate: {stats['conversion_rate']}% ({stats['conversions']}/{stats['total_actions']})",
        "",
        "By type:",
    ]
    for action_type, data in stats["by_type"].items():
        rate = round(data["responses"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        lines.append(f"  {action_type}: {data['total']} sent, {data['responses']} responses ({rate}%), {data['conversions']} conversions")
    return "\n".join(lines)


def generate_insights(reference_date=None):
    """Generate AI-powered weekly insights from outcome data.

    Returns insights text string, or None on failure.
    """
    stats = get_weekly_stats(reference_date=reference_date)
    if stats["total_actions"] == 0:
        return "No tracked outcomes this week. Start logging results to get insights!"

    stats_text = _format_stats_for_prompt(stats)

    # Pipeline context
    try:
        prospects = db.read_pipeline()
        active = [p for p in prospects if p.get("stage") not in ("Closed Won", "Closed Lost", "")]
        pipeline_text = f"{len(active)} active prospects in pipeline."
    except Exception:
        pipeline_text = "Pipeline data unavailable."

    try:
        prompt = INSIGHTS_PROMPT.replace("{stats_summary}", stats_text)
        prompt = prompt.replace("{pipeline_context}", pipeline_text)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Insights generation failed")
        return None


def format_stats_for_telegram(stats):
    """Format weekly stats for Telegram display."""
    lines = [
        "WEEKLY OUTCOMES",
        f"Actions tracked: {stats['total_actions']}",
        f"Response rate: {stats['response_rate']}%",
        f"Conversion rate: {stats['conversion_rate']}%",
        "",
    ]
    for action_type, data in stats["by_type"].items():
        rate = round(data["responses"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        lines.append(f"  {action_type}: {data['total']} sent, {rate}% response rate")
    return "\n".join(lines)


def get_learning_context(reference_date=None):
    """Get a 'what's working' context block for injection into other prompts.

    Returns a short text summary of recent performance data that other modules
    (briefing, follow_up, content_engine) can include in their GPT prompts
    to inform the AI about what's been effective recently.

    Returns empty string if no data.
    """
    stats = get_weekly_stats(reference_date=reference_date)
    if stats["total_actions"] == 0:
        return ""

    lines = ["RECENT PERFORMANCE (last 7 days):"]
    for action_type, data in stats["by_type"].items():
        rate = round(data["responses"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        lines.append(f"  {action_type}: {rate}% response rate ({data['responses']}/{data['total']})")
        if data["conversions"] > 0:
            lines.append(f"    → {data['conversions']} conversions")

    if stats["response_rate"] > 50:
        lines.append("Overall: Strong response rates — maintain current approach.")
    elif stats["response_rate"] > 25:
        lines.append("Overall: Moderate response rates — consider adjusting tone or timing.")
    else:
        lines.append("Overall: Low response rates — try different approaches.")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_analytics.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add analytics.py tests/test_analytics.py
git commit -m "feat: add analytics module with outcome tracking and insights generation"
```

---

## Chunk 2: Outcome Tracking Buttons + /outcomes Command

### Task 3: Outcome Tracking Buttons on Approved Drafts

**Files:**
- Modify: `bot.py` (enhance `handle_draft_callback` approve path)

When Marc approves a draft, the bot should automatically record an outcome and offer follow-up tracking buttons after a delay. Since real-time tracking via buttons is simpler, we add "Got response?" buttons to the approval confirmation message.

- [ ] **Step 1: Read bot.py to find handle_draft_callback**

Search for `handle_draft_callback` and the approve path where `update_draft_status(queue_id, "approved")` is called.

- [ ] **Step 2: Add outcome recording on draft approval**

After `update_draft_status(queue_id, "approved")` in the approve path of `handle_draft_callback`, add:

```python
        # Record outcome for tracking
        try:
            import analytics
            # Resolve target name: approval_queue has prospect_id, not prospect_name
            _target = draft.get("context", "")[:50]
            if draft.get("prospect_id"):
                _p = db.get_prospect_by_id(draft["prospect_id"]) if hasattr(db, "get_prospect_by_id") else None
                if not _p:
                    with db.get_db() as _conn:
                        _row = _conn.execute("SELECT name FROM prospects WHERE id = ?", (draft["prospect_id"],)).fetchone()
                        _target = _row["name"] if _row else _target
                else:
                    _target = _p["name"]
            outcome = analytics.record_outcome(
                action_type=draft.get("type", "unknown"),
                target=_target,
                sent_at=datetime.now().strftime("%Y-%m-%d"),
                action_id=None,
            )
            # Add tracking buttons to the approval confirmation
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            track_keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Got response", callback_data=f"outcome_response_{outcome['id']}"),
                    InlineKeyboardButton("Converted!", callback_data=f"outcome_converted_{outcome['id']}"),
                ],
            ])
            await query.message.reply_text(
                f"Track results for this message (#{outcome['id']})",
                reply_markup=track_keyboard,
            )
        except Exception:
            logger.exception("Outcome tracking failed for draft #%s", queue_id)
```

- [ ] **Step 3: Add outcome callback handler**

Add a new callback handler function in bot.py:

```python
async def handle_outcome_callback(update, context):
    """Handle outcome tracking button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if not _is_admin(query.from_user.id):
        return

    import analytics

    if data.startswith("outcome_response_"):
        outcome_id = int(data.split("_")[-1])
        # Show response type options
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Positive", callback_data=f"outcome_rtype_positive_{outcome_id}"),
                InlineKeyboardButton("Neutral", callback_data=f"outcome_rtype_neutral_{outcome_id}"),
                InlineKeyboardButton("Negative", callback_data=f"outcome_rtype_negative_{outcome_id}"),
            ],
        ])
        await query.edit_message_text("What kind of response?", reply_markup=keyboard)

    elif data.startswith("outcome_rtype_"):
        parts = data.split("_")
        response_type = parts[2]
        outcome_id = int(parts[3])
        analytics.update_outcome(outcome_id, response_received=True, response_type=response_type)
        await query.edit_message_text(f"Logged: {response_type} response for outcome #{outcome_id}")

    elif data.startswith("outcome_converted_"):
        outcome_id = int(data.split("_")[-1])
        analytics.update_outcome(outcome_id, response_received=True, response_type="positive", converted=True)
        await query.edit_message_text(f"Logged: conversion for outcome #{outcome_id}")
```

- [ ] **Step 4: Register the callback handler in build_application()**

Add BEFORE the existing CallbackQueryHandler lines:

```python
    app.add_handler(CallbackQueryHandler(handle_outcome_callback, pattern=r"^outcome_"))
```

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: add outcome tracking buttons on draft approval"
```

---

### Task 4: /outcomes Command

**Files:**
- Modify: `bot.py` (add `/outcomes` command)

- [ ] **Step 1: Add /outcomes command to bot.py**

```python
async def cmd_outcomes(update, context):
    """View outcome tracking stats: /outcomes or /outcomes week"""
    if not await _require_admin(update):
        return

    import analytics

    args = context.args
    stats = analytics.get_weekly_stats()

    if stats["total_actions"] == 0:
        await update.message.reply_text(
            "No outcomes tracked yet.\n"
            "Approve drafts to start tracking — you'll see tracking buttons after each approval."
        )
        return

    text = analytics.format_stats_for_telegram(stats)

    if args and args[0].lower() == "insights":
        await update.message.reply_text("Generating insights...")
        insights = analytics.generate_insights()
        if insights:
            text += f"\n\nINSIGHTS:\n{insights[:3000]}"

    await update.message.reply_text(text)
```

- [ ] **Step 2: Register handler in build_application()**

```python
    app.add_handler(CommandHandler("outcomes", cmd_outcomes))
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "feat: add /outcomes command for viewing tracked results"
```

---

## Chunk 3: Enhanced Weekly Report + Final Integration

### Task 5: Enhance Weekly Report with AI Insights

**Files:**
- Modify: `scheduler.py` (enhance `_weekly_report_inner`)

The spec says to replace the existing `weekly_report` Sunday 7PM job with an enhanced "Weekly Insights Digest" that includes AI analysis. We keep the existing stats, add an insights section, and reschedule from 7PM to 6PM ET.

- [ ] **Step 1: Read scheduler.py to find `_weekly_report_inner` and the job registration**

Read the full function (approximately lines 267-404) to understand the existing report structure. Also find the job registration (approximately line 949-956) where `hour=19` needs to change to `hour=18`.

- [ ] **Step 2: Reschedule weekly report from 7PM to 6PM ET**

In `start_scheduler()`, change the `weekly_report` job from `hour=19` to `hour=18`:

```python
    scheduler.add_job(
        weekly_report,
        "cron",
        day_of_week="sun",
        hour=18,  # Changed from 19 (7PM) to 18 (6PM) per spec
        minute=0,
        id="weekly_report",
        name="Weekly Performance Report",
    )
```

- [ ] **Step 3: Add insights section to the weekly report**

At the end of `_weekly_report_inner()`, BEFORE the final `await _bot.send_message(...)`, add:

```python
    # AI-powered weekly insights
    try:
        import analytics
        stats = analytics.get_weekly_stats()
        if stats["total_actions"] > 0:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("AI INSIGHTS:")
            lines.append(analytics.format_stats_for_telegram(stats))

            insights = analytics.generate_insights()
            if insights:
                lines.append("")
                lines.append(insights[:1500])
    except Exception:
        logger.exception("Insights section failed — sending report without it")
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "feat: enhance weekly report with AI-powered outcome insights, reschedule to 6PM"
```

---

### Task 6: Hook Learning Context into Daily Briefing

**Files:**
- Modify: `briefing.py` (add learning context to briefing data and prompt)

The spec requires that learning feeds back into other phases' prompts. The daily briefing is the highest-impact integration point since Marc sees it every morning.

- [ ] **Step 1: Read briefing.py to find `assemble_briefing_data()` and `BRIEFING_PROMPT`**

Find where briefing data is assembled and where the prompt includes sections like `MARKET INTELLIGENCE`.

- [ ] **Step 2: Add learning context to `assemble_briefing_data()`**

In the `assemble_briefing_data()` function, add after the `market_events` key:

```python
    # Learning context from outcome tracking
    try:
        import analytics
        data["learning_context"] = analytics.get_learning_context()
    except Exception:
        data["learning_context"] = ""
```

- [ ] **Step 3: Add learning context section to `BRIEFING_PROMPT`**

In the `BRIEFING_PROMPT` string, add a new section (after the MARKET INTELLIGENCE section):

```
WHAT'S WORKING (from recent outcome tracking):
{learning_context}
```

Also add instruction: "8. Reference recent performance data to prioritize recommendations."

- [ ] **Step 4: Add learning_context to `_build_briefing_prompt()` format call**

In `_build_briefing_prompt()`, add to the `.format()` call:

```python
    learning_context=_escape_braces(data.get("learning_context", "")),
```

Note: `briefing.py` uses `.format()` with `_escape_braces()`, NOT `.replace()`.

- [ ] **Step 5: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add briefing.py
git commit -m "feat: hook learning context into daily briefing prompt"
```

---

### Task 7: Final Integration — Smoke Test

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify all new modules import cleanly**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -c "import analytics; print('Phase 5 modules OK')"`
Expected: "Phase 5 modules OK"

- [ ] **Step 3: Verify bot loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && TELEGRAM_BOT_TOKEN=test OPENAI_API_KEY=test TELEGRAM_CHAT_ID=123 WEBHOOK_SECRET=test python3 -c "import bot; print('Bot loads OK')"`
Expected: "Bot loads OK"

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: Phase 5 complete — The Analyst with outcome tracking and learning loop"
```

---

## Summary of New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `analytics.py` | ~200 | Outcome tracking, aggregation, AI insights generation |
| `tests/test_analytics.py` | ~110 | Tests for analytics module |
| `tests/test_outcomes_schema.py` | ~45 | Tests for outcomes DB table |

## Modified Files

| File | Changes |
|------|---------|
| `db.py` | +outcomes table in init_db() executescript |
| `bot.py` | +/outcomes command, outcome tracking buttons on approved drafts, outcome callback handler |
| `scheduler.py` | +AI insights section in weekly report, rescheduled to 6PM ET |
| `briefing.py` | +learning context section in daily briefing prompt |
