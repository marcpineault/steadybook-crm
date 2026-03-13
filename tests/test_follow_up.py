import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_followup"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import follow_up


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect():
    db.add_prospect({
        "name": "Sarah Chen", "stage": "Discovery Call", "priority": "Hot",
        "product": "Life Insurance", "revenue": "5000", "email": "sarah@example.com",
    })
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE name = 'Sarah Chen'").fetchone()
        return row[0]


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_draft(mock_compliance, mock_client):
    pid = _seed_prospect()
    db.add_activity({
        "prospect": "Sarah Chen", "action": "Discovery call",
        "outcome": "Discussed life insurance needs, husband runs landscaping biz",
    })

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "Hi Sarah,\n\nGreat speaking with you today about protecting your family's income. "
        "I'll put together some options for term life coverage that factor in your husband's "
        "landscaping business.\n\nI'll have something ready for you by Thursday.\n\nBest,\nMarc"
    )
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    draft = follow_up.generate_follow_up_draft(
        prospect_name="Sarah Chen",
        activity_summary="Discovery call — discussed life insurance, husband runs landscaping",
        activity_type="Discovery call",
    )

    assert draft is not None
    assert draft["prospect_name"] == "Sarah Chen"
    assert "Sarah" in draft["content"]
    assert draft["compliance_passed"] is True
    assert draft["queue_id"] is not None


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_compliance_fail(mock_compliance, mock_client):
    pid = _seed_prospect()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "I guarantee you 8% returns!"
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {
        "passed": False, "issues": ["Contains return guarantee"],
    }

    draft = follow_up.generate_follow_up_draft(
        prospect_name="Sarah Chen",
        activity_summary="Called about investments",
        activity_type="Phone call",
    )

    assert draft is not None
    assert draft["compliance_passed"] is False
    assert len(draft["compliance_issues"]) > 0
    assert draft["queue_id"] is not None


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_no_prospect(mock_compliance, mock_client):
    draft = follow_up.generate_follow_up_draft(
        prospect_name="Nonexistent Person",
        activity_summary="Called",
        activity_type="Phone call",
    )
    assert draft is None


@patch("follow_up.openai_client")
@patch("follow_up.compliance")
def test_generate_follow_up_stores_in_approval_queue(mock_compliance, mock_client):
    pid = _seed_prospect()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Sarah, thanks for chatting today."
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    draft = follow_up.generate_follow_up_draft(
        prospect_name="Sarah Chen",
        activity_summary="Quick check-in call",
        activity_type="Phone call",
    )

    import approval_queue
    pending = approval_queue.get_pending_drafts(draft_type="follow_up")
    assert len(pending) == 1
    assert pending[0]["content"] == draft["content"]


def test_get_stale_drafts():
    import approval_queue
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO approval_queue (type, channel, content, context, status, created_at, prospect_id)
               VALUES (?, ?, ?, ?, 'pending', datetime('now', '-5 hours'), NULL)""",
            ("follow_up", "email_draft", "Old draft content", "test context"),
        )
    stale = follow_up.get_stale_drafts(max_age_hours=4)
    assert len(stale) >= 1


def test_get_stale_drafts_none():
    stale = follow_up.get_stale_drafts(max_age_hours=4)
    assert len(stale) == 0
