"""Tests for Twilio SMS sender module."""
import importlib
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_send_sms_success():
    """send_sms() should call Twilio and return the message SID."""
    import sms_sender

    with patch.dict(os.environ, {
        "TWILIO_ACCOUNT_SID": "ACtest123",
        "TWILIO_AUTH_TOKEN": "authtoken123",
        "TWILIO_FROM_NUMBER": "+15190001111",
    }):
        importlib.reload(sms_sender)

        mock_message = MagicMock()
        mock_message.sid = "SM_test_sid_abc123"

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        with patch("sms_sender.Client", return_value=mock_client):
            result = sms_sender.send_sms(to="5198001234", body="Hey John, looking forward to our call!")

    assert result == "SM_test_sid_abc123"
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["to"] == "+15198001234"
    assert call_kwargs["from_"] == "+15190001111"
    assert "John" in call_kwargs["body"]


def test_send_sms_api_failure():
    """send_sms() should return None on Twilio error."""
    import sms_sender

    with patch.dict(os.environ, {
        "TWILIO_ACCOUNT_SID": "ACtest123",
        "TWILIO_AUTH_TOKEN": "authtoken123",
        "TWILIO_FROM_NUMBER": "+15190001111",
    }):
        importlib.reload(sms_sender)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("Twilio error")

        with patch("sms_sender.Client", return_value=mock_client):
            result = sms_sender.send_sms(to="5198001234", body="Test")

    assert result is None


def test_send_sms_no_credentials():
    """send_sms() should return None without making a call if credentials are missing."""
    import sms_sender

    with patch.dict(os.environ, {
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_FROM_NUMBER": "",
    }):
        importlib.reload(sms_sender)

        with patch("sms_sender.Client") as mock_client_class:
            result = sms_sender.send_sms(to="5198001234", body="Test")
            mock_client_class.assert_not_called()

    assert result is None


def test_normalize_phone_10_digit():
    from sms_sender import _normalize_phone
    assert _normalize_phone("5198001234") == "+15198001234"


def test_normalize_phone_11_digit():
    from sms_sender import _normalize_phone
    assert _normalize_phone("15198001234") == "+15198001234"


def test_normalize_phone_already_e164():
    from sms_sender import _normalize_phone
    assert _normalize_phone("+15198001234") == "+15198001234"


def test_normalize_phone_formatted():
    from sms_sender import _normalize_phone
    assert _normalize_phone("(519) 800-1234") == "+15198001234"
