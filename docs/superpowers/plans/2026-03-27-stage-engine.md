# Stage Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `stage_engine.py` — an async module that evaluates whether a prospect's CRM stage should change after inbound SMS, logged activities, or agent mission completions, using GPT-4o-mini, then notifies the advisor via Telegram and handles cross-sell opportunities with inline keyboard confirmation.

**Architecture:** Single `async def evaluate_prospect(prospect_id, tenant_id)` entry point, fire-and-forget via `asyncio.create_task()` from async contexts or `asyncio.run_coroutine_threadsafe()` from Flask/sync contexts. Module-level dict rate-limits to once per 10 minutes per prospect. Three existing files get minimal additions at their trigger points.

**Tech Stack:** Python asyncio, OpenAI GPT-4o-mini (`openai` SDK), python-telegram-bot (async), psycopg2 via `db.py`

---

## File Map

| File | Change |
|------|--------|
| `stage_engine.py` | **New** — rate limiter, data gathering, GPT call, stage update, Telegram notify |
| `tests/test_stage_engine.py` | **New** — unit tests |
| `bot.py` | Add `handle_create_opp_callback` function + register handler + activity trigger |
| `sms_agent.py` | Replace hardcoded stage update with `evaluate_prospect` trigger in `complete_mission()` |
| `webhook_intake.py` | Add `evaluate_prospect` trigger in `sms_reply()` after `generate_reply()` |

---

### Task 1: Scaffold stage_engine.py with rate limiter and data helpers

**Files:**
- Create: `stage_engine.py`
- Create: `tests/test_stage_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stage_engine.py
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import stage_engine


def test_rate_limit_skips_recent_prospect():
    """Should skip DB query entirely when called within 10 minutes."""
    stage_engine._last_evaluated[42] = datetime.now(timezone.utc)
    queried = []

    async def run():
        with patch("stage_engine.db") as mock_db:
            mock_db.get_prospect_by_id.return_value = None
            await stage_engine.evaluate_prospect(42, tenant_id=1)
            queried.append(mock_db.get_prospect_by_id.called)

    asyncio.run(run())
    assert queried[0] is False, "Should not query DB when rate-limited"


def test_rate_limit_allows_after_10_minutes():
    """Should proceed when last evaluation was >10 minutes ago."""
    stage_engine._last_evaluated[99] = datetime.now(timezone.utc) - timedelta(minutes=11)
    queried = []

    async def run():
        with patch("stage_engine.db") as mock_db, \
             patch("stage_engine._call_gpt", return_value={
                 "should_change": False, "new_stage": None, "reason": "",
                 "cross_sell_opportunity": False, "cross_sell_product": None,
             }), \
             patch("stage_engine._get_sms_thread", return_value=[]), \
             patch("stage_engine._get_activities", return_value=[]), \
             patch("stage_engine._get_meetings", return_value=[]):
            mock_db.get_prospect_by_id.return_value = {
                "id": 99, "name": "Bob", "stage": "Contacted",
                "phone": "+15550001111", "product": "Life",
            }
            await stage_engine.evaluate_prospect(99, tenant_id=1)
            queried.append(mock_db.get_prospect_by_id.called)

    asyncio.run(run())
    assert queried[0] is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v 2>&1 | head -15
```
Expected: `ModuleNotFoundError: No module named 'stage_engine'`

- [ ] **Step 3: Create stage_engine.py**

