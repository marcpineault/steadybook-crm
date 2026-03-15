"""Tests for Resend email sender module."""
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_send_email_success():
    """send_email() should POST to Resend API and return the email ID."""
    import importlib
    import resend_sender

    with patch.dict(os.environ, {
        "RESEND_API_KEY": "re_test_key",
        "RESEND_FROM_EMAIL": "marc@info.calmmoney.ca",
        "RESEND_REPLY_TO": "mpineault1@gmail.com",
    }):
        importlib.reload(resend_sender)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "re_abc123xyz"}

        with patch("resend_sender.requests") as mock_requests:
            mock_requests.post.return_value = mock_response

            result = resend_sender.send_email(
                to="sarah@example.com",
                subject="Following up on our chat",
                body="Hi Sarah, great talking with you about life insurance.",
            )

    assert result == "re_abc123xyz"
    mock_requests.post.assert_called_once()
    call_kwargs = mock_requests.post.call_args
    payload = call_kwargs[1]["json"] if call_kwargs[1] else call_kwargs[0][1]
    assert payload["to"] == ["sarah@example.com"]
    assert payload["subject"] == "Following up on our chat"


def test_send_email_api_failure():
    """send_email() should return None on API error."""
    import importlib
    import resend_sender

    with patch.dict(os.environ, {
        "RESEND_API_KEY": "re_test_key",
        "RESEND_FROM_EMAIL": "marc@info.calmmoney.ca",
        "RESEND_REPLY_TO": "mpineault1@gmail.com",
    }):
        importlib.reload(resend_sender)

        with patch("resend_sender.requests") as mock_requests:
            mock_requests.post.side_effect = Exception("Network error")

            result = resend_sender.send_email(
                to="sarah@example.com",
                subject="Test",
                body="Test body",
            )
    assert result is None


@patch.dict(os.environ, {"RESEND_API_KEY": ""})
def test_send_email_no_api_key():
    """send_email() should return None if RESEND_API_KEY is not set."""
    import importlib
    import resend_sender
    importlib.reload(resend_sender)

    result = resend_sender.send_email(
        to="sarah@example.com",
        subject="Test",
        body="Test body",
    )
    assert result is None
