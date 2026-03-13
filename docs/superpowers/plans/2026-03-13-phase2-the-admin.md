# Phase 2: "The Admin" Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate follow-up drafting, meeting prep, and voice-note processing so Marc spends zero time on admin and 100% on client conversations.

**Architecture:** Two new modules (`follow_up.py`, `meeting_prep.py`) handle content generation. Both store output in the existing `approval_queue` table and send Telegram notifications with inline approval buttons. A nudge scheduler job ensures nothing falls through the cracks. The existing voice handler and intake pipeline get wired to trigger these automatically.

**Tech Stack:** Python 3.13, OpenAI GPT-4.1 (drafts/prep) and GPT-4.1-mini (extraction), python-telegram-bot 21.10 (inline keyboards), APScheduler 3.10.4, SQLite WAL mode.

**Important codebase notes:**
- `db.py` uses `conn.executescript()` for schema in `init_db()` — append new tables there
- OpenAI calls use `max_completion_tokens` (NOT `max_tokens`)
- Bot uses `ADMIN_CHAT_ID` env var and `_is_admin()` / `_require_admin()` helpers
- `get_ranked_call_list()` returns flat merged dicts `{**prospect, **score_data}`
- `STAGE_PROBABILITY` (singular, not PROBABILITIES) in `scoring.py`
- The bot model is `gpt-5` in `_llm_respond()` — new modules use `gpt-4.1` for drafts

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `follow_up.py` | Generate follow-up drafts, queue in approval_queue, send to Telegram |
| Create | `meeting_prep.py` | Generate meeting prep docs, send to Telegram |
| Create | `tests/test_follow_up.py` | Tests for follow_up module |
| Create | `tests/test_meeting_prep.py` | Tests for meeting_prep module |
| Modify | `scheduler.py` | Add nudge job + meeting prep job |
| Modify | `bot.py` | Add inline keyboard approval UX + /drafts command |
| Modify | `voice_handler.py` | Trigger follow-up draft + urgency alert after processing |
| Modify | `intake.py` | Schedule prep doc on new booking + enhanced notification |

---

## Chunk 1: Auto-Drafted Follow-Ups

### Task 1: Follow-Up Draft Generator — Core Module

**Files:**
- Create: `follow_up.py`
- Create: `tests/test_follow_up.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_follow_up.py`:

```python
import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_followup"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import follow_up


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect():
    db.add_prospect({
        "name": "Sarah Chen", "stage": "Discovery Call", "priority": "Hot",
        "product": "Life Insurance", "revenue": "5000", "email": "sarah@example.com",
    })
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE name = 'Sarah Chen'").fetchone()
        return row[0]


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_draft(mock_compliance, mock_client):
    pid = _seed_prospect()
    db.add_activity({
        "prospect": "Sarah Chen", "action": "Discovery call",
        "outcome": "Discussed life insurance needs, husband runs landscaping biz",
    })

    # Mock GPT response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "Hi Sarah,\n\nGreat speaking with you today about protecting your family's income. "
        "I'll put together some options for term life coverage that factor in your husband's "
        "landscaping business.\n\nI'll have something ready for you by Thursday.\n\nBest,\nMarc"
    )
    mock_client.chat.completions.create.return_value = mock_response

    # Mock compliance pass
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    draft = follow_up.generate_follow_up_draft(
        prospect_name="Sarah Chen",
        activity_summary="Discovery call — discussed life insurance, husband runs landscaping",
        activity_type="Discovery call",
    )

    assert draft is not None
    assert draft["prospect_name"] == "Sarah Chen"
    assert "Sarah" in draft["content"]
    assert draft["compliance_passed"] is True
    assert draft["queue_id"] is not None


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_compliance_fail(mock_compliance, mock_client):
    pid = _seed_prospect()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "I guarantee you 8% returns!"
    mock_client.chat.completions.create.return_value = mock_response

    mock_compliance.check_compliance.return_value = {
        "passed": False, "issues": ["Contains return guarantee"],
    }

    draft = follow_up.generate_follow_up_draft(
        prospect_name="Sarah Chen",
        activity_summary="Called about investments",
        activity_type="Phone call",
    )

    assert draft is not None
    assert draft["compliance_passed"] is False
    assert len(draft["compliance_issues"]) > 0
    # Should still be queued but flagged
    assert draft["queue_id"] is not None


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_no_prospect(mock_compliance, mock_client):
    draft = follow_up.generate_follow_up_draft(
        prospect_name="Nonexistent Person",
        activity_summary="Called",
        activity_type="Phone call",
    )
    assert draft is None


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_stores_in_approval_queue(mock_compliance, mock_client):
    pid = _seed_prospect()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Sarah, thanks for chatting today."
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    draft = follow_up.generate_follow_up_draft(
        prospect_name="Sarah Chen",
        activity_summary="Quick check-in call",
        activity_type="Phone call",
    )

    import approval_queue
    pending = approval_queue.get_pending_drafts(draft_type="follow_up")
    assert len(pending) == 1
    assert pending[0]["content"] == draft["content"]


def test_get_stale_drafts():
    import approval_queue
    # Add a draft with old created_at
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO approval_queue (type, channel, content, context, status, created_at, prospect_id)
               VALUES (?, ?, ?, ?, 'pending', datetime('now', '-5 hours'), NULL)""",
            ("follow_up", "email_draft", "Old draft content", "test context"),
        )
    stale = follow_up.get_stale_drafts(max_age_hours=4)
    assert len(stale) >= 1


def test_get_stale_drafts_none():
    stale = follow_up.get_stale_drafts(max_age_hours=4)
    assert len(stale) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_follow_up.py -v`