```python
# stage_engine.py
"""AI-driven prospect stage progression engine.

Public entry point:
    asyncio.create_task(evaluate_prospect(prospect_id, tenant_id))

Rate-limited to once per 10 minutes per prospect (in-memory).
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

_last_evaluated: dict[int, datetime] = {}
_RATE_LIMIT_MINUTES = 10

VALID_STAGES = [
    "New Lead", "Contacted", "Discovery Call", "Needs Analysis",
    "Plan Presentation", "Proposal Sent", "Negotiation", "Nurture",
    "Closed Won", "Closed Lost",
]

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def _is_rate_limited(prospect_id: int) -> bool:
    last = _last_evaluated.get(prospect_id)
    if last is None:
        return False
    return datetime.now(timezone.utc) - last < timedelta(minutes=_RATE_LIMIT_MINUTES)


def _get_sms_thread(phone: str, limit: int = 10) -> list[dict]:
    """Return last N SMS messages for this phone, oldest first."""
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT direction, body, created_at
               FROM sms_messages
               WHERE phone = %s
               ORDER BY id DESC LIMIT %s""",
            (phone, limit),
        )
        rows = cur.fetchall()
    return list(reversed([dict(r) for r in rows]))


def _get_activities(prospect_name: str, tenant_id: int, limit: int = 5) -> list[dict]:
    """Return last N activities for this prospect."""
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT action, outcome, notes, date
               FROM activities
               WHERE LOWER(prospect) LIKE %s AND tenant_id = %s
               ORDER BY id DESC LIMIT %s""",
            (f"%{prospect_name.lower()}%", tenant_id, limit),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def _get_meetings(prospect_name: str, tenant_id: int, limit: int = 3) -> list[dict]:
    """Return last N meetings for this prospect."""
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT type, date, status, prep_notes
               FROM meetings
               WHERE LOWER(prospect) LIKE %s AND tenant_id = %s
               ORDER BY id DESC LIMIT %s""",
            (f"%{prospect_name.lower()}%", tenant_id, limit),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


async def evaluate_prospect(prospect_id: int, tenant_id: int) -> None:
    """Evaluate whether a prospect's stage should change. Fire-and-forget."""
    try:
        if _is_rate_limited(prospect_id):
            logger.debug("Stage engine: prospect %d rate-limited, skipping", prospect_id)
            return

        prospect = db.get_prospect_by_id(prospect_id)
        if not prospect:
            logger.warning("Stage engine: prospect %d not found", prospect_id)
            return

        # placeholder — expanded in Task 5
    except Exception:
        logger.exception("Stage engine: unhandled error for prospect %d", prospect_id)
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v
```
Expected: both tests PASS

- [ ] **Step 5: Commit**

```bash
git add stage_engine.py tests/test_stage_engine.py
git commit -m "feat: stage_engine scaffold — rate limiter and data helpers"
```

---

### Task 2: GPT call and response validation

**Files:**
- Modify: `stage_engine.py`
- Modify: `tests/test_stage_engine.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_stage_engine.py`:

```python
def test_call_gpt_returns_parsed_response():
    """_call_gpt should return parsed dict from a valid GPT JSON response."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"should_change": true, "new_stage": "Discovery Call", '
        '"reason": "Booked a call", "cross_sell_opportunity": false, "cross_sell_product": null}'
    )
    with patch.object(stage_engine.openai_client.chat.completions, "create", return_value=mock_response):
        result = stage_engine._call_gpt(
            current_stage="Contacted",
            product="Life Insurance",
            sms_thread=[{"direction": "inbound", "body": "Sure let's chat"}],
            activities=[],
            meetings=[],
        )
    assert result["should_change"] is True
    assert result["new_stage"] == "Discovery Call"
    assert result["reason"] == "Booked a call"


def test_call_gpt_invalid_json_returns_none():
    """_call_gpt should return None on malformed GPT response."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not json at all"
    with patch.object(stage_engine.openai_client.chat.completions, "create", return_value=mock_response):
        result = stage_engine._call_gpt(
            current_stage="Contacted", product="Life",
            sms_thread=[], activities=[], meetings=[],
        )
    assert result is None


def test_validate_stage_rejects_unknown():
    """_validate_gpt_result should return None for an unknown stage name."""
    result = stage_engine._validate_gpt_result({
        "should_change": True, "new_stage": "Banana Stage",
        "reason": "test", "cross_sell_opportunity": False, "cross_sell_product": None,
    })
    assert result is None


def test_validate_stage_accepts_valid():
    """_validate_gpt_result should return the dict unchanged for a known stage."""
    payload = {
        "should_change": True, "new_stage": "Negotiation",
        "reason": "Close to signing", "cross_sell_opportunity": False, "cross_sell_product": None,
    }
    assert stage_engine._validate_gpt_result(payload) == payload


def test_validate_stage_passes_no_change():
    """_validate_gpt_result should pass through when should_change is False."""
    payload = {
        "should_change": False, "new_stage": None,
        "reason": "", "cross_sell_opportunity": False, "cross_sell_product": None,
    }
    assert stage_engine._validate_gpt_result(payload) == payload
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py::test_call_gpt_returns_parsed_response -v 2>&1 | head -10
```
Expected: `AttributeError: module 'stage_engine' has no attribute '_call_gpt'`

