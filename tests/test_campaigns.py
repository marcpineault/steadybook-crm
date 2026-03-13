import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_campaigns"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import campaigns


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_insurance_book():
    entries = [
        {"name": "Alice Johnson", "phone": "555-0101", "notes": "Life insurance only, no disability"},
        {"name": "Bob Smith", "phone": "555-0102", "notes": "Home and auto, no life insurance"},
        {"name": "Carol White", "phone": "555-0103", "notes": "Full coverage — life, disability, home"},
    ]
    for e in entries:
        db.add_insurance_entry(e)


def _seed_prospects():
    for p in [
        {"name": "Alice Johnson", "stage": "Client", "product": "Life Insurance", "priority": "Warm"},
        {"name": "Dave Brown", "stage": "Discovery Call", "product": "Disability Insurance", "priority": "Hot"},
    ]:
        db.add_prospect(p)


def test_create_campaign():
    camp = campaigns.create_campaign(
        name="Disability cross-sell",
        description="Reach out to life insurance clients who don't have disability",
        channel="email_draft",
    )
    assert camp is not None
    assert camp["id"] > 0
    assert camp["status"] == "draft"


def test_get_campaign():
    camp = campaigns.create_campaign(name="Test campaign", description="test")
    fetched = campaigns.get_campaign(camp["id"])
    assert fetched is not None
    assert fetched["name"] == "Test campaign"


def test_get_campaign_not_found():
    result = campaigns.get_campaign(9999)
    assert result is None


def test_list_campaigns():
    campaigns.create_campaign(name="Camp 1", description="test1")
    campaigns.create_campaign(name="Camp 2", description="test2")
    all_camps = campaigns.list_campaigns()
    assert len(all_camps) >= 2


@patch("campaigns.openai_client")
def test_segment_audience(mock_client):
    _seed_insurance_book()
    _seed_prospects()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(["Alice Johnson"])
    mock_client.chat.completions.create.return_value = mock_response

    matches = campaigns.segment_audience(
        criteria="life insurance clients who don't have disability coverage",
    )
    assert isinstance(matches, list)
    assert len(matches) >= 1


@patch("campaigns.openai_client")
@patch("campaigns.compliance")
def test_generate_campaign_message(mock_compliance, mock_client):
    _seed_insurance_book()
    _seed_prospects()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Hi Alice, I noticed you have great life insurance coverage. Have you considered disability protection?"
    mock_client.chat.completions.create.return_value = mock_response
    mock_compliance.check_compliance.return_value = {"passed": True, "issues": []}

    msg = campaigns.generate_campaign_message(
        prospect_name="Alice Johnson",
        campaign_context="Disability cross-sell for existing life insurance clients",
        channel="email_draft",
    )
    assert msg is not None
    assert "content" in msg
    assert msg["compliance_passed"] is True


def test_update_campaign_status():
    camp = campaigns.create_campaign(name="Status test", description="test")
    campaigns.update_campaign_status(camp["id"], "active")
    updated = campaigns.get_campaign(camp["id"])
    assert updated["status"] == "active"
