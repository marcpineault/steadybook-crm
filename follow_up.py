"""Auto-drafted follow-ups for prospect interactions.

After any logged activity (call, meeting, voice note), this module generates
a personalized follow-up email draft, runs it through compliance, stores it
in the approval queue, and notifies Marc via Telegram.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

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

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")

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
    """Format a follow-up draft for Telegram display."""
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