- [ ] **Step 3: Add `_build_gpt_prompt`, `_call_gpt`, and `_validate_gpt_result` to stage_engine.py**

Add after `_get_meetings`:

```python
def _build_gpt_prompt(
    current_stage: str,
    product: str,
    sms_thread: list[dict],
    activities: list[dict],
    meetings: list[dict],
) -> str:
    sms_text = "\n".join(
        f"[{m['direction'].upper()}] {m['body']}" for m in sms_thread
    ) or "No recent SMS."
    activity_text = "\n".join(
        f"- {a['action']}: {a['outcome']} ({a.get('date', '')})" for a in activities
    ) or "No recent activities."
    meeting_text = "\n".join(
        f"- {m['type']} on {m['date']} ({m['status']})" for m in meetings
    ) or "No recent meetings."

    valid = ", ".join(VALID_STAGES)
    return f"""You are a CRM assistant for a financial advisor. Based on the data below, decide if the prospect's pipeline stage should change.

Current stage: {current_stage}
Current product: {product}

Recent SMS thread:
{sms_text}

Recent activities:
{activity_text}

Recent meetings:
{meeting_text}

Valid stages: {valid}

Rules:
- Only change stage if there is clear evidence (not a single ambiguous message).
- You may move forward OR backward (e.g. regress to Nurture if prospect went cold).
- If the prospect is already Closed Won and the conversation hints at interest in another product, set cross_sell_opportunity to true and suggest a product name.
- cross_sell_product should be a short product name (e.g. "Disability Insurance") or null.

Respond with ONLY valid JSON, no markdown:
{{
  "should_change": true or false,
  "new_stage": "stage name or null",
  "reason": "one sentence explanation",
  "cross_sell_opportunity": true or false,
  "cross_sell_product": "product name or null"
}}"""


def _call_gpt(
    current_stage: str,
    product: str,
    sms_thread: list[dict],
    activities: list[dict],
    meetings: list[dict],
) -> dict | None:
    """Call GPT-4o-mini and return parsed JSON dict, or None on failure."""
    import json
    prompt = _build_gpt_prompt(current_stage, product, sms_thread, activities, meetings)
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=200,
        )
        content = response.choices[0].message.content.strip()
        return json.loads(content)
    except Exception:
        logger.exception("Stage engine: GPT call failed")
        return None


def _validate_gpt_result(result: dict) -> dict | None:
    """Return result if valid, None if the stage name is unrecognized."""
    if not isinstance(result, dict):
        return None
    if result.get("should_change") and result.get("new_stage") not in VALID_STAGES:
        logger.warning("Stage engine: GPT returned unknown stage '%s'", result.get("new_stage"))
        return None
    return result
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add stage_engine.py tests/test_stage_engine.py
git commit -m "feat: stage_engine GPT call with prompt and response validation"
```

---

### Task 3: Stage update, audit log, and Telegram send

**Files:**
- Modify: `stage_engine.py`
- Modify: `tests/test_stage_engine.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_stage_engine.py`:

```python
def test_apply_stage_change_calls_update_and_notify():
    """_apply_stage_change should update DB, write audit log, and notify."""
    with patch("stage_engine.db") as mock_db, \
         patch("stage_engine._notify_stage_change") as mock_notify, \
         patch("stage_engine._log_audit") as mock_audit:
        stage_engine._apply_stage_change(
            prospect_name="Jane Doe",
            old_stage="New Lead",
            new_stage="Contacted",
            reason="Returned the call",
            tenant_id=1,
        )
        mock_db.update_prospect.assert_called_once_with("Jane Doe", {"stage": "Contacted"}, 1)
        mock_notify.assert_called_once_with("Jane Doe", "New Lead", "Contacted", "Returned the call")
        mock_audit.assert_called_once()


def test_send_telegram_calls_run_coroutine_threadsafe():
    """_send_telegram should use run_coroutine_threadsafe when bot_event_loop is available."""
    mock_main = MagicMock()
    mock_main.telegram_app = MagicMock()
    mock_main.bot_event_loop = MagicMock()

    with patch.dict(sys.modules, {"__main__": mock_main}), \
         patch("os.environ.get", return_value="123456"), \
         patch("asyncio.run_coroutine_threadsafe") as mock_rctf:
        stage_engine._send_telegram("hello")
        assert mock_rctf.called


def test_send_telegram_no_op_when_loop_missing():
    """_send_telegram should silently skip when bot_event_loop is None."""
    mock_main = MagicMock()
    mock_main.telegram_app = None
    mock_main.bot_event_loop = None

    with patch.dict(sys.modules, {"__main__": mock_main}), \
         patch("asyncio.run_coroutine_threadsafe") as mock_rctf:
        stage_engine._send_telegram("hello")
        assert not mock_rctf.called
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py::test_apply_stage_change_calls_update_and_notify -v 2>&1 | head -10
```
Expected: `AttributeError: module 'stage_engine' has no attribute '_apply_stage_change'`

