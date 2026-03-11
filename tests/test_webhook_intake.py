import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("INTAKE_WEBHOOK_SECRET", "test-secret-123")
os.environ.setdefault("CLOUDMAILIN_SECRET", "test-cloudmailin-secret")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db
from webhook_intake import intake_bp
from flask import Flask


def create_test_app():
    app = Flask(__name__)
    app.register_blueprint(intake_bp)
    return app


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_webhook_rejects_missing_auth():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post("/api/intake", json={"type": "booking", "data": {"name": "Test"}})
        assert resp.status_code == 401


def test_webhook_rejects_bad_secret():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={"type": "booking", "data": {"name": "Test"}},
            headers={"X-Webhook-Secret": "wrong-secret"},
        )
        assert resp.status_code == 401


def test_webhook_accepts_valid_booking():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={
                "type": "booking",
                "data": {
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "service": "Life Insurance Consultation",
                    "start_time": "2026-03-20T10:00:00",
                },
            },
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "Jane Doe" in body["message"]


def test_webhook_rejects_unknown_type():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            json={"type": "unknown", "data": {}},
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status_code == 400


def test_webhook_rejects_missing_payload():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/intake",
            data="not json",
            content_type="text/plain",
            headers={"X-Webhook-Secret": "test-secret-123"},
        )
        assert resp.status_code == 400


def test_email_inbound_rejects_bad_secret():
    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/email-inbound?secret=wrong",
            json={"headers": {"Subject": "Test"}, "plain": "hello"},
        )
        assert resp.status_code == 401


def test_email_inbound_processes_cloudmailin_payload(monkeypatch):
    import intake

    class MockMessage:
        content = '{"name": "Bob Lee", "phone": "", "email": "bob@example.com", "product": "Home Insurance", "notes": "Wants home insurance quote", "priority": "Warm", "source": "Email Lead", "stage": "New Lead"}'

    class MockChoice:
        message = MockMessage()

    class MockResponse:
        choices = [MockChoice()]

    class MockCompletions:
        def create(self, **kwargs):
            return MockResponse()

    class MockChat:
        completions = MockCompletions()

    class MockClient:
        chat = MockChat()

    monkeypatch.setattr(intake, "client", MockClient())

    app = create_test_app()
    with app.test_client() as c:
        resp = c.post(
            "/api/email-inbound?secret=test-cloudmailin-secret",
            json={
                "envelope": {"from": "marc@cooperators.ca", "to": "abc@cloudmailin.net"},
                "headers": {
                    "Subject": "Meeting with Bob Lee - Home Insurance",
                    "From": "Marc Pineault <marc@cooperators.ca>",
                    "To": "abc@cloudmailin.net",
                },
                "plain": "Just booked a meeting with Bob Lee (bob@example.com) about home insurance. March 25 at 2pm.",
                "html": "",
                "attachments": [],
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True
        assert "Bob Lee" in body["message"]
        prospect = db.get_prospect_by_name("Bob Lee")
        assert prospect is not None
