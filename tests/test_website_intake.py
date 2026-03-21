"""Tests for website lead intake — DB layer."""
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_website"
os.environ.setdefault("INTAKE_WEBHOOK_SECRET", "test-secret-123")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    db.DB_PATH = db_path  # ensure module-level path matches DATA_DIR
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_add_prospect_with_send_channel():
    db.add_prospect({
        "name": "Alice Website",
        "email": "alice@example.com",
        "send_channel": "resend",
    })
    prospect = db.get_prospect_by_name("Alice Website")
    assert prospect is not None
    assert prospect["send_channel"] == "resend"


def test_add_prospect_defaults_send_channel_to_outlook():
    db.add_prospect({
        "name": "Bob Default",
        "email": "bob@example.com",
    })
    prospect = db.get_prospect_by_name("Bob Default")
    assert prospect is not None
    assert prospect["send_channel"] == "outlook"


def test_get_prospect_by_email():
    db.add_prospect({
        "name": "Carol Email",
        "email": "carol@example.com",
        "send_channel": "resend",
    })
    prospect = db.get_prospect_by_email("carol@example.com")
    assert prospect is not None
    assert prospect["name"] == "Carol Email"


def test_get_prospect_by_email_case_insensitive():
    db.add_prospect({
        "name": "Dave Case",
        "email": "Dave@Example.COM",
    })
    prospect = db.get_prospect_by_email("dave@example.com")
    assert prospect is not None
    assert prospect["name"] == "Dave Case"


def test_get_prospect_by_email_returns_none():
    result = db.get_prospect_by_email("nobody@nowhere.com")
    assert result is None


def test_update_prospect_send_channel():
    db.add_prospect({
        "name": "Eve Update",
        "email": "eve@example.com",
        "send_channel": "outlook",
    })
    db.update_prospect("Eve Update", {"send_channel": "resend"})
    prospect = db.get_prospect_by_name("Eve Update")
    assert prospect is not None
    assert prospect["send_channel"] == "resend"


from unittest.mock import patch, MagicMock
import intake


def test_process_website_contact_creates_hot_prospect():
    db.add_prospect  # ensure db is imported
    result = intake.process_website_contact({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "phone": "519-555-0100",
        "service": "Life Insurance",
        "message": "I'd like to learn about life insurance options.",
    })
    assert "Sarah Chen" in result
    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect is not None
    assert prospect["priority"] == "Hot"
    assert prospect["send_channel"] == "resend"
    assert prospect["source"] == "website"
    assert prospect["product"] == "Life Insurance"


def test_process_website_contact_dedup_updates_existing():
    # Create a prospect via quiz first (placeholder name)
    db.add_prospect({
        "name": "sarah",
        "email": "sarah@example.com",
        "source": "website",
        "priority": "Warm",
        "send_channel": "resend",
    })
    result = intake.process_website_contact({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "phone": "519-555-0100",
        "service": "Life Insurance",
        "message": "Following up.",
    })
    prospect = db.get_prospect_by_email("sarah@example.com")
    assert prospect["name"] == "Sarah Chen"  # Name upgraded
    assert prospect["priority"] == "Hot"     # Priority bumped


def test_process_website_quiz_creates_warm_prospect():
    result = intake.process_website_quiz({
        "email": "bob@example.com",
        "score": 72,
        "answers": [
            {"questionId": 1, "optionLabel": "No plan", "points": 5},
            {"questionId": 2, "optionLabel": "Some savings", "points": 15},
        ],
        "tier": "Needs Attention",
    })
    prospect = db.get_prospect_by_email("bob@example.com")
    assert prospect is not None
    assert prospect["priority"] == "Warm"
    assert prospect["send_channel"] == "resend"
    assert prospect["name"] == "bob"
    assert "72" in prospect["notes"]


def test_process_website_tool_creates_cool_prospect():
    result = intake.process_website_tool({
        "email": "jane@example.com",
        "toolName": "Life Insurance Calculator",
    })
    prospect = db.get_prospect_by_email("jane@example.com")
    assert prospect is not None
    assert prospect["priority"] == "Cool"
    assert prospect["send_channel"] == "resend"
    assert prospect["name"] == "jane"
    assert "Life Insurance Calculator" in prospect["notes"]


def test_process_website_contact_no_email_still_works():
    result = intake.process_website_contact({
        "name": "Marc Test",
        "email": "",
        "phone": "519-555-0000",
        "service": "Auto Insurance",
        "message": "Quick question.",
    })
    prospect = db.get_prospect_by_name("Marc Test")
    assert prospect is not None
    assert prospect["send_channel"] == "resend"


from flask import Flask
from webhook_intake import intake_bp


def _create_app():
    app = Flask(__name__)
    app.register_blueprint(intake_bp)
    return app


def test_webhook_website_contact_integration():
    """Full webhook → intake flow for website_contact."""
    app = _create_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "website_contact",
                "data": {
                    "name": "Integration Test",
                    "email": "integration@example.com",
                    "phone": "519-555-9999",
                    "service": "Home Insurance",
                    "message": "Testing the integration.",
                },
            },
            headers={"X-Webhook-Secret": os.environ.get("INTAKE_WEBHOOK_SECRET", "test-secret-123")},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        prospect = db.get_prospect_by_email("integration@example.com")
        assert prospect is not None
        assert prospect["send_channel"] == "resend"


def test_webhook_website_quiz_integration():
    """Full webhook → intake flow for website_quiz."""
    app = _create_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "website_quiz",
                "data": {
                    "email": "quiz@example.com",
                    "score": 65,
                    "answers": [{"questionId": 1, "optionLabel": "Somewhat", "points": 12}],
                    "tier": "Fair",
                },
            },
            headers={"X-Webhook-Secret": os.environ.get("INTAKE_WEBHOOK_SECRET", "test-secret-123")},
        )
        assert resp.status_code == 200


def test_webhook_website_tool_integration():
    """Full webhook → intake flow for website_tool."""
    app = _create_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "website_tool",
                "data": {
                    "email": "tool@example.com",
                    "toolName": "Life Insurance Calculator",
                },
            },
            headers={"X-Webhook-Secret": os.environ.get("INTAKE_WEBHOOK_SECRET", "test-secret-123")},
        )
        assert resp.status_code == 200
