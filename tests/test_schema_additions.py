import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_client_memory_table_exists():
    with db.get_db() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='client_memory'"
        )
        assert cursor.fetchone() is not None


def test_client_memory_columns():
    with db.get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(client_memory)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {"id", "prospect_id", "category", "fact", "source", "needs_review", "extracted_at"}
        assert expected == columns


def test_approval_queue_table_exists():
    with db.get_db() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='approval_queue'"
        )
        assert cursor.fetchone() is not None


def test_approval_queue_columns():
    with db.get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(approval_queue)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "type", "prospect_id", "channel", "content", "context",
            "status", "created_at", "acted_on_at", "telegram_message_id", "tenant_id",
        }
        assert expected == columns


def test_audit_log_table_exists():
    with db.get_db() as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        )
        assert cursor.fetchone() is not None


def test_audit_log_columns():
    with db.get_db() as conn:
        cursor = conn.execute("PRAGMA table_info(audit_log)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "timestamp", "action_type", "target", "content",
            "compliance_check", "approved_by", "outcome", "tenant_id",
        }
        assert expected == columns