Expected: FAIL — `follow_up` module doesn't exist

- [ ] **Step 3: Implement follow_up.py**

Create `follow_up.py`:

```python
"""Auto-drafted follow-ups for prospect interactions.

After any logged activity (call, meeting, voice note), this module generates
a personalized follow-up email draft, runs it through compliance, stores it
in the approval queue, and notifies Marc via Telegram.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta

from openai import OpenAI

import approval_queue
import compliance
import db
import memory_engine

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

FOLLOW_UP_NUDGE_HOURS = int(os.environ.get("FOLLOW_UP_NUDGE_HOURS", "4"))

FOLLOW_UP_PROMPT = """You are drafting a follow-up email for Marc Pereira, a financial advisor at Co-operators in London, Ontario.

Write a professional but warm follow-up email based on the activity below. The email should:
1. Reference specific details from the conversation (shows Marc was listening)
2. Confirm any next steps or commitments made
3. Be concise (under 150 words)
4. Sound like Marc, not like AI — natural, approachable, professional
5. Include a clear next action or question to keep the conversation moving
6. End with Marc's name (no signature block needed)

Do NOT include a subject line — just the email body.

PROSPECT: {prospect_name}
STAGE: {stage}
PRODUCT INTEREST: {product}
ACTIVITY: {activity_type}
SUMMARY: {activity_summary}

CLIENT INTELLIGENCE:
{memory_profile}

RECENT INTERACTIONS:
{recent_interactions}"""


def generate_follow_up_draft(prospect_name, activity_summary, activity_type="call"):
    """Generate a follow-up draft for a prospect after an activity.

    Returns dict with: prospect_name, content, compliance_passed, compliance_issues,
    queue_id, channel. Returns None if prospect not found.
    """
    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect:
        logger.warning("Follow-up draft: prospect '%s' not found", prospect_name)
        return None

    # Gather context
    profile_text = memory_engine.get_profile_summary_text(prospect["id"])
    interactions = db.read_interactions(limit=5, prospect=prospect_name)
    interaction_lines = []
    for ix in interactions[:3]:
        summary = ix.get("summary") or ix.get("raw_text", "")[:200]
        interaction_lines.append(f"- {ix.get('date', '?')}: {ix.get('source', '?')} — {summary}")
    recent_text = "\n".join(interaction_lines) if interaction_lines else "No recent interactions on file."

    # Generate draft via GPT
    try:
        prompt = FOLLOW_UP_PROMPT.replace("{prospect_name}", prospect_name)
        prompt = prompt.replace("{stage}", prospect.get("stage", "Unknown"))
        prompt = prompt.replace("{product}", prospect.get("product", "Not specified"))
        prompt = prompt.replace("{activity_type}", activity_type)
        prompt = prompt.replace("{activity_summary}", activity_summary)
        prompt = prompt.replace("{memory_profile}", profile_text)
        prompt = prompt.replace("{recent_interactions}", recent_text)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=1024,
            temperature=0.7,
        )
        content = response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Follow-up draft generation failed for %s", prospect_name)
        return None

    # Run compliance check
    comp_result = compliance.check_compliance(content)

    # Log to audit trail
    compliance.log_action(
        action_type="follow_up_draft",
        target=prospect_name,
        content=content,
        compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
    )

    # Store in approval queue
    context_text = f"Auto-drafted after: {activity_type} — {activity_summary}"
    draft = approval_queue.add_draft(
        draft_type="follow_up",
        channel="email_draft",
        content=content,
        context=context_text,
        prospect_id=prospect["id"],
    )

    return {
        "prospect_name": prospect_name,
        "content": content,
        "compliance_passed": comp_result["passed"],
        "compliance_issues": comp_result.get("issues", []),
        "queue_id": draft["id"],
        "channel": "email_draft",
    }


def get_stale_drafts(max_age_hours=None):
    """Get pending drafts older than max_age_hours (defaults to FOLLOW_UP_NUDGE_HOURS)."""
    if max_age_hours is None:
        max_age_hours = FOLLOW_UP_NUDGE_HOURS

    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")

    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT aq.*, p.name as prospect_name
               FROM approval_queue aq
               LEFT JOIN prospects p ON aq.prospect_id = p.id
               WHERE aq.status = 'pending' AND aq.created_at <= ?
               ORDER BY aq.created_at ASC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def format_draft_for_telegram(draft_result):
    """Format a follow-up draft for Telegram display.

    Args:
        draft_result: dict from generate_follow_up_draft()
    Returns:
        str: formatted message text
    """
    lines = [
        f"FOLLOW-UP DRAFT — {draft_result['prospect_name']}",
        f"Channel: {draft_result['channel']}",
        "",
        draft_result["content"],
        "",
    ]
    if not draft_result["compliance_passed"]:
        lines.append("COMPLIANCE FLAG: " + "; ".join(draft_result["compliance_issues"]))
        lines.append("")
    lines.append(f"Queue #{draft_result['queue_id']} — /drafts to manage")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_follow_up.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add follow_up.py tests/test_follow_up.py
git commit -m "feat: add follow-up draft generator with compliance and approval queue"
```

