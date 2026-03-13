import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db
import approval_queue as aq


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_add_draft():
    draft = aq.add_draft(
        draft_type="follow_up",
        channel="email_draft",
        content="Hi Sarah, great meeting today...",
        context="post-call follow-up for discovery call",
        prospect_id=None,
    )
    assert draft["id"] is not None
    assert draft["status"] == "pending"
    assert draft["content"] == "Hi Sarah, great meeting today..."


def test_get_pending_drafts():
    aq.add_draft("follow_up", "email_draft", "Draft 1", "ctx1")
    aq.add_draft("outreach", "sms", "Draft 2", "ctx2")
    aq.add_draft("follow_up", "email_draft", "Draft 3", "ctx3")
    pending = aq.get_pending_drafts()
    assert len(pending) == 3


def test_get_pending_drafts_by_type():
    aq.add_draft("follow_up", "email_draft", "Draft 1", "ctx1")
    aq.add_draft("outreach", "sms", "Draft 2", "ctx2")
    pending = aq.get_pending_drafts(draft_type="follow_up")
    assert len(pending) == 1
    assert pending[0]["type"] == "follow_up"


def test_approve_draft():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    updated = aq.update_draft_status(draft["id"], "approved")
    assert updated["status"] == "approved"
    assert updated["acted_on_at"] is not None


def test_dismiss_draft():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    updated = aq.update_draft_status(draft["id"], "dismissed")
    assert updated["status"] == "dismissed"


def test_set_telegram_message_id():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    aq.set_telegram_message_id(draft["id"], "12345")
    pending = aq.get_pending_drafts()
    assert pending[0]["telegram_message_id"] == "12345"


def test_get_draft_by_id():
    draft = aq.add_draft("follow_up", "email_draft", "content", "ctx")
    fetched = aq.get_draft_by_id(draft["id"])
    assert fetched is not None
    assert fetched["content"] == "content"


def test_get_draft_by_id_not_found():
    result = aq.get_draft_by_id(9999)
    assert result is None


def test_pending_count():
    aq.add_draft("follow_up", "email_draft", "d1", "c1")
    aq.add_draft("follow_up", "email_draft", "d2", "c2")
    draft3 = aq.add_draft("follow_up", "email_draft", "d3", "c3")
    aq.update_draft_status(draft3["id"], "approved")
    assert aq.get_pending_count() == 2
