"""Tests for campaign run orchestration — segment + generate messages."""
import os
import sys
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_campaign_run"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import campaigns


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_prospects():
    for name, product in [("Alice Wong", "Life Insurance"), ("Bob Lee", "Home Insurance")]:
        db.add_prospect({
            "name": name, "stage": "New Lead", "priority": "Warm",
            "product": product, "email": f"{name.split()[0].lower()}@example.com",
            "notes": f"Test prospect for {product}.",
        })


@patch("campaigns.openai_client")
def test_segment_returns_matching_names(mock_client):
    """segment_audience should parse GPT response as JSON list of names."""
    _seed_prospects()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '["Alice Wong"]'
    mock_client.chat.completions.create.return_value = mock_response

    result = campaigns.segment_audience("life insurance prospects")
    assert isinstance(result, list)
    assert "Alice Wong" in result


@patch("campaigns.openai_client")
def test_segment_handles_empty_result(mock_client):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "[]"
    mock_client.chat.completions.create.return_value = mock_response

    result = campaigns.segment_audience("nobody matches this")
    assert result == []


@patch("campaigns.openai_client")
def test_segment_handles_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    result = campaigns.segment_audience("anything")
    assert result == []


@patch("campaigns.openai_client")
@patch("campaigns.compliance")
def test_generate_message_for_prospect(mock_compliance, mock_client):
    """generate_campaign_message should produce a message and queue it."""
    _seed_prospects()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Alice, I noticed you were looking into life insurance coverage."
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    campaign = campaigns.create_campaign("Spring Campaign", "Life insurance outreach")
    result = campaigns.generate_campaign_message("Alice Wong", campaign["description"])

    assert result is not None
    assert result["prospect_name"] == "Alice Wong"
    assert "content" in result
    assert result["compliance_passed"] is True
    assert "queue_id" in result


@patch("campaigns.openai_client")
def test_generate_message_api_failure(mock_client):
    """generate_campaign_message should return None on API failure."""
    _seed_prospects()

    mock_client.chat.completions.create.side_effect = Exception("API down")
    result = campaigns.generate_campaign_message("Alice Wong", "Spring campaign")
    assert result is None


def test_campaign_lifecycle():
    """Test create → list → update status flow."""
    c = campaigns.create_campaign("Winter Push", "Auto insurance renewals")
    assert c["status"] == "draft"

    all_campaigns = campaigns.list_campaigns()
    assert len(all_campaigns) >= 1

    campaigns.update_campaign_status(c["id"], "active")
    updated = campaigns.get_campaign(c["id"])
    assert updated["status"] == "active"

    campaigns.update_campaign_status(c["id"], "completed")
    updated = campaigns.get_campaign(c["id"])
    assert updated["status"] == "completed"


@patch("campaigns.openai_client")
def test_segment_prompt_ordering_prevents_injection(mock_client):
    """User-sourced criteria should not corrupt other placeholders."""
    _seed_prospects()

    # Criteria containing a placeholder token — should be treated as literal text
    malicious_criteria = "{insurance_book_summary}"

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "[]"
    mock_client.chat.completions.create.return_value = mock_response

    result = campaigns.segment_audience(malicious_criteria)
    assert isinstance(result, list)

    # Verify the prompt sent to OpenAI has the literal string, not double-expanded
    call_args = mock_client.chat.completions.create.call_args
    prompt_sent = call_args[1]["messages"][0]["content"]
    # The literal {insurance_book_summary} should appear in the CRITERIA line
    assert "{insurance_book_summary}" in prompt_sent
    # But it should NOT appear as the full insurance book (would indicate double-expansion)
    assert prompt_sent.count("CRITERIA:") == 1
