"""
SQLite database module for Calm Money Pipeline Bot.

Replaces all Excel (openpyxl) operations with SQLite for reliability
and concurrent access. Uses WAL mode for safe concurrent reads.

Usage:
    from db import init_db, add_prospect, read_pipeline, ...
    init_db()
"""

import os
import re
import sqlite3
import logging
from contextlib import contextmanager
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ── Database path ──

DATA_DIR = os.environ.get("DATA_DIR", "")
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(DATA_DIR, "pipeline.db")
else:
    DB_PATH = "pipeline.db"


# ── Connection management ──

@contextmanager
def get_db():
    """Context manager for database connections with WAL mode."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows):
    """Convert a list of sqlite3.Row to list of dicts."""
    return [dict(r) for r in rows]


# ── Numeric parsing ──

def _parse_numeric(val):
    """Parse a numeric value, stripping $ and commas. Returns float (0 if empty/invalid)."""
    if val is None or val == "":
        return 0.0
    try:
        return float(str(val).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _parse_date_val(val):
    """Parse a date value from various formats. Returns string YYYY-MM-DD or None."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    if isinstance(val, date):
        return val.strftime("%Y-%m-%d")
    s = str(val).strip()
    # Already YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s.split(" ")[0]
    # Try common formats
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s  # return as-is if unparseable


# ── Schema ──

def init_db():
    """Create all tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                source TEXT DEFAULT '',
                priority TEXT DEFAULT '',
                stage TEXT DEFAULT 'New Lead',
                product TEXT DEFAULT '',
                aum REAL,
                revenue REAL,
                first_contact TEXT,
                next_followup TEXT,
                notes TEXT DEFAULT '',
                send_channel TEXT DEFAULT 'outlook',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                prospect TEXT DEFAULT '',
                action TEXT DEFAULT '',
                outcome TEXT DEFAULT '',
                next_step TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                time TEXT DEFAULT '',
                prospect TEXT DEFAULT '',
                type TEXT DEFAULT '',
                prep_notes TEXT DEFAULT '',
                status TEXT DEFAULT 'Scheduled'
            );

            CREATE TABLE IF NOT EXISTS insurance_book (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone TEXT DEFAULT '',
                address TEXT DEFAULT '',
                policy_start TEXT DEFAULT '',
                status TEXT DEFAULT 'Not Called',
                last_called TEXT,
                notes TEXT DEFAULT '',
                retry_date TEXT
            );

            CREATE TABLE IF NOT EXISTS win_loss_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                prospect TEXT DEFAULT '',
                outcome TEXT DEFAULT '',
                reason TEXT DEFAULT '',
                product TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT DEFAULT (datetime('now')),
                prospect TEXT DEFAULT '',
                source TEXT DEFAULT '',
                raw_text TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                action_items TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                prospect TEXT DEFAULT '',
                due_date TEXT,
                remind_at TEXT,
                assigned_to TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS client_memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER REFERENCES prospects(id),
                category    TEXT NOT NULL,
                fact        TEXT NOT NULL,
                source      TEXT,
                needs_review INTEGER DEFAULT 0,
                extracted_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS approval_queue (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                type                TEXT NOT NULL,
                prospect_id         INTEGER REFERENCES prospects(id),
                channel             TEXT NOT NULL,
                content             TEXT NOT NULL,
                context             TEXT,
                status              TEXT DEFAULT 'pending',
                created_at          TEXT DEFAULT (datetime('now')),
                acted_on_at         TEXT,
                telegram_message_id TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT DEFAULT (datetime('now')),
                action_type      TEXT NOT NULL,
                target           TEXT,
                content          TEXT,
                compliance_check TEXT,
                approved_by      TEXT,
                outcome          TEXT
            );

            CREATE TABLE IF NOT EXISTS brand_voice (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL DEFAULT 'linkedin',
                content TEXT NOT NULL,
                post_type TEXT NOT NULL DEFAULT 'general',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS market_calendar (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                description TEXT DEFAULT '',
                relevance_products TEXT DEFAULT '',
                recurring INTEGER DEFAULT 0
            );

        CREATE TABLE IF NOT EXISTS trust_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trust_level INTEGER NOT NULL DEFAULT 1,
            changed_at TEXT DEFAULT (datetime('now')),
            changed_by TEXT DEFAULT 'system'
        );

        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            segment_query TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            channel TEXT DEFAULT 'email_draft',
            wave_count INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS campaign_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            prospect_name TEXT NOT NULL,
            content TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            queue_id INTEGER,
            wave INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
        );

        CREATE TABLE IF NOT EXISTS nurture_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prospect_id INTEGER,
            prospect_name TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            current_touch INTEGER DEFAULT 0,
            total_touches INTEGER DEFAULT 4,
            next_touch_date TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (prospect_id) REFERENCES prospects(id)
        );

        CREATE TABLE IF NOT EXISTS outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action_id INTEGER,
            action_type TEXT NOT NULL,
            target TEXT,
            sent_at TEXT,
            response_received INTEGER DEFAULT 0,
            response_at TEXT,
            response_type TEXT,
            converted INTEGER DEFAULT 0,
            notes TEXT DEFAULT '',
            resend_email_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (action_id) REFERENCES audit_log(id)
        );
        """)

        # Performance indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_resend_id ON outcomes(resend_email_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_target ON outcomes(target, sent_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_remind ON tasks(remind_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_queue_status ON approval_queue(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nurture_status ON nurture_sequences(status, next_touch_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_prospect ON activities(prospect)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_prospect ON interactions(prospect)")

    # Seed default trust level (idempotent — skips if any row exists)
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO trust_config (id, trust_level, changed_by) VALUES (1, 1, 'system')"
        )

    _migrate_phase6()
    logger.info(f"Database initialized at {DB_PATH}")


