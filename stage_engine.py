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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

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


async def evaluate_prospect(prospect_id: int, tenant_id: int) -> None:
    """Evaluate whether a prospect's stage should change. Fire-and-forget."""
    try:
        if _is_rate_limited(prospect_id):
            logger.debug("Stage engine: prospect %d rate-limited, skipping", prospect_id)
            return

        _last_evaluated[prospect_id] = datetime.now(timezone.utc)

        prospect = db.get_prospect_by_id(prospect_id)
        if not prospect:
            logger.warning("Stage engine: prospect %d not found", prospect_id)
            return

        # placeholder — expanded in Task 5
    except Exception:
        logger.exception("Stage engine: unhandled error for prospect %d", prospect_id)
