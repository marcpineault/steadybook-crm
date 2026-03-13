"""Database-backed approval queue for AI-generated drafts.

All drafted messages (follow-ups, outreach, content) are persisted here
so nothing is lost if the bot restarts or Marc misses a Telegram notification.
"""

from datetime import datetime, timezone
import db


def add_draft(draft_type, channel, content, context, prospect_id=None):
    """Add a new draft to the approval queue. Returns the created draft dict."""
    with db.get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO approval_queue (type, prospect_id, channel, content, context)
               VALUES (?, ?, ?, ?, ?)""",
            (draft_type, prospect_id, channel, content, context),
        )
        return _row_to_dict(
            conn.execute("SELECT * FROM approval_queue WHERE id = ?", (cursor.lastrowid,)).fetchone()
        )


def get_pending_drafts(draft_type=None, limit=50):
    """Get pending drafts, optionally filtered by type."""
    with db.get_db() as conn:
        if draft_type:
            rows = conn.execute(
                "SELECT * FROM approval_queue WHERE status = 'pending' AND type = ? ORDER BY created_at ASC LIMIT ?",
                (draft_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM approval_queue WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_draft_by_id(draft_id):
    """Get a single draft by ID. Returns None if not found."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (draft_id,)).fetchone()
        return _row_to_dict(row) if row else None


def update_draft_status(draft_id, status):
    """Update draft status (approved, edited, dismissed, snoozed, sent). Returns updated draft."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as conn:
        conn.execute(
            "UPDATE approval_queue SET status = ?, acted_on_at = ? WHERE id = ?",
            (status, now, draft_id),
        )
        row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (draft_id,)).fetchone()
        return _row_to_dict(row) if row else None


def set_telegram_message_id(draft_id, message_id):
    """Link a draft to its Telegram notification message."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE approval_queue SET telegram_message_id = ? WHERE id = ?",
            (str(message_id), draft_id),
        )


def get_pending_count():
    """Return count of pending drafts."""
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM approval_queue WHERE status = 'pending'"
        ).fetchone()
        return row[0] if row else 0


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)
