"""Tests for send_channel routing in approval flow.

Since bot.py requires TELEGRAM_BOT_TOKEN at import time, we test
the routing logic extracted from the approval flow.
"""
import os
import sys
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_routing"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _get_send_channel_for_prospect(prospect_name: str) -> str:
    """Mirror of the routing logic in bot.py approval flow."""
    prospect = db.get_prospect_by_name(prospect_name)
    if prospect and prospect.get("send_channel") == "resend":
        return "resend"
    return "outlook"


def test_website_lead_routes_to_resend():
    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    assert _get_send_channel_for_prospect("Sarah Chen") == "resend"


def test_outlook_lead_routes_to_outlook():
    db.add_prospect({
        "name": "Bob Lee",
        "email": "bob@example.com",
        "source": "Outlook Booking",
    })
    assert _get_send_channel_for_prospect("Bob Lee") == "outlook"


def test_unknown_prospect_defaults_to_outlook():
    assert _get_send_channel_for_prospect("Nobody") == "outlook"


@patch("resend_sender.send_email")
def test_resend_send_email_called_for_resend_channel(mock_send):
    """When channel is resend, send_email should be called."""
    import resend_sender

    mock_send.return_value = "re_abc123"

    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })

    prospect = db.get_prospect_by_name("Sarah Chen")
    if prospect and prospect.get("send_channel") == "resend" and prospect.get("email"):
        result = resend_sender.send_email(
            to=prospect["email"],
            subject="Following up",
            body="Hi Sarah, thanks for reaching out.",
        )
        assert result == "re_abc123"
        mock_send.assert_called_once()
