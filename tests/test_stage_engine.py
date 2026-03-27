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


def test_call_gpt_returns_parsed_response():
    """_call_gpt should return parsed dict from a valid GPT JSON response."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"should_change": true, "new_stage": "Discovery Call", '
        '"reason": "Booked a call", "cross_sell_opportunity": false, "cross_sell_product": null}'
    )
    with patch.object(stage_engine.openai_client.chat.completions, "create", return_value=mock_response):
        result = stage_engine._call_gpt(
            current_stage="Contacted",
            product="Life Insurance",
            sms_thread=[{"direction": "inbound", "body": "Sure let's chat"}],
            activities=[],
            meetings=[],
        )
    assert result["should_change"] is True
    assert result["new_stage"] == "Discovery Call"
    assert result["reason"] == "Booked a call"


def test_call_gpt_invalid_json_returns_none():
    """_call_gpt should return None on malformed GPT response."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "not json at all"
    with patch.object(stage_engine.openai_client.chat.completions, "create", return_value=mock_response):
        result = stage_engine._call_gpt(
            current_stage="Contacted", product="Life",
            sms_thread=[], activities=[], meetings=[],
        )
    assert result is None


def test_validate_stage_rejects_unknown():
    """_validate_gpt_result should return None for an unknown stage name."""
    result = stage_engine._validate_gpt_result({
        "should_change": True, "new_stage": "Banana Stage",
        "reason": "test", "cross_sell_opportunity": False, "cross_sell_product": None,
    })
    assert result is None


def test_validate_stage_accepts_valid():
    """_validate_gpt_result should return the dict unchanged for a known stage."""
    payload = {
        "should_change": True, "new_stage": "Negotiation",
        "reason": "Close to signing", "cross_sell_opportunity": False, "cross_sell_product": None,
    }
    assert stage_engine._validate_gpt_result(payload) == payload


def test_validate_stage_passes_no_change():
    """_validate_gpt_result should pass through when should_change is False."""
    payload = {
        "should_change": False, "new_stage": None,
        "reason": "", "cross_sell_opportunity": False, "cross_sell_product": None,
    }
    assert stage_engine._validate_gpt_result(payload) == payload


def test_apply_stage_change_calls_update_and_notify():
    """_apply_stage_change should update DB, write audit log, and notify."""
    with patch("stage_engine.db") as mock_db, \
         patch("stage_engine._notify_stage_change") as mock_notify, \
         patch("stage_engine._log_audit") as mock_audit:
        stage_engine._apply_stage_change(
            prospect_name="Jane Doe",
            old_stage="New Lead",
            new_stage="Contacted",
            reason="Returned the call",
            tenant_id=1,
        )
        mock_db.update_prospect.assert_called_once_with("Jane Doe", {"stage": "Contacted"}, 1)
        mock_notify.assert_called_once_with("Jane Doe", "New Lead", "Contacted", "Returned the call")
        mock_audit.assert_called_once()


def test_send_telegram_calls_run_coroutine_threadsafe():
    """_send_telegram should use run_coroutine_threadsafe when bot_event_loop is available."""
    mock_main = MagicMock()
    mock_main.telegram_app = MagicMock()
    mock_main.bot_event_loop = MagicMock()

    with patch.dict(sys.modules, {"__main__": mock_main}), \
         patch("os.environ.get", return_value="123456"), \
         patch("asyncio.run_coroutine_threadsafe") as mock_rctf:
        stage_engine._send_telegram("hello")
        assert mock_rctf.called


def test_send_telegram_no_op_when_loop_missing():
    """_send_telegram should silently skip when bot_event_loop is None."""
    mock_main = MagicMock()
    mock_main.telegram_app = None
    mock_main.bot_event_loop = None

    with patch.dict(sys.modules, {"__main__": mock_main}), \
         patch("asyncio.run_coroutine_threadsafe") as mock_rctf:
        stage_engine._send_telegram("hello")
        assert not mock_rctf.called


def test_notify_cross_sell_sends_inline_keyboard():
    """_notify_cross_sell should send a Telegram message with Create/Skip buttons."""
    with patch("stage_engine._send_telegram") as mock_send:
        stage_engine._notify_cross_sell(
            prospect_id=7,
            prospect_name="Alice Brown",
            current_product="Life Insurance",
            cross_sell_product="Disability Insurance",
            reason="Asked about income protection",
        )
        assert mock_send.called
        call_text = mock_send.call_args[0][0]
        assert "Alice Brown" in call_text
        assert "Disability Insurance" in call_text
        # reply_markup kwarg must not be None
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs.get("reply_markup") is not None
