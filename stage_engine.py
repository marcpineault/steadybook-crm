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


async def _call_gpt(prospect: dict, sms_thread: list, activities: list, meetings: list) -> dict:
    """Placeholder — expanded in Task 5."""
    return {
        "should_change": False,
        "new_stage": None,
        "reason": "",
        "cross_sell_opportunity": False,
        "cross_sell_product": None,
    }


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
