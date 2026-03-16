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


def test_dashboard_redirects_to_login(client):
    """GET / without credentials redirects to /login."""
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_login_page_renders(client):
    """GET /login shows login form."""
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Password" in resp.data or b"password" in resp.data


def test_login_with_correct_password(client):
    """POST /login with correct password sets auth cookie."""
    resp = client.post("/login", data={"password": "test-secret-key"})
    assert resp.status_code == 200
    # Verify cookie is set by checking we can now access the dashboard
    dash_resp = client.get("/")
    assert dash_resp.status_code == 200


def test_login_with_wrong_password(client):
    """POST /login with wrong password shows error."""
    resp = client.post("/login", data={"password": "wrong"})
    assert b"Wrong password" in resp.data


def test_dashboard_accessible_after_login(client):
    """After logging in, GET / returns 200."""
    client.post("/login", data={"password": "test-secret-key"})
    resp = client.get("/")
    assert resp.status_code == 200


def test_dashboard_accessible_with_api_key(client):
    """GET / with valid API key returns 200."""
    resp = client.get("/", headers={"X-API-Key": "test-secret-key"})
    assert resp.status_code == 200


def test_dashboard_accessible_with_query_key(client):
    """GET / with valid query param key returns 200."""
    resp = client.get("/?key=test-secret-key")
    assert resp.status_code == 200


def test_chart_labels_use_json_dumps(client):
    """Chart labels must use json.dumps format, not Python str(list)."""
    import db
    db.add_prospect({
        "name": "Test XSS",
        "source": "test</script><script>alert(1)</script>",
        "stage": "New Lead",
        "priority": "Hot",
    })
    resp = client.get("/", headers={"X-API-Key": "test-secret-key"})
    html = resp.data.decode()
    assert "</script><script>alert(1)</script>" not in html
    db.delete_prospect("Test XSS")