def _migrate_phase6():
    """Add Phase 6 columns if they don't exist (safe to run repeatedly)."""
    with get_db() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(prospects)").fetchall()]
        if "send_channel" not in cols:
            conn.execute("ALTER TABLE prospects ADD COLUMN send_channel TEXT DEFAULT 'outlook'")
            logger.info("Migration: added send_channel to prospects")
        outcome_cols = [row[1] for row in conn.execute("PRAGMA table_info(outcomes)").fetchall()]
        if "resend_email_id" not in outcome_cols:
            conn.execute("ALTER TABLE outcomes ADD COLUMN resend_email_id TEXT")
            logger.info("Migration: added resend_email_id to outcomes")


# ── Prospects CRUD ──

def read_pipeline():
    """Return all prospects as a list of dicts."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM prospects ORDER BY id").fetchall()
    return _rows_to_dicts(rows)


def add_prospect(data: dict) -> str:
    """Insert a new prospect. Returns status string."""
    name = data.get("name", "").strip()
    if not name:
        return "No name provided for prospect."

    aum = _parse_numeric(data.get("aum"))
    revenue = _parse_numeric(data.get("revenue"))
    first_contact = data.get("first_contact") or date.today().strftime("%Y-%m-%d")
    stage = data.get("stage") or "New Lead"

    with get_db() as conn:
        conn.execute(
            """INSERT INTO prospects
               (name, phone, email, source, priority, stage, product,
                aum, revenue, first_contact, next_followup, notes, send_channel)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                data.get("phone", ""),
                data.get("email", ""),
                data.get("source", ""),
                data.get("priority", ""),
                stage,
                data.get("product", ""),
                aum,
                revenue,
                first_contact,
                data.get("next_followup", ""),
                data.get("notes", ""),
                data.get("send_channel", "outlook"),
            ),
        )
    return f"Added {name} to pipeline."


