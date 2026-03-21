# Design: Sales SMS Agent + Bug Fix Sweep
**Date:** 2026-03-21
**Status:** Approved by Marc

---

## Part 1 — Bug Fixes (19 Issues)

### Critical

1. **No Twilio webhook signature validation** (`webhook_intake.py:249`)
   Validate `X-Twilio-Signature` HMAC using `twilio.request_validator.RequestValidator` on the `/api/sms-reply` endpoint. Reject any request that fails.

2. **`gpt-5` model does not exist** (`bot.py:1085, 1254, 1520, 1652`)
   Replace all `model="gpt-5"` with `model="gpt-4.1"` across `bot.py`.

3. **Opt-out not re-checked before delayed auto-reply fires** (`sms_conversations.py:244`)
   Add `db.get_prospect_by_phone(phone) -> dict | None` (new function in `db.py` — looks up by normalized last-10 digits). In `_delayed_send()`, call it immediately before `send_sms()` and abort if `sms_opted_out == 1`. Also remove the redundant `import random` inside `_delayed_send` — the one inside `_business_hours_delay` is sufficient.

4. **Booking nurture fires after opt-out** (`booking_nurture.py:156`)
   In `generate_touch()`, after the recent-contact check, load the prospect and check `is_opted_out()` and `stage == "Do Not Contact"`. Return `None` if either is true.

5. **Phone lookup strips all `'1'` digits** (`webhook_intake.py:243`)
   Replace the broken `REPLACE(phone, '1', '')` approach with last-10-digit extraction using `SUBSTR`/`LENGTH` on both sides, normalized to the last 10 digits only.

6. **Double-send race condition** (`webhook_intake.py:291`, `sms_conversations.py:117`)
   Add a partial unique index on `sms_conversations` to deduplicate inbound retries: `CREATE UNIQUE INDEX IF NOT EXISTS ux_sms_inbound_sid ON sms_conversations(phone, twilio_sid) WHERE direction='inbound' AND twilio_sid != ''`. Use `INSERT OR IGNORE` in `log_message()` for inbound direction. Outbound rows are unaffected.

### High

7. **Business hours delay function never called** (`sms_conversations.py:245`)
   Replace `delay = random.randint(45, 90)` with `delay = _business_hours_delay()` in `_delayed_send()`.

8. **Booking dedup by name only** (`intake.py:36`)
   In `process_booking()`, attempt email lookup first, then phone, then fall back to name — matching the `_dedup_or_create()` pattern used elsewhere.

9. **Anonymous opt-outs never cancel booking sequences** (`sms_conversations.py:130`)
   In `handle_opt_out()`, also run `UPDATE booking_nurture_sequences SET status='cancelled' WHERE phone=? AND status='queued'` regardless of whether `prospect_id` is known.

10. **"Do Not Contact" stage not checked in outreach paths** (`booking_nurture.py`, `nurture.py`)
    Add `stage == "Do Not Contact"` guard alongside the opt-out check in all outreach generators.

11. **Opt-out flag lives in notes text** (`sms_conversations.py:103`, `db.py`)
    Add `sms_opted_out INTEGER DEFAULT 0` column to `prospects` table. Migration must include backfill: `UPDATE prospects SET sms_opted_out=1 WHERE notes LIKE '%[SMS_OPTED_OUT]%'`. `is_opted_out()` reads the column; `handle_opt_out()` sets it via `UPDATE prospects SET sms_opted_out=1 WHERE id=?`.

12. **Fuzzy name match updates wrong prospect** (`db.py:618`)
    When multiple name matches exist, log a warning and return `None` instead of silently picking the shortest. Existing callers that previously assumed a non-None return are **not** changed in this pass — they retain their existing behavior. Only new callers introduced in this PR must handle `None` explicitly. A follow-up audit of all `get_prospect_by_name()` call sites is noted as future work.

### Medium

13. **Unknown phone numbers get auto-replied**
    In `webhook_intake.py` SMS handler, if `prospect is None` and no prior conversation exists, do not auto-reply. Log and notify Marc via Telegram instead.

