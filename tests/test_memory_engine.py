import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import memory_engine


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()
    db.add_prospect({"name": "Sarah Chen", "product": "Life Insurance", "stage": "Discovery Call"})


def _get_prospect_id(name="Sarah Chen"):
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM prospects WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None


# --- Task 3: CRUD Tests ---

def test_add_fact():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(
        prospect_id=pid, category="life_context",
        fact="Daughter starts university Sept 2027", source="voice_note_2026-03-10",
    )
    assert fact["id"] is not None
    assert fact["category"] == "life_context"
    assert fact["needs_review"] == 0


def test_add_fact_needs_review():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(
        pid, "financial_context", "Risk tolerance seems low", "meeting_transcript", needs_review=True,
    )
    assert fact["needs_review"] == 1


def test_get_client_profile():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Has two kids", "voice_note")
    memory_engine.add_fact(pid, "financial_context", "Owns home in London", "chat")
    memory_engine.add_fact(pid, "key_dates", "Birthday March 15", "chat")
    profile = memory_engine.get_client_profile(pid)
    assert "life_context" in profile
    assert len(profile["life_context"]) == 1
    assert len(profile["financial_context"]) == 1
    assert len(profile["key_dates"]) == 1


def test_get_client_profile_empty():
    pid = _get_prospect_id()
    profile = memory_engine.get_client_profile(pid)
    assert profile == {}


def test_get_facts_needing_review():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Fact 1", "src", needs_review=False)
    memory_engine.add_fact(pid, "life_context", "Fact 2", "src", needs_review=True)
    memory_engine.add_fact(pid, "financial_context", "Fact 3", "src", needs_review=True)
    review = memory_engine.get_facts_needing_review()
    assert len(review) == 2


def test_confirm_fact():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(pid, "life_context", "Maybe has a dog", "chat", needs_review=True)
    memory_engine.confirm_fact(fact["id"])
    review = memory_engine.get_facts_needing_review()
    assert len(review) == 0


def test_delete_fact():
    pid = _get_prospect_id()
    fact = memory_engine.add_fact(pid, "life_context", "Wrong fact", "chat")
    memory_engine.delete_fact(fact["id"])
    profile = memory_engine.get_client_profile(pid)
    assert profile == {}


def test_get_profile_summary_text():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Has two kids aged 8 and 12", "voice_note")
    memory_engine.add_fact(pid, "financial_context", "Risk-averse investor", "meeting")
    memory_engine.add_fact(pid, "communication_prefs", "Prefers text over email", "chat")
    summary = memory_engine.get_profile_summary_text(pid)
    assert "life_context" in summary.lower() or "Life" in summary
    assert "two kids" in summary
    assert "Risk-averse" in summary


# --- Task 4: Extraction Tests ---

def test_build_extraction_prompt():
    pid = _get_prospect_id()
    memory_engine.add_fact(pid, "life_context", "Has two kids", "voice_note")
    system_prompt, user_prompt = memory_engine.build_extraction_prompt(
        prospect_name="Sarah Chen", prospect_id=pid,
        interaction_text="Sarah mentioned her husband runs a landscaping business in Byron",
        source="voice_note_2026-03-13",
    )
    # PII redaction: name should be tokenized, not raw
    assert "Sarah Chen" not in user_prompt
    assert "[CLIENT_01]" in user_prompt
    assert "husband runs a landscaping business" in user_prompt
    assert "life_context" in system_prompt


def test_parse_extraction_response_valid():
    response = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband runs a landscaping business in Byron", "needs_review": False},
            {"category": "relationship_signals", "fact": "Referred by colleague at work", "needs_review": False},
        ]
    })
    facts = memory_engine.parse_extraction_response(response)
    assert len(facts) == 2
    assert facts[0]["category"] == "life_context"


def test_parse_extraction_response_with_backticks():
    response = '```json\n{"facts": [{"category": "life_context", "fact": "Has a dog", "needs_review": false}]}\n```'
    facts = memory_engine.parse_extraction_response(response)
    assert len(facts) == 1


def test_parse_extraction_response_invalid():
    facts = memory_engine.parse_extraction_response("not json at all")
    assert facts == []


def test_parse_extraction_response_empty_facts():
    response = json.dumps({"facts": []})
    facts = memory_engine.parse_extraction_response(response)
    assert facts == []


@patch("memory_engine.openai_client")
def test_extract_facts_from_interaction(mock_client):
    pid = _get_prospect_id()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "facts": [{"category": "life_context", "fact": "Husband owns landscaping business", "needs_review": False}]
    })
    mock_client.chat.completions.create.return_value = mock_response
    new_facts = memory_engine.extract_facts_from_interaction(
        prospect_name="Sarah Chen", prospect_id=pid,
        interaction_text="Sarah mentioned her husband runs a landscaping business",
        source="voice_note_2026-03-13",
    )
    assert len(new_facts) == 1
    profile = memory_engine.get_client_profile(pid)
    assert "life_context" in profile


@patch("memory_engine.openai_client")
def test_extract_facts_api_failure(mock_client):
    pid = _get_prospect_id()
    mock_client.chat.completions.create.side_effect = Exception("API error")
    new_facts = memory_engine.extract_facts_from_interaction(
        prospect_name="Sarah Chen", prospect_id=pid,
        interaction_text="Some interaction", source="chat",
    )
    assert new_facts == []


@patch("memory_engine.openai_client")
def test_backfill_from_existing_data(mock_client):
    pid = _get_prospect_id()
    db.update_prospect("Sarah Chen", {"notes": "Husband is a teacher. Two kids aged 8 and 12."})
    db.add_interaction({
        "prospect": "Sarah Chen", "source": "voice_note",
        "raw_text": "Sarah called about her RRSP contributions",
        "summary": "RRSP discussion",
    })
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "facts": [
            {"category": "life_context", "fact": "Husband is a teacher", "needs_review": False},
            {"category": "life_context", "fact": "Two kids aged 8 and 12", "needs_review": False},
        ]
    })
    mock_client.chat.completions.create.return_value = mock_response
    count = memory_engine.backfill_prospect(pid, "Sarah Chen")
    assert count > 0


def test_memory_extraction_prompt_covers_financial_terms():
    prompt = memory_engine.EXTRACTION_SYSTEM_PROMPT
    assert "AUM" in prompt
    assert "insurance premium" in prompt.lower()
    assert "insurance commission" in prompt.lower()
    # Still covers the existing financial_context category
    assert "financial_context" in prompt