def update_prospect(name: str, updates: dict) -> str:
    """Update a prospect by partial name match (case insensitive).
    Skips empty values. Returns status string."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name FROM prospects WHERE LOWER(name) LIKE ? LIMIT 1",
            (f"%{name.lower()}%",),
        ).fetchone()

        if not row:
            return f"Could not find prospect matching '{name}'."

        prospect_id = row["id"]
        matched_name = row["name"]

        allowed = {
            "name", "phone", "email", "source", "priority", "stage",
            "product", "aum", "revenue", "first_contact", "next_followup", "notes",
            "send_channel",
        }

        safe_fields = {}
        for field, value in updates.items():
            if field not in allowed or value is None:
                continue
            if field in ("aum", "revenue"):
                parsed = _parse_numeric(value)
                if parsed is not None:
                    value = parsed
            safe_fields[field] = value

        if not safe_fields:
            return f"No valid updates for {matched_name}."

        # Build SET clause using only validated field names from the allowlist
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = ?' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [prospect_id]
        conn.execute(
            f"UPDATE prospects SET {set_clauses}, updated_at = datetime('now') WHERE id = ?",
            values,
        )

    return f"Updated {matched_name}: {', '.join(f'{f} → {v}' for f, v in safe_fields.items())}"


def delete_prospect(name: str) -> str:
    """Delete a prospect by partial name match. Returns status string."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, name FROM prospects WHERE LOWER(name) LIKE ? LIMIT 1",
            (f"%{name.lower()}%",),
        ).fetchone()

        if not row:
            return f"Could not find prospect matching '{name}'."

        matched_name = row["name"]
        pid = row["id"]
        # Delete related records first to avoid foreign key constraint failures
        conn.execute("DELETE FROM client_memory WHERE prospect_id = ?", (pid,))
        conn.execute("DELETE FROM approval_queue WHERE prospect_id = ?", (pid,))
        conn.execute("DELETE FROM nurture_sequences WHERE prospect_id = ?", (pid,))
        conn.execute("DELETE FROM activities WHERE LOWER(prospect) = ?", (matched_name.lower(),))
        conn.execute("DELETE FROM interactions WHERE LOWER(prospect) = ?", (matched_name.lower(),))
        conn.execute("DELETE FROM prospects WHERE id = ?", (pid,))

    return f"Deleted {matched_name} from pipeline."


def merge_prospects(keep_name: str, merge_name: str) -> str:
    """Merge one prospect into another. Keeps keep_name, deletes merge_name.

    Transfers all activities, interactions, memory, approvals, and nurture
    sequences from merge_name to keep_name. Merges notes.
    """
    with get_db() as conn:
        keep = conn.execute(
            "SELECT * FROM prospects WHERE LOWER(name) LIKE ? LIMIT 1",
            (f"%{keep_name.lower()}%",),
        ).fetchone()
        merge = conn.execute(
            "SELECT * FROM prospects WHERE LOWER(name) LIKE ? LIMIT 1",
            (f"%{merge_name.lower()}%",),
        ).fetchone()

        if not keep:
            return f"Could not find prospect '{keep_name}'."
        if not merge:
            return f"Could not find prospect '{merge_name}'."
        if keep["id"] == merge["id"]:
            return "Cannot merge a prospect with itself."

        keep_id = keep["id"]
        merge_id = merge["id"]
        keep_real = keep["name"]
        merge_real = merge["name"]

        # Transfer activities and interactions (name-based)
        conn.execute(
            "UPDATE activities SET prospect = ? WHERE LOWER(prospect) = ?",
            (keep_real, merge_real.lower()),
        )
        conn.execute(
            "UPDATE interactions SET prospect = ? WHERE LOWER(prospect) = ?",
            (keep_real, merge_real.lower()),
        )

        # Transfer FK-based records
        conn.execute(
            "UPDATE client_memory SET prospect_id = ? WHERE prospect_id = ?",
            (keep_id, merge_id),
        )
        conn.execute(
            "UPDATE approval_queue SET prospect_id = ? WHERE prospect_id = ?",
            (keep_id, merge_id),
        )
        conn.execute(
            "UPDATE nurture_sequences SET prospect_id = ?, prospect_name = ? WHERE prospect_id = ?",
            (keep_id, keep_real, merge_id),
        )

        # Merge notes
        keep_notes = keep["notes"] or ""
        merge_notes = merge["notes"] or ""
        if merge_notes:
            combined = f"{keep_notes} | Merged from {merge_real}: {merge_notes}".strip(" |")
            conn.execute("UPDATE prospects SET notes = ? WHERE id = ?", (combined, keep_id))

        # Fill empty fields on keep from merge
        for field in ("phone", "email", "product", "aum", "revenue"):
            if not keep[field] and merge[field]:
                conn.execute(f"UPDATE prospects SET {field} = ? WHERE id = ?", (merge[field], keep_id))

        # Delete the merged prospect
        conn.execute("DELETE FROM prospects WHERE id = ?", (merge_id,))

    return f"Merged {merge_real} into {keep_real}."


