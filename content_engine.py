"""Content engine — brand voice examples for Marc's posts."""

import logging

import db

logger = logging.getLogger(__name__)


def get_brand_voice_examples(platform=None, limit=10):
    """Get brand voice examples, optionally filtered by platform."""
    with db.get_db() as conn:
        if platform:
            rows = conn.execute(
                "SELECT * FROM brand_voice WHERE platform = ? ORDER BY id DESC LIMIT ?",
                (platform, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM brand_voice ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def add_brand_voice_example(platform, content, post_type="general"):
    """Add a new brand voice example."""
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO brand_voice (platform, content, post_type) VALUES (?, ?, ?)",
            (platform, content, post_type),
        )
    logger.info("Added brand voice example: %s / %s", platform, post_type)
