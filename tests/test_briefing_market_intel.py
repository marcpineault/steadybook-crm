"""Tests for market intelligence integration in morning briefing."""

import os
import sys
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_briefing_market"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    db.DB_PATH = db_path
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def test_assemble_briefing_data_includes_market_events():
    """assemble_briefing_data() should include market_events key."""
    import briefing
    data = briefing.assemble_briefing_data()
    assert "market_events" in data


def test_assemble_briefing_data_market_events_is_string():
    """market_events should be a string (possibly empty)."""
    import briefing
    data = briefing.assemble_briefing_data()
    assert isinstance(data["market_events"], str)


def test_assemble_briefing_data_market_events_with_data():
    """market_events should contain event info when events are seeded."""
    import briefing
    import market_intel

    # Seed an event within 7 days
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    market_intel.add_event(
        event_type="rate_decision",
        title="BoC Rate Decision",
        date=tomorrow,
        description="Bank of Canada interest rate announcement",
        relevance_products="Wealth Management",
    )

    data = briefing.assemble_briefing_data()
    assert "BoC" in data["market_events"]


def test_briefing_prompt_contains_market_events_placeholder():
    """BRIEFING_PROMPT should contain {market_events} placeholder."""
    import briefing
    assert "{market_events}" in briefing.BRIEFING_PROMPT


def test_briefing_prompt_contains_market_context_instruction():
    """BRIEFING_PROMPT should instruct about market context."""
    import briefing
    assert "MARKET" in briefing.BRIEFING_PROMPT.upper()


@patch("briefing.openai_client")
def test_build_briefing_prompt_includes_market_events(mock_client):
    """_build_briefing_prompt should format market_events into the prompt."""
    import briefing
    import market_intel

    # Seed an event
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    market_intel.add_event(
        event_type="tax_deadline",
        title="RRSP Contribution Deadline",
        date=tomorrow,
        description="Last day to contribute to RRSP",
        relevance_products="Wealth Management",
    )

    data = briefing.assemble_briefing_data()
    prompt = briefing._build_briefing_prompt(data)
    assert "RRSP" in prompt


@patch("briefing.openai_client")
def test_build_briefing_prompt_handles_empty_market_events(mock_client):
    """_build_briefing_prompt should not crash with empty market_events."""
    import briefing
    data = briefing.assemble_briefing_data()
    # Should not raise
    prompt = briefing._build_briefing_prompt(data)
    assert isinstance(prompt, str)


def test_market_intel_failure_does_not_crash_briefing():
    """If market_intel fails, briefing should still work."""
    import briefing

    with patch("market_intel.format_for_briefing", side_effect=Exception("DB error")):
        data = briefing.assemble_briefing_data()
        assert "market_events" in data
        assert data["market_events"] == ""