def get_prospect_by_name(name: str):
    """Lookup by exact match first, then fuzzy partial match. Returns single dict or None."""
    with get_db() as conn:
        # Try exact match first
        row = conn.execute(
            "SELECT * FROM prospects WHERE LOWER(name) = ? LIMIT 1",
            (name.lower(),),
        ).fetchone()
        if row is None:
            # Fall back to partial match
            row = conn.execute(
                "SELECT * FROM prospects WHERE LOWER(name) LIKE ? LIMIT 1",
                (f"%{name.lower()}%",),
            ).fetchone()
            if row:
                logger.info(f"Prospect fuzzy match: '{name}' → '{dict(row)['name']}'")
    return _row_to_dict(row)


def get_prospect_by_email(email: str):
    """Lookup prospect by exact email match (case-insensitive). Returns dict or None."""
    if not email:
        return None
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM prospects WHERE LOWER(email) = ? LIMIT 1",
            (email.lower().strip(),),
        ).fetchone()
    return _row_to_dict(row)


# ── Activities CRUD ──

def add_activity(data: dict) -> str:
    """Add an entry to the activity log. Defaults date to today."""
    activity_date = data.get("date") or date.today().strftime("%Y-%m-%d")
    with get_db() as conn:
        conn.execute(
            """INSERT INTO activities (date, prospect, action, outcome, next_step, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                activity_date,
                data.get("prospect", ""),
                data.get("action", ""),
                data.get("outcome", ""),
                data.get("next_step", ""),
                data.get("notes", ""),
            ),
        )
    return f"Logged activity for {data.get('prospect', 'unknown')}."


def read_activities(limit: int = 100):
    """Return recent activities as list of dicts, newest first."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM activities ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return _rows_to_dicts(rows)


# ── Meetings CRUD ──

def add_meeting(data: dict) -> str:
    """Add a meeting. Defaults status to 'Scheduled'."""
    status = data.get("status") or "Scheduled"
    with get_db() as conn:
        conn.execute(
            """INSERT INTO meetings (date, time, prospect, type, prep_notes, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.get("date", ""),
                data.get("time", ""),
                data.get("prospect", ""),
                data.get("type", ""),
                data.get("prep_notes", ""),
                status,
            ),
        )
    return f"Meeting added: {data.get('prospect', '?')} on {data.get('date', '?')} at {data.get('time', '?')}"


def read_meetings():
    """Return all meetings ordered by date and time."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meetings ORDER BY date, time"
        ).fetchall()
    return _rows_to_dicts(rows)


def update_meeting(meeting_id: int, updates: dict) -> str:
    """Update a meeting by ID."""
    allowed = {"date", "time", "prospect", "type", "prep_notes", "status"}
    safe_fields = {f: v for f, v in updates.items() if f in allowed and v is not None}
    if not safe_fields:
        return f"No valid updates for meeting {meeting_id}."
    with get_db() as conn:
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = ?' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [meeting_id]
        conn.execute(f"UPDATE meetings SET {set_clauses} WHERE id = ?", values)
    return f"Updated meeting {meeting_id}: {', '.join(f'{f} → {v}' for f, v in safe_fields.items())}"


# ── Insurance Book CRUD ──

def read_insurance_book():
    """Return all insurance book entries."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM insurance_book ORDER BY id"
        ).fetchall()
    return _rows_to_dicts(rows)


def add_insurance_entry(data: dict) -> str:
    """Add an entry to the insurance book."""
    name = data.get("name", "").strip()
    if not name:
        return "No name provided for insurance entry."

    with get_db() as conn:
        conn.execute(
            """INSERT INTO insurance_book
               (name, phone, address, policy_start, status, last_called, notes, retry_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                data.get("phone", ""),
                data.get("address", ""),
                data.get("policy_start", ""),
                data.get("status", "Not Called"),
                data.get("last_called"),
                data.get("notes", ""),
                data.get("retry_date"),
            ),
        )
    return f"Added {name} to insurance book."


