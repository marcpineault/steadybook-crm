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
