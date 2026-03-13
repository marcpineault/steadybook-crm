"""Tests for weekly content plan and daily market check scheduler jobs."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_scheduler_content"
os.environ["TELEGRAM_CHAT_ID"] = "123456"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


@patch("content_engine.generate_weekly_plan")
@patch("approval_queue.add_draft")
@patch("approval_queue.set_telegram_message_id")
def test_weekly_content_plan_success(mock_set_msg, mock_add_draft, mock_gen_plan):
    import scheduler

    scheduler._bot = AsyncMock()
    msg_mock = AsyncMock()
    msg_mock.message_id = 42
    scheduler._bot.send_message.return_value = msg_mock

    mock_gen_plan.return_value = [
        {"day": "Monday", "platform": "linkedin", "type": "educational", "topic": "RRSP tips", "angle": "Tax season angle"},
    ]
    mock_add_draft.return_value = {"id": 1}

    asyncio.run(scheduler.weekly_content_plan())

    scheduler._bot.send_message.assert_called_once()
    call_args = scheduler._bot.send_message.call_args
    text = call_args.kwargs.get("text", "")
    assert "WEEKLY CONTENT PLAN" in text
    mock_add_draft.assert_called_once()
    mock_set_msg.assert_called_once_with(1, "42")


@patch("content_engine.generate_weekly_plan")
def test_weekly_content_plan_failure(mock_gen_plan):
    import scheduler

    scheduler._bot = AsyncMock()
    mock_gen_plan.return_value = None

    asyncio.run(scheduler.weekly_content_plan())

    scheduler._bot.send_message.assert_called_once()
    call_args = scheduler._bot.send_message.call_args
    text = call_args.kwargs.get("text", "")
    assert "Failed" in text or "failed" in text


def test_weekly_content_plan_no_bot():
    import scheduler

    scheduler._bot = None
    # Should not raise
    asyncio.run(scheduler.weekly_content_plan())


def test_daily_market_check_no_events():
    import scheduler

    scheduler._bot = AsyncMock()

    asyncio.run(scheduler.daily_market_check())
    # No events today = no message sent
    scheduler._bot.send_message.assert_not_called()


def test_daily_market_check_with_events():
    import scheduler
    import market_intel

    scheduler._bot = AsyncMock()

    # Seed an event for today
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    market_intel.add_event(
        event_type="rate_decision",
        title="BoC Rate Decision",
        date=today,
        description="Bank of Canada interest rate announcement",
        relevance_products="Wealth Management",
    )

    asyncio.run(scheduler.daily_market_check())
    scheduler._bot.send_message.assert_called_once()
    call_args = scheduler._bot.send_message.call_args
    text = call_args.kwargs.get("text", "")
    assert "MARKET ALERT" in text
    assert "BoC" in text


def test_daily_market_check_no_bot():
    import scheduler

    scheduler._bot = None
    asyncio.run(scheduler.daily_market_check())


def test_scheduler_has_content_jobs():
    """Verify the new jobs are registered in start_scheduler."""
    import scheduler
    import inspect

    source = inspect.getsource(scheduler.start_scheduler)
    assert "weekly_content_plan" in source
    assert "daily_market_check" in source
