import os
import sys
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_sms_conv"
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import db
import sms_conversations


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def _seed_prospect(phone="5198001234"):
    db.add_prospect({
        "name": "Jane Smith", "phone": phone,
        "stage": "New Lead", "priority": "Warm",
    })
    with db.get_db() as conn:
        return dict(conn.execute(
            "SELECT * FROM prospects WHERE name = 'Jane Smith'"
        ).fetchone())


def test_log_message_inbound():
    row_id = sms_conversations.log_message(
        phone="+15198001234", body="Hi Marc, is Tuesday still good?",
        direction="inbound", twilio_sid="SM123abc",
    )
    assert row_id is not None
    with db.get_db() as conn:
        row = dict(conn.execute("SELECT * FROM sms_conversations WHERE id = ?", (row_id,)).fetchone())
    assert row["direction"] == "inbound"
    assert row["body"] == "Hi Marc, is Tuesday still good?"
    assert row["twilio_sid"] == "SM123abc"


def test_log_message_outbound():
    row_id = sms_conversations.log_message(
        phone="+15198001234", body="Hey Jane, yes Tuesday works! - Marc",
        direction="outbound", prospect_id=1, twilio_sid="SM456def",
    )
    with db.get_db() as conn:
        row = dict(conn.execute("SELECT * FROM sms_conversations WHERE id = ?", (row_id,)).fetchone())
    assert row["direction"] == "outbound"
    assert row["prospect_id"] == 1


def test_get_recent_thread_ordered():
    sms_conversations.log_message("+15551111111", "First", "inbound")
    sms_conversations.log_message("+15551111111", "Second", "outbound")
    sms_conversations.log_message("+15551111111", "Third", "inbound")
    thread = sms_conversations.get_recent_thread("+15551111111", limit=10)
    assert len(thread) == 3
    assert thread[0]["body"] == "First"
    assert thread[2]["body"] == "Third"


def test_get_recent_thread_limit():
    for i in range(15):
        sms_conversations.log_message("+15552222222", f"Msg {i}", "inbound")
    thread = sms_conversations.get_recent_thread("+15552222222", limit=5)
    assert len(thread) == 5
    assert thread[-1]["body"] == "Msg 14"


def test_get_recent_thread_empty():
    assert sms_conversations.get_recent_thread("+19999999999") == []


@patch("sms_conversations.openai_client")
def test_generate_reply_queues_draft(mock_openai):
    prospect = _seed_prospect("+15198001234")
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Hey Jane, Tuesday at 10 works!"))]
    )
    with patch("sms_sender.send_sms", return_value="SM_test_sid") as mock_send, \
         patch("random.randint", return_value=0), \
         patch("time.sleep"):
        result = sms_conversations.generate_reply(
            phone="+15198001234", inbound_body="Is Tuesday at 10 still good?", prospect=prospect,
        )
        import time as _t; _t.sleep(0.1)  # let background thread fire
    assert result is not None
    mock_send.assert_called_once()
    assert "+15198001234" in str(mock_send.call_args)


@patch("sms_conversations.openai_client")
def test_generate_reply_unknown_prospect(mock_openai):
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Hey, happy to chat"))]
    )
    with patch("sms_sender.send_sms", return_value="SM_test_sid_2") as mock_send, \
         patch("random.randint", return_value=0), \
         patch("time.sleep"):
        result = sms_conversations.generate_reply(
            phone="+19995550000", inbound_body="Hey is this Marc?", prospect=None,
        )
        import time as _t; _t.sleep(0.1)
    assert result is not None
    mock_send.assert_called_once()


@patch("sms_conversations.openai_client")
def test_generate_reply_no_client_memory(mock_openai):
    """No memory on file — should still auto-reply without crashing."""
    prospect = _seed_prospect("+15198005678")
    mock_openai.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Hey Jane, sure thing"))]
    )
    with patch("sms_conversations.memory_engine") as mock_mem:
        mock_mem.get_profile_summary_text.return_value = ""
        with patch("sms_sender.send_sms", return_value="SM_test_sid_3"):
            result = sms_conversations.generate_reply(
                phone="+15198005678", inbound_body="Quick question about my policy",
                prospect=prospect,
            )
    assert result is not None
    # GPT was still called even without memory
    mock_openai.chat.completions.create.assert_called_once()


@patch("sms_conversations.openai_client")
def test_generate_reply_openai_failure_returns_none(mock_openai):
    mock_openai.chat.completions.create.side_effect = Exception("API error")
    result = sms_conversations.generate_reply(phone="+15198001234", inbound_body="hello", prospect=None)
    assert result is None


def test_sms_reply_webhook_returns_204():
    os.environ.setdefault("INTAKE_WEBHOOK_SECRET", "test-secret")
    from webhook_intake import intake_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(intake_bp)

    with patch("sms_conversations.openai_client") as mock_ai:
        mock_ai.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Sure! - Marc"))]
        )
        with app.test_client() as c:
            resp = c.post("/api/sms-reply", data={
                "From": "+15198001234", "Body": "Is the meeting still on?",
                "MessageSid": "SMtest123", "To": "+15195550000",
            })
    assert resp.status_code == 204


def test_sms_reply_webhook_missing_from_returns_400():
    from webhook_intake import intake_bp
    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(intake_bp)

    with app.test_client() as c:
        resp = c.post("/api/sms-reply", data={"Body": "Hello", "MessageSid": "SM000"})
    assert resp.status_code == 400