- [ ] **Step 3: Add `_send_telegram`, `_notify_stage_change`, `_log_audit`, `_apply_stage_change`**

Add to `stage_engine.py`:

```python
def _send_telegram(text: str, reply_markup=None) -> None:
    """Send a Telegram message to ADMIN_CHAT_ID. Best-effort, non-blocking."""
    try:
        main_mod = sys.modules.get("__main__")
        telegram_app = getattr(main_mod, "telegram_app", None)
        bot_event_loop = getattr(main_mod, "bot_event_loop", None)
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if not telegram_app or not bot_event_loop or not chat_id:
            logger.debug("Stage engine: Telegram not available, skipping")
            return

        async def _send():
            kwargs = {"chat_id": chat_id, "text": text}
            if reply_markup:
                kwargs["reply_markup"] = reply_markup
            await telegram_app.bot.send_message(**kwargs)

        asyncio.run_coroutine_threadsafe(_send(), bot_event_loop)
    except Exception:
        logger.warning("Stage engine: Telegram send failed")


def _notify_stage_change(
    prospect_name: str, old_stage: str, new_stage: str, reason: str
) -> None:
    text = f"Stage updated: {prospect_name}\n{old_stage} \u2192 {new_stage}\n\"{reason}\""
    _send_telegram(text)


def _log_audit(
    prospect_name: str, old_stage: str, new_stage: str, reason: str, tenant_id: int
) -> None:
    try:
        with db.get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO audit_log (action, details, tenant_id, created_at)
                   VALUES (%s, %s, %s, NOW())""",
                (
                    "stage_change",
                    f"{prospect_name}: {old_stage} \u2192 {new_stage}. Reason: {reason}",
                    tenant_id,
                ),
            )
    except Exception:
        logger.exception("Stage engine: audit log write failed")


def _apply_stage_change(
    prospect_name: str,
    old_stage: str,
    new_stage: str,
    reason: str,
    tenant_id: int,
) -> None:
    db.update_prospect(prospect_name, {"stage": new_stage}, tenant_id)
    _log_audit(prospect_name, old_stage, new_stage, reason, tenant_id)
    _notify_stage_change(prospect_name, old_stage, new_stage, reason)
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add stage_engine.py tests/test_stage_engine.py
git commit -m "feat: stage_engine stage update, audit log, and Telegram notification"
```

---

### Task 4: Cross-sell detection with inline keyboard

**Files:**
- Modify: `stage_engine.py`
- Modify: `tests/test_stage_engine.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_stage_engine.py`:

```python
def test_notify_cross_sell_sends_inline_keyboard():
    """_notify_cross_sell should send a Telegram message with Create/Skip buttons."""
    with patch("stage_engine._send_telegram") as mock_send:
        stage_engine._notify_cross_sell(
            prospect_id=7,
            prospect_name="Alice Brown",
            current_product="Life Insurance",
            cross_sell_product="Disability Insurance",
            reason="Asked about income protection",
        )
        assert mock_send.called
        call_text = mock_send.call_args[0][0]
        assert "Alice Brown" in call_text
        assert "Disability Insurance" in call_text
        # second positional arg or 'reply_markup' kwarg must not be None
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs.get("reply_markup") is not None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py::test_notify_cross_sell_sends_inline_keyboard -v 2>&1 | head -10
```
Expected: `AttributeError: module 'stage_engine' has no attribute '_notify_cross_sell'`

