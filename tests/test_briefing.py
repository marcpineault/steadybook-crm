import os
import sys
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_briefing"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import briefing


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    db.DB_PATH = db_path
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_data():
    """Create test prospects, activities, and tasks."""
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    overdue = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    db.add_prospect({
        "name": "Sarah Chen", "stage": "Discovery Call", "priority": "Hot",
        "revenue": "5000", "aum": "200000", "next_followup": today,
    })
    db.add_prospect({
        "name": "Mike Johnson", "stage": "Needs Analysis", "priority": "Warm",
        "revenue": "3000", "next_followup": overdue,
    })
    db.add_prospect({
        "name": "Lisa Park", "stage": "Closed-Won", "priority": "Hot",
        "revenue": "8000",
    })
    db.add_activity({
        "date": yesterday, "prospect": "Sarah Chen",
        "action": "Phone call", "outcome": "Booked discovery call",
    })
    db.add_task({
        "title": "Send brochure to Mike", "prospect": "Mike Johnson",
        "due_date": overdue, "assigned_to": "123", "created_by": "123",
    })
    db.add_task({
        "title": "Prep for Sarah meeting", "prospect": "Sarah Chen",
        "due_date": today, "assigned_to": "123", "created_by": "123",
    })
    db.add_meeting({
        "date": today, "time": "14:00", "prospect": "Sarah Chen",
        "type": "Discovery Call",
    })


def test_assemble_briefing_data():
    _seed_data()
    data = briefing.assemble_briefing_data()
    assert "prospects" in data
    assert "activities_recent" in data
    assert "tasks_due_today" in data
    assert "tasks_overdue" in data
    assert "meetings_today" in data
    assert "pipeline_stats" in data
    assert "call_list" in data


def test_pipeline_stats():
    _seed_data()
    data = briefing.assemble_briefing_data()
    stats = data["pipeline_stats"]
    assert stats["active_count"] == 2  # excludes Closed-Won
    assert stats["total_revenue"] > 0
    assert "weighted_forecast" in stats


def test_pipeline_stats_empty():
    data = briefing.assemble_briefing_data()
    stats = data["pipeline_stats"]
    assert stats["active_count"] == 0


def test_call_list_ranked():
    _seed_data()
    data = briefing.assemble_briefing_data()
    assert len(data["call_list"]) > 0
    # Should be ranked by score descending
    if len(data["call_list"]) > 1:
        assert data["call_list"][0]["score"] >= data["call_list"][1]["score"]


from unittest.mock import patch, MagicMock


@patch("briefing.openai_client")
def test_generate_briefing_text(mock_client):
    _seed_data()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Good morning Marc! Pipeline health: 75/100..."
    mock_client.chat.completions.create.return_value = mock_response

    text = briefing.generate_briefing_text()
    assert "Pipeline" in text or "pipeline" in text or "Marc" in text


@patch("briefing.openai_client")
def test_generate_briefing_text_api_failure(mock_client):
    _seed_data()
    mock_client.chat.completions.create.side_effect = Exception("API down")
    text = briefing.generate_briefing_text()
    # Should fall back to simple format
    assert text is not None
    assert len(text) > 0