def update_insurance_entry(entry_id: int, updates: dict) -> str:
    """Update an insurance book entry by ID."""
    allowed = {"name", "phone", "address", "policy_start", "status",
               "last_called", "notes", "retry_date"}
    safe_fields = {f: v for f, v in updates.items() if f in allowed and v is not None}
    if not safe_fields:
        return f"No valid updates for insurance entry {entry_id}."
    with get_db() as conn:
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = ?' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [entry_id]
        conn.execute(f"UPDATE insurance_book SET {set_clauses} WHERE id = ?", values)
    return f"Updated insurance entry {entry_id}: {', '.join(f'{f} → {v}' for f, v in safe_fields.items())}"


# ── Win/Loss Log ──

def log_win_loss(prospect_name: str, outcome: str, reason: str, product: str = "") -> str:
    """Log a win or loss with reason."""
    # If product not provided, look it up from prospects
    if not product:
        p = get_prospect_by_name(prospect_name)
        if p:
            product = p.get("product", "")

    with get_db() as conn:
        conn.execute(
            """INSERT INTO win_loss_log (date, prospect, outcome, reason, product)
               VALUES (?, ?, ?, ?, ?)""",
            (
                date.today().strftime("%Y-%m-%d"),
                prospect_name,
                outcome,
                reason,
                product,
            ),
        )
    return f"Logged {outcome} for {prospect_name}: {reason}"


def get_win_loss_stats():
    """Return all win/loss entries as list of dicts."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM win_loss_log ORDER BY id DESC"
        ).fetchall()
    return _rows_to_dicts(rows)


# ── Interactions CRUD ──

def add_interaction(data: dict) -> str:
    """Log an interaction (voice note, transcript, email, booking)."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO interactions (date, prospect, source, raw_text, summary, action_items)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                data.get("prospect", ""),
                data.get("source", ""),
                data.get("raw_text", ""),
                data.get("summary", ""),
                data.get("action_items", ""),
            ),
        )
    return f"Logged interaction for {data.get('prospect', 'unknown')} via {data.get('source', '?')}."


def read_interactions(limit: int = 50, prospect: str = ""):
    """Return recent interactions, newest first. Optionally filter by prospect."""
    with get_db() as conn:
        if prospect:
            rows = conn.execute(
                "SELECT * FROM interactions WHERE LOWER(prospect) LIKE ? ORDER BY id DESC LIMIT ?",
                (f"%{prospect.lower()}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM interactions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return _rows_to_dicts(rows)


# ── Tasks CRUD ──

def add_task(data: dict):
    """Add a task. Returns the created task as dict, or None if no title."""
    title = data.get("title", "").strip()
    if not title:
        return None

    # Normalize remind_at to "YYYY-MM-DD HH:MM" (replace T from datetime-local inputs)
    remind_at = data.get("remind_at")
    if remind_at and isinstance(remind_at, str):
        remind_at = remind_at.replace("T", " ")

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks
               (title, prospect, due_date, remind_at, assigned_to, created_by, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                data.get("prospect", ""),
                data.get("due_date"),
                remind_at,
                data.get("assigned_to", ""),
                data.get("created_by", ""),
                data.get("notes", ""),
            ),
        )
        task_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row)


