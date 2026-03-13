import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_content_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_brand_voice_table_exists():
    with db.get_db() as conn:
        conn.execute("SELECT id, platform, content, post_type, created_at FROM brand_voice LIMIT 1")


def test_market_calendar_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, event_type, title, date, description, relevance_products, recurring FROM market_calendar LIMIT 1"
        )


def test_brand_voice_insert_and_read():
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            ("linkedin", "Great chat with a young couple about protecting their growing family.", "story"),
        )
        rows = conn.execute("SELECT * FROM brand_voice WHERE platform = 'linkedin'").fetchall()
        assert len(rows) == 1
        assert rows[0]["post_type"] == "story"


def test_market_calendar_insert_and_read():
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO market_calendar (event_type, title, date, description, relevance_products, recurring)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("rate_decision", "BoC Rate Decision", "2026-04-15", "Bank of Canada interest rate announcement", "Home Insurance,Wealth Management", 0),
        )
        rows = conn.execute("SELECT * FROM market_calendar WHERE event_type = 'rate_decision'").fetchall()
        assert len(rows) == 1
        assert "BoC" in rows[0]["title"]
