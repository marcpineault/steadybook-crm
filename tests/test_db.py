"""Integration tests for db.py — requires DATABASE_URL env var pointing to a test Postgres DB."""
import os
import pytest


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping Postgres integration tests"
)
def test_get_db_connects():
    import db
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 AS val")
        row = cur.fetchone()
    assert row["val"] == 1


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping Postgres integration tests"
)
def test_init_db_creates_tables():
    import db
    db.init_db()
    with db.get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        tables = {r["table_name"] for r in cur.fetchall()}
    assert "prospects" in tables
    assert "tenants" in tables
    assert "tenant_config" in tables
    assert "users" in tables


def test_tenant_context_var():
    import db
    db._current_tenant_id.set(42)
    try:
        assert db._current_tenant_id.get() == 42
    finally:
        db._current_tenant_id.set(1)
