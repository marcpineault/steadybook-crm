import pytest
import db


def test_normalize_phone_strips_formatting():
    assert db.normalize_phone("+1-519-555-1234") == "5195551234"


def test_normalize_phone_strips_country_code():
    assert db.normalize_phone("+15195551234") == "5195551234"


def test_normalize_phone_already_10_digits():
    assert db.normalize_phone("5195551234") == "5195551234"


def test_normalize_phone_handles_spaces():
    assert db.normalize_phone("519 555 1234") == "5195551234"


def test_normalize_phone_with_ones_in_number():
    """Must NOT strip internal 1s — only strip down to last 10 digits."""
    # +1-519-111-1234 → last 10 digits = 5191111234
    assert db.normalize_phone("+15191111234") == "5191111234"


def test_normalize_phone_empty():
    assert db.normalize_phone("") == ""


def test_get_prospect_by_phone_finds_by_last_10(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    db.init_db()
    db.add_prospect({"name": "John Smith", "phone": "+1-519-555-1234"})

    result = db.get_prospect_by_phone("+15195551234")
    assert result is not None
    assert result["name"] == "John Smith"


def test_get_prospect_by_phone_returns_none_when_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    db.init_db()

    result = db.get_prospect_by_phone("+15199999999")
    assert result is None


def test_get_prospect_by_phone_with_ones_in_number(tmp_path, monkeypatch):
    """Stored as 519-111-1234, lookup with +15191111234 — must find correct record."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    db.init_db()
    db.add_prospect({"name": "Jane Doe", "phone": "519-111-1234"})

    result = db.get_prospect_by_phone("+15191111234")
    assert result is not None
    assert result["name"] == "Jane Doe"


def test_sms_opted_out_column_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    db.init_db()
    with db.get_db() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()]
    assert "sms_opted_out" in cols


def test_sms_opted_out_backfill(tmp_path, monkeypatch):
    """Prospects with [SMS_OPTED_OUT] in notes get sms_opted_out=1 after migration."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    db.init_db()
    # Manually insert a prospect with old-style opt-out in notes
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO prospects (name, notes) VALUES (?, ?)",
            ("Old Prospect", "Some notes [SMS_OPTED_OUT] more notes"),
        )
    # Re-run migration (idempotent)
    db._migrate_sms_agent()
    result = db.get_prospect_by_name("Old Prospect")
    assert result["sms_opted_out"] == 1


def test_sms_agents_table_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import importlib
    importlib.reload(db)
    db.init_db()
    with db.get_db() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "sms_agents" in tables
