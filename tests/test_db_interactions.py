import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    """Reset test database before each test."""
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_add_interaction():
    result = db.add_interaction({
        "prospect": "John Smith",
        "source": "voice_note",
        "raw_text": "Just had coffee with John Smith, interested in life insurance",
        "summary": "Met for coffee, interested in life insurance",
        "action_items": "Send quote by Friday",
    })
    assert "Logged interaction" in result


def test_read_interactions():
    db.add_interaction({
        "prospect": "John Smith",
        "source": "voice_note",
        "raw_text": "test transcript",
    })
    db.add_interaction({
        "prospect": "Sarah Chen",
        "source": "otter_transcript",
        "raw_text": "test transcript 2",
    })
    interactions = db.read_interactions()
    assert len(interactions) == 2
    assert interactions[0]["prospect"] == "Sarah Chen"  # newest first


def test_read_interactions_by_prospect():
    db.add_interaction({"prospect": "John Smith", "source": "voice_note", "raw_text": "a"})
    db.add_interaction({"prospect": "Sarah Chen", "source": "voice_note", "raw_text": "b"})
    interactions = db.read_interactions(prospect="John")
    assert len(interactions) == 1
    assert interactions[0]["prospect"] == "John Smith"
