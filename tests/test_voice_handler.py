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


def test_voice_extraction_prompt_contains_financial_terms():
    from voice_handler import VOICE_EXTRACTION_SYSTEM_PROMPT
    prompt = VOICE_EXTRACTION_SYSTEM_PROMPT
    # Domain glossary
    assert "AUM" in prompt
    assert "Assets Under Management" in prompt
    assert "insurance premium" in prompt.lower()
    assert "insurance commission" in prompt.lower()
    # New JSON fields
    assert '"aum"' in prompt
    assert '"insurance_premium"' in prompt
    assert '"insurance_commission"' in prompt


def test_parse_extraction_response_with_financial_fields():
    from voice_handler import parse_extraction_response
    import json
    raw = json.dumps({
        "prospects": [{
            "name": "John Smith",
            "product": "Life Insurance",
            "notes": "Has large investment portfolio",
            "action_items": "Send illustration",
            "source": "voice_note",
            "aum": 450000,
            "insurance_premium": 180,
            "insurance_commission": 2400,
        }]
    })
    result = parse_extraction_response(raw)
    assert len(result) == 1
    assert result[0]["aum"] == 450000
    assert result[0]["insurance_premium"] == 180
    assert result[0]["insurance_commission"] == 2400


def test_parse_extraction_response_null_financial_fields():
    from voice_handler import parse_extraction_response
    import json
    raw = json.dumps({
        "prospects": [{
            "name": "Jane Doe",
            "product": "Auto Insurance",
            "notes": "Wants quote",
            "action_items": "",
            "source": "voice_note",
            "aum": None,
            "insurance_premium": None,
            "insurance_commission": None,
        }]
    })
    result = parse_extraction_response(raw)
    assert result[0].get("aum") is None
    assert result[0].get("insurance_premium") is None
    assert result[0].get("insurance_commission") is None


def test_extract_and_update_writes_aum_and_revenue():
    from unittest.mock import patch, MagicMock
    import json
    import asyncio
    import db

    ai_response = json.dumps({
        "prospects": [{
            "name": "Sarah Chen",
            "product": "Wealth Management",
            "notes": "Has $450K AUM with RBC, wants to transfer",
            "action_items": "Follow up next week",
            "source": "voice_note",
            "priority": "Hot",
            "stage": "Discovery Call",
            "phone": "",
            "email": "",
            "aum": 450000,
            "insurance_premium": None,
            "insurance_commission": 2400,
        }]
    })

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = ai_response

    with patch("voice_handler.client") as mock_client, \
         patch("intake._score_and_schedule"), \
         patch("memory_engine.extract_facts_from_interaction"), \
         patch("follow_up.generate_follow_up_draft", return_value=None):
        mock_client.chat.completions.create.return_value = mock_response
        result = asyncio.run(
            __import__("voice_handler").extract_and_update("Sarah has $450K in investments and I'll earn $2,400")
        )

    prospect = db.get_prospect_by_name("Sarah Chen")
    assert prospect is not None
    assert prospect["aum"] == 450000
    assert prospect["revenue"] == 2400
