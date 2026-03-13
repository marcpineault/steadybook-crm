import os
import sys
import json
from unittest.mock import patch, MagicMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_content_engine"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import content_engine


def setup_function():
    db_path = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    db.init_db()


def _seed_brand_voice():
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            ("linkedin", "Had a great chat with a young couple about protecting their growing family. Life insurance isn't exciting, but knowing your kids are covered? That's peace of mind.", "story"),
        )
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            ("linkedin", "Quick tip: Review your home insurance annually. Renovations, new furniture, even a home office setup can change what you need covered.", "educational"),
        )


def test_get_brand_voice_examples():
    _seed_brand_voice()
    examples = content_engine.get_brand_voice_examples(platform="linkedin")
    assert len(examples) == 2
    assert all("content" in e for e in examples)


def test_get_brand_voice_examples_empty():
    examples = content_engine.get_brand_voice_examples(platform="instagram")
    assert examples == []


def test_add_brand_voice_example():
    content_engine.add_brand_voice_example(
        platform="linkedin",
        content="Disability insurance isn't just for physical jobs.",
        post_type="educational",
    )
    examples = content_engine.get_brand_voice_examples(platform="linkedin")
    assert len(examples) == 1


@patch("content_engine.openai_client")
def test_generate_post(mock_client):
    _seed_brand_voice()

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Just had a great conversation about life insurance with a growing family in Byron. The look of relief when they realize coverage is more affordable than they thought? That's why I do this."
    mock_client.chat.completions.create.return_value = mock_response

    post = content_engine.generate_post(
        platform="linkedin",
        post_type="story",
        topic="Life insurance for young families",
        context="Spring season, several young family prospects in pipeline",
    )
    assert post is not None
    assert "content" in post
    assert len(post["content"]) > 20


@patch("content_engine.openai_client")
def test_generate_post_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    post = content_engine.generate_post(
        platform="linkedin",
        post_type="educational",
        topic="RRSP tips",
        context="RRSP season",
    )
    assert post is None


@patch("content_engine.openai_client")
def test_generate_weekly_plan(mock_client):
    _seed_brand_voice()

    plan_json = json.dumps([
        {"day": "Monday", "platform": "linkedin", "type": "educational", "topic": "RRSP deadline approaching", "angle": "Last-minute RRSP tips for 2025 tax year"},
        {"day": "Tuesday", "platform": "facebook", "type": "local", "topic": "London housing market", "angle": "What rising home values mean for your insurance coverage"},
        {"day": "Wednesday", "platform": "linkedin", "type": "story", "topic": "Client win", "angle": "Anonymized story about a family getting the right coverage"},
        {"day": "Thursday", "platform": "instagram", "type": "educational", "topic": "Disability insurance", "angle": "Most people underestimate disability risk"},
        {"day": "Friday", "platform": "linkedin", "type": "timely", "topic": "BoC rate decision", "angle": "What the latest rate hold means for your finances"},
    ])
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = plan_json
    mock_client.chat.completions.create.return_value = mock_response

    plan = content_engine.generate_weekly_plan()
    assert plan is not None
    assert isinstance(plan, list)
    assert len(plan) == 5


@patch("content_engine.openai_client")
def test_generate_weekly_plan_api_failure(mock_client):
    mock_client.chat.completions.create.side_effect = Exception("API down")
    plan = content_engine.generate_weekly_plan()
    assert plan is None
