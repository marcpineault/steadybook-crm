"""Lead nurture sequences — personalized multi-touch outreach.

For prospects who enter the pipeline but aren't meeting-ready, this module
builds and executes 3-5 value touches over 2-4 weeks:
  Touch 1: Relevant educational content
  Touch 2: Specific insight related to their situation
  Touch 3: Soft ask (booking link)
  Touch 4+: Additional value or re-engagement
"""

import logging
import os
from datetime import datetime, timedelta

from openai import OpenAI

import approval_queue
import compliance
import db
import memory_engine

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

TOUCH_TYPES = {
    1: {"type": "educational", "description": "Share relevant educational content about their product interest"},
    2: {"type": "insight", "description": "Share a specific insight related to their situation"},
    3: {"type": "soft_ask", "description": "Soft ask — invite them to book a chat, include booking link"},
    4: {"type": "value_add", "description": "Additional value — a different angle or follow-up on earlier touches"},
}

TOUCH_SPACING_DAYS = [3, 5, 7, 10]  # Days between touches 1→2, 2→3, 3→4, 4→end

NURTURE_PROMPT = """You are writing a nurture message for Marc Pereira, a financial advisor at Co-operators in London, Ontario.

This is touch {touch_number} of {total_touches} in a nurture sequence.
TOUCH TYPE: {touch_type} — {touch_description}

PROSPECT: {prospect_name}
PRODUCT INTEREST: {product}
STAGE: {stage}

CLIENT INTELLIGENCE:
{client_intel}

CHANNEL: email

GUIDELINES:
1. Sound like Marc — warm, approachable, not salesy
2. This is a nurture message, not a hard sell
3. Keep it concise (100-150 words for email)
4. Reference their specific situation when possible
5. Touch 3 should include Marc's booking link: https://outlook.office365.com/book/MarcPereira
6. NEVER make return promises or misleading claims

Write ONLY the message text."""


def create_sequence(prospect_name, prospect_id=None, total_touches=4):
    """Create a nurture sequence for a prospect. Returns existing if already active."""
    with db.get_db() as conn:
        # Check for existing active sequence
        existing = conn.execute(
            "SELECT * FROM nurture_sequences WHERE prospect_name = ? AND status = 'active'",
            (prospect_name,),
        ).fetchone()
        if existing:
            return dict(existing)

        next_date = (datetime.now() + timedelta(days=TOUCH_SPACING_DAYS[0])).strftime("%Y-%m-%d")
        cursor = conn.execute(
            """INSERT INTO nurture_sequences (prospect_id, prospect_name, total_touches, next_touch_date)
               VALUES (?, ?, ?, ?)""",
            (prospect_id, prospect_name, total_touches, next_date),
        )
        row = conn.execute("SELECT * FROM nurture_sequences WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


def get_sequence(sequence_id):
    """Get a nurture sequence by ID. Returns dict or None."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM nurture_sequences WHERE id = ?", (sequence_id,)).fetchone()
        return dict(row) if row else None


def get_active_sequences():
    """Get all active nurture sequences."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM nurture_sequences WHERE status = 'active' ORDER BY next_touch_date ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_due_touches():
    """Get nurture sequences with touches due today or earlier."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM nurture_sequences WHERE status = 'active' AND next_touch_date <= ? ORDER BY next_touch_date ASC",
            (today,),
        ).fetchall()
        return [dict(r) for r in rows]


def generate_touch(sequence_id):
    """Generate the next nurture touch for a sequence.

    Returns dict with: prospect_name, content, touch_number, queue_id. Returns None on failure.
    """
    seq = get_sequence(sequence_id)
    if not seq or seq["status"] != "active":
        return None

    next_touch = seq["current_touch"] + 1
    if next_touch > seq["total_touches"]:
        complete_sequence(sequence_id, reason="all_touches_sent")
        return None

    touch_info = TOUCH_TYPES.get(next_touch, TOUCH_TYPES[4])

    # Gather context
    prospect = db.get_prospect_by_name(seq["prospect_name"])
    if prospect:
        client_intel = memory_engine.get_profile_summary_text(prospect["id"])
        if not client_intel or "No additional" in client_intel:
            client_intel = f"Notes: {prospect.get('notes', '')[:200]}"
        product = prospect.get("product", "Not specified")
        stage = prospect.get("stage", "Unknown")
    else:
        client_intel = "No client data on file."
        product = "Not specified"
        stage = "New Lead"

    try:
        # Static replacements first, user-sourced last
        prompt = NURTURE_PROMPT.replace("{touch_number}", str(next_touch))
        prompt = prompt.replace("{total_touches}", str(seq["total_touches"]))
        prompt = prompt.replace("{touch_type}", touch_info["type"])
        prompt = prompt.replace("{touch_description}", touch_info["description"])
        prompt = prompt.replace("{product}", product)
        prompt = prompt.replace("{stage}", stage)
        prompt = prompt.replace("{prospect_name}", seq["prospect_name"])
        prompt = prompt.replace("{client_intel}", client_intel)

        response = openai_client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=512,
            temperature=0.7,
        )
        content = response.choices[0].message.content.strip()
    except Exception:
        logger.exception("Nurture touch generation failed for %s", seq["prospect_name"])
        return None

    # Compliance
    comp_result = compliance.check_compliance(content)
    compliance.log_action(
        action_type="nurture_touch",
        target=seq["prospect_name"],
        content=content,
        compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
    )

    # Queue for approval
    draft = approval_queue.add_draft(
        draft_type="nurture",
        channel="email_draft",
        content=content,
        context=f"Nurture touch {next_touch}/{seq['total_touches']} — {touch_info['type']}",
        prospect_id=seq.get("prospect_id"),
    )

    # Advance sequence (re-read to guard against concurrent double-fire)
    with db.get_db() as conn:
        current = conn.execute(
            "SELECT current_touch FROM nurture_sequences WHERE id = ?", (sequence_id,)
        ).fetchone()
        if current is None or current["current_touch"] != seq["current_touch"]:
            return None  # already advanced by another call
        spacing_idx = min(next_touch, len(TOUCH_SPACING_DAYS)) - 1
        next_date = (datetime.now() + timedelta(days=TOUCH_SPACING_DAYS[spacing_idx])).strftime("%Y-%m-%d")
        conn.execute(
            "UPDATE nurture_sequences SET current_touch = ?, next_touch_date = ? WHERE id = ?",
            (next_touch, next_date, sequence_id),
        )

    return {
        "prospect_name": seq["prospect_name"],
        "content": content,
        "touch_number": next_touch,
        "total_touches": seq["total_touches"],
        "queue_id": draft["id"],
    }


def complete_sequence(sequence_id, reason="manual"):
    """Mark a nurture sequence as completed."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE nurture_sequences SET status = 'completed' WHERE id = ?",
            (sequence_id,),
        )
    logger.info("Nurture sequence #%s completed: %s", sequence_id, reason)


def format_sequence_for_telegram(seq):
    """Format a nurture sequence for Telegram display."""
    return (
        f"NURTURE: {seq['prospect_name']}\n"
        f"Progress: {seq['current_touch']}/{seq['total_touches']} touches\n"
        f"Status: {seq['status']}\n"
        f"Next touch: {seq.get('next_touch_date', 'N/A')}"
    )
