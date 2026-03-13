"""Strategic morning briefing generator.

Replaces the simple morning briefing with a CEO-level daily brief:
pipeline health, revenue forecast, priority moves, risk/opportunity alerts,
ranked call list, and queued actions.
"""

import logging
import os
import json
import re
from datetime import datetime, timedelta

import db
import scoring
import memory_engine

logger = logging.getLogger(__name__)

ACTIVE_STAGES = {
    "New Lead", "Contacted", "Discovery Call", "Needs Analysis",
    "Plan Presentation", "Proposal Sent", "Negotiation",
}


def assemble_briefing_data():
    """Gather all data needed for the morning briefing. Returns a dict."""
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    prospects = db.read_pipeline()
    active = [p for p in prospects if p.get("stage") in ACTIVE_STAGES]
    activities = db.read_activities(limit=50)
    recent_activities = [a for a in activities if a.get("date", "") >= week_ago]
    tasks_all = db.get_tasks(status="pending")
    tasks_due_today = db.get_due_tasks(today)
    tasks_overdue = db.get_overdue_tasks()
    meetings_today = [
        m for m in db.read_meetings()
        if m.get("date") == today and m.get("status") == "Scheduled"
    ]

    # Pipeline stats
    total_revenue = sum(float(p.get("revenue") or 0) for p in active)
    weighted_forecast = sum(
        float(p.get("revenue") or 0) * scoring.STAGE_PROBABILITY.get(p.get("stage", ""), 0.05)
        for p in active
    )

    # Ranked call list
    call_list = scoring.get_ranked_call_list(10)

    # Pending approval count (import here to avoid circular)
    try:
        import approval_queue
        pending_approvals = approval_queue.get_pending_count()
    except Exception:
        pending_approvals = 0

    # Market intelligence (calendar seeded at bot startup, not here)
    try:
        import market_intel
        market_events_text = market_intel.format_for_briefing(days_ahead=7)
    except Exception:
        logger.exception("Market intel failed for briefing (non-blocking)")
        market_events_text = ""

    return {
        "date": today,
        "prospects": active,
        "all_prospects": prospects,
        "activities_recent": recent_activities,
        "tasks_due_today": tasks_due_today,
        "tasks_overdue": tasks_overdue,
        "tasks_pending_count": len(tasks_all),
        "meetings_today": meetings_today,
        "call_list": call_list,
        "pending_approvals": pending_approvals,
        "market_events": market_events_text,
        "pipeline_stats": {
            "active_count": len(active),
            "total_revenue": total_revenue,
            "weighted_forecast": round(weighted_forecast, 2),
            "hot_count": sum(1 for p in active if p.get("priority") == "Hot"),
            "stages": _stage_distribution(active),
        },
    }


def _stage_distribution(active_prospects):
    """Count prospects per stage."""
    dist = {}
    for p in active_prospects:
        stage = p.get("stage", "Unknown")
        dist[stage] = dist.get(stage, 0) + 1
    return dist


from openai import OpenAI

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

BRIEFING_PROMPT = """You are Marc's AI business partner for his financial planning practice at Co-operators in London, Ontario. Generate his morning briefing.

Write in plain text, no markdown, no emojis. Write like a sharp chief of staff texting the boss — concise, direct, actionable.

DATA:
Date: {date}

PIPELINE ({active_count} active deals):
{prospect_summary}

REVENUE:
- Total pipeline revenue: ${total_revenue:,.0f}
- Weighted forecast this month: ${weighted_forecast:,.0f}

TODAY'S MEETINGS:
{meetings_summary}

TASKS DUE TODAY:
{tasks_today_summary}

OVERDUE TASKS:
{tasks_overdue_summary}

CALL LIST (ranked by impact):
{call_list_summary}

RECENT ACTIVITY (last 7 days):
{activity_summary}

PENDING APPROVALS: {pending_approvals} items in queue

MARKET INTELLIGENCE:
{market_events}

INSTRUCTIONS:
1. Start with a pipeline health score (0-100) and one-line trend assessment
2. Revenue forecast for the month
3. Top 2-3 priority moves for today with reasoning
4. Risk alerts (deals going cold, overdue follow-ups)
5. Today's call list with brief talking points for each
6. Mention pending approvals if any
7. MARKET CONTEXT — if any upcoming events, mention how they affect prospects

Keep it under 2000 characters. Be specific — use names, numbers, and days."""


def generate_briefing_text():
    """Generate the full strategic morning briefing. Falls back to simple format on failure."""
    data = None
    try:
        data = assemble_briefing_data()
        prompt = _build_briefing_prompt(data)
        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=2048,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Strategic briefing generation failed, falling back to simple format")
        return _fallback_briefing(data)


def _escape_braces(s):
    """Escape curly braces in user-supplied strings to prevent str.format() KeyError."""
    return s.replace("{", "{{").replace("}", "}}")


