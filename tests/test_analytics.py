import os
import sys
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_analytics"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import analytics


_ANALYTICS_DATA_DIR = "/tmp/test_calm_bot_analytics"
_ANALYTICS_DB_PATH = os.path.join(_ANALYTICS_DATA_DIR, "pipeline.db")


def setup_function():
    # Force db module to use the analytics test DB regardless of env overrides
    os.environ["DATA_DIR"] = _ANALYTICS_DATA_DIR
    db.DB_PATH = _ANALYTICS_DB_PATH
    if os.path.exists(_ANALYTICS_DB_PATH):
        os.remove(_ANALYTICS_DB_PATH)
    db.init_db()


def _seed_outcomes():
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type, converted) "
            "VALUES ('email_draft', 'Alice', '2026-03-07', 1, 'positive', 1)"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type) "
            "VALUES ('email_draft', 'Bob', '2026-03-08', 1, 'neutral')"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received) "
            "VALUES ('email_draft', 'Carol', '2026-03-09', 0)"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type) "
            "VALUES ('content_post', 'linkedin', '2026-03-06', 1, 'positive')"
        )
        conn.execute(
            "INSERT INTO outcomes (action_type, target, sent_at, response_received) "
            "VALUES ('content_post', 'facebook', '2026-03-07', 0)"
        )


def test_record_outcome():
    outcome = analytics.record_outcome(
        action_type="email_draft",
        target="Dave",
        sent_at="2026-03-10",
    )
    assert outcome is not None
    assert outcome["id"] > 0
    assert outcome["response_received"] == 0


def test_update_outcome_response():
    outcome = analytics.record_outcome(
        action_type="email_draft",
        target="Eve",
        sent_at="2026-03-10",
    )
    updated = analytics.update_outcome(
        outcome["id"],
        response_received=True,
        response_type="positive",
        converted=True,
    )
    assert updated["response_received"] == 1
    assert updated["response_type"] == "positive"
    assert updated["converted"] == 1


def test_get_weekly_stats():
    _seed_outcomes()
    stats = analytics.get_weekly_stats(reference_date="2026-03-13")
    assert stats["total_actions"] >= 5
    assert stats["response_rate"] > 0
    assert "by_type" in stats


def test_get_weekly_stats_by_type():
    _seed_outcomes()
    stats = analytics.get_weekly_stats(reference_date="2026-03-13")
    assert "email_draft" in stats["by_type"]
    assert stats["by_type"]["email_draft"]["total"] >= 3
    assert stats["by_type"]["email_draft"]["responses"] >= 2


def test_get_outcome_by_id():
    outcome = analytics.record_outcome(
        action_type="campaign", target="Test", sent_at="2026-03-10"
    )
    fetched = analytics.get_outcome(outcome["id"])
    assert fetched is not None
    assert fetched["target"] == "Test"


def test_get_outcome_not_found():
    result = analytics.get_outcome(9999)
    assert result is None


def test_get_recent_outcomes():
    _seed_outcomes()
    recent = analytics.get_recent_outcomes(limit=3)
    assert len(recent) <= 3


@patch("analytics.openai_client")
def test_generate_insights(mock_client):
    _seed_outcomes()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "INSIGHTS:\n- Email response rate was 67%\n- Content on LinkedIn outperformed Facebook"
    mock_client.chat.completions.create.return_value = mock_response

    insights = analytics.generate_insights(reference_date="2026-03-13")
    assert insights is not None
    assert len(insights) > 0


def test_get_learning_context():
    _seed_outcomes()
    context = analytics.get_learning_context(reference_date="2026-03-13")
    assert isinstance(context, str)
    assert "email_draft" in context or "response" in context.lower()


def test_get_learning_context_empty():
    context = analytics.get_learning_context(reference_date="2026-03-13")
    assert context == ""
