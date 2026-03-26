"""Tests for the social intake webhook."""
import os
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("DASHBOARD_API_KEY", "test-dashboard-key")

import pytest
import hashlib
import hmac
import json
from unittest.mock import patch, MagicMock

from dashboard import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def make_signature(body: bytes, secret: str = "test-secret") -> str:
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_social_intake_missing_signature(client):
    payload = json.dumps({"type": "instagram_dm", "tenant_id": 1, "data": {"name": "Test"}}).encode()
    with patch("social_intake.WEBHOOK_SECRET", "test-secret"):
        resp = client.post("/api/social-intake", data=payload, content_type="application/json")
    assert resp.status_code == 401


def test_social_intake_invalid_signature(client):
    payload = json.dumps({"type": "instagram_dm", "tenant_id": 1, "data": {"name": "Test"}}).encode()
    with patch("social_intake.WEBHOOK_SECRET", "test-secret"):
        resp = client.post(
            "/api/social-intake",
            data=payload,
            content_type="application/json",
            headers={"X-SteadyBook-Signature": "sha256=invalidsig"}
        )
    assert resp.status_code == 401


def test_social_intake_valid_instagram_dm(client):
    payload_dict = {"type": "instagram_dm", "tenant_id": 1, "data": {"name": "Sarah Chen", "message": "Hi"}}
    payload = json.dumps(payload_dict).encode()
    sig = make_signature(payload)
    with patch("social_intake.WEBHOOK_SECRET", "test-secret"), \
         patch("social_intake.process_intake_event", MagicMock()):
        resp = client.post(
            "/api/social-intake",
            data=payload,
            content_type="application/json",
            headers={"X-SteadyBook-Signature": sig}
        )
    assert resp.status_code == 200


def test_social_intake_missing_name_returns_400(client):
    payload_dict = {"type": "instagram_dm", "tenant_id": 1, "data": {"message": "Hi"}}
    payload = json.dumps(payload_dict).encode()
    sig = make_signature(payload)
    with patch("social_intake.WEBHOOK_SECRET", "test-secret"), \
         patch("social_intake.process_intake_event", MagicMock()):
        resp = client.post(
            "/api/social-intake",
            data=payload,
            content_type="application/json",
            headers={"X-SteadyBook-Signature": sig}
        )
    assert resp.status_code == 400