def _build_briefing_prompt(data):
    """Format the briefing prompt with assembled data."""
    stats = data["pipeline_stats"]

    # Prospect summary
    prospect_lines = []
    for p in data["prospects"][:15]:
        days_since = ""
        if p.get("updated_at"):
            try:
                updated = datetime.strptime(p["updated_at"][:10], "%Y-%m-%d")
                days = (datetime.now() - updated).days
                days_since = f" ({days}d ago)"
            except (ValueError, TypeError):
                pass
        prospect_lines.append(
            f"- {p.get('name')}: {p.get('stage')} | {p.get('priority', 'N/A')} | ${float(p.get('revenue') or 0):,.0f}{days_since}"
        )
    prospect_summary = "\n".join(prospect_lines) if prospect_lines else "No active prospects"

    # Meetings
    meeting_lines = [
        f"- {m.get('time')} — {m.get('prospect')} ({m.get('type', 'Meeting')})"
        for m in data["meetings_today"]
    ]
    meetings_summary = "\n".join(meeting_lines) if meeting_lines else "No meetings today"

    # Tasks
    today_lines = [f"- {t.get('title')} (prospect: {t.get('prospect', 'N/A')})" for t in data["tasks_due_today"]]
    tasks_today_summary = "\n".join(today_lines) if today_lines else "None"

    overdue_lines = [f"- {t.get('title')} — due {t.get('due_date')} (prospect: {t.get('prospect', 'N/A')})" for t in data["tasks_overdue"]]
    tasks_overdue_summary = "\n".join(overdue_lines) if overdue_lines else "None"

    # Enrich call list with memory context
    # Note: get_ranked_call_list returns flat merged dicts {**prospect, **score_data}
    enriched_calls = []
    for entry in data["call_list"][:5]:
        name = entry.get("name", "Unknown")
        line = f"- {name} (score: {entry.get('score', 0)}) — {entry.get('action', 'Follow up')}"
        if entry.get("id"):
            try:
                profile = memory_engine.get_profile_summary_text(entry["id"])
                if profile and "No additional" not in profile:
                    snippet = profile[:200]
                    cutoff = snippet.rfind(" ")
                    if cutoff > 100:
                        snippet = snippet[:cutoff]
                    line += f"\n  Context: {snippet}"
            except Exception:
                pass
        enriched_calls.append(line)
    call_list_summary = "\n".join(enriched_calls) if enriched_calls else "No calls recommended"

    # Recent activity
    act_lines = [
        f"- {a.get('date')}: {a.get('prospect')} — {a.get('action')} → {a.get('outcome', 'N/A')}"
        for a in data["activities_recent"][:10]
    ]
    activity_summary = "\n".join(act_lines) if act_lines else "No recent activity"

    return BRIEFING_PROMPT.format(
        date=data["date"],
        active_count=stats["active_count"],
        prospect_summary=_escape_braces(prospect_summary),
        total_revenue=stats["total_revenue"],
        weighted_forecast=stats["weighted_forecast"],
        meetings_summary=_escape_braces(meetings_summary),
        tasks_today_summary=_escape_braces(tasks_today_summary),
        tasks_overdue_summary=_escape_braces(tasks_overdue_summary),
        call_list_summary=_escape_braces(call_list_summary),
        activity_summary=_escape_braces(activity_summary),
        pending_approvals=data["pending_approvals"],
        market_events=_escape_braces(data.get("market_events", "")),
    )


def _fallback_briefing(data=None):
    """Simple fallback briefing when GPT is unavailable (matches current morning briefing style)."""
    try:
        if data is None:
            data = assemble_briefing_data()
        stats = data["pipeline_stats"]
        lines = [
            f"MORNING BRIEFING — {data['date']}",
            f"Pipeline: {stats['active_count']} active | ${stats['total_revenue']:,.0f} revenue | {stats['hot_count']} hot",
            "",
        ]
        if data["tasks_overdue"]:
            lines.append(f"OVERDUE TASKS ({len(data['tasks_overdue'])}):")
            for t in data["tasks_overdue"]:
                lines.append(f"  - {t.get('title')} (due {t.get('due_date')})")
            lines.append("")
        if data["tasks_due_today"]:
            lines.append(f"DUE TODAY ({len(data['tasks_due_today'])}):")
            for t in data["tasks_due_today"]:
                lines.append(f"  - {t.get('title')}")
            lines.append("")
        if data["meetings_today"]:
            lines.append("TODAY'S MEETINGS:")
            for m in data["meetings_today"]:
                lines.append(f"  - {m.get('time')} — {m.get('prospect')} ({m.get('type', 'Meeting')})")
        return "\n".join(lines)
    except Exception:
        logger.exception("Fallback briefing also failed")
        return "Morning briefing unavailable — check bot logs."
