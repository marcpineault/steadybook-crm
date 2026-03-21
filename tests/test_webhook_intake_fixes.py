import pytest
from unittest.mock import patch, MagicMock
import os


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("INTAKE_WEBHOOK_SECRET", "test-secret")
    import importlib
    import db
    importlib.reload(db)
    db.init_db()
    from webhook_intake import intake_bp
    from flask import Flask
    flask_app = Flask(__name__)
    flask_app.register_blueprint(intake_bp)
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_sms_reply_rejects_invalid_twilio_signature(app):
    """Requests without valid X-Twilio-Signature are rejected with 403."""
    with patch("webhook_intake.RequestValidator") as mock_rv:
        mock_rv.return_value.validate.return_value = False
        resp = app.post(
            "/api/sms-reply",
            data={"From": "+15195551234", "Body": "Hey", "MessageSid": "SM123"},
            headers={"X-Twilio-Signature": "invalid"},
        )
    assert resp.status_code == 403


def test_sms_reply_accepts_valid_signature_and_processes(app):
    """Valid signature is accepted and processed."""
    with patch("webhook_intake.RequestValidator") as mock_rv, \
         patch("webhook_intake._db") as mock_db, \
         patch("webhook_intake.sms_conversations") as mock_sms:
        mock_rv.return_value.validate.return_value = True
        mock_db.get_prospect_by_phone.return_value = None
        mock_sms.OPT_OUT_KEYWORDS = set()
        mock_sms.is_opted_out.return_value = False
        mock_sms.log_message.return_value = 1
        mock_sms.get_recent_thread.return_value = [{"direction": "inbound", "body": "Hi"}]
        mock_sms.generate_reply.return_value = None
        resp = app.post(
            "/api/sms-reply",
            data={"From": "+15195551234", "Body": "Hey", "MessageSid": "SM123"},
            headers={"X-Twilio-Signature": "valid"},
        )
    assert resp.status_code == 204


def test_sms_reply_unknown_number_no_prior_thread_does_not_auto_reply(app):
    """Inbound from unknown number with no prior thread is not auto-replied."""
    with patch("webhook_intake.RequestValidator") as mock_rv, \
         patch("webhook_intake._db") as mock_db, \
         patch("webhook_intake.sms_conversations") as mock_sms, \
         patch("webhook_intake._notify_telegram") as mock_notify:
        mock_rv.return_value.validate.return_value = True
        mock_db.get_prospect_by_phone.return_value = None
        mock_sms.OPT_OUT_KEYWORDS = set()
        mock_sms.is_opted_out.return_value = False
        mock_sms.log_message.return_value = 1
        mock_sms.get_recent_thread.return_value = []  # no prior thread
        resp = app.post(
            "/api/sms-reply",
            data={"From": "+15199999999", "Body": "Who is this?", "MessageSid": "SM999"},
            headers={"X-Twilio-Signature": "valid"},
        )
    mock_sms.generate_reply.assert_not_called()
    mock_notify.assert_called_once()
    assert resp.status_code == 204
