# Stage Engine Design
**Date:** 2026-03-27
**Status:** Approved

---

## Overview

`stage_engine.py` is a new module that automatically evaluates whether a prospect's CRM pipeline stage should change, based on recent SMS activity, logged activities, and meetings. It uses GPT-4o-mini to make the decision, applies the change directly to the DB, and notifies the advisor via Telegram. It also detects cross-sell opportunities for existing clients and asks the advisor to confirm before creating a new opportunity record.

---

## Architecture

### Module
- **File:** `stage_engine.py` (root, alongside other modules)
- **Public entry point:** `async def evaluate_prospect(prospect_id: int, tenant_id: int) -> None`
- **Pattern:** Fire-and-forget async coroutine

### Rate Limiting
- Module-level dict: `_last_evaluated: dict[int, datetime] = {}`
- If `now - _last_evaluated[prospect_id] < 10 minutes`, return immediately without evaluation
- Updated on every evaluation that passes the check
- In-memory only — resets on bot restart (acceptable; worst case is one extra evaluation)

### Calling Convention
- **From async context (bot.py, sms_agent.py):** `asyncio.create_task(evaluate_prospect(prospect_id, tenant_id))`
- **From sync context (webhook_intake.py / Flask):** `asyncio.run_coroutine_threadsafe(evaluate_prospect(prospect_id, tenant_id), bot_loop)`

---

## Data Gathered Per Evaluation

| Source | Query |
|--------|-------|
| Prospect record | `id`, `name`, `stage`, `phone`, `product` |
| Recent SMS | Last 10 messages from `sms_messages` table for this prospect's phone |
| Recent activities | Last 5 rows from `activities` table for this prospect |
| Recent meetings | Last 3 rows from `meetings` table for this prospect |

---

## GPT Call

- **Model:** `gpt-4o-mini`
- **Role:** CRM stage analyst for a financial advisor
- **Input:** Current stage, product, recent SMS thread, activities, meetings
- **Output:** Structured JSON

### Response Schema
```json
{
  "should_change": true,
  "new_stage": "Proposal Sent",
  "reason": "Prospect confirmed they reviewed the quote and asked about next steps",
  "cross_sell_opportunity": false,
  "cross_sell_product": null
}
```

### Valid Stages
`New Lead`, `Contacted`, `Discovery Call`, `Needs Analysis`, `Plan Presentation`, `Proposal Sent`, `Negotiation`, `Nurture`, `Closed Won`, `Closed Lost`

### Cross-Sell Logic
GPT flags `cross_sell_opportunity: true` only when:
- Prospect is already `Closed Won`
- Conversation signals interest or readiness for a different product (e.g., has Life, asking about Disability)

Cross-sell is evaluated independently of stage changes — a `Closed Won` prospect can trigger a cross-sell notification without a stage change occurring.

### Validation
- GPT response parsed and validated against the known stage list before any action
- Malformed response or unknown stage → silent skip, logged to `audit_log`

---

## Actions on Stage Change

1. Call `db.update_prospect(name, {"stage": new_stage}, tenant_id)`
2. Log to `audit_log` with source `"stage_engine"` and the GPT reason
3. Send Telegram notification to the advisor:

```
Stage updated: John Smith
Contacted → Discovery Call
"Prospect replied confirming he wants to talk this week"
```

---

## Cross-Sell Flow

Telegram message with inline buttons sent to advisor:

```
Cross-sell opportunity: John Smith
He mentioned he doesn't have disability coverage.
Suggested product: Disability Insurance

[Create Opportunity]  [Skip]
```

### Create Opportunity (button pressed)
Creates a new prospect record:
- `name`: same as original
- `phone`, `email`: same as original
- `stage`: `New Lead`
- `product`: GPT-suggested product
- `source`: `"Cross-sell - [original product]"`
- `notes`: `"Cross-sell from existing client"`

Sends Telegram confirmation after creation.

### Skip (button pressed)
Dismisses silently — no DB changes.

### Callback Handler
New `CallbackQueryHandler` in `bot.py` for pattern `create_opp_{prospect_id}_{product}`.

---

## Trigger Integration (3 call sites)

| Location | Event | Change |
|----------|-------|--------|
| `bot.py` | Inbound SMS processed | Add `asyncio.create_task(evaluate_prospect(...))` |
| `bot.py` | Activity logged as completed | Add `asyncio.create_task(evaluate_prospect(...))` |
| `sms_agent.py` | Agent mission completed | Add `asyncio.create_task(evaluate_prospect(...))` |

---

## Error Handling

- All exceptions caught and logged — never raise to caller (fire-and-forget)
- GPT parse failure → log warning, skip silently
- DB error on update → log error, skip Telegram notification
- Telegram send failure → log warning, DB update already applied

---

## Files Changed

| File | Change |
|------|--------|
| `stage_engine.py` | **New file** |
| `bot.py` | Add 2 `create_task` calls + 1 `CallbackQueryHandler` |
| `sms_agent.py` | Add 1 `create_task` call on mission completion |
