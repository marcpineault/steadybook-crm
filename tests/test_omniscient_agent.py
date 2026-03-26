"""Tests for the Omniscient AI Assistant."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta


def test_get_trust_level_returns_int(monkeypatch):
    from omniscient_agent import get_trust_level
    import db
    monkeypatch.setattr(db, "get_trust_level", MagicMock(return_value=2))
    result = get_trust_level(tenant_id=1)
    assert result == 2


def test_build_prospect_context_returns_string(monkeypatch):
    from omniscient_agent import build_prospect_context
    import db
    monkeypatch.setattr(db, "get_pipeline_metrics", MagicMock(return_value={
        "total": 10, "active": 6, "closed_won": 2, "new_leads": 3
    }))
    monkeypatch.setattr(db, "get_prospects_by_tag", MagicMock(return_value=[]))

    context = build_prospect_context(tenant_id=1)
    assert isinstance(context, str)
    assert len(context) > 0


def test_should_alert_with_stale_prospects():
    from omniscient_agent import should_alert
    analysis = {"has_action_items": True, "stale_count": 3, "urgent_count": 0}
    assert should_alert(analysis) is True


def test_should_alert_no_action_items():
    from omniscient_agent import should_alert
    analysis = {"has_action_items": False, "stale_count": 0, "urgent_count": 0}
    assert should_alert(analysis) is False


def test_format_alert_message_contains_header():
    from omniscient_agent import format_alert_message
    analysis = {
        "summary": "3 prospects need follow-up",
        "action_items": ["Call Sarah Chen", "Send proposal to Bob"],
        "stale_count": 3,
    }
    msg = format_alert_message(analysis)
    assert "🤖" in msg or "Morning" in msg or "Alert" in msg
    assert "Sarah Chen" in msg or "follow-up" in msg.lower()
