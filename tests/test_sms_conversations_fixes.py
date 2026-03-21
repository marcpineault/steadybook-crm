import sqlite3
import pytest
from unittest.mock import patch, MagicMock
import db
import sms_conversations


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    importlib.reload(sms_conversations)
    db.init_db()
    yield


def test_is_opted_out_uses_column(fresh_db):
    """is_opted_out reads sms_opted_out column, not notes substring."""
    db.add_prospect({"name": "Alice", "phone": "5195550001"})
    with db.get_db() as conn:
        conn.execute("UPDATE prospects SET sms_opted_out = 1 WHERE name = 'Alice'")
    prospect = db.get_prospect_by_name("Alice")
    assert sms_conversations.is_opted_out(prospect) is True


def test_is_opted_out_false_when_zero(fresh_db):
    db.add_prospect({"name": "Bob", "phone": "5195550002"})
    prospect = db.get_prospect_by_name("Bob")
    assert sms_conversations.is_opted_out(prospect) is False


def test_has_replied_since_last_outbound_true_when_inbound_after_outbound(fresh_db):
    """Returns True (should reply) when inbound arrives after our last outbound."""
    phone = "+15195550003"
    sms_conversations.log_message(phone, "Hey!", "outbound")
    sms_conversations.log_message(phone, "Yeah sounds good", "inbound")
    assert sms_conversations.has_replied_since_last_outbound(phone) is True


def test_has_replied_since_last_outbound_false_when_no_reply(fresh_db):
    """Returns False (don't reply) when we've texted but they haven't replied yet."""
    phone = "+15195550004"
    sms_conversations.log_message(phone, "Hey!", "outbound")
    assert sms_conversations.has_replied_since_last_outbound(phone) is False


def test_has_replied_since_last_outbound_true_on_first_message(fresh_db):
    """First inbound with no prior outbound should allow reply."""
    phone = "+15195550005"
    sms_conversations.log_message(phone, "Hi Marc", "inbound")
    assert sms_conversations.has_replied_since_last_outbound(phone) is True


def test_handle_opt_out_sets_column(fresh_db):
    """handle_opt_out sets sms_opted_out=1 via column."""
    db.add_prospect({"name": "Carol", "phone": "5195550006"})
    prospect = db.get_prospect_by_name("Carol")
    sms_conversations.handle_opt_out("+15195550006", prospect_id=prospect["id"])
    updated = db.get_prospect_by_name("Carol")
    assert updated["sms_opted_out"] == 1


def test_handle_opt_out_cancels_by_phone_when_no_prospect_id(fresh_db):
    """Anonymous opt-out still cancels queued booking touches by phone."""
    phone = "+15195550007"
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO booking_nurture_sequences
               (prospect_name, phone, touch_number, scheduled_for, meeting_datetime, meeting_date, meeting_time, status)
               VALUES ('Unknown', ?, 1, datetime('now'), datetime('now'), '2026-04-01', '10:00', 'queued')""",
            (phone,),
        )
    sms_conversations.handle_opt_out(phone, prospect_id=None)
    with db.get_db() as conn:
        remaining = conn.execute(
            "SELECT * FROM booking_nurture_sequences WHERE phone=? AND status='queued'", (phone,)
        ).fetchall()
    assert len(remaining) == 0
