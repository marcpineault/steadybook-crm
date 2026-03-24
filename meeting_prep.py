"""Meeting preparation document generator.

Generates comprehensive prep docs sent to Marc before meetings:
client snapshot, interaction history, recommended agenda, objection prep,
product recommendations, and personal touch points.
"""

import logging
import os
from datetime import datetime, timedelta

from openai import OpenAI

import db
import memory_engine
import scoring
from branding import build_advisor_intro, build_anti_injection_warning, get_prompt_context

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))


def get_prep_doc_system_prompt(tenant_id=1):
    ctx = get_prompt_context(tenant_id)
    intro = build_advisor_intro(tenant_id)
    return f"""You are preparing {ctx['advisor_name']} for a meeting with a client/prospect. {ctx['advisor_name']} is {intro.split(', ', 1)[1] if ', ' in intro else 'a financial advisor'}.

Generate a concise meeting prep document. Write in plain text, no markdown. Be specific and actionable.

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

Keep the entire document under 1500 characters.
Use the client's name token (e.g. [CLIENT_01]) as-is throughout the document.
{build_anti_injection_warning()}"""


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
        from pii import RedactionContext, sanitize_for_prompt

        with RedactionContext(prospect_names=[prospect_name]) as pii_ctx:
            user_content = pii_ctx.redact(sanitize_for_prompt(
                f"MEETING: {meeting_type} with {prospect_name} at {meeting_time}\n"
                f"STAGE: {ctx['stage']}\n"
                f"PRODUCT INTEREST: {prospect.get('product', 'Not specified')}\n"
                f"PRIORITY: {prospect.get('priority', 'N/A')}\n\n"
                f"CLIENT INTELLIGENCE:\n{ctx['memory_profile']}\n\n"
                f"LAST 5 INTERACTIONS:\n{ix_text}\n\n"
                f"RECENT ACTIVITIES:\n{act_text}\n\n"
                f"PROSPECT SCORE: {score_data.get('score', 0)}/100\n"
                f"SCORING REASONS: {'; '.join(score_data.get('reasons', []))}"
            ))

            response = openai_client.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {"role": "system", "content": get_prep_doc_system_prompt()},
                    {"role": "user", "content": user_content},
                ],
                max_completion_tokens=2048,
                temperature=0.6,
            )
            return pii_ctx.restore(response.choices[0].message.content.strip())
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
