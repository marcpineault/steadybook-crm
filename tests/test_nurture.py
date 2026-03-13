import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_nurture"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import nurture


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect():
    db.add_prospect({
        "name": "Sarah Chen", "stage": "New Lead", "priority": "Warm",
        "product": "Life Insurance", "email": "sarah@example.com",
        "notes": "Referred by existing client. Has two kids.",
    })
    with db.get_db() as conn:
        return conn.execute("SELECT id FROM prospects WHERE name = 'Sarah Chen'").fetchone()[0]


def test_create_sequence():
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    assert seq is not None
    assert seq["status"] == "active"
    assert seq["total_touches"] == 4
    assert seq["current_touch"] == 0


def test_create_sequence_no_duplicate():
    pid = _seed_prospect()
    seq1 = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    seq2 = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    # Should return existing active sequence, not create duplicate
    assert seq2["id"] == seq1["id"]


def test_get_active_sequences():
    pid = _seed_prospect()
    nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    active = nurture.get_active_sequences()
    assert len(active) >= 1


def test_get_due_touches():
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    # Set next_touch_date to yesterday so it's due
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE nurture_sequences SET next_touch_date = ? WHERE id = ?",
            (yesterday, seq["id"]),
        )
    due = nurture.get_due_touches()
    assert len(due) >= 1


@patch("nurture.openai_client")
@patch("nurture.compliance")
def test_generate_touch(mock_compliance, mock_client):
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Sarah, I came across an article about life insurance for young families that I thought you might find helpful."
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    touch = nurture.generate_touch(seq["id"])
    assert touch is not None
    assert touch["touch_number"] == 1
    updated = nurture.get_sequence(seq["id"])
    assert updated["current_touch"] == 1
    assert "content" in touch


def test_complete_sequence():
    pid = _seed_prospect()
    seq = nurture.create_sequence(prospect_name="Sarah Chen", prospect_id=pid)
    nurture.complete_sequence(seq["id"], reason="booked_meeting")
    updated = nurture.get_sequence(seq["id"])
    assert updated["status"] == "completed"


def test_get_sequence_not_found():
    result = nurture.get_sequence(9999)
    assert result is None
