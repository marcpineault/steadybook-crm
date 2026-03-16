import os
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("DASHBOARD_API_KEY", "test-secret-key")

import pytest
from dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_dashboard_requires_auth(client):
    """GET / without credentials returns 401."""
    resp = client.get("/")
    assert resp.status_code == 401


def test_dashboard_accessible_with_api_key(client):
    """GET / with valid API key returns 200."""
    resp = client.get("/", headers={"X-API-Key": "test-secret-key"})
    assert resp.status_code == 200


def test_dashboard_accessible_with_query_key(client):
    """GET / with valid query param key returns 200."""
    resp = client.get("/?key=test-secret-key")
    assert resp.status_code == 200
