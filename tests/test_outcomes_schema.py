import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_outcomes_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_outcomes_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, action_id, action_type, target, sent_at, response_received, "
            "response_at, response_type, converted, notes, created_at FROM outcomes LIMIT 1"
        )


def test_outcomes_insert_and_read():
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO outcomes (action_type, target, sent_at)
               VALUES ('email_draft', 'Alice Johnson', '2026-03-10')"""
        )
        row = conn.execute("SELECT * FROM outcomes WHERE target = 'Alice Johnson'").fetchone()
    assert row is not None
    assert row["action_type"] == "email_draft"
    assert row["response_received"] == 0
    assert row["converted"] == 0


def test_outcomes_response_tracking():
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO outcomes (action_type, target, sent_at, response_received, response_type, converted)
               VALUES ('follow_up', 'Bob Smith', '2026-03-08', 1, 'positive', 1)"""
        )
        row = conn.execute("SELECT * FROM outcomes WHERE target = 'Bob Smith'").fetchone()
    assert row["response_received"] == 1
    assert row["response_type"] == "positive"
    assert row["converted"] == 1