---

### Task 2: Wire Follow-Up Into Activity Logging

**Files:**
- Modify: `bot.py` (trigger follow-up draft after `add_activity` tool call)
- Modify: `voice_handler.py` (trigger follow-up draft after voice note processing)

- [ ] **Step 1: Add follow-up trigger to bot.py tool dispatch**

In `bot.py`, inside `_llm_respond()`, after the memory extraction block (around line 1373), add a follow-up draft trigger for `add_activity`. Note: The Telegram notification with inline buttons is wired in Task 3 — this step just generates the draft and logs it.

```python
            # Trigger follow-up draft for activity-related tools
            if tool_name == "add_activity" and "prospect" in tool_input:
                try:
                    import follow_up as fu
                    fu_prospect = tool_input.get("prospect", "")
                    fu_summary = f"{tool_input.get('action', '')} — {tool_input.get('outcome', '')}"
                    fu_draft = fu.generate_follow_up_draft(
                        prospect_name=fu_prospect,
                        activity_summary=fu_summary,
                        activity_type=tool_input.get("action", "activity"),
                    )
                    if fu_draft:
                        logger.info("Follow-up draft generated for %s (queue #%s)", fu_prospect, fu_draft["queue_id"])
                except Exception:
                    logger.exception("Follow-up draft generation failed (non-blocking)")
```

- [ ] **Step 2: Add follow-up trigger to voice_handler.py**

In `voice_handler.py`, inside `extract_and_update()`, after the existing memory extraction block (around line 198). **IMPORTANT: Preserve the existing memory extraction try/except block — add this new code AFTER it, not replacing it.** Add:

```python
        # Auto-draft follow-up email
        try:
            import follow_up as fu
            activity_summary = f"Voice note ({source}): {transcript[:300]}"
            fu_draft = fu.generate_follow_up_draft(
                prospect_name=name,
                activity_summary=activity_summary,
                activity_type=f"Voice note ({source})",
            )
            if fu_draft:
                logger.info("Follow-up draft generated for %s (queue #%s)", name, fu_draft["queue_id"])
        except Exception:
            logger.exception("Follow-up draft failed for %s (non-blocking)", name)
```

- [ ] **Step 3: Add urgency detection to voice_handler.py**

In `voice_handler.py`, after the follow-up draft block, add urgency detection:

```python
        # Check for urgency signals in transcript
        try:
            urgency_keywords = ["urgent", "asap", "emergency", "right away", "immediately", "time sensitive", "deadline"]
            transcript_lower = transcript.lower()
            if any(kw in transcript_lower for kw in urgency_keywords):
                logger.info("URGENCY detected in voice note for %s", name)
                # Will be sent via Telegram notification below in the summary
                urgency_flag = True
        except Exception:
            pass
```

And at the top of the for-loop (before prospect processing), initialize `urgency_flag = False`. Then in the summary text at the end of `extract_and_update()`, include urgency if flagged.

Note: The urgency detection is simple keyword matching for now. The voice note summary already gets sent to Marc — this just adds an "URGENT" prefix when detected.

- [ ] **Step 4: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py voice_handler.py
git commit -m "feat: wire follow-up drafts into activity logging and voice handler"
```

---

## Chunk 2: Telegram Approval UX

### Task 3: Inline Keyboard Approval Buttons

**Files:**
- Modify: `bot.py` (add inline keyboard for draft approval + callback handler)

- [ ] **Step 1: Add inline keyboard helper and send function**

In `bot.py`, near the top (after imports, around line 35), add:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
```