14. **Booking Touch 1 fires immediately regardless of time**
    Apply business hours logic to Touch 1 `scheduled_for` in `booking_nurture.py`.

15. **Inconsistent phone normalization**
    Add `normalize_phone(phone: str) -> str` to `db.py` (returns last 10 digits after stripping `+`, `-`, spaces). Co-locate with `get_prospect_by_phone()` since both are lookup utilities. Use it in `webhook_intake.py` and `bot.py` cold outreach lookup. `sms_sender.py` retains its existing E.164 normalizer for sending only.

16. **Audit log deleted after 90 days on every startup** (`db.py:321`)
    Change retention to 2555 days (7 years) for FSRA compliance. Make it a named constant `AUDIT_LOG_RETENTION_DAYS = 2555`.

17. **Internal Co-operators employees get booking SMS**
    In `process_booking()`, if `_is_internal` is true, skip nurture sequence creation entirely.

18. **Mixed UTC/ET datetime comparisons**
    Standardize all rate-limit comparisons to use `datetime.now(timezone.utc)` consistently. Remove bare `datetime.utcnow()` calls.

19. **Notes field grows unbounded**
    Cap notes appends at 2000 characters total in `update_prospect()`. Oldest content is truncated from the front. The `sms_opted_out` column fix (issue 11) removes the most dangerous flag-in-text dependency.

---

## Part 2 — Sales SMS Agent

### Overview

A goal-directed SMS agent that Marc can aim at a specific prospect. Marc gives it a name, phone number, and objective. The agent drafts an opening message for Marc's approval, then handles the entire conversation autonomously until the goal is achieved, the prospect declines, or the thread goes cold. Results are written back to the prospect's CRM record.

### Command

```
/agent +15191234567 John Smith — book a discovery call
```

Components parsed:
- **phone** — E.164 number
- **name** — prospect name (looked up in DB, or created if new)
- **objective** — free-form text after the `—`

### Database

New table `sms_agents`:

```sql
CREATE TABLE sms_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    prospect_id INTEGER,
    prospect_name TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT DEFAULT 'pending_approval',  -- pending_approval | active | success | cold | needs_marc | cancelled
    attempts INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    summary TEXT  -- filled in on completion
);
CREATE INDEX IF NOT EXISTS idx_sms_agents_phone ON sms_agents(phone, status);
```

### Flow

**Step 1 — Opener draft**
- Look up prospect in DB (or create if new)
- Load client memory if available
- GPT drafts opening SMS using the objective + memory as context
- Compliance check
- Queue to `approval_queue` with Approve/Skip buttons in Telegram

**Step 2 — On approval**
- Send via Twilio
- Log to `sms_conversations`
- Set `sms_agents.status = 'active'`
- Increment `attempts`

**Step 3 — Inbound reply handling**
- `webhook_intake.py` checks for active agent on the inbound phone before routing to generic auto-reply
- If active agent found → route to `sms_agent.handle_reply()`
- Agent generates reply using objective-aware system prompt (thread + objective + client memory)
- Business hours delay applied (same as auto-reply)
- Send, log, increment `attempts`

**Step 4 — Mission status classification**
After each outbound reply, a fast GPT call (`gpt-4.1-mini`) classifies the thread:
- `ongoing` — conversation still moving toward goal
- `success` — goal achieved (call booked, interest confirmed, etc.)
- `cold` — prospect clearly disengaged (explicit "not interested", ghosting detected by scheduler)
- `needs_marc` — prospect asked something the agent shouldn't handle (rates, complaints, legal, "who is this really")

**48h cold detection:** The existing scheduler in `scheduler.py` runs periodic jobs. A new scheduled job runs every 6 hours and queries `SELECT * FROM sms_agents WHERE status='active'`. For each active agent, it checks if the last inbound message for that phone is older than 48 hours (or if there have been 2+ outbound messages with no reply at all). If so, it calls `complete_mission(agent_id, 'cold', thread)`.

