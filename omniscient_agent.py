"""
Omniscient AI Assistant.
Runs every 15 minutes via APScheduler. Reads all prospect data, synthesizes
insights with GPT-4.1, and acts based on the configured trust level:
  L1 (trust_level=1): Draft only — creates tasks for advisor approval
  L2 (trust_level=2): Routine auto — sends routine follow-ups automatically
  L3 (trust_level=3): Full autonomy — acts on all recommendations

Sends alerts to the advisor via Telegram.
"""

import logging
import os
from datetime import datetime, timedelta

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

_openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
TELEGRAM_CHAT_ID = os.environ.get("ADVISOR_TELEGRAM_CHAT_ID", "")

SYSTEM_PROMPT = """You are an AI assistant for a financial advisor CRM called SteadyBook.
You analyze prospect pipeline data and surface the most important actions for the advisor.

Your output must be valid JSON with this structure:
{
  "summary": "One sentence summary of the pipeline state",
  "action_items": ["Action 1", "Action 2", ...],
  "stale_prospects": ["Name 1", "Name 2"],
  "urgent": ["Name of urgent prospect", ...],
  "has_action_items": true,
  "stale_count": 0,
  "urgent_count": 0
}

Be specific — name the prospect, suggest the exact action, note how long since last contact.
Keep action_items to the top 3-5 most important."""


def get_trust_level(tenant_id: int = 1) -> int:
    """Return trust level for the tenant (1, 2, or 3)."""
    return db.get_trust_level(tenant_id)


def build_prospect_context(tenant_id: int = 1) -> str:
    """Build a text summary of the current pipeline state for GPT."""
    try:
        metrics = db.get_pipeline_metrics()
        stale_prospects = _get_stale_prospects(days=7)
        new_leads = db.get_prospects_by_tag("new_lead")

        lines = [
            f"Pipeline Snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"Total prospects: {metrics.get('total', 0)}",
            f"Active: {metrics.get('active', 0)}",
            f"New leads: {metrics.get('new_leads', 0)}",
            f"Closed Won: {metrics.get('closed_won', 0)}",
            "",
        ]

        if stale_prospects:
            lines.append(f"STALE (no contact in 7+ days): {len(stale_prospects)} prospects")
            for p in stale_prospects[:5]:
                lines.append(f"  - {p.get('name')} | Stage: {p.get('stage')} | Last: {p.get('updated_at', 'unknown')[:10]}")
            lines.append("")

        if new_leads:
            lines.append(f"Recent new leads ({len(new_leads)} total):")
            for p in new_leads[:5]:
                lines.append(f"  - {p.get('name')} via {p.get('source', 'unknown')}")

        return "\n".join(lines)
    except Exception as e:
        logger.error("build_prospect_context error: %s", e)
        return "Pipeline data unavailable."


def _get_stale_prospects(days: int = 7) -> list[dict]:
    """Return prospects with no update in the last `days` days."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with db.get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM prospects
                WHERE stage NOT IN ('Closed Won', 'Closed Lost')
                AND (updated_at IS NULL OR updated_at < ?)
                ORDER BY updated_at ASC
                LIMIT 20
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("_get_stale_prospects error: %s", e)
        return []


def _synthesize_with_gpt(context: str) -> dict:
    """Send pipeline context to GPT-4.1 and parse the JSON response."""
    import json
    try:
        response = _openai.chat.completions.create(
            model="gpt-4.1",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            max_tokens=600,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as e:
        logger.error("GPT synthesis error: %s", e)
        return {
            "summary": "Analysis unavailable",
            "action_items": [],
            "has_action_items": False,
            "stale_count": 0,
            "urgent_count": 0,
        }


def should_alert(analysis: dict) -> bool:
    """Return True if the analysis warrants a Telegram alert."""
    return (
        analysis.get("has_action_items", False)
        or analysis.get("stale_count", 0) > 0
        or analysis.get("urgent_count", 0) > 0
    )


def format_alert_message(analysis: dict) -> str:
    """Format the analysis as a Telegram message."""
    lines = ["🤖 *SteadyBook Morning Brief*\n"]
    summary = analysis.get("summary", "")
    if summary:
        lines.append(summary)
        lines.append("")

    action_items = analysis.get("action_items", [])
    if action_items:
        lines.append("*Action Items:*")
        for item in action_items:
            lines.append(f"• {item}")
        lines.append("")

    stale = analysis.get("stale_prospects", [])
    if stale:
        lines.append(f"*Stale Prospects ({len(stale)}):* " + ", ".join(stale[:3]))

    return "\n".join(lines)


async def _send_telegram_alert(message: str) -> None:
    """Send a Telegram message to the advisor chat."""
    if not TELEGRAM_CHAT_ID:
        logger.debug("ADVISOR_TELEGRAM_CHAT_ID not set — skipping alert")
        return
    try:
        import telegram
        bot = telegram.Bot(token=os.environ.get("TELEGRAM_BOT_TOKEN", ""))
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Telegram alert failed: %s", e)


def _queue_draft(action_item: str, tenant_id: int) -> None:
    """Queue an action item as a draft task for advisor approval."""
    try:
        db.add_task(
            {
                "title": f"[AI Draft] {action_item[:80]}",
                "prospect": "",
                "due_date": datetime.now().strftime("%Y-%m-%d"),
                "notes": f"AI-generated draft — review and approve.\n{action_item}",
                "created_by": "omniscient_agent",
            },
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.error("_queue_draft failed: %s", e)


def run_omniscient_cycle(tenant_id: int = 1) -> None:
    """
    Main cycle — called by APScheduler every 15 minutes.
    Reads pipeline, synthesizes with GPT, acts per trust level.
    """
    import asyncio
    try:
        logger.info("Omniscient cycle starting for tenant %d", tenant_id)
        trust_level = get_trust_level(tenant_id)
        context = build_prospect_context(tenant_id)
        analysis = _synthesize_with_gpt(context)

        if not should_alert(analysis):
            logger.info("No action items — skipping alert")
            return

        message = format_alert_message(analysis)

        if trust_level >= 1:
            # Always send the alert
            asyncio.run(_send_telegram_alert(message))

        if trust_level >= 1:
            # Queue top action items as draft tasks
            for item in analysis.get("action_items", [])[:3]:
                _queue_draft(item, tenant_id)

        logger.info("Omniscient cycle complete — %d action items", len(analysis.get("action_items", [])))
    except Exception as e:
        logger.error("run_omniscient_cycle failed: %s", e)
