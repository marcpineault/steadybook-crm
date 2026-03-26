"""Tests for the unified intake pipeline."""
import pytest
from unittest.mock import patch, MagicMock


def test_classify_intent_booking():
    from intake_pipeline import classify_intent
    event = {"type": "calendly", "data": {"name": "Sarah", "email": "s@test.com"}}
    assert classify_intent(event) == "booking"


def test_classify_intent_lead():
    from intake_pipeline import classify_intent
    event = {"type": "instagram_dm", "data": {"name": "Bob", "message": "interested"}}
    assert classify_intent(event) == "lead"


def test_classify_intent_lead_form():
    from intake_pipeline import classify_intent
    event = {"type": "linkedin_ad", "data": {"name": "Carol"}}
    assert classify_intent(event) == "lead"


def test_classify_intent_unknown_defaults_to_lead():
    from intake_pipeline import classify_intent
    event = {"type": "unknown_channel", "data": {}}
    assert classify_intent(event) == "lead"


def test_intake_event_dataclass():
    from intake_pipeline import IntakeEvent
    ev = IntakeEvent(
        channel="instagram_dm",
        name="Sarah Chen",
        email="sarah@test.com",
        phone="",
        company="",
        message="I saw your post",
        raw={}
    )
    assert ev.channel == "instagram_dm"
    assert ev.name == "Sarah Chen"


def test_process_intake_event_creates_prospect(monkeypatch):
    from intake_pipeline import process_intake_event, IntakeEvent
    import db

    monkeypatch.setattr(db, "get_prospect_by_email", MagicMock(return_value=None))
    monkeypatch.setattr(db, "get_prospect_by_phone", MagicMock(return_value=None))
    # First call (dedup) returns None; second call (post-add lookup) returns the created prospect
    monkeypatch.setattr(db, "get_prospect_by_name", MagicMock(side_effect=[None, {"id": 42, "name": "Sarah Chen"}]))
    monkeypatch.setattr(db, "add_prospect", MagicMock(return_value=None))
    monkeypatch.setattr(db, "apply_tag", MagicMock(return_value=True))
    monkeypatch.setattr(db, "queue_enrichment", MagicMock())

    ev = IntakeEvent(
        channel="instagram_dm",
        name="Sarah Chen",
        email="",
        phone="",
        company="",
        message="interested",
        raw={}
    )
    process_intake_event(ev, tenant_id=1)
    db.add_prospect.assert_called_once()