Add a helper function near other helper functions (around line 100):

```python
def _draft_keyboard(queue_id):
    """Build inline keyboard for draft approval."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"draft_approve_{queue_id}"),
            InlineKeyboardButton("Edit", callback_data=f"draft_edit_{queue_id}"),
        ],
        [
            InlineKeyboardButton("Skip", callback_data=f"draft_dismiss_{queue_id}"),
            InlineKeyboardButton("Snooze 1h", callback_data=f"draft_snooze_{queue_id}"),
        ],
    ])


async def send_draft_to_telegram(bot, draft_result):
    """Send a follow-up draft to Telegram with approval buttons.

    Args:
        bot: Telegram Bot instance
        draft_result: dict from follow_up.generate_follow_up_draft()
    """
    import follow_up as fu
    text = fu.format_draft_for_telegram(draft_result)
    queue_id = draft_result["queue_id"]
    keyboard = _draft_keyboard(queue_id)

    try:
        msg = await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=text,
            reply_markup=keyboard,
        )
        # Store telegram message ID for reference
        import approval_queue
        approval_queue.set_telegram_message_id(queue_id, str(msg.message_id))
    except Exception:
        logger.exception("Failed to send draft notification for queue #%s", queue_id)
```

- [ ] **Step 2: Add callback query handler for draft actions**

Add to `bot.py` (near other command handlers):

```python
async def handle_draft_callback(update, context):
    """Handle inline keyboard callbacks for draft approval."""
    query = update.callback_query
    await query.answer()

    if not _is_admin(update):
        return

    data = query.data
    if not data.startswith("draft_"):
        return

    parts = data.split("_", 2)  # draft_action_queueid
    if len(parts) < 3:
        return

    action = parts[1]
    try:
        queue_id = int(parts[2])
    except ValueError:
        return

    import approval_queue
    import compliance as comp

    draft = approval_queue.get_draft_by_id(queue_id)
    if not draft:
        await query.edit_message_text("Draft not found or already processed.")
        return

    if action == "approve":
        approval_queue.update_draft_status(queue_id, "approved")
        try:
            audit_id = _find_audit_entry(queue_id, draft)
            if audit_id:
                comp.update_audit_outcome(audit_id, outcome="approved", approved_by="marc")
        except Exception:
            logger.warning("Could not update audit log for draft #%s", queue_id)
        await query.edit_message_text(
            f"APPROVED — {draft.get('type', 'draft')} for queue #{queue_id}\n\n"
            f"{draft['content']}\n\n"
            "Copy-paste the above into Outlook."
        )

    elif action == "dismiss":
        approval_queue.update_draft_status(queue_id, "dismissed")
        await query.edit_message_text(f"Dismissed draft #{queue_id}.")

    elif action == "snooze":
        # Mark as snoozed — nudge job will re-surface it later
        approval_queue.update_draft_status(queue_id, "snoozed")
        await query.edit_message_text(f"Snoozed draft #{queue_id} — will remind in 1 hour.")

    elif action == "edit":
        # Prompt Marc to reply with edits
        await query.edit_message_text(
            f"EDITING draft #{queue_id}\n\n"
            f"Original:\n{draft['content']}\n\n"
            "Reply to this message with your changes and I'll regenerate."
        )
        # Store edit state in context for the reply handler
        context.user_data["editing_draft_id"] = queue_id


def _find_audit_entry(queue_id, draft):
    """Find the audit log entry for this draft. Returns log_id or None."""
    import compliance as comp
    entries = comp.get_audit_log(action_type="follow_up_draft", target=None, limit=20)
    for entry in entries:
        if draft["content"] in (entry.get("content") or ""):
            return entry["id"]
    return None
```

- [ ] **Step 3: Add /drafts command**

Add to `bot.py`:

```python
async def cmd_drafts(update, context):
    """Show pending drafts in the approval queue."""
    if not await _require_admin(update):
        return

    import approval_queue
    pending = approval_queue.get_pending_drafts(limit=10)

    if not pending:
        await update.message.reply_text("No pending drafts.")
        return

    for draft in pending[:5]:
        prospect_name = ""
        if draft.get("prospect_id"):
            with db.get_db() as conn:
                row = conn.execute("SELECT name FROM prospects WHERE id = ?", (draft["prospect_id"],)).fetchone()
                if row:
                    prospect_name = row["name"]

        text = (
            f"DRAFT #{draft['id']} — {draft['type']}\n"
            f"Prospect: {prospect_name or 'N/A'}\n"
            f"Channel: {draft['channel']}\n"
            f"Created: {draft['created_at']}\n\n"
            f"{draft['content']}"
        )
        keyboard = _draft_keyboard(draft["id"])
        await update.message.reply_text(text, reply_markup=keyboard)
```

