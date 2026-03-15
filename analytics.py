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


def record_outcome(action_type, target, sent_at, action_id=None, notes="", resend_email_id=None):
    """Record an outcome for an AI-generated action. Returns dict."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO outcomes (action_id, action_type, target, sent_at, notes, resend_email_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action_id, action_type, target, sent_at, notes, resend_email_id),
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
    """Aggregate outcome stats for the past 7 days."""
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
    """Generate AI-powered weekly insights from outcome data. Returns insights text or None."""
    stats = get_weekly_stats(reference_date=reference_date)
    if stats["total_actions"] == 0:
        return "No tracked outcomes this week. Start logging results to get insights!"

    stats_text = _format_stats_for_prompt(stats)

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
    can include in their GPT prompts. Returns empty string if no data.
    """
    stats = get_weekly_stats(reference_date=reference_date)
    if stats["total_actions"] == 0:
        return ""

    lines = ["RECENT PERFORMANCE (last 7 days):"]
    for action_type, data in stats["by_type"].items():
        rate = round(data["responses"] / data["total"] * 100, 1) if data["total"] > 0 else 0
        lines.append(f"  {action_type}: {rate}% response rate ({data['responses']}/{data['total']})")
        if data["conversions"] > 0:
            lines.append(f"    -> {data['conversions']} conversions")

    if stats["response_rate"] > 50:
        lines.append("Overall: Strong response rates — maintain current approach.")
    elif stats["response_rate"] > 25:
        lines.append("Overall: Moderate response rates — consider adjusting tone or timing.")
    else:
        lines.append("Overall: Low response rates — try different approaches.")

    return "\n".join(lines)
