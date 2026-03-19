"""Tests for Sendblue SMS sender module."""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_send_sms_success():
    """send_sms() should POST to Sendblue API with correct headers and return message_handle."""
    import importlib
    import sms_sender

    with patch.dict(os.environ, {
        "SENDBLUE_API_KEY": "sb_test_key",
        "SENDBLUE_API_SECRET": "sb_test_secret",
    }):
        importlib.reload(sms_sender)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"message_handle": "msg_abc123"}

        with patch("sms_sender.requests") as mock_requests:
            mock_requests.post.return_value = mock_response

            result = sms_sender.send_sms(
                to="5198001234",
                body="Hi, just following up on our conversation.",
            )

    assert result == "msg_abc123"
    mock_requests.post.assert_called_once()
    call_kwargs = mock_requests.post.call_args
    headers = call_kwargs[1]["headers"] if call_kwargs[1] else call_kwargs[0][1]
    assert headers["sb-api-key-id"] == "sb_test_key"
    assert headers["sb-api-secret-key"] == "sb_test_secret"
    payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][2]
    assert payload["number"] == "+15198001234"


def test_send_sms_api_failure():
    """send_sms() should return None on API error."""
    import importlib
    import sms_sender

    with patch.dict(os.environ, {
        "SENDBLUE_API_KEY": "sb_test_key",
        "SENDBLUE_API_SECRET": "sb_test_secret",
    }):
        importlib.reload(sms_sender)

        with patch("sms_sender.requests") as mock_requests:
            mock_requests.post.side_effect = Exception("Network error")

            result = sms_sender.send_sms(
                to="5198001234",
                body="Test body",
            )
    assert result is None


@patch.dict(os.environ, {"SENDBLUE_API_KEY": ""})
def test_send_sms_no_credentials():
    """send_sms() should return None if SENDBLUE_API_KEY is not set."""
    import importlib
    import sms_sender
    importlib.reload(sms_sender)

    with patch("sms_sender.requests") as mock_requests:
        result = sms_sender.send_sms(
            to="5198001234",
            body="Test body",
        )
    assert result is None
    mock_requests.post.assert_not_called()


def test_normalize_phone_10_digit():
    """10-digit number should get +1 prefix."""
    from sms_sender import _normalize_phone
    assert _normalize_phone("5198001234") == "+15198001234"


def test_normalize_phone_11_digit():
    """11-digit number starting with 1 should get + prefix."""
    from sms_sender import _normalize_phone
    assert _normalize_phone("15198001234") == "+15198001234"


def test_normalize_phone_already_e164():
    """+1XXXXXXXXXX should remain unchanged."""
    from sms_sender import _normalize_phone
    assert _normalize_phone("+15198001234") == "+15198001234"


def test_normalize_phone_formatted():
    """Formatted number like (519) 800-1234 should normalize to +15198001234."""
    from sms_sender import _normalize_phone
    assert _normalize_phone("(519) 800-1234") == "+15198001234"
