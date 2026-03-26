"""
Tag-based trigger engine.
When a tag is applied to a prospect, this module determines what actions to take.

Trigger rules are defined in TRIGGER_RULES below.
Action types:
  - create_task: creates a task assigned to the prospect
  - enroll_sequence: enrolls prospect in a named follow-up sequence
  - schedule_crosssell: schedules cross-sell evaluation (deferred to cross_sell.py)
  - apply_tag: applies another tag (recursive — use carefully)
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

# ── Trigger Rules ─────────────────────────────────────────────────────────────
# Each tag maps to a list of actions.
# {{name}} in strings is replaced with the prospect's name at runtime.

TRIGGER_RULES: dict[str, list[dict]] = {
    "new_lead": [
        {
            "type": "create_task",
            "subject": "Follow up with {{name}}",
            "description": "New lead — reach out within 48 hours",
            "due_days": 2,
        }
    ],
    "source_qr": [
        {
            "type": "create_task",
            "subject": "QR lead — contact {{name}}",
            "description": "They scanned your QR code at an event. Reach out while interest is fresh.",
            "due_days": 1,
        }
    ],
    "source_card": [
        {
            "type": "create_task",
            "subject": "Business card — follow up with {{name}}",
            "description": "Met at an event. Follow up within 24 hours.",
            "due_days": 1,
        }
    ],
    "booked_meeting": [
        {
            "type": "create_task",
            "subject": "Prepare for meeting with {{name}}",
            "description": "Review their profile and prepare agenda before the meeting.",
            "due_days": 0,
        }
    ],
    "closed_life": [
        {
            "type": "enroll_sequence",
            "sequence_name": "life_onboarding",
        },
        {
            "type": "schedule_crosssell",
            "product": "disability",
        }
    ],
    "closed_disability": [
        {
            "type": "enroll_sequence",
            "sequence_name": "disability_onboarding",
        },
        {
            "type": "schedule_crosssell",
            "product": "critical_illness",
        }
    ],
    "closed_group_benefits": [
        {
            "type": "create_task",
            "subject": "Schedule annual review with {{name}}",
            "description": "Annual group benefits review — schedule 12 months out.",
            "due_days": 365,
        }
    ],
    "referral_given": [
        {
            "type": "create_task",
            "subject": "Thank {{name}} for referral",
            "description": "Send thank-you within 24 hours of receiving a referral.",
            "due_days": 1,
        }
    ],
}


def get_trigger_actions(tag: str) -> list[dict]:
    """Return the list of actions for a given tag. Returns [] if no rules defined."""
    return TRIGGER_RULES.get(tag, [])


def _execute_action(prospect: dict, action: dict) -> None:
    """Execute a single action for a prospect."""
    prospect_id = prospect["id"]
    name = prospect.get("name", "")
    action_type = action["type"]

    def sub(text: str) -> str:
        return text.replace("{{name}}", name)

    if action_type == "create_task":
        due_date = (datetime.now() + timedelta(days=action.get("due_days", 1))).strftime("%Y-%m-%d")
        try:
            db.add_task(
                {
                    "title": sub(action.get("subject", "Follow up")),
                    "prospect": name,
                    "due_date": due_date,
                    "notes": sub(action.get("description", "")),
                    "created_by": "tag_engine",
                },
                tenant_id=prospect.get("tenant_id", 1),
            )
        except Exception as e:
            logger.error("create_task failed for prospect %d: %s", prospect_id, e)

    elif action_type == "enroll_sequence":
        sequence_name = action.get("sequence_name", "")
        logger.info("Enrolling prospect %d in sequence '%s'", prospect_id, sequence_name)
        # Sequence enrollment is handled by the sequences module
        # Tag triggers it; sequences.py picks it up

    elif action_type == "schedule_crosssell":
        product = action.get("product", "")
        logger.info("Scheduling cross-sell for prospect %d: %s", prospect_id, product)
        # cross_sell.py handles this when called directly

    elif action_type == "apply_tag":
        new_tag = action.get("tag", "")
        if new_tag and new_tag != "do_not_contact":
            db.apply_tag(prospect_id, new_tag)

    else:
        logger.warning("Unknown action type '%s' for prospect %d", action_type, prospect_id)


def process_tag(prospect: dict, tag: str) -> None:
    """
    Process all trigger actions for a tag applied to a prospect.
    Skips all actions if the prospect has a 'do_not_contact' tag.
    """
    prospect_id = prospect["id"]

    # Safety: never trigger automations on do_not_contact prospects
    current_tags = db.get_tags(prospect_id)
    if "do_not_contact" in current_tags:
        logger.info("Skipping triggers for prospect %d (do_not_contact)", prospect_id)
        return

    actions = get_trigger_actions(tag)
    for action in actions:
        _execute_action(prospect, action)


def process_tags_for_prospect(prospect: dict) -> None:
    """Re-process all current tags for a prospect. Used after bulk tag operations."""
    tags = db.get_tags(prospect["id"])
    for tag in tags:
        process_tag(prospect, tag)