def get_tasks(assigned_to=None, status="pending", prospect=None, limit=50):
    """Get tasks with filters. Orders by due_date ASC (nulls last), then created_at DESC."""
    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if assigned_to:
        conditions.append("assigned_to = ?")
        params.append(assigned_to)
    if prospect:
        conditions.append("LOWER(prospect) LIKE ?")
        params.append(f"%{prospect.lower()}%")

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM tasks WHERE {where}
                ORDER BY
                    CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                    due_date ASC,
                    created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()
    return _rows_to_dicts(rows)


def update_task(task_id: int, updates: dict, updated_by: str = "", is_admin: bool = False) -> str:
    """Update a task's fields. Only assignee or admin can update."""
    allowed = {"title", "prospect", "due_date", "remind_at", "notes"}
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != updated_by:
            return f"Not authorized to update task {task_id}."
        safe_fields = {}
        for field, value in updates.items():
            if field not in allowed:
                continue
            if field == "remind_at" and value and isinstance(value, str):
                value = value.replace("T", " ")
            safe_fields[field] = value
        if not safe_fields:
            return f"No valid updates for task {task_id}."
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = ?' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [task_id]
        conn.execute(f"UPDATE tasks SET {set_clauses} WHERE id = ?", values)
    return f"Updated task {task_id}."


def complete_task(task_id: int, completed_by: str, is_admin: bool = False) -> str:
    """Mark a task as completed. Only assignee or admin can complete."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != completed_by:
            return f"Not authorized to complete task {task_id}."
        conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
            (task_id,),
        )
    return f"Completed: {row['title']}"


def delete_task(task_id: int, deleted_by: str, is_admin: bool = False) -> str:
    """Delete a task. Only assignee or admin can delete."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != deleted_by:
            return f"Not authorized to delete task {task_id}."
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return f"Deleted: {row['title']}"


def get_due_tasks(date_str: str):
    """Get pending tasks due on a specific date."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE due_date = ? AND status = 'pending' ORDER BY created_at",
            (date_str,),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_overdue_tasks():
    """Get pending tasks with due_date before today."""
    today = date.today().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE due_date < ? AND status = 'pending' ORDER BY due_date ASC",
            (today,),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_reminder_tasks(now_str: str):
    """Get pending tasks with remind_at <= now that haven't been cleared.
    Normalizes remind_at by replacing 'T' with space for consistent comparison."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE remind_at IS NOT NULL AND REPLACE(remind_at, 'T', ' ') <= ? AND status = 'pending' ORDER BY remind_at",
            (now_str.replace("T", " "),),
        ).fetchall()
    return _rows_to_dicts(rows)


def clear_reminder(task_id: int):
    """Clear remind_at after firing so it doesn't repeat."""
    with get_db() as conn:
        conn.execute("UPDATE tasks SET remind_at = NULL WHERE id = ?", (task_id,))


# ── Migration from Excel ──

