"""Integration tests for auth routes — requires DATABASE_URL."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping auth integration tests"
)


@pytest.fixture
def client():
    import db
    db.init_db()
    import dashboard
    dashboard.app.config["TESTING"] = True
    dashboard.app.config["SECRET_KEY"] = "test-secret-key-for-testing"
    with dashboard.app.test_client() as c:
        yield c


def _unique_email(base="test"):
    import time
    return f"{base}_{int(time.time() * 1000)}@testbroker.com"


def test_register_creates_tenant_and_user(client):
    resp = client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage",
        "name": "Alice Smith",
        "email": _unique_email("alice"),
        "password": "Secure123!"
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "tenant_id" in data


def test_login_returns_session(client):
    email = _unique_email("bob")
    client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage 2",
        "name": "Bob Jones",
        "email": email,
        "password": "Secure123!"
    })
    resp = client.post("/api/auth/login", json={
        "email": email,
        "password": "Secure123!"
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_login_wrong_password_fails(client):
    email = _unique_email("charlie")
    client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage 3",
        "name": "Charlie Brown",
        "email": email,
        "password": "Secure123!"
    })
    resp = client.post("/api/auth/login", json={
        "email": email,
        "password": "wrongpassword"
    })
    assert resp.status_code == 401


def test_me_returns_user_when_logged_in(client):
    email = _unique_email("carol")
    client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage 4",
        "name": "Carol Lee",
        "email": email,
        "password": "Secure123!"
    })
    client.post("/api/auth/login", json={
        "email": email,
        "password": "Secure123!"
    })
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["email"] == email
    assert data["role"] == "owner"


def test_no_tenants_redirects_to_register(client):
    """First-boot: if no tenants exist, any request should redirect to /register."""
    import db
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM users")
        cur.execute("DELETE FROM tenants")
    resp = client.get("/")
    assert resp.status_code in (302, 303)
    location = resp.headers.get("Location", "")
    assert "/register" in location
