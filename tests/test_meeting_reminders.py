"""Tests for the meeting confirmation sequence (no-show killer)."""
import importlib
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

import pytest
import db
import meeting_reminders


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACfake")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15195550000")
    importlib.reload(db)
    importlib.reload(meeting_reminders)
    db.init_db()
    meeting_reminders._ensure_table()
    # Set trust level to 2 so reminders send directly (not queued) for testing
    with db.get_db() as conn:
        conn.execute("INSERT INTO trust_config (trust_level, changed_by) VALUES (2, 'test')")
    yield


def _add_prospect_with_phone(name, phone):
    db.add_prospect({"name": name, "phone": phone, "stage": "New Lead"})


def _add_meeting(prospect, meeting_date, time="10:00", status="Scheduled"):
    db.add_meeting({
        "date": meeting_date,
        "time": time,
        "prospect": prospect,
        "type": "Discovery Call",
        "status": status,
    })


class TestDayBeforeReminder:
    def test_sends_day_before(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("John Smith", "+15191234567")
        _add_meeting("John Smith", tomorrow, time="2:00 PM")

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM123") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 1
        assert sent[0]["type"] == "day_before"
        assert sent[0]["prospect"] == "John Smith"
        assert "tomorrow" in sent[0]["message"].lower()
        assert "2:00 PM" in sent[0]["message"]
        assert "Marc" in sent[0]["message"]
        mock_send.assert_called_once()

    def test_no_duplicate_day_before(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("John Smith", "+15191234567")
        _add_meeting("John Smith", tomorrow)

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM123"):
            sent1 = meeting_reminders.send_meeting_reminders(today=today)
            sent2 = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent1) == 1
        assert len(sent2) == 0  # duplicate blocked


class TestMorningOfReminder:
    def test_sends_morning_of(self):
        today = date(2026, 3, 23)
        today_str = today.strftime("%Y-%m-%d")
        _add_prospect_with_phone("Sarah Jones", "+15199876543")
        _add_meeting("Sarah Jones", today_str, time="11:00 AM")

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM456"):
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 1
        assert sent[0]["type"] == "morning_of"
        assert "today" in sent[0]["message"].lower()
        assert "11:00 AM" in sent[0]["message"]


class TestSafetyGuards:
    def test_skips_cancelled_meeting(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Mike Chen", "+15195551111")
        _add_meeting("Mike Chen", tomorrow, status="Cancelled")

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_completed_meeting(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Mike Chen", "+15195551111")
        _add_meeting("Mike Chen", tomorrow, status="Completed")

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_no_phone(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        db.add_prospect({"name": "No Phone Guy", "stage": "New Lead"})
        _add_meeting("No Phone Guy", tomorrow)

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_no_prospect_name(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_meeting("", tomorrow)

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_opted_out_prospect(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Opted Out", "+15195552222")
        # Mark as opted out
        with db.get_db() as conn:
            conn.execute("UPDATE prospects SET sms_opted_out = 1 WHERE name = 'Opted Out'")
        _add_meeting("Opted Out", tomorrow)

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_recently_contacted(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Recent Contact", "+15195553333")
        _add_meeting("Recent Contact", tomorrow)

        with patch("meeting_reminders.sms_conversations.was_recently_contacted", return_value=True), \
             patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_past_meetings(self):
        today = date(2026, 3, 23)
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Past Meeting", "+15195554444")
        _add_meeting("Past Meeting", yesterday)

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_skips_future_meetings(self):
        today = date(2026, 3, 23)
        next_week = (today + timedelta(days=5)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Future Meeting", "+15195555555")
        _add_meeting("Future Meeting", next_week)

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0
        mock_send.assert_not_called()

    def test_handles_sms_failure_gracefully(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("SMS Fail", "+15195556666")
        _add_meeting("SMS Fail", tomorrow)

        with patch("meeting_reminders.sms_sender.send_sms", return_value=None):
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 0  # Not counted as sent


class TestMultipleMeetings:
    def test_sends_for_multiple_meetings(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Alice", "+15195551001")
        _add_prospect_with_phone("Bob", "+15195551002")
        _add_meeting("Alice", tomorrow, time="10:00")
        _add_meeting("Bob", tomorrow, time="2:00")

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM789"):
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 2
        names = {s["prospect"] for s in sent}
        assert names == {"Alice", "Bob"}

    def test_both_day_before_and_morning_of(self):
        today = date(2026, 3, 23)
        today_str = today.strftime("%Y-%m-%d")
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Alice", "+15195551001")
        _add_prospect_with_phone("Bob", "+15195551002")
        _add_meeting("Alice", tomorrow, time="10:00")  # day before
        _add_meeting("Bob", today_str, time="2:00")     # morning of

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM999"):
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 2
        types = {s["type"] for s in sent}
        assert types == {"day_before", "morning_of"}


class TestMessageContent:
    def test_first_name_only(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("John Michael Smith", "+15195551234")
        _add_meeting("John Michael Smith", tomorrow, time="3:00 PM")

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM111"):
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert "John" in sent[0]["message"]
        assert "Smith" not in sent[0]["message"]

    def test_no_time_still_works(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Jane Doe", "+15195559999")
        _add_meeting("Jane Doe", tomorrow, time="")

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM222"):
            sent = meeting_reminders.send_meeting_reminders(today=today)

        assert len(sent) == 1
        assert "tomorrow" in sent[0]["message"].lower()


class TestReminderStats:
    def test_stats_empty(self):
        stats = meeting_reminders.get_reminder_stats()
        assert stats["total_sent"] == 0
        assert stats["sent_today"] == 0

    def test_stats_after_send(self):
        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Stats Test", "+15195557777")
        _add_meeting("Stats Test", tomorrow)

        with patch("meeting_reminders.sms_sender.send_sms", return_value="SM333"):
            meeting_reminders.send_meeting_reminders(today=today)

        stats = meeting_reminders.get_reminder_stats()
        assert stats["total_sent"] == 1


class TestApprovalQueueFlow:
    """Test that at trust level 1, reminders go through approval queue."""

    def test_queues_for_approval_at_trust_1(self):
        """At trust level 1, reminders should be queued, not sent directly."""
        # Set trust to 1
        with db.get_db() as conn:
            conn.execute("DELETE FROM trust_config")
            conn.execute("INSERT INTO trust_config (trust_level, changed_by) VALUES (1, 'test')")

        today = date(2026, 3, 23)
        tomorrow = (today + timedelta(days=1)).strftime("%Y-%m-%d")
        _add_prospect_with_phone("Queue Test", "+15195558888")
        _add_meeting("Queue Test", tomorrow, time="3:00 PM")

        with patch("meeting_reminders.sms_sender.send_sms") as mock_send:
            sent = meeting_reminders.send_meeting_reminders(today=today)

        # Should NOT have called send_sms directly
        mock_send.assert_not_called()
        # Should have queued
        assert len(sent) == 1
        assert sent[0].get("queued_for_approval") is True
        assert sent[0].get("queue_id") is not None
