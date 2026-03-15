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