**Step 5 — Terminal state handling**
On `success`, `cold`, or `needs_marc`:
- Update `sms_agents.status` and `completed_at`
- Log activity to `activities` table
- Run `memory_engine.extract_facts_from_interaction()` on the full thread
- Update prospect stage if success (e.g. → "Discovery Call Booked")
- Send Telegram summary to Marc:
  - Success: "✅ John Smith — booked a call. Thread saved."
  - Cold: "🧊 John Smith — went cold after 3 attempts. Last message: '...'"
  - needs_marc: "⚠️ John Smith — needs you. Asked: '...' — agent paused."

**Step 6 — `needs_marc` resume**
Marc can reply to the Telegram notification with `/agent resume <id>` to re-activate the agent after he's handled the escalation manually.

`/agent resume <id>` does the following:
1. Sets `sms_agents.status = 'active'`
2. Does **not** send a message immediately
3. Waits for the next inbound reply from the prospect to continue autonomously
4. Sends Marc a confirmation: "Agent for [name] resumed — will continue on next reply."

This avoids the agent sending a message Marc may have already handled manually.

### System Prompt (Agent Replies)

```
You are handling an SMS conversation on behalf of Marc Pineault, a financial advisor at Co-operators in London, Ontario.

MISSION: {objective}

Your job is to move this conversation toward that goal — naturally, without pressure.

Rules:
- 1-2 sentences max
- First name only, no sign-off
- If they seem interested → send booking link
- If hesitant → low pressure, keep door open
- If they ask about rates/specifics → "I'll walk you through it on a call"
- Never make promises, guarantees, or specific recommendations over text
- If they ask something you can't handle (complaints, legal, who you really are) → reply "Let me have Marc reach out to you directly" and stop

Write ONLY the SMS text.
```

### New Module: `sms_agent.py`

Functions:
- `create_mission(phone, prospect_name, objective) -> dict` — creates DB record, drafts opener, queues for approval using `draft_type="sms_agent"`, `channel="sms_draft"`
- `handle_reply(phone, inbound_body, prospect) -> bool` — generates and sends agent reply, runs status check
- `classify_mission_status(thread, objective) -> str` — GPT status check returning ongoing/success/cold/needs_marc
- `complete_mission(agent_id, status, thread) -> None` — writes activity, extracts memory, updates stage, sends Telegram summary
- `get_active_agent(phone) -> dict | None` — lookup by phone with index on `(phone, status)`
- `check_cold_agents() -> None` — called by scheduler every 6h; closes agents where last inbound > 48h ago or 2+ unanswered outbound

### Changes to Existing Files

- `bot.py` — add `/agent` command handler, `/agent resume <id>` handler
- `webhook_intake.py` — in `sms_reply()`, check `sms_agent.get_active_agent(phone)` before routing to `generate_reply()`
- `db.py` — add `sms_agents` table and index, add `sms_opted_out` column with backfill, add `normalize_phone()` utility, add `get_prospect_by_phone()`, fix audit retention to 2555 days
- `scheduler.py` — add `check_cold_agents()` call every 6 hours

### What It Does Not Do

- Does not initiate outreach to multiple prospects at once (one agent per phone)
- Does not send without Marc approving the opening message
- Does not make financial recommendations or discuss rates/products in text
- Does not continue after a hard no or opt-out keyword

---

## File Change Summary

| File | Changes |
|------|---------|
| `webhook_intake.py` | Twilio sig validation, phone normalization, unknown-number guard, agent routing |
| `sms_conversations.py` | Business hours delay fix, opt-out re-check in delayed send, anonymous opt-out cancel, `is_opted_out` uses new column |
| `booking_nurture.py` | Opt-out + DNC check in `generate_touch()`, business hours for Touch 1 |
| `nurture.py` | DNC check in outreach path |
| `intake.py` | Booking dedup by email/phone first, skip nurture for internal |
| `bot.py` | Fix `gpt-5` → `gpt-4.1`, add `/agent` command |
| `db.py` | `sms_opted_out` column, phone normalization utility, audit retention 7yr, notes cap, `sms_agents` table, fuzzy match fix |
| `sms_agent.py` | **New file** — full agent module |
