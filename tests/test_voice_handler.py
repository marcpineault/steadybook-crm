import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_extract_prospect_data_from_transcript():
    from voice_handler import build_extraction_prompt
    transcript = "Just had coffee with John Smith, he's interested in life insurance for his wife, currently has auto and home with us, wants a quote by Friday"
    system_prompt, user_prompt = build_extraction_prompt(transcript)
    # PII redaction: names should be stripped from user prompt
    assert "transcript" in user_prompt.lower()
    assert "prospect" in system_prompt.lower()
    assert "action_items" in system_prompt.lower()


def test_parse_extraction_response_valid():
    from voice_handler import parse_extraction_response
    raw = json.dumps({
        "prospects": [{
            "name": "John Smith",
            "product": "Life Insurance",
            "notes": "Interested in life insurance for wife",
            "action_items": "Send quote by Friday",
            "source": "voice_note",
        }]
    })
    result = parse_extraction_response(raw)
    assert len(result) == 1
    assert result[0]["name"] == "John Smith"
    assert result[0]["product"] == "Life Insurance"


def test_parse_extraction_response_with_referral():
    from voice_handler import parse_extraction_response
    raw = json.dumps({
        "prospects": [
            {"name": "John Smith", "product": "Life Insurance", "notes": "Wants quote", "action_items": "Quote by Friday", "source": "voice_note"},
            {"name": "Mike Smith", "product": "Commercial Insurance", "notes": "John's brother, plumbing business", "action_items": "Initial contact", "source": "referral"},
        ]
    })
    result = parse_extraction_response(raw)
    assert len(result) == 2
    assert result[1]["name"] == "Mike Smith"


def test_parse_extraction_response_invalid_json():
    from voice_handler import parse_extraction_response
    result = parse_extraction_response("this is not json at all")
    assert result == []
