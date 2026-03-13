import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_meetprep"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import meeting_prep


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospect_and_meeting():
    db.add_prospect({
        "name": "Sarah Chen", "stage": "Discovery Call", "priority": "Hot",
        "product": "Life Insurance", "revenue": "5000", "email": "sarah@example.com",
        "notes": "Husband runs landscaping. Two kids.",
    })
    today = datetime.now().strftime("%Y-%m-%d")
    db.add_meeting({
        "date": today, "time": "14:00", "prospect": "Sarah Chen",
        "type": "Discovery Call",
    })
    db.add_activity({
        "date": today, "prospect": "Sarah Chen",
        "action": "Phone call", "outcome": "Booked discovery call, excited about coverage options",
    })
    db.add_interaction({
        "prospect": "Sarah Chen", "source": "phone_call",
        "raw_text": "Sarah called to ask about life insurance. Husband runs landscaping business in Byron.",
        "summary": "Initial inquiry about life insurance",
    })
    with db.get_db() as conn:
        return conn.execute("SELECT id FROM prospects WHERE name = 'Sarah Chen'").fetchone()[0]


def test_assemble_prep_context():
    pid = _seed_prospect_and_meeting()
    ctx = meeting_prep.assemble_prep_context("Sarah Chen", "Discovery Call")
    assert ctx["prospect"]["name"] == "Sarah Chen"
    assert ctx["stage"] == "Discovery Call"
    assert "interactions" in ctx
    assert "activities" in ctx
    assert "memory_profile" in ctx
    assert "score_data" in ctx


def test_assemble_prep_context_no_prospect():
    ctx = meeting_prep.assemble_prep_context("Nobody", "Discovery Call")
    assert ctx is None


@patch("meeting_prep.openai_client")
def test_generate_prep_doc(mock_client):
    pid = _seed_prospect_and_meeting()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        "MEETING PREP: Sarah Chen — Discovery Call\n\n"
        "CLIENT SNAPSHOT:\nHusband runs landscaping in Byron. Two kids.\n\n"
        "RECOMMENDED AGENDA:\n1. Review life insurance needs\n2. Discuss term vs whole life\n\n"
        "TALKING POINTS:\n- Ask about the landscaping business\n- Term life for mortgage protection"
    )
    mock_client.chat.completions.create.return_value = mock_response

    doc = meeting_prep.generate_prep_doc("Sarah Chen", "Discovery Call", "14:00")
    assert doc is not None
    assert "Sarah Chen" in doc
    assert len(doc) > 50


@patch("meeting_prep.openai_client")
def test_generate_prep_doc_api_failure(mock_client):
    pid = _seed_prospect_and_meeting()
    mock_client.chat.completions.create.side_effect = Exception("API down")

    doc = meeting_prep.generate_prep_doc("Sarah Chen", "Discovery Call", "14:00")
    # Should fall back to simple format
    assert doc is not None
    assert "Sarah Chen" in doc


def test_get_upcoming_meetings():
    _seed_prospect_and_meeting()
    today = datetime.now().strftime("%Y-%m-%d")
    meetings = meeting_prep.get_meetings_needing_prep(today)
    assert len(meetings) >= 1
