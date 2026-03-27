import asyncio
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import stage_engine


def test_rate_limit_skips_recent_prospect():
    """Should skip DB query entirely when called within 10 minutes."""
    stage_engine._last_evaluated.clear()
    stage_engine._last_evaluated[42] = datetime.now(timezone.utc)
    queried = []

    async def run():
        with patch("stage_engine.db") as mock_db:
            mock_db.get_prospect_by_id.return_value = None
            await stage_engine.evaluate_prospect(42, tenant_id=1)
            queried.append(mock_db.get_prospect_by_id.called)

    asyncio.run(run())
    assert queried[0] is False, "Should not query DB when rate-limited"


def test_rate_limit_allows_after_10_minutes():
    """Should proceed when last evaluation was >10 minutes ago."""
    stage_engine._last_evaluated.clear()
    stage_engine._last_evaluated[99] = datetime.now(timezone.utc) - timedelta(minutes=11)
    queried = []

    async def run():
        with patch("stage_engine.db") as mock_db, \
             patch("stage_engine._call_gpt", return_value={
                 "should_change": False, "new_stage": None, "reason": "",
                 "cross_sell_opportunity": False, "cross_sell_product": None,
             }), \
             patch("stage_engine._get_sms_thread", return_value=[]), \
             patch("stage_engine._get_activities", return_value=[]), \
             patch("stage_engine._get_meetings", return_value=[]):
            mock_db.get_prospect_by_id.return_value = {
                "id": 99, "name": "Bob", "stage": "Contacted",
                "phone": "+15550001111", "product": "Life",
            }
            await stage_engine.evaluate_prospect(99, tenant_id=1)
            queried.append(mock_db.get_prospect_by_id.called)

    asyncio.run(run())
    assert queried[0] is True
    updated = stage_engine._last_evaluated.get(99)
    assert updated is not None
    assert datetime.now(timezone.utc) - updated < timedelta(seconds=5), "Timestamp should be updated after evaluation"
