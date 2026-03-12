import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_add_task_basic():
    result = db.add_task({
        "title": "Send John the brochure",
        "assigned_to": "123",
        "created_by": "123",
    })
    assert result["id"] is not None
    assert result["title"] == "Send John the brochure"
    assert result["status"] == "pending"
    assert result["assigned_to"] == "123"


def test_add_task_with_prospect_and_due_date():
    result = db.add_task({
        "title": "Call about term quote",
        "prospect": "John Smith",
        "due_date": "2026-03-15",
        "remind_at": "2026-03-14 09:00",
        "assigned_to": "123",
        "created_by": "123",
    })
    assert result["prospect"] == "John Smith"
    assert result["due_date"] == "2026-03-15"
    assert result["remind_at"] == "2026-03-14 09:00"


def test_add_task_requires_title():
    result = db.add_task({"title": "", "assigned_to": "123", "created_by": "123"})
    assert result is None


def test_get_tasks_filters_by_assignee():
    db.add_task({"title": "Task A", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Task B", "assigned_to": "222", "created_by": "222"})
    tasks = db.get_tasks(assigned_to="111")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Task A"


def test_get_tasks_filters_by_status():
    t = db.add_task({"title": "Task C", "assigned_to": "111", "created_by": "111"})
    db.complete_task(t["id"], "111")
    pending = db.get_tasks(assigned_to="111", status="pending")
    assert len(pending) == 0
    completed = db.get_tasks(assigned_to="111", status="completed")
    assert len(completed) == 1


def test_get_tasks_filters_by_prospect():
    db.add_task({"title": "Task D", "prospect": "John", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Task E", "prospect": "Sarah", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_tasks(assigned_to="111", prospect="John")
    assert len(tasks) == 1
    assert tasks[0]["prospect"] == "John"


def test_get_tasks_orders_by_due_date():
    db.add_task({"title": "Later", "due_date": "2026-03-20", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Sooner", "due_date": "2026-03-10", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "No date", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_tasks(assigned_to="111")
    assert tasks[0]["title"] == "Sooner"
    assert tasks[1]["title"] == "Later"
    assert tasks[2]["title"] == "No date"


def test_complete_task():
    t = db.add_task({"title": "Finish this", "assigned_to": "111", "created_by": "111"})
    result = db.complete_task(t["id"], "111")
    assert "Completed" in result
    tasks = db.get_tasks(assigned_to="111", status="completed")
    assert len(tasks) == 1
    assert tasks[0]["completed_at"] is not None


def test_complete_task_wrong_user():
    t = db.add_task({"title": "Not yours", "assigned_to": "111", "created_by": "111"})
    result = db.complete_task(t["id"], "999")
    assert "not authorized" in result.lower() or "not found" in result.lower()


def test_complete_task_admin_override():
    t = db.add_task({"title": "Admin completes", "assigned_to": "222", "created_by": "222"})
    result = db.complete_task(t["id"], "222", is_admin=True)
    assert "Completed" in result


def test_delete_task():
    t = db.add_task({"title": "Delete me", "assigned_to": "111", "created_by": "111"})
    result = db.delete_task(t["id"], "111")
    assert "Deleted" in result
    tasks = db.get_tasks(assigned_to="111")
    assert len(tasks) == 0


def test_get_due_tasks():
    db.add_task({"title": "Due today", "due_date": "2026-03-11", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Due tomorrow", "due_date": "2026-03-12", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_due_tasks("2026-03-11")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Due today"


def test_get_overdue_tasks():
    db.add_task({"title": "Overdue", "due_date": "2026-03-01", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Future", "due_date": "2099-12-31", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_overdue_tasks()
    titles = [t["title"] for t in tasks]
    assert "Overdue" in titles
    assert "Future" not in titles


def test_get_reminder_tasks():
    db.add_task({
        "title": "Remind me",
        "remind_at": "2026-03-11 09:00",
        "assigned_to": "111",
        "created_by": "111",
    })
    db.add_task({
        "title": "Later reminder",
        "remind_at": "2026-03-11 15:00",
        "assigned_to": "111",
        "created_by": "111",
    })
    tasks = db.get_reminder_tasks("2026-03-11 10:00")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Remind me"


def test_update_task():
    t = db.add_task({"title": "Original", "prospect": "John", "assigned_to": "111", "created_by": "111"})
    result = db.update_task(t["id"], {"title": "Updated", "due_date": "2026-03-20", "notes": "Call back"}, "111")
    assert "Updated" in result
    tasks = db.get_tasks(assigned_to="111")
    updated = [x for x in tasks if x["id"] == t["id"]][0]
    assert updated["title"] == "Updated"
    assert updated["due_date"] == "2026-03-20"
    assert updated["notes"] == "Call back"


def test_update_task_wrong_user():
    t = db.add_task({"title": "Protected", "assigned_to": "111", "created_by": "111"})
    result = db.update_task(t["id"], {"title": "Hacked"}, "999")
    assert "not authorized" in result.lower()


def test_update_task_normalizes_remind_at():
    t = db.add_task({"title": "Remind edit", "assigned_to": "111", "created_by": "111"})
    db.update_task(t["id"], {"remind_at": "2026-03-15T10:00"}, "111")
    tasks = db.get_reminder_tasks("2026-03-15 11:00")
    assert len(tasks) == 1
    assert tasks[0]["remind_at"] == "2026-03-15 10:00"


def test_clear_reminder():
    t = db.add_task({
        "title": "Clear me",
        "remind_at": "2026-03-11 09:00",
        "assigned_to": "111",
        "created_by": "111",
    })
    db.clear_reminder(t["id"])
    tasks = db.get_reminder_tasks("2026-03-11 10:00")
    assert len(tasks) == 0