- [ ] **Step 3: Add the import and `_notify_cross_sell`**

At the top of `stage_engine.py`, add the Telegram keyboard imports:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
```

Then add the function after `_apply_stage_change`:

```python
def _notify_cross_sell(
    prospect_id: int,
    prospect_name: str,
    current_product: str,
    cross_sell_product: str,
    reason: str,
) -> None:
    """Send a Telegram cross-sell alert with Create Opportunity / Skip buttons."""
    text = (
        f"Cross-sell opportunity: {prospect_name}\n"
        f"{reason}\n"
        f"Suggested product: {cross_sell_product}"
    )
    safe_product = cross_sell_product.replace(" ", "_")[:30]
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Create Opportunity",
                callback_data=f"create_opp_{prospect_id}_{safe_product}",
            ),
            InlineKeyboardButton(
                "Skip",
                callback_data=f"create_opp_skip_{prospect_id}",
            ),
        ]
    ])
    _send_telegram(text, reply_markup=keyboard)
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add stage_engine.py tests/test_stage_engine.py
git commit -m "feat: stage_engine cross-sell detection with Telegram inline keyboard"
```

---

### Task 5: Complete the evaluate_prospect main loop

**Files:**
- Modify: `stage_engine.py`
- Modify: `tests/test_stage_engine.py`

- [ ] **Step 1: Write integration tests**

Add to `tests/test_stage_engine.py`:

```python
def test_evaluate_prospect_applies_stage_change():
    """evaluate_prospect should apply stage change when GPT says to."""
    stage_engine._last_evaluated.pop(55, None)

    async def run():
        with patch("stage_engine.db") as mock_db, \
             patch("stage_engine._get_sms_thread", return_value=[{"direction": "inbound", "body": "Sure let's meet"}]), \
             patch("stage_engine._get_activities", return_value=[]), \
             patch("stage_engine._get_meetings", return_value=[]), \
             patch("stage_engine._call_gpt", return_value={
                 "should_change": True, "new_stage": "Discovery Call",
                 "reason": "Prospect agreed to meet",
                 "cross_sell_opportunity": False, "cross_sell_product": None,
             }), \
             patch("stage_engine._apply_stage_change") as mock_apply, \
             patch("stage_engine._notify_cross_sell") as mock_cross:
            mock_db.get_prospect_by_id.return_value = {
                "id": 55, "name": "Tom Harris", "stage": "Contacted",
                "phone": "+15550001234", "product": "Life Insurance",
            }
            await stage_engine.evaluate_prospect(55, tenant_id=1)
            mock_apply.assert_called_once_with(
                prospect_name="Tom Harris",
                old_stage="Contacted",
                new_stage="Discovery Call",
                reason="Prospect agreed to meet",
                tenant_id=1,
            )
            mock_cross.assert_not_called()

    asyncio.run(run())


def test_evaluate_prospect_notifies_cross_sell():
    """evaluate_prospect should call _notify_cross_sell when GPT flags opportunity."""
    stage_engine._last_evaluated.pop(66, None)

    async def run():
        with patch("stage_engine.db") as mock_db, \
             patch("stage_engine._get_sms_thread", return_value=[]), \
             patch("stage_engine._get_activities", return_value=[]), \
             patch("stage_engine._get_meetings", return_value=[]), \
             patch("stage_engine._call_gpt", return_value={
                 "should_change": False, "new_stage": None, "reason": "Mentioned no disability coverage",
                 "cross_sell_opportunity": True, "cross_sell_product": "Disability Insurance",
             }), \
             patch("stage_engine._apply_stage_change") as mock_apply, \
             patch("stage_engine._notify_cross_sell") as mock_cross:
            mock_db.get_prospect_by_id.return_value = {
                "id": 66, "name": "Sara Lee", "stage": "Closed Won",
                "phone": "+15559876543", "product": "Life Insurance",
            }
            await stage_engine.evaluate_prospect(66, tenant_id=1)
            mock_apply.assert_not_called()
            mock_cross.assert_called_once_with(
                prospect_id=66,
                prospect_name="Sara Lee",
                current_product="Life Insurance",
                cross_sell_product="Disability Insurance",
                reason="Mentioned no disability coverage",
            )

    asyncio.run(run())


