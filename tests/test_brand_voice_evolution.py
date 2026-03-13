"""Tests for brand voice evolution — approved content_post drafts save to brand_voice table."""
import os
import sys
from unittest.mock import patch, MagicMock, AsyncMock

os.environ["DATA_DIR"] = "/tmp/test_calm_bot_brand_evo"
os.makedirs(os.environ["DATA_DIR"], exist_ok=True)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db
import approval_queue
import content_engine


def setup_function():
    os.environ["DATA_DIR"] = "/tmp/test_calm_bot_brand_evo"
    db.DB_PATH = os.path.join(os.environ["DATA_DIR"], "pipeline.db")
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_brand_voice_evolution_on_content_post_approve():
    """When a content_post draft is approved via handle_draft_callback, it should be saved as a brand voice example."""
    # Create a content_post draft
    draft = approval_queue.add_draft(
        draft_type="content_post",
        channel="linkedin_post",
        content="Protect what matters most. Here's how.",
        context="educational: insurance basics",
    )

    # Verify no brand voice examples yet
    examples_before = content_engine.get_brand_voice_examples(platform="linkedin")
    assert len(examples_before) == 0

    # Simulate approval (same logic as handle_draft_callback approve block)
    approval_queue.update_draft_status(draft["id"], "approved")
    full_draft = approval_queue.get_draft_by_id(draft["id"])

    if full_draft.get("type") == "content_post" and full_draft.get("content"):
        channel = full_draft.get("channel", "linkedin_post")
        platform = channel.replace("_post", "")
        context_text = full_draft.get("context", "")
        post_type = context_text.split(":")[0].strip() if ":" in context_text else "general"
        content_engine.add_brand_voice_example(platform, full_draft["content"], post_type)

    # Verify brand voice was updated
    examples_after = content_engine.get_brand_voice_examples(platform="linkedin")
    assert len(examples_after) == 1
    assert examples_after[0]["content"] == "Protect what matters most. Here's how."
    assert examples_after[0]["post_type"] == "educational"


def test_brand_voice_not_triggered_for_non_content_post():
    """Non content_post drafts (e.g. follow_up) should NOT add brand voice examples."""
    draft = approval_queue.add_draft(
        draft_type="follow_up",
        channel="email",
        content="Hi Sarah, following up on our chat.",
        context="follow up",
    )

    approval_queue.update_draft_status(draft["id"], "approved")
    full_draft = approval_queue.get_draft_by_id(draft["id"])

    # The condition should NOT match
    assert full_draft.get("type") != "content_post"

    examples = content_engine.get_brand_voice_examples()
    assert len(examples) == 0


def test_brand_voice_extracts_post_type_from_context():
    """Post type should be extracted from the context field (format: 'type: topic')."""
    draft = approval_queue.add_draft(
        draft_type="content_post",
        channel="facebook_post",
        content="London is a great place to raise a family.",
        context="local: community event",
    )

    approval_queue.update_draft_status(draft["id"], "approved")
    full_draft = approval_queue.get_draft_by_id(draft["id"])

    channel = full_draft.get("channel", "linkedin_post")
    platform = channel.replace("_post", "")
    context_text = full_draft.get("context", "")
    post_type = context_text.split(":")[0].strip() if ":" in context_text else "general"

    content_engine.add_brand_voice_example(platform, full_draft["content"], post_type)

    examples = content_engine.get_brand_voice_examples(platform="facebook")
    assert len(examples) == 1
    assert examples[0]["post_type"] == "local"
    assert examples[0]["platform"] == "facebook"
