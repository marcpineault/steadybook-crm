import os
import sys

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
