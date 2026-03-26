"""Tests for the referral tracking engine."""
import pytest
from unittest.mock import patch, MagicMock


def test_record_referral_inserts_row(monkeypatch):
    from referral import record_referral
    import db

    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        record_referral(referrer_id=1, referred_id=2, notes="Met at event")
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        assert "referrals" in call_args[0][0]


def test_get_top_referrers_returns_list(monkeypatch):
    from referral import get_top_referrers
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_top_referrers()
        assert isinstance(result, list)


def test_should_send_referral_ask_14_day(monkeypatch):
    from referral import should_send_referral_ask
    from datetime import datetime, timedelta
    closed_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    result = should_send_referral_ask({"closed_date": closed_date, "id": 1}, ask_day=14)
    assert result is True


def test_should_send_referral_ask_too_early():
    from referral import should_send_referral_ask
    from datetime import datetime, timedelta
    closed_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    result = should_send_referral_ask({"closed_date": closed_date, "id": 1}, ask_day=14)
    assert result is False


def test_format_referral_ask_message_contains_name():
    from referral import format_referral_ask_message
    msg = format_referral_ask_message({"name": "Sarah Chen"})
    assert "Sarah Chen" in msg
