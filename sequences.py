"""Configurable automation sequence engine for Calm Money CRM SaaS.

Replaces hardcoded nurture/booking sequences with a flexible, tenant-configurable
automation system. Each sequence has a trigger, ordered steps, and enrollment logic.

Trigger types:
    manual        — enrolled by user action
    new_lead      — auto-enroll when prospect is created
    stage_change  — when prospect moves to a specified stage
    no_show       — when a meeting is marked as no-show
    meeting_booked — when a meeting is scheduled
    cold_call     — after a cold call activity is logged
    form_submit   — when a lead intake form is submitted

Step types:
    sms           — send SMS via Twilio (goes through approval queue)
    email         — send email via Resend (goes through approval queue)
    wait          — delay before next step (minutes, hours, or days)
    task          — create a task for the advisor
    webhook       — call an external URL

Usage:
    from sequences import enroll_prospect, process_due_steps, get_templates
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pytz
from openai import OpenAI

import approval_queue
import db

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

# ── Sequence CRUD ──


def create_sequence(tenant_id: int, name: str, trigger_type: str,
                    description: str = "", trigger_config: dict | None = None,
                    steps: list | None = None) -> dict:
    """Create a new automation sequence.

    Args:
        tenant_id: Owner tenant
        name: Human-readable name (e.g. "No-Show Killer")
        trigger_type: One of the supported trigger types
        description: Optional description
        trigger_config: JSON-serializable config for the trigger
            e.g. {"stage": "Proposal Sent"} for stage_change trigger
        steps: List of step dicts, each with:
            step_type, delay_minutes, content_template, channel, config

    Returns:
        The created sequence dict with steps included.
    """
    valid_triggers = {
        "manual", "new_lead", "stage_change", "no_show",
        "meeting_booked", "cold_call", "form_submit",
    }
    if trigger_type not in valid_triggers:
        raise ValueError(f"Invalid trigger_type: {trigger_type}. Must be one of {valid_triggers}")

    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO sequences (tenant_id, name, description, trigger_type, trigger_config, status)
               VALUES (?, ?, ?, ?, ?, 'active')""",
            (tenant_id, name, description, trigger_type,
             json.dumps(trigger_config or {})),
        )
        sequence_id = cursor.lastrowid

        # Insert steps
        if steps:
            for i, step in enumerate(steps):
                conn.execute(
                    """INSERT INTO sequence_steps
                       (sequence_id, step_order, step_type, delay_minutes,
                        content_template, channel, config)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        sequence_id,
                        i + 1,
                        step.get("step_type", "wait"),
                        step.get("delay_minutes", 0),
                        step.get("content_template", ""),
                        step.get("channel", ""),
                        json.dumps(step.get("config") or {}),
                    ),
                )

        row = conn.execute("SELECT * FROM sequences WHERE id = ?", (sequence_id,)).fetchone()

    result = dict(row)
    result["steps"] = get_sequence_steps(sequence_id)
    return result


def get_sequence(sequence_id: int) -> dict | None:
    """Get a sequence by ID with its steps."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM sequences WHERE id = ?", (sequence_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["steps"] = get_sequence_steps(sequence_id)
    return result


def get_sequence_steps(sequence_id: int) -> list[dict]:
    """Get all steps for a sequence, ordered."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_order",
            (sequence_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_sequences(tenant_id: int, include_archived: bool = False) -> list[dict]:
    """List all sequences for a tenant."""
    with db.get_db() as conn:
        if include_archived:
            rows = conn.execute(
                "SELECT * FROM sequences WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM sequences WHERE tenant_id = ? AND status != 'archived' ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()

    results = []
    for row in rows:
        seq = dict(row)
        seq["steps"] = get_sequence_steps(seq["id"])
        # Get enrollment counts
        with db.get_db() as conn:
            counts = conn.execute(
                """SELECT status, COUNT(*) as cnt FROM sequence_enrollments
                   WHERE sequence_id = ? GROUP BY status""",
                (seq["id"],),
            ).fetchall()
        seq["enrollment_counts"] = {r["status"]: r["cnt"] for r in counts}
        results.append(seq)
    return results


def update_sequence(sequence_id: int, updates: dict) -> dict | None:
    """Update a sequence's metadata. Returns updated sequence or None."""
    allowed = {"name", "description", "trigger_type", "trigger_config", "status"}
    safe = {k: v for k, v in updates.items() if k in allowed and v is not None}

    if "trigger_config" in safe and isinstance(safe["trigger_config"], dict):
        safe["trigger_config"] = json.dumps(safe["trigger_config"])

    if not safe:
        return get_sequence(sequence_id)

    set_clause = ", ".join(f'"{k}" = ?' for k in safe)
    values = list(safe.values()) + [sequence_id]

    with db.get_db() as conn:
        conn.execute(
            f"UPDATE sequences SET {set_clause}, updated_at = datetime('now') WHERE id = ?",
            values,
        )
    return get_sequence(sequence_id)


def update_sequence_steps(sequence_id: int, steps: list[dict]) -> list[dict]:
    """Replace all steps for a sequence. Returns new steps list."""
    with db.get_db() as conn:
        conn.execute("DELETE FROM sequence_steps WHERE sequence_id = ?", (sequence_id,))
        for i, step in enumerate(steps):
            conn.execute(
                """INSERT INTO sequence_steps
                   (sequence_id, step_order, step_type, delay_minutes,
                    content_template, channel, config)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    sequence_id,
                    i + 1,
                    step.get("step_type", "wait"),
                    step.get("delay_minutes", 0),
                    step.get("content_template", ""),
                    step.get("channel", ""),
                    json.dumps(step.get("config") or {}),
                ),
            )
    return get_sequence_steps(sequence_id)


# ── Enrollment ──


def enroll_prospect(sequence_id: int, prospect_id: int,
                    trigger_data: dict | None = None) -> dict | None:
    """Enroll a prospect in a sequence.

    Skips if prospect is already actively enrolled in this sequence.
    Returns enrollment dict or None if skipped.
    """
    with db.get_db() as conn:
        # Check for active enrollment
        existing = conn.execute(
            """SELECT id FROM sequence_enrollments
               WHERE sequence_id = ? AND prospect_id = ? AND status = 'active'""",
            (sequence_id, prospect_id),
        ).fetchone()
        if existing:
            logger.info("Prospect %d already enrolled in sequence %d", prospect_id, sequence_id)
            return None

        # Get first step to calculate next_step_at
        first_step = conn.execute(
            "SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_order LIMIT 1",
            (sequence_id,),
        ).fetchone()

        if not first_step:
            logger.warning("Sequence %d has no steps", sequence_id)
            return None

        delay = first_step["delay_minutes"] if first_step["delay_minutes"] else 0
        next_at = (datetime.now(timezone.utc) + timedelta(minutes=delay)).strftime("%Y-%m-%d %H:%M:%S")

        cursor = conn.execute(
            """INSERT INTO sequence_enrollments
               (sequence_id, prospect_id, status, current_step, next_step_at, trigger_data)
               VALUES (?, ?, 'active', 1, ?, ?)""",
            (sequence_id, prospect_id, next_at, json.dumps(trigger_data or {})),
        )
        row = conn.execute(
            "SELECT * FROM sequence_enrollments WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()

    logger.info("Prospect %d enrolled in sequence %d", prospect_id, sequence_id)
    return dict(row) if row else None


def unenroll_prospect(sequence_id: int, prospect_id: int, reason: str = "manual") -> bool:
    """Remove a prospect from a sequence. Returns True if found and cancelled."""
    with db.get_db() as conn:
        result = conn.execute(
            """UPDATE sequence_enrollments SET status = 'cancelled', completed_at = datetime('now')
               WHERE sequence_id = ? AND prospect_id = ? AND status = 'active'""",
            (sequence_id, prospect_id),
        )
    cancelled = result.rowcount > 0
    if cancelled:
        logger.info("Prospect %d unenrolled from sequence %d (%s)", prospect_id, sequence_id, reason)
    return cancelled


def get_enrollment(enrollment_id: int) -> dict | None:
    """Get an enrollment by ID."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sequence_enrollments WHERE id = ?", (enrollment_id,)
        ).fetchone()
    return dict(row) if row else None


def get_prospect_enrollments(prospect_id: int) -> list[dict]:
    """Get all enrollments for a prospect."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT se.*, s.name as sequence_name, s.trigger_type
               FROM sequence_enrollments se
               JOIN sequences s ON se.sequence_id = s.id
               WHERE se.prospect_id = ?
               ORDER BY se.enrolled_at DESC""",
            (prospect_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_sequence_enrollments(sequence_id: int, status: str = "active") -> list[dict]:
    """Get all enrollments for a sequence, optionally filtered by status."""
    with db.get_db() as conn:
        if status:
            rows = conn.execute(
                """SELECT se.*, p.name as prospect_name, p.phone, p.email
                   FROM sequence_enrollments se
                   JOIN prospects p ON se.prospect_id = p.id
                   WHERE se.sequence_id = ? AND se.status = ?
                   ORDER BY se.enrolled_at DESC""",
                (sequence_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT se.*, p.name as prospect_name, p.phone, p.email
                   FROM sequence_enrollments se
                   JOIN prospects p ON se.prospect_id = p.id
                   WHERE se.sequence_id = ?
                   ORDER BY se.enrolled_at DESC""",
                (sequence_id,),
            ).fetchall()
    return [dict(r) for r in rows]


# ── Step execution ──


def get_due_enrollments() -> list[dict]:
    """Get all enrollments with steps due for execution."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT se.*, s.tenant_id, s.name as sequence_name
               FROM sequence_enrollments se
               JOIN sequences s ON se.sequence_id = s.id
               WHERE se.status = 'active'
               AND se.next_step_at <= ?
               AND s.status = 'active'
               ORDER BY se.next_step_at ASC""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def execute_step(enrollment_id: int) -> dict | None:
    """Execute the current step for an enrollment.

    Returns step execution result dict, or None if nothing to do.
    """
    enrollment = get_enrollment(enrollment_id)
    if not enrollment or enrollment["status"] != "active":
        return None

    sequence = get_sequence(enrollment["sequence_id"])
    if not sequence or sequence["status"] != "active":
        return None

    steps = sequence["steps"]
    current_step_num = enrollment["current_step"]

    # Find the current step
    step = None
    for s in steps:
        if s["step_order"] == current_step_num:
            step = s
            break

    if not step:
        # No more steps — complete the enrollment
        _complete_enrollment(enrollment_id)
        return {"status": "completed", "enrollment_id": enrollment_id}

    # Check prospect opt-out / DNC
    prospect = db.get_prospect_by_id(enrollment["prospect_id"])
    if not prospect:
        _complete_enrollment(enrollment_id, reason="prospect_deleted")
        return {"status": "cancelled", "reason": "prospect_deleted"}

    if prospect.get("stage") == "Do Not Contact" or prospect.get("sms_opted_out"):
        _complete_enrollment(enrollment_id, reason="opted_out")
        return {"status": "cancelled", "reason": "opted_out"}

    # Execute based on step type
    step_type = step["step_type"]
    result = None

    if step_type == "sms":
        result = _execute_sms_step(step, prospect, sequence)
    elif step_type == "email":
        result = _execute_email_step(step, prospect, sequence)
    elif step_type == "task":
        result = _execute_task_step(step, prospect, sequence)
    elif step_type == "webhook":
        result = _execute_webhook_step(step, prospect, sequence)
    elif step_type == "wait":
        result = {"status": "ok", "action": "wait_completed"}
    else:
        logger.warning("Unknown step type: %s", step_type)
        result = {"status": "skipped", "reason": f"unknown step type: {step_type}"}

    # Log the step execution
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO sequence_step_logs
               (enrollment_id, step_id, status, content, executed_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (enrollment_id, step["id"],
             result.get("status", "ok") if result else "failed",
             json.dumps(result) if result else "{}"),
        )

    # Advance to next step
    next_step_num = current_step_num + 1
    next_step = None
    for s in steps:
        if s["step_order"] == next_step_num:
            next_step = s
            break

    if next_step:
        delay = next_step["delay_minutes"] if next_step["delay_minutes"] else 0
        next_at = (datetime.now(timezone.utc) + timedelta(minutes=delay)).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_db() as conn:
            conn.execute(
                """UPDATE sequence_enrollments
                   SET current_step = ?, next_step_at = ?, last_step_at = datetime('now')
                   WHERE id = ?""",
                (next_step_num, next_at, enrollment_id),
            )
    else:
        _complete_enrollment(enrollment_id)

    return result


def _complete_enrollment(enrollment_id: int, reason: str = "all_steps_completed"):
    """Mark an enrollment as completed."""
    with db.get_db() as conn:
        conn.execute(
            """UPDATE sequence_enrollments
               SET status = 'completed', completed_at = datetime('now')
               WHERE id = ?""",
            (enrollment_id,),
        )
    logger.info("Enrollment %d completed: %s", enrollment_id, reason)


def _generate_message(template: str, prospect: dict, sequence: dict,
                      channel: str = "sms") -> str:
    """Generate a personalized message from a template using AI.

    If template contains {{variables}}, does simple substitution.
    If template is a prompt/instruction, uses AI to generate the message.
    """
    # Simple variable substitution
    first_name = prospect.get("name", "").split()[0] if prospect.get("name") else "there"
    product = prospect.get("product", "")

    # Check if this is a simple template (has {{vars}} but no AI instructions)
    if "{{" in template and not any(kw in template.lower() for kw in ["generate", "write", "create"]):
        result = template.replace("{{first_name}}", first_name)
        result = result.replace("{{name}}", prospect.get("name", ""))
        result = result.replace("{{product}}", product)
        result = result.replace("{{email}}", prospect.get("email", ""))
        result = result.replace("{{phone}}", prospect.get("phone", ""))
        return result

    # AI generation
    tenant_config = {}
    try:
        import tenants
        tenant_config = tenants.get_tenant_config(sequence.get("tenant_id", 1))
    except Exception:
        pass

    sender_name = tenant_config.get("sender_name", "Marc")
    signature = tenant_config.get("sender_signature", f"- {sender_name}")
    booking_url = tenant_config.get("booking_url", "")

    max_tokens = 200 if channel == "sms" else 512
    system_prompt = f"""You are writing a {channel} message for {sender_name}, a financial/insurance advisor.

RULES:
1. {"1-3 sentences ONLY" if channel == "sms" else "80-150 words"}
2. Use FIRST NAME ONLY
3. Sign off with "{signature}"
4. No corporate language, no "I hope this finds you well"
5. Sound like a real person, not marketing copy
6. Never make financial promises or return guarantees
{"7. Include booking link: " + booking_url if booking_url and "book" in template.lower() else ""}

Write ONLY the message text."""

    try:
        from pii import RedactionContext, sanitize_for_prompt

        with RedactionContext(prospect_names=[prospect.get("name", "")]) as pii_ctx:
            user_content = pii_ctx.redact(sanitize_for_prompt(
                f"TEMPLATE/INSTRUCTION: {template}\n\n"
                f"PROSPECT: {prospect.get('name', '')}\n"
                f"PRODUCT INTEREST: {product}\n"
                f"STAGE: {prospect.get('stage', 'New Lead')}\n"
            ))

            response = openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                max_completion_tokens=max_tokens,
                temperature=0.7,
            )
            content = pii_ctx.restore(response.choices[0].message.content.strip())

        # Ensure first name only in output
        full_name = prospect.get("name", "")
        if full_name and first_name != full_name:
            content = content.replace(full_name, first_name)

        return content
    except Exception:
        logger.exception("AI message generation failed")
        # Fallback to template with simple substitution
        return template.replace("{{first_name}}", first_name).replace("{{name}}", prospect.get("name", ""))


def _execute_sms_step(step: dict, prospect: dict, sequence: dict) -> dict:
    """Execute an SMS step — generates content and queues for approval."""
    template = step.get("content_template", "")
    if not template:
        return {"status": "skipped", "reason": "no template"}

    phone = prospect.get("phone", "")
    if not phone:
        return {"status": "skipped", "reason": "no phone number"}

    content = _generate_message(template, prospect, sequence, channel="sms")

    # Run compliance check
    try:
        import compliance
        comp_result = compliance.check_compliance(content)
        compliance.log_action(
            action_type="sequence_sms",
            target=prospect.get("name", ""),
            content=content,
            compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
        )
    except Exception:
        pass

    draft = approval_queue.add_draft(
        draft_type="sequence",
        channel="sms_draft",
        content=content,
        context=f"Sequence '{sequence['name']}' step {step['step_order']} for {prospect.get('name', '')}",
        prospect_id=prospect.get("id"),
    )

    return {"status": "queued", "queue_id": draft["id"], "channel": "sms", "content": content}


def _execute_email_step(step: dict, prospect: dict, sequence: dict) -> dict:
    """Execute an email step — generates content and queues for approval."""
    template = step.get("content_template", "")
    if not template:
        return {"status": "skipped", "reason": "no template"}

    email = prospect.get("email", "")
    if not email:
        return {"status": "skipped", "reason": "no email address"}

    content = _generate_message(template, prospect, sequence, channel="email")

    try:
        import compliance
        comp_result = compliance.check_compliance(content)
        compliance.log_action(
            action_type="sequence_email",
            target=prospect.get("name", ""),
            content=content,
            compliance_check="PASS" if comp_result["passed"] else f"FAIL: {'; '.join(comp_result['issues'])}",
        )
    except Exception:
        pass

    draft = approval_queue.add_draft(
        draft_type="sequence",
        channel="email_draft",
        content=content,
        context=f"Sequence '{sequence['name']}' step {step['step_order']} for {prospect.get('name', '')}",
        prospect_id=prospect.get("id"),
    )

    return {"status": "queued", "queue_id": draft["id"], "channel": "email", "content": content}


def _execute_task_step(step: dict, prospect: dict, sequence: dict) -> dict:
    """Execute a task step — creates a task for the advisor."""
    config = json.loads(step.get("config") or "{}")
    title = config.get("title", step.get("content_template", "Follow up"))

    # Variable substitution in title
    first_name = prospect.get("name", "").split()[0] if prospect.get("name") else "prospect"
    title = title.replace("{{first_name}}", first_name).replace("{{name}}", prospect.get("name", ""))

    due_date = None
    if config.get("due_in_days"):
        due_date = (datetime.now() + timedelta(days=config["due_in_days"])).strftime("%Y-%m-%d")

    db.add_task({
        "title": title,
        "prospect": prospect.get("name", ""),
        "due_date": due_date or datetime.now().strftime("%Y-%m-%d"),
        "notes": f"Auto-created by sequence: {sequence['name']}",
    })

    return {"status": "ok", "action": "task_created", "title": title}


def _execute_webhook_step(step: dict, prospect: dict, sequence: dict) -> dict:
    """Execute a webhook step — POSTs prospect data to a URL."""
    config = json.loads(step.get("config") or "{}")
    url = config.get("url", "")
    if not url:
        return {"status": "skipped", "reason": "no webhook URL"}

    import requests
    try:
        payload = {
            "event": "sequence_step",
            "sequence_name": sequence["name"],
            "step_order": step["step_order"],
            "prospect": {
                "name": prospect.get("name"),
                "email": prospect.get("email"),
                "phone": prospect.get("phone"),
                "stage": prospect.get("stage"),
                "product": prospect.get("product"),
            },
        }
        resp = requests.post(url, json=payload, timeout=10)
        return {"status": "ok", "http_status": resp.status_code}
    except Exception as e:
        logger.exception("Webhook step failed: %s", url)
        return {"status": "failed", "reason": str(e)}


# ── Trigger processing ──


def process_trigger(trigger_type: str, tenant_id: int, prospect_id: int,
                    trigger_data: dict | None = None) -> list[dict]:
    """Fire a trigger — enrolls prospect in all matching active sequences.

    Args:
        trigger_type: The trigger that fired (e.g. "new_lead", "no_show")
        tenant_id: The tenant context
        prospect_id: The prospect to enroll
        trigger_data: Extra context (e.g. {"stage": "Proposal Sent"} for stage_change)

    Returns:
        List of enrollment dicts created.
    """
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM sequences
               WHERE tenant_id = ? AND trigger_type = ? AND status = 'active'""",
            (tenant_id, trigger_type),
        ).fetchall()

    enrollments = []
    for row in rows:
        seq = dict(row)
        trigger_config = json.loads(seq.get("trigger_config") or "{}")

        # Check trigger config conditions
        if trigger_type == "stage_change" and trigger_data:
            required_stage = trigger_config.get("stage", "")
            if required_stage and trigger_data.get("new_stage") != required_stage:
                continue

        enrollment = enroll_prospect(seq["id"], prospect_id, trigger_data)
        if enrollment:
            enrollments.append(enrollment)

    return enrollments


def process_due_steps() -> list[dict]:
    """Process all due enrollment steps. Called by the scheduler.

    Returns list of execution results.
    """
    due = get_due_enrollments()
    results = []
    for enrollment in due:
        try:
            result = execute_step(enrollment["id"])
            if result:
                result["enrollment_id"] = enrollment["id"]
                result["sequence_name"] = enrollment.get("sequence_name", "")
                results.append(result)
        except Exception:
            logger.exception("Failed to execute step for enrollment %d", enrollment["id"])
    return results


# ── Pre-built templates ──

SEQUENCE_TEMPLATES = {
    "no_show_killer": {
        "name": "No-Show Killer",
        "description": "3-touch recovery sequence when a prospect misses their meeting. SMS immediately, email next day, task to call in 3 days.",
        "trigger_type": "no_show",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "sms",
                "delay_minutes": 5,
                "content_template": "Hey {{first_name}}, looks like we missed each other today. No worries at all — want to rebook for this week? I've got some openings.",
                "channel": "sms",
            },
            {
                "step_type": "wait",
                "delay_minutes": 1440,  # 24 hours
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a casual follow-up email. Mention you tried to reach them yesterday about rescheduling. Keep it light, no guilt. Include booking link if available.",
                "channel": "email",
            },
            {
                "step_type": "task",
                "delay_minutes": 2880,  # 48 hours after email
                "content_template": "Call {{first_name}} — no-show recovery",
                "channel": "",
                "config": {"title": "Call {{first_name}} — no-show recovery, 3rd attempt", "due_in_days": 0},
            },
        ],
    },
    "cold_call_followup": {
        "name": "Cold Call Follow-Up",
        "description": "After a cold call, send a quick SMS recap, then a value email 2 days later, then a soft ask to book.",
        "trigger_type": "cold_call",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "sms",
                "delay_minutes": 30,
                "content_template": "Hey {{first_name}}, good chatting with you. As mentioned, I'll send over some info on {{product}}. Talk soon.",
                "channel": "sms",
            },
            {
                "step_type": "wait",
                "delay_minutes": 2880,  # 2 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a value-add email following up on a cold call. Share one relevant insight about their product interest. Keep it educational, not salesy.",
                "channel": "email",
            },
            {
                "step_type": "wait",
                "delay_minutes": 4320,  # 3 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "sms",
                "delay_minutes": 0,
                "content_template": "Hey {{first_name}}, wanted to circle back on our chat. Happy to run some numbers for you if you're interested — no pressure. Want to grab 15 minutes this week?",
                "channel": "sms",
            },
        ],
    },
    "new_lead_nurture": {
        "name": "New Lead Nurture",
        "description": "4-touch nurture for new leads: educational content → specific insight → soft booking ask → re-engagement.",
        "trigger_type": "new_lead",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "wait",
                "delay_minutes": 4320,  # 3 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write an educational email sharing relevant content about their product interest. Position yourself as helpful, not salesy.",
                "channel": "email",
            },
            {
                "step_type": "wait",
                "delay_minutes": 7200,  # 5 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a specific insight email related to their situation. Reference something relevant to their product interest or stage.",
                "channel": "email",
            },
            {
                "step_type": "wait",
                "delay_minutes": 10080,  # 7 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "sms",
                "delay_minutes": 0,
                "content_template": "Hey {{first_name}}, I've been sending over some info — happy to hop on a quick call if any of it sparked questions. No pitch, just a chat.",
                "channel": "sms",
            },
            {
                "step_type": "wait",
                "delay_minutes": 14400,  # 10 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a light re-engagement email. Mention you haven't heard back and that's totally fine. Offer to be a resource whenever they're ready.",
                "channel": "email",
            },
        ],
    },
    "meeting_booked_warmup": {
        "name": "Meeting Booked Warm-Up",
        "description": "3-touch pre-meeting sequence: confirmation SMS → day-before reminder → 2-hour heads-up.",
        "trigger_type": "meeting_booked",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "sms",
                "delay_minutes": 0,
                "content_template": "Hey {{first_name}}, just confirming our meeting. Looking forward to chatting about your {{product}} options.",
                "channel": "sms",
            },
            {
                "step_type": "task",
                "delay_minutes": 60,
                "content_template": "",
                "channel": "",
                "config": {"title": "Prep meeting brief for {{first_name}}", "due_in_days": 0},
            },
            # Note: day-before and 2-hour touches need dynamic scheduling based on meeting time.
            # For now, these use fixed delays. The trigger_data should include meeting_datetime
            # for smarter scheduling in a future version.
            {
                "step_type": "sms",
                "delay_minutes": 1380,  # ~23 hours (day before placeholder)
                "content_template": "Hey {{first_name}}, just a heads up about tomorrow. Let me know if anything comes up or if you have questions beforehand.",
                "channel": "sms",
            },
        ],
    },
    "renewal_reminder": {
        "name": "Renewal Reminder",
        "description": "Proactive renewal outreach: email 60 days before, SMS 30 days, call task 14 days before renewal.",
        "trigger_type": "manual",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a friendly renewal reminder email. Mention their policy is coming up for renewal and you'd like to review their coverage to make sure they're still well-protected. Keep it casual.",
                "channel": "email",
            },
            {
                "step_type": "wait",
                "delay_minutes": 43200,  # 30 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "sms",
                "delay_minutes": 0,
                "content_template": "Hey {{first_name}}, your policy renewal is coming up soon. Want to grab 15 minutes to review? Might be able to save you some money.",
                "channel": "sms",
            },
            {
                "step_type": "wait",
                "delay_minutes": 23040,  # ~16 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "task",
                "delay_minutes": 0,
                "content_template": "",
                "channel": "",
                "config": {"title": "URGENT: Call {{first_name}} — renewal in 14 days", "due_in_days": 0},
            },
        ],
    },
    "re_engagement": {
        "name": "Gone Cold Re-Engagement",
        "description": "3-touch sequence to re-engage cold prospects who haven't responded in 30+ days.",
        "trigger_type": "manual",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "sms",
                "delay_minutes": 0,
                "content_template": "Hey {{first_name}}, been a while — hope all is well. No pitch, just checking in. Let me know if you ever want to revisit our chat.",
                "channel": "sms",
            },
            {
                "step_type": "wait",
                "delay_minutes": 10080,  # 7 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a re-engagement email. Share a relevant market update or rate change related to their product interest. Position it as 'thought of you when I saw this'. No hard sell.",
                "channel": "email",
            },
            {
                "step_type": "wait",
                "delay_minutes": 10080,  # 7 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "task",
                "delay_minutes": 0,
                "content_template": "",
                "channel": "",
                "config": {"title": "Final re-engagement attempt: call {{first_name}}", "due_in_days": 0},
            },
        ],
    },
    "referral_ask": {
        "name": "Referral Ask",
        "description": "2-touch referral request for happy clients: warm SMS ask, then email with easy referral template.",
        "trigger_type": "manual",
        "trigger_config": {},
        "steps": [
            {
                "step_type": "sms",
                "delay_minutes": 0,
                "content_template": "Hey {{first_name}}, glad everything worked out with your {{product}}. Quick question — know anyone who might benefit from similar coverage? Happy to take good care of them.",
                "channel": "sms",
            },
            {
                "step_type": "wait",
                "delay_minutes": 7200,  # 5 days
                "content_template": "",
                "channel": "",
            },
            {
                "step_type": "email",
                "delay_minutes": 0,
                "content_template": "Write a gentle referral follow-up email. Mention you appreciate their trust and that referrals are how you grow your practice. Make it easy — they can just reply with a name and number.",
                "channel": "email",
            },
        ],
    },
}


def get_templates() -> dict:
    """Return all available sequence templates."""
    return SEQUENCE_TEMPLATES


def create_from_template(tenant_id: int, template_key: str,
                         overrides: dict | None = None) -> dict | None:
    """Create a sequence from a pre-built template.

    Args:
        tenant_id: Owner tenant
        template_key: Key from SEQUENCE_TEMPLATES
        overrides: Optional dict to override template fields (name, description, steps)

    Returns:
        Created sequence dict, or None if template not found.
    """
    template = SEQUENCE_TEMPLATES.get(template_key)
    if not template:
        return None

    name = (overrides or {}).get("name", template["name"])
    description = (overrides or {}).get("description", template["description"])
    steps = (overrides or {}).get("steps", template["steps"])

    return create_sequence(
        tenant_id=tenant_id,
        name=name,
        trigger_type=template["trigger_type"],
        description=description,
        trigger_config=template.get("trigger_config"),
        steps=steps,
    )


def install_default_templates(tenant_id: int) -> list[dict]:
    """Install all default sequence templates for a new tenant.

    Returns list of created sequences.
    """
    created = []
    for key in SEQUENCE_TEMPLATES:
        seq = create_from_template(tenant_id, key)
        if seq:
            created.append(seq)
    logger.info("Installed %d default sequences for tenant %d", len(created), tenant_id)
    return created
