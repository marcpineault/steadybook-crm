import os
import sys

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_outreach_schema"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_trust_config_table_exists():
    with db.get_db() as conn:
        conn.execute("SELECT id, trust_level, changed_at, changed_by FROM trust_config LIMIT 1")


def test_campaigns_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, name, description, segment_query, status, channel, wave_count, created_at FROM campaigns LIMIT 1"
        )


def test_campaign_messages_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, campaign_id, prospect_name, content, status, queue_id, wave, created_at FROM campaign_messages LIMIT 1"
        )


def test_nurture_sequences_table_exists():
    with db.get_db() as conn:
        conn.execute(
            "SELECT id, prospect_id, prospect_name, status, current_touch, total_touches, next_touch_date, created_at FROM nurture_sequences LIMIT 1"
        )


def test_trust_config_default():
    with db.get_db() as conn:
        row = conn.execute("SELECT trust_level FROM trust_config ORDER BY id DESC LIMIT 1").fetchone()
    # Should have a default row with level 1
    assert row is not None
    assert row["trust_level"] == 1


def test_init_db_idempotent_trust_seed():
    db.init_db()  # second call on existing DB
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM trust_config").fetchone()[0]
    assert count == 1