- [ ] **Step 4: Register handlers in build_application()**

In `build_application()`, add:

```python
    app.add_handler(CommandHandler("drafts", cmd_drafts))
    from telegram.ext import CallbackQueryHandler
    app.add_handler(CallbackQueryHandler(handle_draft_callback, pattern=r"^draft_"))
```

- [ ] **Step 5: Update the follow-up trigger in bot.py to use send_draft_to_telegram**

Replace the `logger.info(...)` line in the `_llm_respond()` follow-up trigger (added in Task 2) with the inline-button version:

```python
                    if fu_draft:
                        try:
                            await send_draft_to_telegram(update.get_bot(), fu_draft)
                        except Exception:
                            logger.exception("Could not send follow-up draft notification")
```

- [ ] **Step 6: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add bot.py
git commit -m "feat: add inline keyboard approval UX for follow-up drafts"
```

---

### Task 4: Nudge Scheduler Job for Stale Drafts

**Files:**
- Modify: `scheduler.py` (add nudge job)

- [ ] **Step 1: Add nudge function to scheduler.py**

Add to `scheduler.py` (after the existing scheduled functions, before `start_scheduler()`):

```python
async def nudge_stale_drafts():
    """Check for pending drafts that haven't been acted on and send reminders."""
    if not _bot or not CHAT_ID:
        return

    try:
        import follow_up as fu
        import approval_queue

        # First, re-surface snoozed drafts older than 1 hour
        with db.get_db() as conn:
            snoozed = conn.execute(
                """SELECT id FROM approval_queue
                   WHERE status = 'snoozed'
                   AND acted_on_at <= datetime('now', '-1 hour')""",
            ).fetchall()
            for row in snoozed:
                approval_queue.update_draft_status(row["id"], "pending")

        # Then check for stale pending drafts (computed AFTER re-surfacing)
        stale = fu.get_stale_drafts()
        if not stale:
            return

        count = len(stale)
        if count == 1:
            draft = stale[0]
            name = draft.get("prospect_name", "Unknown")
            text = f"NUDGE: You have a pending {draft['type']} draft for {name} (#{draft['id']}).\n/drafts to review."
        else:
            text = f"NUDGE: You have {count} pending drafts awaiting review.\n/drafts to review them."

        await _bot.send_message(chat_id=CHAT_ID, text=text)
        logger.info("Sent nudge for %d stale drafts", count)

    except Exception:
        logger.exception("Nudge stale drafts failed")
```

- [ ] **Step 2: Register the nudge job in start_scheduler()**

In `start_scheduler()`, add after the existing jobs:

```python
    # Nudge for stale drafts every 2 hours during business hours
    scheduler.add_job(
        nudge_stale_drafts,
        "cron",
        day_of_week="mon-fri",
        hour="10,12,14,16",
        minute=30,
        id="nudge_stale_drafts",
        name="Nudge Stale Drafts",
    )
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "feat: add nudge scheduler for stale approval queue drafts"
```

---

## Chunk 3: Meeting Prep Documents

### Task 5: Meeting Prep Generator — Core Module

**Files:**
- Create: `meeting_prep.py`
- Create: `tests/test_meeting_prep.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_meeting_prep.py`:

```python
import os
import sys
import json
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_meetprep"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import meeting_prep


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect_and_meeting():
    db.add_prospect({
        "name": "Sarah Chen", "stage": "Discovery Call", "priority": "Hot",
        "product": "Life Insurance", "revenue": "5000", "email": "sarah@example.com",
        "notes": "Husband runs landscaping. Two kids.",
    })
    today = datetime.now().strftime("%Y-%m-%d")
    db.add_meeting({
        "date": today, "time": "14:00", "prospect": "Sarah Chen",
        "type": "Discovery Call",
    })
    db.add_activity({
        "date": today, "prospect": "Sarah Chen",
        "action": "Phone call", "outcome": "Booked discovery call, excited about coverage options",
    })
    db.add_interaction({
        "prospect": "Sarah Chen", "source": "phone_call",
        "raw_text": "Sarah called to ask about life insurance. Husband runs landscaping business in Byron.",
        "summary": "Initial inquiry about life insurance",
    })
    with db.get_db() as conn:
        return conn.execute("SELECT id FROM prospects WHERE name = 'Sarah Chen'").fetchone()[0]


def test_assemble_prep_context():
    pid = _seed_prospect_and_meeting()
    ctx = meeting_prep.assemble_prep_context("Sarah Chen", "Discovery Call")
    assert ctx["prospect"]["name"] == "Sarah Chen"
    assert ctx["stage"] == "Discovery Call"
    assert "interactions" in ctx
    assert "activities" in ctx
    assert "memory_profile" in ctx
    assert "score_data" in ctx


