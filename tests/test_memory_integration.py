import os
import sys
from unittest.mock import patch, MagicMock
import json

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_memint"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import memory_engine


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _get_prospect_id(name):
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None


@patch("memory_engine.openai_client")
def test_voice_handler_triggers_memory_extraction(mock_me_client):
    """After voice_handler processes a transcript, memory extraction should run."""
    db.add_prospect({"name": "Sarah Chen", "product": "Life Insurance", "stage": "Discovery Call"})
    pid = _get_prospect_id("Sarah Chen")

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband runs landscaping business", "needs_review": False},
        ]
    })
    mock_me_client.chat.completions.create.return_value = mock_response

    new_facts = memory_engine.extract_facts_from_interaction(
        prospect_name="Sarah Chen",
        prospect_id=pid,
        interaction_text="Sarah mentioned her husband runs a landscaping business in Byron.",
        source="voice_note_2026-03-13",
    )

    assert len(new_facts) == 1
    profile = memory_engine.get_client_profile(pid)
    assert "life_context" in profile
