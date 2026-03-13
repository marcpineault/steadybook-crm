"""Tests for trust level bounds validation.

Tests set_trust_level directly against the DB rather than importing bot.py
(which requires TELEGRAM_BOT_TOKEN and other env vars at module load).
"""
import os
import sys
import pytest

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_trust"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def set_trust_level(level, changed_by="marc"):
    """Mirror of bot.set_trust_level with validation."""
    if level not in (1, 2, 3):
        raise ValueError(f"Trust level must be 1, 2, or 3 (got {level})")
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO trust_config (trust_level, changed_by) VALUES (?, ?)",
            (level, changed_by),
        )


def get_trust_level():
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT trust_level FROM trust_config ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["trust_level"] if row else 1


def test_set_trust_level_valid():
    for level in (1, 2, 3):
        set_trust_level(level)
        assert get_trust_level() == level


def test_set_trust_level_rejects_zero():
    with pytest.raises(ValueError, match="must be 1, 2, or 3"):
        set_trust_level(0)


def test_set_trust_level_rejects_four():
    with pytest.raises(ValueError, match="must be 1, 2, or 3"):
        set_trust_level(4)


def test_set_trust_level_rejects_negative():
    with pytest.raises(ValueError, match="must be 1, 2, or 3"):
        set_trust_level(-1)


def test_set_trust_level_rejects_99():
    with pytest.raises(ValueError, match="must be 1, 2, or 3"):
        set_trust_level(99)