def test_assemble_prep_context_no_prospect():
    ctx = meeting_prep.assemble_prep_context("Nobody", "Discovery Call")
    assert ctx is None


@patch("meeting_prep.openai_client")
def test_generate_prep_doc(mock_client):
    pid = _seed_prospect_and_meeting()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "MEETING PREP: Sarah Chen — Discovery Call\n\n"
        "CLIENT SNAPSHOT:\nHusband runs landscaping in Byron. Two kids.\n\n"
        "RECOMMENDED AGENDA:\n1. Review life insurance needs\n2. Discuss term vs whole life\n\n"
        "TALKING POINTS:\n- Ask about the landscaping business\n- Term life for mortgage protection"
    )
    mock_client.chat.completions.create.return_value = mock_response

    doc = meeting_prep.generate_prep_doc("Sarah Chen", "Discovery Call", "14:00")
    assert doc is not None
    assert "Sarah Chen" in doc
    assert len(doc) > 50


@patch("meeting_prep.openai_client")
def test_generate_prep_doc_api_failure(mock_client):
    pid = _seed_prospect_and_meeting()
    mock_client.chat.completions.create.side_effect = Exception("API down")

    doc = meeting_prep.generate_prep_doc("Sarah Chen", "Discovery Call", "14:00")
    # Should fall back to simple format
    assert doc is not None
    assert "Sarah Chen" in doc


def test_get_upcoming_meetings():
    _seed_prospect_and_meeting()
    today = datetime.now().strftime("%Y-%m-%d")
    meetings = meeting_prep.get_meetings_needing_prep(today)
    assert len(meetings) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_meeting_prep.py -v`
Expected: FAIL — `meeting_prep` module doesn't exist

- [ ] **Step 3: Implement meeting_prep.py**

Create `meeting_prep.py`:

```python
"""Meeting preparation document generator.

Generates comprehensive prep docs sent to Marc before meetings:
client snapshot, interaction history, recommended agenda, objection prep,
product recommendations, and personal touch points.
"""

import logging
import os
import re
from datetime import datetime, timedelta

from openai import OpenAI

import db
import memory_engine
import scoring

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

PREP_DOC_PROMPT = """You are preparing Marc Pereira for a meeting with a client/prospect. Marc is a financial advisor at Co-operators in London, Ontario.

Generate a concise meeting prep document. Write in plain text, no markdown. Be specific and actionable.

MEETING: {meeting_type} with {prospect_name} at {meeting_time}
STAGE: {stage}
PRODUCT INTEREST: {product}
PRIORITY: {priority}

CLIENT INTELLIGENCE:
{memory_profile}

LAST 5 INTERACTIONS:
{interaction_history}

RECENT ACTIVITIES:
{activity_history}

PROSPECT SCORE: {score}/100
SCORING REASONS: {score_reasons}

STRUCTURE YOUR RESPONSE AS:

CLIENT SNAPSHOT
[Key facts about this person — family, work, financial situation. Pull from client intelligence.]

WHERE WE LEFT OFF
[Last conversation's key points, promises made, open questions]

RECOMMENDED AGENDA
[3-4 bullet points for what to cover in this meeting, based on stage and needs]

OBJECTION PREP
[1-2 likely concerns based on profile and common objections for this product/stage]

PRODUCT RECOMMENDATION
[What to present and why, with 1-2 talking points]

PERSONAL TOUCH
[Something to ask about from their life — kids, hobbies, work. Makes the meeting feel personal.]

Keep the entire document under 1500 characters."""


def assemble_prep_context(prospect_name, meeting_type):
    """Gather all context needed for a meeting prep doc. Returns dict or None."""
    prospect = db.get_prospect_by_name(prospect_name)
    if not prospect:
        return None

    profile_text = memory_engine.get_profile_summary_text(prospect["id"])
    interactions = db.read_interactions(limit=5, prospect=prospect_name)
    activities = db.read_activities(limit=10)
    prospect_activities = [a for a in activities if a.get("prospect") == prospect_name][:5]

    # Score
    try:
        score_data = scoring.score_prospect(prospect)
    except Exception:
        score_data = {"score": 0, "reasons": [], "action": "Follow up"}

    return {
        "prospect": prospect,
        "stage": prospect.get("stage", "Unknown"),
        "meeting_type": meeting_type,
        "memory_profile": profile_text,
        "interactions": interactions,
        "activities": prospect_activities,
        "score_data": score_data,
    }


