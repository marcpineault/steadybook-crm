"""Compliance filter and audit logging for financial services.

Every AI-generated client-facing message passes through the compliance filter.
Every AI action is logged to the audit trail.
"""

import json
import logging
import os
import re

from openai import OpenAI

import db

logger = logging.getLogger(__name__)

openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

COMPLIANCE_PROMPT = """You are a compliance reviewer for a Canadian financial advisor (insurance and wealth management at Co-operators).

Review the following message that will be sent to a client or posted publicly. Check for:
1. Promises of specific returns or guaranteed outcomes
2. Misleading claims about products or coverage
3. Missing disclaimers where they would be required
4. Sharing of other clients' personal information
5. Unprofessional tone inappropriate for financial services
6. Any language that could be construed as financial advice without proper qualification

MESSAGE:
{message}

Respond with JSON only:
{{"passed": true/false, "issues": ["issue description 1", "issue description 2"]}}

If the message is compliant, return: {{"passed": true, "issues": []}}"""


def check_compliance(message):
    """Run compliance check on a message. Returns {"passed": bool, "issues": [str]}.

    On API failure, returns failed with explanation (fail-safe: never send unchecked).
    """
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": COMPLIANCE_PROMPT.replace("{message}", message)}],
            max_completion_tokens=512,
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return {
            "passed": result.get("passed", False),
            "issues": result.get("issues", []),
        }
    except Exception as e:
        logger.exception("Compliance check failed")
        return {
            "passed": False,
            "issues": ["Compliance check could not complete due to a system error. Please retry."],
        }


def log_action(action_type, target, content, compliance_check=None, approved_by=None, outcome=None):
    """Log an AI action to the audit trail. Returns the created log entry."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO audit_log (action_type, target, content, compliance_check, approved_by, outcome)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (action_type, target, content, compliance_check, approved_by, outcome),
        )
        row = conn.execute("SELECT * FROM audit_log WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError(f"audit_log insert succeeded but row {cursor.lastrowid} not found")
        return dict(row)


def get_audit_log(action_type=None, target=None, limit=50):
    """Get audit log entries, optionally filtered."""
    with db.get_db() as conn:
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        if action_type:
            query += " AND action_type = ?"
            params.append(action_type)
        if target:
            query += " AND target = ?"
            params.append(target)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def update_audit_outcome(log_id, outcome, approved_by=None):
    """Update the outcome and approval status of an audit log entry."""
    with db.get_db() as conn:
        if approved_by:
            cursor = conn.execute(
                "UPDATE audit_log SET outcome = ?, approved_by = ? WHERE id = ?",
                (outcome, approved_by, log_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE audit_log SET outcome = ? WHERE id = ?",
                (outcome, log_id),
            )
        if cursor.rowcount == 0:
            raise ValueError(f"audit_log entry {log_id} not found")
