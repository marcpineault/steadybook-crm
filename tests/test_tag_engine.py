"""Tests for the tag-based trigger engine."""
import pytest
from unittest.mock import patch, MagicMock, call


def test_get_trigger_actions_new_lead():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("new_lead")
    assert len(actions) > 0
    action_types = [a["type"] for a in actions]
    assert "create_task" in action_types


def test_get_trigger_actions_source_qr():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("source_qr")
    action_types = [a["type"] for a in actions]
    assert "enroll_sequence" in action_types or "create_task" in action_types


def test_get_trigger_actions_unknown_tag_returns_empty():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("nonexistent_tag_xyz")
    assert actions == []


def test_get_trigger_actions_closed_life():
    from tag_engine import get_trigger_actions
    actions = get_trigger_actions("closed_life")
    action_types = [a["type"] for a in actions]
    assert "enroll_sequence" in action_types or "schedule_crosssell" in action_types


def test_process_tag_creates_task(monkeypatch):
    from tag_engine import process_tag
    import db

    monkeypatch.setattr(db, "add_task", MagicMock(return_value=1))
    monkeypatch.setattr(db, "get_tags", MagicMock(return_value=["new_lead"]))

    prospect = {"id": 1, "name": "Sarah Chen", "stage": "New Lead"}
    with patch("tag_engine.get_trigger_actions", return_value=[
        {"type": "create_task", "subject": "Follow up with {{name}}", "due_days": 2}
    ]):
        process_tag(prospect, "new_lead")
        db.add_task.assert_called_once()


def test_process_tag_skips_do_not_contact(monkeypatch):
    from tag_engine import process_tag
    import db

    monkeypatch.setattr(db, "get_tags", MagicMock(return_value=["do_not_contact"]))
    add_task_mock = MagicMock()
    monkeypatch.setattr(db, "add_task", add_task_mock)

    prospect = {"id": 1, "name": "Sarah Chen", "stage": "New Lead"}
    process_tag(prospect, "new_lead")
    add_task_mock.assert_not_called()
