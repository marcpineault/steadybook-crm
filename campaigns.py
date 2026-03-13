"""Campaign management for batch outreach.

Creates and manages targeted outreach campaigns against the insurance book
and prospect pipeline. Each campaign segments an audience, generates
personalized messages, and queues them for Marc's approval.
"""

import json
import logging
import os

from openai import OpenAI

import approval_queue
import compliance
import db
import memory_engine

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

SEGMENT_PROMPT = """You are helping Marc Pereira, a financial advisor at Co-operators in London, Ontario, segment his client base for a targeted outreach campaign.

CRITERIA: {criteria}

Here are Marc's current clients from his insurance book:
{insurance_book_summary}

And his active prospects:
{prospects_summary}

Return a JSON array of names that match the criteria. Include ONLY names that clearly match.
Return ONLY the JSON array, no explanation. Example: ["Alice Johnson", "Bob Smith"]"""

MESSAGE_PROMPT = """You are drafting a personalized outreach message for Marc Pereira, a financial advisor at Co-operators in London, Ontario.

CAMPAIGN: {campaign_context}
RECIPIENT: {prospect_name}
CHANNEL: {channel}

CLIENT INTELLIGENCE:
{client_intel}

GUIDELINES:
1. Sound like Marc — warm, professional, never salesy
2. Reference something specific about the client (shows you know them)
3. Keep it concise: email 100-150 words, SMS 50-80 words, LinkedIn DM 80-120 words
4. Include a clear, low-pressure call to action
5. NEVER make specific return promises or misleading claims
6. For existing clients: acknowledge the relationship, don't sell from scratch

Write ONLY the message text. No subject lines, no meta-commentary."""


def create_campaign(name, description, channel="email_draft"):
    """Create a new campaign. Returns dict with campaign data."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO campaigns (name, description, channel)
               VALUES (?, ?, ?)""",
            (name, description, channel),
        )
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_campaign(campaign_id):
    """Get a campaign by ID. Returns dict or None."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return dict(row) if row else None


def list_campaigns(status=None):
    """List all campaigns, optionally filtered by status."""
    with db.get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE status = ? ORDER BY id DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM campaigns ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_campaign_status(campaign_id, status):
    """Update campaign status (draft, active, paused, completed)."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE campaigns SET status = ? WHERE id = ?",
            (status, campaign_id),
        )


def segment_audience(criteria):
    """Use AI to segment the audience based on natural language criteria.

    Returns list of matching client names.
    """
    # Gather data
    insurance_entries = db.read_insurance_book()
    prospects = db.read_pipeline()

    book_lines = []
    for e in insurance_entries[:50]:
        book_lines.append(f"- {e['name']}: {e.get('notes', '')[:100]}")
    book_text = "\n".join(book_lines) if book_lines else "No insurance book entries."

    prospect_lines = []
    for p in prospects[:50]:
        prospect_lines.append(f"- {p['name']}: {p.get('product', '?')} ({p.get('stage', '?')}), notes: {p.get('notes', '')[:80]}")
    prospect_text = "\n".join(prospect_lines) if prospect_lines else "No prospects."

    try:
        prompt = SEGMENT_PROMPT.replace("{criteria}", criteria)
        prompt = prompt.replace("{insurance_book_summary}", book_text)
        prompt = prompt.replace("{prospects_summary}", prospect_text)

        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            raw = raw.rstrip()
            if raw.endswith("```"):
                raw = raw[:-3].rstrip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        names = json.loads(raw)
        return names if isinstance(names, list) else []
    except Exception:
        logger.exception("Audience segmentation failed")
        return []


def generate_campaign_message(prospect_name, campaign_context, channel="email_draft"):
    """Generate a single personalized campaign message.

    Returns dict with: prospect_name, content, compliance_passed, compliance_issues, queue_id.
    Returns None on failure.
    """
    # Gather client intelligence
    prospect = db.get_prospect_by_name(prospect_name)
    if prospect:
        client_intel = memory_engine.get_profile_summary_text(prospect["id"])
        if not client_intel or "No additional" in client_intel:
            client_intel = f"Product: {prospect.get('product', '?')}. Stage: {prospect.get('stage', '?')}. Notes: {prospect.get('notes', '')[:200]}"
    else:
        # Check insurance book
        book_entries = db.read_insurance_book()
        entry = next((e for e in book_entries if e["name"].lower() == prospect_name.lower()), None)
        client_intel = f"Insurance book client. Notes: {entry.get('notes', '')[:200]}" if entry else "No client data on file."

    try:
        # Static replacements first, user-sourced last
        prompt = MESSAGE_PROMPT.replace("{channel}", channel)
        prompt = prompt.replace("{campaign_context}", campaign_context)
        prompt = prompt.replace("{prospect_name}", prospect_name)
        prompt = prompt.replace("{client_intel}", client_intel)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            temperature=0.7,
        )
        content = response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Campaign message generation failed for %s", prospect_name)
        return None

    # Compliance check + queue for approval
    try:
        comp_result = compliance.check_compliance(content)
        compliance.log_action(
            action_type="campaign_message",
            target=prospect_name,
            content=content,
            compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
        )

        draft = approval_queue.add_draft(
            draft_type="campaign",
            channel=channel,
            content=content,
            context=f"Campaign: {campaign_context}",
            prospect_id=prospect["id"] if prospect else None,
        )
    except Exception:
        logger.exception("Campaign compliance/queue failed for %s", prospect_name)
        return None

    return {
        "prospect_name": prospect_name,
        "content": content,
        "compliance_passed": comp_result["passed"],
        "compliance_issues": comp_result.get("issues", []),
        "queue_id": draft["id"],
    }


def format_campaign_summary(campaign):
    """Format a campaign for Telegram display."""
    lines = [
        f"CAMPAIGN #{campaign['id']}: {campaign['name']}",
        f"Status: {campaign['status']} | Channel: {campaign['channel']}",
        f"Description: {campaign['description'][:200]}",
    ]

    # Count messages
    with db.get_db() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = ?", (campaign["id"],)
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = ? AND status = 'pending'",
            (campaign["id"],),
        ).fetchone()[0]
        approved = conn.execute(
            "SELECT COUNT(*) FROM campaign_messages WHERE campaign_id = ? AND status = 'approved'",
            (campaign["id"],),
        ).fetchone()[0]

    lines.append(f"Messages: {total} total, {pending} pending, {approved} approved")
    return "\n".join(lines)
