import pytest
from unittest.mock import patch, MagicMock
import db
import sms_agent


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    importlib.reload(sms_agent)
    db.init_db()
    yield


def test_get_active_agent_returns_none_when_none(fresh_db):
    assert sms_agent.get_active_agent("+15195550001") is None


def test_get_active_agent_finds_active(fresh_db):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO sms_agents (phone, prospect_name, objective, status) VALUES (?, ?, ?, ?)",
            ("+15195550002", "John Smith", "book a discovery call", "active"),
        )
    result = sms_agent.get_active_agent("+15195550002")
    assert result is not None
    assert result["prospect_name"] == "John Smith"
    assert result["status"] == "active"


def test_get_active_agent_ignores_completed(fresh_db):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO sms_agents (phone, prospect_name, objective, status) VALUES (?, ?, ?, ?)",
            ("+15195550003", "Jane Doe", "book a call", "success"),
        )
    assert sms_agent.get_active_agent("+15195550003") is None


def test_classify_mission_status_returns_valid_status(fresh_db):
    thread = [
        {"direction": "outbound", "body": "Hey John, want to connect?"},
        {"direction": "inbound", "body": "Sure, sounds good"},
    ]
    with patch("sms_agent.openai_client") as mock_client:
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="ongoing"))]
        )
        status = sms_agent.classify_mission_status(thread, "book a discovery call")
    assert status in ("ongoing", "success", "cold", "needs_marc")


def test_complete_mission_updates_status(fresh_db):
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO sms_agents (phone, prospect_name, objective, status) VALUES (?, ?, ?, ?)",
            ("+15195550004", "Bob Jones", "book a call", "active"),
        )
        agent_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    with patch("sms_agent._notify_telegram"), \
         patch("sms_agent.memory_engine"), \
         patch("sms_agent.db.add_activity"):
        sms_agent.complete_mission(agent_id, "success", [], "Bob Jones", None)

    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM sms_agents WHERE id = ?", (agent_id,)).fetchone()
    assert row["status"] == "success"
    assert row["completed_at"] is not None