def generate_prep_doc(prospect_name, meeting_type, meeting_time):
    """Generate a meeting prep document. Returns formatted text or fallback on failure."""
    ctx = assemble_prep_context(prospect_name, meeting_type)
    if not ctx:
        return f"Meeting prep unavailable — prospect '{prospect_name}' not found."

    prospect = ctx["prospect"]

    # Format interactions
    ix_lines = []
    for ix in ctx["interactions"][:5]:
        summary = ix.get("summary") or (ix.get("raw_text", "")[:150])
        ix_lines.append(f"- {ix.get('date', '?')} ({ix.get('source', '?')}): {summary}")
    ix_text = "\n".join(ix_lines) if ix_lines else "No interactions on file."

    # Format activities
    act_lines = []
    for a in ctx["activities"][:5]:
        act_lines.append(f"- {a.get('date', '?')}: {a.get('action', '?')} — {a.get('outcome', 'N/A')}")
    act_text = "\n".join(act_lines) if act_lines else "No recent activities."

    score_data = ctx["score_data"]

    try:
        prompt = PREP_DOC_PROMPT.replace("{meeting_type}", meeting_type)
        prompt = prompt.replace("{prospect_name}", prospect_name)
        prompt = prompt.replace("{meeting_time}", meeting_time)
        prompt = prompt.replace("{stage}", ctx["stage"])
        prompt = prompt.replace("{product}", prospect.get("product", "Not specified"))
        prompt = prompt.replace("{priority}", prospect.get("priority", "N/A"))
        prompt = prompt.replace("{memory_profile}", ctx["memory_profile"])
        prompt = prompt.replace("{interaction_history}", ix_text)
        prompt = prompt.replace("{activity_history}", act_text)
        prompt = prompt.replace("{score}", str(score_data.get("score", 0)))
        prompt = prompt.replace("{score_reasons}", "; ".join(score_data.get("reasons", [])))

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=2048,
            temperature=0.6,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Meeting prep generation failed for %s, using fallback", prospect_name)
        return _fallback_prep(ctx, meeting_time)


def _fallback_prep(ctx, meeting_time):
    """Simple fallback prep doc when GPT is unavailable."""
    prospect = ctx["prospect"]
    lines = [
        f"MEETING PREP — {prospect['name']} at {meeting_time}",
        f"Stage: {ctx['stage']} | Product: {prospect.get('product', '?')} | Priority: {prospect.get('priority', '?')}",
        "",
        "CLIENT INTELLIGENCE:",
        ctx["memory_profile"] or "No intelligence on file.",
        "",
    ]
    if ctx["interactions"]:
        lines.append("LAST INTERACTION:")
        ix = ctx["interactions"][0]
        lines.append(f"  {ix.get('date', '?')}: {ix.get('summary') or ix.get('raw_text', 'N/A')[:200]}")
    if prospect.get("notes"):
        lines.append(f"\nNOTES: {prospect['notes'][:300]}")
    return "\n".join(lines)


