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
        """)
    logger.info(f"Database initialized at {DB_PATH}")


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
                aum, revenue, first_contact, next_followup, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
        }

        changes = []
        for field, value in updates.items():
            if field not in allowed or not value:
                continue
            if field in ("aum", "revenue"):
                parsed = _parse_numeric(value)
                if parsed is not None:
                    value = parsed
            conn.execute(
                f"UPDATE prospects SET {field} = ?, updated_at = datetime('now') WHERE id = ?",
                (value, prospect_id),
            )
            changes.append(f"{field} → {value}")

        if not changes:
            return f"No valid updates for {matched_name}."

    return f"Updated {matched_name}: {', '.join(changes)}"


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
        conn.execute("DELETE FROM prospects WHERE id = ?", (row["id"],))

    return f"Deleted {matched_name} from pipeline."


def get_prospect_by_name(name: str):
    """Partial match lookup. Returns single dict or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM prospects WHERE LOWER(name) LIKE ? LIMIT 1",
            (f"%{name.lower()}%",),
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
    with get_db() as conn:
        changes = []
        for field, value in updates.items():
            if field not in allowed:
                continue
            conn.execute(
                f"UPDATE meetings SET {field} = ? WHERE id = ?",
                (value, meeting_id),
            )
            changes.append(f"{field} → {value}")

        if not changes:
            return f"No valid updates for meeting {meeting_id}."

    return f"Updated meeting {meeting_id}: {', '.join(changes)}"


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
    with get_db() as conn:
        changes = []
        for field, value in updates.items():
            if field not in allowed:
                continue
            conn.execute(
                f"UPDATE insurance_book SET {field} = ? WHERE id = ?",
                (value, entry_id),
            )
            changes.append(f"{field} → {value}")

        if not changes:
            return f"No valid updates for insurance entry {entry_id}."

    return f"Updated insurance entry {entry_id}: {', '.join(changes)}"


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
