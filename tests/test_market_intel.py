import os
import sys
from datetime import datetime, timedelta

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_market_intel"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import market_intel


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_seed_default_calendar():
    market_intel.seed_default_calendar()
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
    assert count > 0


def test_seed_idempotent():
    market_intel.seed_default_calendar()
    market_intel.seed_default_calendar()
    with db.get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
    # Should not double-insert
    first_count = count
    market_intel.seed_default_calendar()
    with db.get_db() as conn:
        count2 = conn.execute("SELECT COUNT(*) FROM market_calendar").fetchone()[0]
    assert count2 == first_count


def test_get_upcoming_events():
    market_intel.seed_default_calendar()
    # Get events in the next 30 days
    events = market_intel.get_upcoming_events(days_ahead=365)
    assert isinstance(events, list)
    # Should have at least some events in a full year window
    assert len(events) > 0


def test_get_upcoming_events_empty_range():
    # No events seeded
    events = market_intel.get_upcoming_events(days_ahead=7)
    assert events == []


def test_get_seasonal_context():
    ctx = market_intel.get_seasonal_context()
    assert isinstance(ctx, str)
    assert len(ctx) > 0


def test_get_content_angles():
    market_intel.seed_default_calendar()
    angles = market_intel.get_content_angles(days_ahead=365)
    assert isinstance(angles, list)


def test_add_custom_event():
    market_intel.add_event(
        event_type="product_update",
        title="New disability product launch",
        date="2026-04-01",
        description="Co-operators launching enhanced disability coverage",
        relevance_products="Disability Insurance",
    )
    with db.get_db() as conn:
        rows = conn.execute("SELECT * FROM market_calendar WHERE event_type = 'product_update'").fetchall()
    assert len(rows) == 1
    assert "disability" in rows[0]["title"].lower()
