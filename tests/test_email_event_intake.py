"""Tests for email event intake and outcome matching."""
import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_email_events"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import analytics


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_record_outcome_with_resend_email_id():
    """record_outcome() should store resend_email_id when provided."""
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Sarah Chen",
        sent_at="2026-03-15",
        resend_email_id="re_abc123xyz",
    )
    assert outcome["resend_email_id"] == "re_abc123xyz"


def test_record_outcome_without_resend_email_id():
    """record_outcome() should work without resend_email_id (backwards compat)."""
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Bob Lee",
        sent_at="2026-03-15",
    )
    assert outcome["resend_email_id"] is None


def test_find_outcome_by_resend_email_id():
    """Should be able to find an outcome by resend_email_id."""
    analytics.record_outcome(
        action_type="follow_up",
        target="Sarah Chen",
        sent_at="2026-03-15",
        resend_email_id="re_findme123",
    )
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM outcomes WHERE resend_email_id = ?",
            ("re_findme123",),
        ).fetchone()
    assert row is not None
    assert row["target"] == "Sarah Chen"


import intake


def test_email_opened_updates_outcome():
    """email.opened event should mark outcome as response_received."""
    db.add_prospect({
        "name": "Sarah Chen",
        "email": "sarah@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Sarah Chen",
        sent_at="2026-03-15",
        resend_email_id="re_open_test",
    )

    result = intake.process_email_event({
        "event_type": "email.opened",
        "email": "sarah@example.com",
        "resend_email_id": "re_open_test",
    })

    updated = analytics.get_outcome(outcome["id"])
    assert updated["response_received"] == 1
    assert "opened" in result.lower()


def test_email_clicked_updates_outcome():
    """email.clicked event should set response_type to 'clicked'."""
    db.add_prospect({
        "name": "Bob Lee",
        "email": "bob@example.com",
        "source": "website",
        "send_channel": "resend",
    })
    outcome = analytics.record_outcome(
        action_type="follow_up",
        target="Bob Lee",
        sent_at="2026-03-15",
        resend_email_id="re_click_test",
    )

    intake.process_email_event({
        "event_type": "email.clicked",
        "email": "bob@example.com",
        "resend_email_id": "re_click_test",
    })

    updated = analytics.get_outcome(outcome["id"])
    assert updated["response_received"] == 1
    assert updated["response_type"] == "clicked"


def test_email_bounced_marks_prospect():
    """email.bounced event should mark prospect and pause nurture."""
    db.add_prospect({
        "name": "Jane Doe",
        "email": "jane@example.com",
        "source": "website",
        "send_channel": "resend",
    })

    result = intake.process_email_event({
        "event_type": "email.bounced",
        "email": "jane@example.com",
    })

    prospect = db.get_prospect_by_email("jane@example.com")
    assert "BOUNCED" in prospect["notes"]
    assert "bounced" in result.lower()


def test_email_complained_stops_outreach():
    """email.complained should set stage to 'Do Not Contact'."""
    db.add_prospect({
        "name": "Spam Reporter",
        "email": "spam@example.com",
        "source": "website",
        "send_channel": "resend",
    })

    intake.process_email_event({
        "event_type": "email.complained",
        "email": "spam@example.com",
    })

    prospect = db.get_prospect_by_email("spam@example.com")
    assert prospect["stage"] == "Do Not Contact"
    assert "COMPLAINT" in prospect["notes"]


def test_email_event_no_match_ignored():
    """Events with no matching outcome should be silently ignored."""
    result = intake.process_email_event({
        "event_type": "email.opened",
        "email": "unknown@example.com",
    })
    assert "ignored" in result.lower() or "no matching" in result.lower()
