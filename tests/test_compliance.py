import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_compliance"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import compliance


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    # Also truncate audit_log in the active DB (in case DATA_DIR was set before db import)
    if os.path.exists(db.DB_PATH):
        with db.get_db() as conn:
            conn.execute("DELETE FROM audit_log")
    db.init_db()


def test_log_action():
    entry = compliance.log_action(
        action_type="email_draft",
        target="Sarah Chen",
        content="Hi Sarah, following up on our meeting...",
    )
    assert entry["id"] is not None
    assert entry["action_type"] == "email_draft"
    assert entry["target"] == "Sarah Chen"


def test_log_action_with_compliance_result():
    entry = compliance.log_action(
        action_type="email_draft",
        target="Mike Johnson",
        content="Your returns are guaranteed at 8%!",
        compliance_check="FAIL: contains return guarantee",
    )
    assert "FAIL" in entry["compliance_check"]


def test_get_audit_log():
    compliance.log_action("email_draft", "Person A", "content A")
    compliance.log_action("content_generated", "LinkedIn", "post content")
    compliance.log_action("prospect_updated", "Person B", "stage changed")
    log = compliance.get_audit_log(limit=10)
    assert len(log) == 3


def test_get_audit_log_by_type():
    compliance.log_action("email_draft", "Person A", "content")
    compliance.log_action("content_generated", "LinkedIn", "post")
    log = compliance.get_audit_log(action_type="email_draft")
    assert len(log) == 1


def test_update_audit_outcome():
    entry = compliance.log_action("email_draft", "Sarah", "content")
    compliance.update_audit_outcome(entry["id"], outcome="sent", approved_by="marc")
    updated = compliance.get_audit_log(limit=1)[0]
    assert updated["outcome"] == "sent"
    assert updated["approved_by"] == "marc"


@patch("compliance.openai_client")
def test_check_compliance_pass(mock_client):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "passed": True,
        "issues": [],
    })
    mock_client.chat.completions.create.return_value = mock_response

    result = compliance.check_compliance("Hi Sarah, great talking today. Let's schedule a follow-up next week.")
    assert result["passed"] is True
    assert result["issues"] == []


@patch("compliance.openai_client")
def test_check_compliance_fail(mock_client):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "passed": False,
        "issues": ["Contains guarantee of returns"],
    })
    mock_client.chat.completions.create.return_value = mock_response

    result = compliance.check_compliance("I guarantee you'll see 8% returns on this investment!")
    assert result["passed"] is False
    assert len(result["issues"]) > 0


@patch("compliance.openai_client")
def test_check_compliance_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    result = compliance.check_compliance("Some message")
    assert result["passed"] is False
    assert "system error" in result["issues"][0]