def test_evaluate_prospect_skips_on_gpt_failure():
    """evaluate_prospect should silently skip when GPT returns None."""
    stage_engine._last_evaluated.pop(77, None)

    async def run():
        with patch("stage_engine.db") as mock_db, \
             patch("stage_engine._get_sms_thread", return_value=[]), \
             patch("stage_engine._get_activities", return_value=[]), \
             patch("stage_engine._get_meetings", return_value=[]), \
             patch("stage_engine._call_gpt", return_value=None), \
             patch("stage_engine._apply_stage_change") as mock_apply:
            mock_db.get_prospect_by_id.return_value = {
                "id": 77, "name": "Ed Flynn", "stage": "New Lead",
                "phone": "+15551112222", "product": "Life",
            }
            await stage_engine.evaluate_prospect(77, tenant_id=1)
            mock_apply.assert_not_called()

    asyncio.run(run())
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py::test_evaluate_prospect_applies_stage_change -v 2>&1 | head -15
```
Expected: FAIL — the placeholder body doesn't call `_apply_stage_change` yet

- [ ] **Step 3: Replace the `evaluate_prospect` stub with the full implementation**

Replace the entire `evaluate_prospect` function in `stage_engine.py`:

```python
async def evaluate_prospect(prospect_id: int, tenant_id: int) -> None:
    """Evaluate whether a prospect's stage should change. Fire-and-forget."""
    try:
        if _is_rate_limited(prospect_id):
            logger.debug("Stage engine: prospect %d rate-limited, skipping", prospect_id)
            return

        prospect = db.get_prospect_by_id(prospect_id)
        if not prospect:
            logger.warning("Stage engine: prospect %d not found", prospect_id)
            return

        _last_evaluated[prospect_id] = datetime.now(timezone.utc)

        name = prospect["name"]
        stage = prospect.get("stage", "New Lead")
        phone = prospect.get("phone", "")
        product = prospect.get("product", "")

        sms_thread = _get_sms_thread(phone)
        activities = _get_activities(name, tenant_id)
        meetings = _get_meetings(name, tenant_id)

        result = _call_gpt(stage, product, sms_thread, activities, meetings)
        if result is None:
            logger.warning("Stage engine: GPT returned no result for prospect %d", prospect_id)
            return

        result = _validate_gpt_result(result)
        if result is None:
            return

        if result["should_change"] and result["new_stage"]:
            _apply_stage_change(
                prospect_name=name,
                old_stage=stage,
                new_stage=result["new_stage"],
                reason=result["reason"],
                tenant_id=tenant_id,
            )

        if result.get("cross_sell_opportunity") and result.get("cross_sell_product"):
            _notify_cross_sell(
                prospect_id=prospect_id,
                prospect_name=name,
                current_product=product,
                cross_sell_product=result["cross_sell_product"],
                reason=result["reason"],
            )

    except Exception:
        logger.exception("Stage engine: unhandled error for prospect %d", prospect_id)
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add stage_engine.py tests/test_stage_engine.py
git commit -m "feat: stage_engine full evaluate_prospect implementation"
```

---

### Task 6: Add create_opp callback handler to bot.py

**Files:**
- Modify: `bot.py`

- [ ] **Step 1: Verify existing callback handler area**

```bash
grep -n "handle_nurture_offer\|handle_outcome_callback\|handle_draft_callback\|handle_card_confirmation" /Users/map98/Projects/steadybook-crm/bot.py | head -10
```

Note the line numbers. You'll add `handle_create_opp_callback` near the other `handle_*_callback` functions.

- [ ] **Step 2: Find where to insert the new function**

```bash
grep -n "async def handle_nurture_offer\|async def handle_outcome_callback\|async def handle_draft_callback" /Users/map98/Projects/steadybook-crm/bot.py
```

Add `handle_create_opp_callback` immediately before or after one of these existing handlers.

- [ ] **Step 3: Add the handler function**

Insert in bot.py (near the other callback handlers):

```python
async def handle_create_opp_callback(update, context) -> None:
    """Handle Create Opportunity / Skip buttons from stage_engine cross-sell alerts."""
    query = update.callback_query
    await query.answer()
    data = query.data  # "create_opp_{prospect_id}_{Product_Name}" or "create_opp_skip_{prospect_id}"

    if data.startswith("create_opp_skip_"):
        await query.edit_message_reply_markup(reply_markup=None)
        return

    # parse: create_opp_{prospect_id}_{product_with_underscores}
    remainder = data[len("create_opp_"):]
    parts = remainder.split("_", 1)
    if len(parts) != 2:
        logger.warning("create_opp callback: unexpected format %s", data)
        return

    try:
        prospect_id = int(parts[0])
        product = parts[1].replace("_", " ")
    except ValueError:
        logger.warning("create_opp callback: could not parse prospect_id from %s", data)
        return

    original = db.get_prospect_by_id(prospect_id)
    if not original:
        await query.edit_message_text("Could not find original prospect.")
        return

    new_prospect = {
        "name": original["name"],
        "phone": original.get("phone", ""),
        "email": original.get("email", ""),
        "stage": "New Lead",
        "product": product,
        "source": f"Cross-sell - {original.get('product', 'existing')}",
        "notes": "Cross-sell from existing client",
        "priority": "medium",
    }
    result = db.add_prospect(new_prospect, tenant_id=original.get("tenant_id", 1))
    await query.edit_message_text(
        f"Opportunity created for {original['name']}.\nProduct: {product}\n{result}"
    )