def get_meetings_needing_prep(date_str):
    """Get meetings on a given date that need prep docs sent."""
    all_meetings = db.read_meetings()
    return [
        m for m in all_meetings
        if m.get("date") == date_str and m.get("status") == "Scheduled"
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/test_meeting_prep.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add meeting_prep.py tests/test_meeting_prep.py
git commit -m "feat: add meeting prep document generator with GPT and fallback"
```

---

### Task 6: Meeting Prep Scheduler Job

**Files:**
- Modify: `scheduler.py` (add meeting prep job that fires hourly)

- [ ] **Step 1: Add meeting prep check to scheduler.py**

Add to `scheduler.py` (before `start_scheduler()`):

```python
async def send_meeting_prep_docs():
    """Check for meetings in the next 1-2 hours and send prep docs."""
    if not _bot or not CHAT_ID:
        return

    try:
        import meeting_prep

        now = datetime.now(ET)
        today = now.strftime("%Y-%m-%d")
        meetings = meeting_prep.get_meetings_needing_prep(today)

        for m in meetings:
            meeting_time = m.get("time", "")
            prospect_name = m.get("prospect", "")

            if not meeting_time or not prospect_name:
                continue

            # Parse meeting time and check if it's 1-2 hours from now
            try:
                hour, minute = int(meeting_time.split(":")[0]), int(meeting_time.split(":")[1])
                meeting_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                delta = (meeting_dt - now).total_seconds() / 3600

                if 0.5 <= delta <= 2.0:
                    # Check if we already sent prep for this meeting (avoid duplicates)
                    meeting_id = m.get("id", "")
                    if hasattr(send_meeting_prep_docs, "_sent_preps"):
                        if meeting_id in send_meeting_prep_docs._sent_preps:
                            continue
                    else:
                        send_meeting_prep_docs._sent_preps = set()

                    doc = meeting_prep.generate_prep_doc(prospect_name, m.get("type", "Meeting"), meeting_time)
                    if doc:
                        if len(doc) > 4096:
                            doc = doc[:4076] + "\n...(truncated)"
                        await _bot.send_message(chat_id=CHAT_ID, text=doc)
                        send_meeting_prep_docs._sent_preps.add(meeting_id)
                        logger.info("Meeting prep sent for %s at %s", prospect_name, meeting_time)
            except (ValueError, IndexError):
                logger.warning("Could not parse meeting time '%s'", meeting_time)
                continue

    except Exception:
        logger.exception("Meeting prep doc check failed")
```

- [ ] **Step 2: Register the job in start_scheduler()**

Add to `start_scheduler()`:

```python
    # Meeting prep docs — check every hour during business hours
    scheduler.add_job(
        send_meeting_prep_docs,
        "cron",
        day_of_week="mon-fri",
        hour="7,8,9,10,11,12,13,14,15,16",
        minute=0,
        id="meeting_prep_docs",
        name="Meeting Prep Docs",
    )
```

- [ ] **Step 3: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "feat: add hourly meeting prep scheduler job"
```

---

## Chunk 4: Bookings Integration Enhancement

### Task 7: Enhanced Bookings — Prep Scheduling + Better Notifications

**Files:**
- Modify: `intake.py` (enhance `process_booking` to schedule prep + notify Marc)

- [ ] **Step 1: Enhance process_booking notification in intake.py**

In `intake.py`, at the end of `process_booking()` (after the memory extraction block, before the return), add:

```python
    # Schedule meeting prep doc
    try:
        import meeting_prep
        meeting_date = data.get("start_time", "")[:10] if data.get("start_time") else ""
        meeting_time = ""
        if data.get("start_time") and "T" in data["start_time"]:
            meeting_time = data["start_time"].split("T")[1][:5]

        if meeting_date and prospect_obj:
            # Prep doc will be picked up by the scheduler job
            logger.info(
                "Booking added for %s on %s at %s — prep doc will be auto-generated",
                prospect_obj["name"], meeting_date, meeting_time,
            )
    except Exception:
        logger.exception("Booking prep scheduling failed (non-blocking)")

    # Send enhanced Telegram notification to Marc
    try:
        from telegram import Bot
        tg_bot = Bot(token=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        service = data.get("service", "Meeting")
        start = data.get("start_time", "TBD")
        prospect_display = data.get("name", "Unknown")
        is_new = "NEW prospect" if not existing else "existing prospect"
        notif = (
            f"New booking: {prospect_display} ({is_new})\n"
            f"Service: {service}\n"
            f"When: {start}\n"
            f"I'll have prep ready before the meeting."
        )
        import asyncio
        asyncio.get_event_loop().create_task(
            tg_bot.send_message(chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""), text=notif)
        )
    except Exception:
        logger.exception("Booking notification failed (non-blocking)")
```

Note: You'll need to capture `existing` (whether prospect already existed) before the existing if/else block. Add `existing = db.get_prospect_by_name(data.get("name", ""))` at the top of the function near line 36 — this is already done, the variable is called `prospect_obj` after the `db.get_prospect_by_name()` call. Just capture it before the update block.

- [ ] **Step 2: Run all tests**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add intake.py
git commit -m "feat: enhanced booking notifications with prep scheduling"
```

---

### Task 8: Final Integration Test

- [ ] **Step 1: Run full test suite**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -m pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify all new modules import cleanly**

Run: `cd /Users/map98/Desktop/calm-money-bot && python3 -c "import follow_up; import meeting_prep; print('Phase 2 modules OK')"`
Expected: "Phase 2 modules OK"

- [ ] **Step 3: Verify bot loads**

Run: `cd /Users/map98/Desktop/calm-money-bot && TELEGRAM_BOT_TOKEN=test OPENAI_API_KEY=test ADMIN_CHAT_ID=123 WEBHOOK_SECRET=test python3 -c "import bot; print('Bot loads OK')"`
Expected: "Bot loads OK"

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: Phase 2 complete — The Admin autopilot operations"
```

---

## Summary of New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `follow_up.py` | ~160 | Auto-draft follow-ups with compliance and approval queue |
| `meeting_prep.py` | ~180 | Meeting prep doc generation with GPT and fallback |
| `tests/test_follow_up.py` | ~120 | Tests for follow_up module |
| `tests/test_meeting_prep.py` | ~100 | Tests for meeting_prep module |

## Modified Files

| File | Changes |
|------|---------|
| `bot.py` | +inline keyboard approval UX, +callback handler, +/drafts command, +follow-up trigger in tool dispatch |
| `voice_handler.py` | +follow-up draft trigger, +urgency detection |
| `intake.py` | +enhanced booking notification with prep scheduling |
| `scheduler.py` | +nudge stale drafts job, +meeting prep docs job |