def migrate_from_excel(excel_path: str) -> str:
    """Migrate data from the existing Excel pipeline file to SQLite.

    Skips if the database already has prospects.
    """
    import openpyxl

    if not os.path.exists(excel_path):
        return f"Excel file not found: {excel_path}"

    # Skip if DB already has data
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
        if count > 0:
            return f"Database already has {count} prospects. Skipping migration."

    wb = openpyxl.load_workbook(excel_path, data_only=True)

    def cell_str(ws, row, col):
        v = ws.cell(row=row, column=col).value
        return str(v) if v is not None else ""

    def cell_val(ws, row, col):
        return ws.cell(row=row, column=col).value

    migrated = {"prospects": 0, "activities": 0, "meetings": 0,
                "insurance": 0, "win_loss": 0}

    with get_db() as conn:
        # ── Pipeline sheet: starts row 5, columns 1-13 ──
        if "Pipeline" in wb.sheetnames:
            ws = wb["Pipeline"]
            for r in range(5, 5 + 80):
                name = cell_val(ws, r, 1)
                if not name:
                    continue
                aum = _parse_numeric(cell_val(ws, r, 8))
                revenue = _parse_numeric(cell_val(ws, r, 9))
                first_contact = _parse_date_val(cell_val(ws, r, 10))
                next_followup = _parse_date_val(cell_val(ws, r, 11))
                # Column 12 is days_open (computed), skip it
                conn.execute(
                    """INSERT INTO prospects
                       (name, phone, email, source, priority, stage, product,
                        aum, revenue, first_contact, next_followup, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cell_str(ws, r, 1),
                        cell_str(ws, r, 2),
                        cell_str(ws, r, 3),
                        cell_str(ws, r, 4),
                        cell_str(ws, r, 5),
                        cell_str(ws, r, 6) or "New Lead",
                        cell_str(ws, r, 7),
                        aum,
                        revenue,
                        first_contact or date.today().strftime("%Y-%m-%d"),
                        next_followup or "",
                        cell_str(ws, r, 13),
                    ),
                )
                migrated["prospects"] += 1

        # ── Activity Log sheet: starts row 3, columns 1-6 ──
        if "Activity Log" in wb.sheetnames:
            ws = wb["Activity Log"]
            for r in range(3, 3 + 200):
                d = cell_val(ws, r, 1)
                if not d:
                    continue
                conn.execute(
                    """INSERT INTO activities (date, prospect, action, outcome, next_step, notes)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        _parse_date_val(d) or cell_str(ws, r, 1),
                        cell_str(ws, r, 2),
                        cell_str(ws, r, 3),
                        cell_str(ws, r, 4),
                        cell_str(ws, r, 5),
                        cell_str(ws, r, 6),
                    ),
                )
                migrated["activities"] += 1

        # ── Meetings sheet: starts row 3, columns 1-6 ──
        if "Meetings" in wb.sheetnames:
            ws = wb["Meetings"]
            for r in range(3, 3 + 100):
                d = cell_val(ws, r, 1)
                if not d:
                    continue
                conn.execute(
                    """INSERT INTO meetings (date, time, prospect, type, prep_notes, status)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        _parse_date_val(d) or cell_str(ws, r, 1),
                        cell_str(ws, r, 2),
                        cell_str(ws, r, 3),
                        cell_str(ws, r, 4),
                        cell_str(ws, r, 5),
                        cell_str(ws, r, 6) or "Scheduled",
                    ),
                )
                migrated["meetings"] += 1

        # ── Insurance Book sheet: starts row 3, columns 1-8 ──
        if "Insurance Book" in wb.sheetnames:
            ws = wb["Insurance Book"]
            for r in range(3, 3 + 500):
                name = cell_val(ws, r, 1)
                if not name:
                    continue
                conn.execute(
                    """INSERT INTO insurance_book
                       (name, phone, address, policy_start, status, last_called, notes, retry_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cell_str(ws, r, 1),
                        cell_str(ws, r, 2),
                        cell_str(ws, r, 3),
                        _parse_date_val(cell_val(ws, r, 4)) or cell_str(ws, r, 4),
                        cell_str(ws, r, 5) or "Not Called",
                        _parse_date_val(cell_val(ws, r, 6)) or "",
                        cell_str(ws, r, 7),
                        _parse_date_val(cell_val(ws, r, 8)) or "",
                    ),
                )
                migrated["insurance"] += 1

        # ── Win Loss Log sheet: starts row 3, columns 1-5 ──
        if "Win Loss Log" in wb.sheetnames:
            ws = wb["Win Loss Log"]
            for r in range(3, 3 + 100):
                d = cell_val(ws, r, 1)
                if not d:
                    continue
                conn.execute(
                    """INSERT INTO win_loss_log (date, prospect, outcome, reason, product)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        _parse_date_val(d) or cell_str(ws, r, 1),
                        cell_str(ws, r, 2),
                        cell_str(ws, r, 3),
                        cell_str(ws, r, 4),
                        cell_str(ws, r, 5),
                    ),
                )
                migrated["win_loss"] += 1

    wb.close()

    summary = (
        f"Migration complete: {migrated['prospects']} prospects, "
        f"{migrated['activities']} activities, {migrated['meetings']} meetings, "
        f"{migrated['insurance']} insurance entries, {migrated['win_loss']} win/loss records."
    )
    logger.info(summary)
    return summary