```

- [ ] **Step 4: Register the handler**

In bot.py at the callback registration block (~line 3755), add:

```python
app.add_handler(CallbackQueryHandler(handle_create_opp_callback, pattern=r"^create_opp_"))
```

- [ ] **Step 5: Syntax check**

```bash
cd /Users/map98/Projects/steadybook-crm && python -c "import ast; ast.parse(open('bot.py').read()); print('bot.py OK')"
```
Expected: `bot.py OK`

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: bot.py create_opp callback handler for cross-sell confirmation"
```

---

### Task 7: Wire trigger into sms_agent.py after mission completion

**Files:**
- Modify: `sms_agent.py`

`complete_mission()` at line 375 currently hardcodes a stage update on success (lines 421–427):
```python
if status == "success":
    if prospect_id:
        try:
            db.update_prospect(prospect_name, {"stage": "Discovery Call Booked"})
        except Exception:
            logger.exception("Stage update failed after agent success")
```

Replace this with a smart `evaluate_prospect` trigger on all outcomes (not just success), and remove the hardcoded stage string.

- [ ] **Step 1: Read complete_mission to confirm line numbers**

```bash
sed -n '375,450p' /Users/map98/Projects/steadybook-crm/sms_agent.py
```

- [ ] **Step 2: Add import at top of sms_agent.py**

`sms_agent.py` already imports `db` at line 17. Add directly after it:

```python
import stage_engine
```

- [ ] **Step 3: Replace the hardcoded stage block**

Remove these lines (approximately 421–427):
```python
    # Update prospect stage on success
    if status == "success":
        if prospect_id:
            try:
                db.update_prospect(prospect_name, {"stage": "Discovery Call Booked"})
            except Exception:
                logger.exception("Stage update failed after agent success")
        else:
            logger.warning("Cannot update stage -no prospect_id for mission %d", agent_id)
```

Replace with:
```python
    # Trigger smart stage evaluation after mission completes (any status)
    if prospect_id:
        try:
            main_mod = sys.modules.get("__main__")
            bot_event_loop = getattr(main_mod, "bot_event_loop", None)
            original = db.get_prospect_by_id(prospect_id)
            tenant_id = original.get("tenant_id", 1) if original else 1
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    stage_engine.evaluate_prospect(prospect_id, tenant_id),
                    bot_event_loop,
                )
        except Exception:
            logger.exception("Stage engine trigger failed after mission %d", agent_id)
```

- [ ] **Step 4: Confirm `asyncio` and `sys` are imported in sms_agent.py**

```bash
grep -n "^import asyncio\|^import sys" /Users/map98/Projects/steadybook-crm/sms_agent.py
```

If either is missing, add it to the import block at the top of the file.

- [ ] **Step 5: Syntax check**

```bash
cd /Users/map98/Projects/steadybook-crm && python -c "import ast; ast.parse(open('sms_agent.py').read()); print('sms_agent.py OK')"
```
Expected: `sms_agent.py OK`

- [ ] **Step 6: Commit**

```bash
git add sms_agent.py
git commit -m "feat: trigger stage_engine after sms_agent mission completion"
```

---

### Task 8: Wire trigger into webhook_intake.py after inbound SMS

**Files:**
- Modify: `webhook_intake.py`

`sms_reply()` calls `sms_conversations.generate_reply()` at approximately line 325. Add the stage engine trigger after that call, inside the existing `try` block.

- [ ] **Step 1: Read lines 314–334 to confirm exact structure**

```bash
sed -n '314,334p' /Users/map98/Projects/steadybook-crm/webhook_intake.py
```

- [ ] **Step 2: Confirm `sys` is imported**

```bash
grep -n "^import sys" /Users/map98/Projects/steadybook-crm/webhook_intake.py
```

If missing, add `import sys` to the imports block at the top of the file.

- [ ] **Step 3: Add the trigger after `generate_reply()`**

After the `sms_conversations.generate_reply(...)` call block, add:

```python
        # Trigger stage evaluation after inbound SMS
        if prospect_id:
            try:
                import asyncio
                import stage_engine as _stage_engine
                main_mod = sys.modules.get("__main__")
                bot_event_loop = getattr(main_mod, "bot_event_loop", None)
                if bot_event_loop:
                    tenant_id = prospect.get("tenant_id", 1) if prospect else 1
                    asyncio.run_coroutine_threadsafe(
                        _stage_engine.evaluate_prospect(prospect_id, tenant_id),
                        bot_event_loop,
                    )
            except Exception:
                logger.exception("Stage engine trigger failed after inbound SMS")
```

- [ ] **Step 4: Syntax check**

```bash
cd /Users/map98/Projects/steadybook-crm && python -c "import ast; ast.parse(open('webhook_intake.py').read()); print('webhook_intake.py OK')"
```
Expected: `webhook_intake.py OK`

- [ ] **Step 5: Commit**

```bash
git add webhook_intake.py
git commit -m "feat: trigger stage_engine after inbound SMS in webhook_intake"
```

---

### Task 9: Wire trigger into bot.py after activity logged

**Files:**
- Modify: `bot.py`

When the advisor logs a call or activity via chat, bot.py calls GPT tools including `add_activity`. After `add_activity` is processed (around line 1687), there's already a follow-up draft trigger block. Add the stage engine trigger in the same place.

- [ ] **Step 1: Read lines 1687–1705 of bot.py**

```bash
sed -n '1687,1710p' /Users/map98/Projects/steadybook-crm/bot.py
```

This shows the `if tool_name == "add_activity"` block that triggers follow-up draft generation.

- [ ] **Step 2: Add stage engine trigger after line 1703 (after the follow-up draft block)**

After the `except Exception: logger.exception("Follow-up draft generation failed...")` block, add:

```python
            # Trigger stage evaluation after activity logged
            if tool_name == "add_activity" and "prospect" in tool_input:
                try:
                    import stage_engine as _stage_engine
                    _prospect_obj = db.get_prospect_by_name(tool_input["prospect"])
                    if _prospect_obj and _prospect_obj.get("id"):
                        asyncio.ensure_future(
                            _stage_engine.evaluate_prospect(
                                _prospect_obj["id"],
                                _prospect_obj.get("tenant_id", 1),
                            )
                        )
                except Exception:
                    logger.exception("Stage engine trigger failed after activity (non-blocking)")
```

Note: `asyncio.ensure_future()` is used here (instead of `create_task`) because this runs inside the bot's async handler where the event loop is already running. Both work, but `ensure_future` is available in older Python versions too.

- [ ] **Step 3: Syntax check**

```bash
cd /Users/map98/Projects/steadybook-crm && python -c "import ast; ast.parse(open('bot.py').read()); print('bot.py OK')"
```
Expected: `bot.py OK`

- [ ] **Step 4: Run all stage_engine tests one final time**

```bash
cd /Users/map98/Projects/steadybook-crm && python -m pytest tests/test_stage_engine.py -v
```
Expected: all 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "feat: trigger stage_engine after activity logged in bot.py"
```
