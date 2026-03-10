# Smart Sales Assistant + SQLite Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace Excel with SQLite and add a scoring engine, smart morning briefing, cross-sell engine, and referral nudges.

**Architecture:** New `db.py` centralizes all data access via SQLite. One-time migration reads existing Excel on first boot. `scoring.py` scores prospects 0-100 and generates actionable recommendations. Scheduler upgraded to use scoring for intelligent briefings.

**Tech Stack:** Python 3.13, sqlite3 (stdlib), python-telegram-bot 21.10, OpenAI gpt-4.1, Flask 3.1.1, APScheduler 3.10.4

---

## Task 1: Create `db.py` — Database Module

**Files:**
- Create: `db.py`

**Step 1: Write db.py with schema, connection, and all CRUD operations**

```python
"""
SQLite database module for Calm Money Pipeline Bot.
Replaces Excel (openpyxl) with SQLite for all data storage.
"""
import os
import sqlite3
import logging
from datetime import date, datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "")
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(DATA_DIR, "pipeline.db")
else:
    DB_PATH = "pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    phone TEXT DEFAULT '',
    email TEXT DEFAULT '',
    source TEXT DEFAULT '',
    priority TEXT DEFAULT '',
    stage TEXT DEFAULT 'New Lead',
    product TEXT DEFAULT '',
    aum REAL DEFAULT 0,
    revenue REAL DEFAULT 0,
    first_contact TEXT DEFAULT '',
    next_followup TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    prospect TEXT DEFAULT '',
    action TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    next_step TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT DEFAULT '',
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
    last_called TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    retry_date TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS win_loss_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    prospect TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    reason TEXT DEFAULT '',
    product TEXT DEFAULT ''
);
"""


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
    logger.info(f"Database initialized at {DB_PATH}")


@contextmanager
def get_conn():
    """Get a database connection with WAL mode for concurrent reads."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
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


# ── Prospects ──

def read_pipeline():
    """Read all prospects."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM prospects ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def add_prospect(data: dict) -> str:
    """Add a new prospect."""
    fields = {
        "name", "phone", "email", "source", "priority", "stage",
        "product", "aum", "revenue", "first_contact", "next_followup", "notes"
    }
    filtered = {k: v for k, v in data.items() if k in fields and v}

    # Parse numeric fields
    for f in ("aum", "revenue"):
        if f in filtered:
            try:
                filtered[f] = float(str(filtered[f]).replace("$", "").replace(",", ""))
            except (ValueError, TypeError):
                filtered[f] = 0

    if not filtered.get("first_contact"):
        filtered["first_contact"] = date.today().strftime("%Y-%m-%d")
    if not filtered.get("stage"):
        filtered["stage"] = "New Lead"

    cols = ", ".join(filtered.keys())
    placeholders = ", ".join("?" * len(filtered))
    with get_conn() as conn:
        conn.execute(f"INSERT INTO prospects ({cols}) VALUES ({placeholders})", list(filtered.values()))

    return f"Added {filtered.get('name', 'prospect')} to pipeline."


def update_prospect(name: str, updates: dict) -> str:
    """Update a prospect by name (partial match)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM prospects").fetchall()

    target = None
    name_lower = name.lower()
    for r in rows:
        if name_lower in r["name"].lower():
            target = r
            break

    if not target:
        return f"Could not find prospect matching '{name}'."

    fields = {
        "name", "phone", "email", "source", "priority", "stage",
        "product", "aum", "revenue", "first_contact", "next_followup", "notes"
    }
    filtered = {}
    for k, v in updates.items():
        if k in fields and v is not None and v != "":
            if k in ("aum", "revenue"):
                try:
                    v = float(str(v).replace("$", "").replace(",", ""))
                except (ValueError, TypeError):
                    continue
            filtered[k] = v

    if not filtered:
        return f"No valid fields to update for {target['name']}."

    filtered["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    with get_conn() as conn:
        conn.execute(f"UPDATE prospects SET {set_clause} WHERE id = ?",
                     list(filtered.values()) + [target["id"]])

    changes = [f"{k} → {v}" for k, v in filtered.items() if k != "updated_at"]
    return f"Updated {target['name']}: {', '.join(changes)}"


def delete_prospect(name: str) -> str:
    """Delete a prospect by name."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM prospects").fetchall()

    target = None
    name_lower = name.lower()
    for r in rows:
        if name_lower in r["name"].lower():
            target = r
            break

    if not target:
        return f"Could not find prospect matching '{name}'."

    with get_conn() as conn:
        conn.execute("DELETE FROM prospects WHERE id = ?", (target["id"],))

    return f"Deleted {target['name']} from pipeline."


def get_prospect_by_name(name: str):
    """Find a single prospect by partial name match."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM prospects").fetchall()
    name_lower = name.lower()
    for r in rows:
        if name_lower in r["name"].lower():
            return dict(r)
    return None


# ── Activities ──

def add_activity(data: dict) -> str:
    """Add entry to activity log."""
    fields = {"date", "prospect", "action", "outcome", "next_step", "notes"}
    filtered = {k: v for k, v in data.items() if k in fields and v}
    if not filtered.get("date"):
        filtered["date"] = date.today().strftime("%Y-%m-%d")

    cols = ", ".join(filtered.keys())
    placeholders = ", ".join("?" * len(filtered))
    with get_conn() as conn:
        conn.execute(f"INSERT INTO activities ({cols}) VALUES ({placeholders})", list(filtered.values()))

    return f"Logged activity for {filtered.get('prospect', 'unknown')}."


def read_activities(limit=100):
    """Read recent activities."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM activities ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ── Meetings ──

def add_meeting(data: dict) -> str:
    """Add a meeting."""
    fields = {"date", "time", "prospect", "type", "prep_notes", "status"}
    filtered = {k: v for k, v in data.items() if k in fields and v}
    if not filtered.get("status"):
        filtered["status"] = "Scheduled"

    cols = ", ".join(filtered.keys())
    placeholders = ", ".join("?" * len(filtered))
    with get_conn() as conn:
        conn.execute(f"INSERT INTO meetings ({cols}) VALUES ({placeholders})", list(filtered.values()))

    return f"Meeting added for {filtered.get('prospect', 'unknown')}."


def read_meetings():
    """Read all meetings."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM meetings ORDER BY date, time").fetchall()
    return [dict(r) for r in rows]


def update_meeting(meeting_id: int, updates: dict) -> str:
    """Update a meeting by ID."""
    fields = {"date", "time", "prospect", "type", "prep_notes", "status"}
    filtered = {k: v for k, v in updates.items() if k in fields and v is not None}
    if not filtered:
        return "No valid fields to update."
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    with get_conn() as conn:
        conn.execute(f"UPDATE meetings SET {set_clause} WHERE id = ?",
                     list(filtered.values()) + [meeting_id])
    return "Meeting updated."


# ── Insurance Book ──

def read_insurance_book():
    """Read all insurance book entries."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM insurance_book ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def update_insurance_entry(entry_id: int, updates: dict) -> str:
    """Update an insurance book entry."""
    fields = {"status", "last_called", "notes", "retry_date"}
    filtered = {k: v for k, v in updates.items() if k in fields and v is not None}
    if not filtered:
        return "No valid fields to update."
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    with get_conn() as conn:
        conn.execute(f"UPDATE insurance_book SET {set_clause} WHERE id = ?",
                     list(filtered.values()) + [entry_id])
    return "Entry updated."


def add_insurance_entry(data: dict) -> str:
    """Add an insurance book entry."""
    fields = {"name", "phone", "address", "policy_start", "status", "last_called", "notes", "retry_date"}
    filtered = {k: v for k, v in data.items() if k in fields and v}
    if not filtered.get("status"):
        filtered["status"] = "Not Called"

    cols = ", ".join(filtered.keys())
    placeholders = ", ".join("?" * len(filtered))
    with get_conn() as conn:
        conn.execute(f"INSERT INTO insurance_book ({cols}) VALUES ({placeholders})", list(filtered.values()))
    return f"Added {filtered.get('name', 'entry')} to insurance book."


# ── Win/Loss Log ──

def log_win_loss(prospect_name: str, outcome: str, reason: str, product: str = "") -> str:
    """Log a win or loss."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) VALUES (?, ?, ?, ?, ?)",
            (date.today().strftime("%Y-%m-%d"), prospect_name, outcome, reason, product)
        )
    return f"Logged {outcome} for {prospect_name}: {reason}"


def get_win_loss_stats():
    """Read all win/loss entries."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM win_loss_log ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ── Migration from Excel ──

def migrate_from_excel(excel_path: str):
    """One-time migration: read Excel pipeline and insert into SQLite."""
    try:
        import openpyxl
    except ImportError:
        logger.error("openpyxl not available for migration")
        return

    if not os.path.exists(excel_path):
        logger.info(f"No Excel file at {excel_path}, skipping migration")
        return

    # Check if we already have data
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    if count > 0:
        logger.info(f"Database already has {count} prospects, skipping migration")
        return

    logger.info(f"Migrating from {excel_path}...")
    wb = openpyxl.load_workbook(excel_path, data_only=True)

    # Pipeline
    if "Pipeline" in wb.sheetnames:
        ws = wb["Pipeline"]
        for r in range(5, 85):
            name = ws.cell(row=r, column=1).value
            if not name:
                continue
            data = {
                "name": str(name),
                "phone": str(ws.cell(row=r, column=2).value or ""),
                "email": str(ws.cell(row=r, column=3).value or ""),
                "source": str(ws.cell(row=r, column=4).value or ""),
                "priority": str(ws.cell(row=r, column=5).value or ""),
                "stage": str(ws.cell(row=r, column=6).value or ""),
                "product": str(ws.cell(row=r, column=7).value or ""),
                "aum": ws.cell(row=r, column=8).value or 0,
                "revenue": ws.cell(row=r, column=9).value or 0,
                "first_contact": str(ws.cell(row=r, column=10).value or "").split(" ")[0],
                "next_followup": str(ws.cell(row=r, column=11).value or "").split(" ")[0],
                "notes": str(ws.cell(row=r, column=13).value or ""),
            }
            # Parse numeric
            for f in ("aum", "revenue"):
                try:
                    data[f] = float(str(data[f]).replace("$", "").replace(",", ""))
                except (ValueError, TypeError):
                    data[f] = 0

            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO prospects (name, phone, email, source, priority, stage,
                       product, aum, revenue, first_contact, next_followup, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (data["name"], data["phone"], data["email"], data["source"],
                     data["priority"], data["stage"], data["product"], data["aum"],
                     data["revenue"], data["first_contact"], data["next_followup"], data["notes"])
                )

    # Activity Log
    if "Activity Log" in wb.sheetnames:
        ws = wb["Activity Log"]
        for r in range(3, 103):
            d = ws.cell(row=r, column=1).value
            if not d:
                continue
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO activities (date, prospect, action, outcome, next_step, notes) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(d).split(" ")[0], str(ws.cell(row=r, column=2).value or ""),
                     str(ws.cell(row=r, column=3).value or ""), str(ws.cell(row=r, column=4).value or ""),
                     str(ws.cell(row=r, column=5).value or ""), str(ws.cell(row=r, column=6).value or ""))
                )

    # Meetings
    if "Meetings" in wb.sheetnames:
        ws = wb["Meetings"]
        for r in range(3, 103):
            d = ws.cell(row=r, column=1).value
            if not d:
                continue
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO meetings (date, time, prospect, type, prep_notes, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (str(d).split(" ")[0], str(ws.cell(row=r, column=2).value or ""),
                     str(ws.cell(row=r, column=3).value or ""), str(ws.cell(row=r, column=4).value or ""),
                     str(ws.cell(row=r, column=5).value or ""), str(ws.cell(row=r, column=6).value or "Scheduled"))
                )

    # Insurance Book
    if "Insurance Book" in wb.sheetnames:
        ws = wb["Insurance Book"]
        for r in range(3, 203):
            name = ws.cell(row=r, column=1).value
            if not name:
                continue
            with get_conn() as conn:
                conn.execute(
                    """INSERT INTO insurance_book (name, phone, address, policy_start, status, last_called, notes, retry_date)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(name), str(ws.cell(row=r, column=2).value or ""),
                     str(ws.cell(row=r, column=3).value or ""),
                     str(ws.cell(row=r, column=4).value or "").split(" ")[0],
                     str(ws.cell(row=r, column=5).value or "Not Called"),
                     str(ws.cell(row=r, column=6).value or "").split(" ")[0],
                     str(ws.cell(row=r, column=7).value or ""),
                     str(ws.cell(row=r, column=8).value or "").split(" ")[0])
                )

    # Win Loss Log
    if "Win Loss Log" in wb.sheetnames:
        ws = wb["Win Loss Log"]
        for r in range(3, 103):
            d = ws.cell(row=r, column=1).value
            if not d:
                continue
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO win_loss_log (date, prospect, outcome, reason, product) VALUES (?, ?, ?, ?, ?)",
                    (str(d).split(" ")[0], str(ws.cell(row=r, column=2).value or ""),
                     str(ws.cell(row=r, column=3).value or ""), str(ws.cell(row=r, column=4).value or ""),
                     str(ws.cell(row=r, column=5).value or ""))
                )

    wb.close()

    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    logger.info(f"Migration complete: {count} prospects imported")
```

**Step 2: Verify syntax**

Run: `python3 -c "import py_compile; py_compile.compile('db.py', doraise=True)"`
Expected: No output (success)

**Step 3: Commit**

```bash
git add db.py
git commit -m "feat: add SQLite database module with migration from Excel"
```

---

## Task 2: Update `bot.py` — Switch from Excel to SQLite

**Files:**
- Modify: `bot.py`

**Step 1: Replace imports and initialization**

Remove:
- `import openpyxl`
- `from openpyxl.styles import Font, PatternFill, Alignment, Border, Side`
- `pipeline_lock = threading.RLock()`
- All Excel styling constants (TEAL, NAVY, WHITE, etc. lines 46-57)
- `DATA_START`, `MAX_ROWS`, `PIPELINE_COLS`, `LOG_COLS` constants (lines 61-74)
- `PIPELINE_PATH` setup (lines 26-38)

Add:
- `import db` at top
- In `main()`, before starting the bot, call `db.init_db()` and `db.migrate_from_excel("pipeline.xlsx")` (or from DATA_DIR path)

**Step 2: Replace all Excel CRUD functions**

Replace these functions with thin wrappers around `db.py`:
- `read_pipeline()` → `db.read_pipeline()`
- `add_prospect()` → `db.add_prospect()`
- `update_prospect()` → `db.update_prospect()`
- `delete_prospect()` → `db.delete_prospect()`
- `add_activity()` → `db.add_activity()`
- `log_win_loss()` → `db.log_win_loss()` (remove openpyxl sheet creation logic)
- `get_win_loss_stats()` → rewrite to use `db.get_win_loss_stats()` which returns list of dicts

Keep these functions unchanged (they don't use Excel):
- `get_term_quote()`, `get_disability_quote()`, `_fetch_term4sale()`
- `get_overdue()` — update to use `db.read_pipeline()`
- `auto_set_follow_up()` — update to use `db.update_prospect()`
- All the tool-calling functions and handlers

**Step 3: Update `get_overdue()`**

Change from `read_pipeline()` to `db.read_pipeline()`. The data format is the same (list of dicts) so the logic stays identical.

**Step 4: Update meeting/insurance book functions**

Replace any openpyxl meeting functions with `db.add_meeting()`, `db.read_meetings()`, etc.
Replace insurance book functions with `db.read_insurance_book()`, `db.update_insurance_entry()`, etc.

**Step 5: Keep `pipeline_lock` as a dummy export**

The scheduler and dashboard currently import `pipeline_lock` from bot.py. Keep it as a dummy so imports don't break, then remove it in Task 3/4:
```python
pipeline_lock = threading.RLock()  # DEPRECATED — kept for import compat during migration
```

**Step 6: Verify syntax and commit**

Run: `python3 -c "import py_compile; py_compile.compile('bot.py', doraise=True)"`

```bash
git add bot.py
git commit -m "feat: switch bot.py from Excel to SQLite via db.py"
```

---

## Task 3: Update `dashboard.py` — Switch from Excel to SQLite

**Files:**
- Modify: `dashboard.py`

**Step 1: Replace imports and data reading**

Remove:
- `import openpyxl`
- `from pathlib import Path`
- `PIPELINE_PATH` setup
- `_get_lock()` function
- `read_data()` and `_read_data_inner()` functions

Add:
- `import db`

Replace `read_data()` with:
```python
def read_data():
    prospects = db.read_pipeline()
    activities = db.read_activities()
    meetings = db.read_meetings()
    book_entries = db.read_insurance_book()
    return prospects, activities, meetings, book_entries
```

**Step 2: Simplify API endpoints**

Replace `api_add_prospect()`:
```python
@app.route("/api/prospect", methods=["POST"])
def api_add_prospect():
    data = request.json
    if not data or not data.get("name"):
        return jsonify({"error": "Name required"}), 400
    db.add_prospect(data)
    return jsonify({"ok": True})
```

Replace `api_update_prospect()`:
```python
@app.route("/api/prospect/<name>", methods=["PUT"])
def api_update_prospect(name):
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400
    result = db.update_prospect(name, data)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True})
```

Replace `api_delete_prospect()`:
```python
@app.route("/api/prospect/<name>", methods=["DELETE"])
def api_delete_prospect(name):
    result = db.delete_prospect(name)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True})
```

**Step 3: Fix data field access**

The dashboard accesses `p["aum"]` and `p["revenue"]` — these will now be actual floats from SQLite instead of strings/cell values. The `parse_money()` function already handles floats, so this should work. But verify `calc_fyc()` still works since `product` will now be a string directly.

**Step 4: Verify syntax and commit**

Run: `python3 -c "import py_compile; py_compile.compile('dashboard.py', doraise=True)"`

```bash
git add dashboard.py
git commit -m "feat: switch dashboard.py from Excel to SQLite via db.py"
```

---

## Task 4: Update `scheduler.py` — Switch from Excel to SQLite

**Files:**
- Modify: `scheduler.py`

**Step 1: Replace all Excel reading with db.py**

Remove:
- `import openpyxl`
- `PIPELINE_PATH`, `PIPELINE_DATA_START`, `PIPELINE_MAX_ROWS`, all column dicts
- `_get_lock()`, `_read_prospects()`, `_read_prospects_inner()`
- `_read_meetings_today()`, `_read_meetings_today_inner()`
- `_read_meetings_tomorrow()`, `_read_meetings_tomorrow_inner()`
- `_read_insurance_calls()`, `_read_insurance_calls_inner()`

Add:
- `import db`

Replace `_read_prospects()` calls with `db.read_pipeline()`.

For `_read_meetings_today()` and `_read_meetings_tomorrow()`, filter in Python:
```python
def _read_meetings_today():
    today_str = date.today().strftime("%Y-%m-%d")
    return [m for m in db.read_meetings() if m["date"].startswith(today_str)]

def _read_meetings_tomorrow():
    tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return [m for m in db.read_meetings() if m["date"].startswith(tomorrow_str)]
```

For `_read_insurance_calls()`:
```python
def _read_insurance_calls():
    today = date.today()
    calls = []
    for entry in db.read_insurance_book():
        status = (entry.get("status") or "").strip().lower()
        eligible = status in ("not called", "")
        if not eligible and entry.get("retry_date"):
            try:
                rd = datetime.strptime(entry["retry_date"].split(" ")[0], "%Y-%m-%d").date()
                eligible = rd <= today
            except (ValueError, IndexError):
                pass
        if eligible:
            calls.append(entry)
            if len(calls) >= INSURANCE_DAILY_LIMIT:
                break
    return calls
```

Update `_parse_date()` references — prospect dates are now strings like "2026-03-10" directly, so `_parse_date()` still works but simplify where possible.

The `morning_briefing()`, `auto_nag()`, and `weekly_report()` functions keep the same logic but use the new data sources.

For `weekly_report()`: replace the big `with _get_lock(): wb = openpyxl.load_workbook(...)` block with:
```python
raw_activities = [(a["date"], (a["action"] or "").lower()) for a in db.read_activities()]
raw_book = [(e.get("last_called", ""), (e.get("status") or "").lower()) for e in db.read_insurance_book()]
raw_wl = [(w["date"], (w["outcome"] or "").lower()) for w in db.get_win_loss_stats()]
```

**Step 2: Verify syntax and commit**

Run: `python3 -c "import py_compile; py_compile.compile('scheduler.py', doraise=True)"`

```bash
git add scheduler.py
git commit -m "feat: switch scheduler.py from Excel to SQLite via db.py"
```

---

## Task 5: Update `requirements.txt` — Remove openpyxl dependency

**Files:**
- Modify: `requirements.txt`

**Step 1: Remove openpyxl**

Keep openpyxl in requirements for now — it's still needed for the one-time migration. But add a comment:

```
openpyxl==3.1.5  # needed for one-time Excel migration only
```

**Step 2: Commit**

```bash
git add requirements.txt
git commit -m "chore: mark openpyxl as migration-only dependency"
```

---

## Task 6: Create `scoring.py` — Lead Scoring Engine

**Files:**
- Create: `scoring.py`

**Step 1: Write scoring.py**

```python
"""
Lead scoring engine for Calm Money Pipeline Bot.
Scores prospects 0-100 and generates actionable recommendations.
"""
import re
from datetime import date, datetime

import db


# ── Scoring Weights ──

def score_prospect(prospect: dict, avg_stage_days: dict = None) -> dict:
    """Score a prospect 0-100 with breakdown and recommendation.

    Returns: {
        "score": int,
        "reasons": [str],
        "action": str,
        "deal_score": float,
        "urgency_score": float,
        "stage_score": float,
        "priority_score": float,
    }
    """
    today = date.today()

    # ── Deal Size (40%) ──
    aum = float(prospect.get("aum") or 0)
    revenue = float(prospect.get("revenue") or 0)
    fyc = _calc_fyc(revenue, prospect.get("product", ""))

    # Normalize: $1M AUM or $10K premium or $5K FYC = max score
    aum_score = min(aum / 1_000_000, 1.0)
    rev_score = min(revenue / 10_000, 1.0)
    fyc_score = min(fyc / 5_000, 1.0)
    deal_score = max(aum_score, rev_score, fyc_score) * 40

    # ── Urgency (30%) ──
    urgency_score = 0
    fu = prospect.get("next_followup", "")
    stage = prospect.get("stage", "")
    fc = prospect.get("first_contact", "")

    # Overdue follow-up
    if fu and fu != "None":
        try:
            fu_date = datetime.strptime(fu.split(" ")[0], "%Y-%m-%d").date()
            days_overdue = (today - fu_date).days
            if days_overdue > 0:
                urgency_score = min(days_overdue / 14, 1.0) * 30  # 14 days overdue = max
        except (ValueError, IndexError):
            pass

    # Stage velocity — if in stage longer than average, increase urgency
    if avg_stage_days and stage in avg_stage_days and fc:
        try:
            fc_date = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
            days_in = (today - fc_date).days
            avg = avg_stage_days[stage]
            if avg > 0 and days_in > avg * 1.5:
                urgency_score = max(urgency_score, 20)  # bump urgency for stale deals
        except (ValueError, IndexError):
            pass

    # ── Stage Probability (20%) ──
    STAGE_PROB = {
        "New Lead": 0.05, "Contacted": 0.10, "Discovery Call": 0.20,
        "Needs Analysis": 0.35, "Plan Presentation": 0.50, "Proposal Sent": 0.65,
        "Negotiation": 0.80, "Nurture": 0.05,
    }
    stage_score = STAGE_PROB.get(stage, 0.1) * 20

    # ── Priority (10%) ──
    PRIORITY_MAP = {"hot": 10, "warm": 6, "cold": 2}
    priority_score = PRIORITY_MAP.get((prospect.get("priority") or "").lower(), 3)

    total = int(deal_score + urgency_score + stage_score + priority_score)
    total = min(total, 100)

    # ── Generate Action ──
    reasons = []
    if deal_score >= 20:
        reasons.append(f"High-value deal (${aum:,.0f} AUM)" if aum > revenue else f"${revenue:,.0f} premium")
    if urgency_score >= 15:
        reasons.append("Follow-up overdue")
    if stage_score >= 10:
        reasons.append(f"Close to closing ({stage})")
    if priority_score >= 8:
        reasons.append("Hot lead")

    action = _get_stage_action(prospect, today, avg_stage_days)

    return {
        "score": total,
        "reasons": reasons,
        "action": action,
        "deal_score": deal_score,
        "urgency_score": urgency_score,
        "stage_score": stage_score,
        "priority_score": priority_score,
    }


def _get_stage_action(prospect: dict, today: date, avg_stage_days: dict = None) -> str:
    """Get a specific recommended action based on stage and staleness."""
    stage = prospect.get("stage", "")
    fu = prospect.get("next_followup", "")
    fc = prospect.get("first_contact", "")

    # Check if stale
    is_stale = False
    if fc:
        try:
            fc_date = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
            days_in = (today - fc_date).days
            if avg_stage_days and stage in avg_stage_days:
                is_stale = days_in > avg_stage_days[stage] * 1.5
            else:
                is_stale = days_in > 14
        except (ValueError, IndexError):
            pass

    STALE_ACTIONS = {
        "New Lead": "Try a different channel — call instead of email",
        "Contacted": "Try a different channel — call if you emailed, email if you called",
        "Discovery Call": "Send a relevant article or rate comparison to re-engage",
        "Needs Analysis": "Offer to run fresh numbers — get a new quote",
        "Plan Presentation": "Ask if they had time to review, offer a quick recap call",
        "Proposal Sent": "Follow up with urgency — rates may change",
        "Negotiation": "Direct ask — what's holding you back?",
        "Nurture": "Share a market update or relevant content piece",
    }

    STANDARD_ACTIONS = {
        "New Lead": "Make first contact — introduce yourself and book a discovery call",
        "Contacted": "Follow up on initial contact",
        "Discovery Call": "Prepare questions, understand their full financial picture",
        "Needs Analysis": "Build their plan, run the numbers",
        "Plan Presentation": "Present the plan, address objections",
        "Proposal Sent": "Check if they received it, answer questions",
        "Negotiation": "Handle objections, close the deal",
        "Nurture": "Stay in touch with value-add content",
    }

    if is_stale:
        return STALE_ACTIONS.get(stage, "Follow up — this deal is going cold")
    return STANDARD_ACTIONS.get(stage, "Follow up")


def _calc_fyc(premium, product):
    """Calculate FYC from premium and product."""
    try:
        prem = float(str(premium).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0
    if prem <= 0:
        return 0
    term_match = re.search(r'(\d+)', str(product or ""))
    if not term_match:
        return 0
    term = int(term_match.group(1))
    if term in (20, 25, 30):
        return prem * 11.11 * 0.5
    elif term in (10, 15):
        return prem * 11.11 * 0.4
    return 0


# ── Cross-Sell ──

CROSS_SELL_MATRIX = {
    "Life Insurance": ["Disability Insurance", "Critical Illness", "Wealth Management"],
    "Wealth Management": ["Life Insurance", "Estate Planning"],
    "Disability Insurance": ["Critical Illness", "Life Insurance"],
    "Critical Illness": ["Disability Insurance", "Life Insurance"],
    "Group Benefits": ["Life Insurance", "Wealth Management"],
    "Estate Planning": ["Life Insurance", "Wealth Management"],
}


def get_cross_sell_suggestions(product: str) -> list:
    """Given a product, return list of cross-sell opportunities."""
    for key, suggestions in CROSS_SELL_MATRIX.items():
        if key.lower() in (product or "").lower():
            return suggestions
    return []


# ── Referral Nudge ──

def get_referral_candidates():
    """Find won clients who should be asked for referrals.
    14 days after close: first nudge. 90 days: second nudge.
    """
    today = date.today()
    prospects = db.read_pipeline()
    candidates = []

    for p in prospects:
        if p.get("stage") != "Closed-Won":
            continue
        fc = p.get("first_contact", "")
        if not fc or fc == "None":
            continue
        try:
            close_date = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
            days_since = (today - close_date).days
            notes = (p.get("notes") or "").lower()
            referral_asked = "referral" in notes

            if days_since >= 14 and days_since <= 30 and not referral_asked:
                candidates.append({
                    "name": p["name"],
                    "product": p.get("product", ""),
                    "days_since_close": days_since,
                    "nudge_type": "first",
                })
            elif days_since >= 90 and days_since <= 120 and not referral_asked:
                candidates.append({
                    "name": p["name"],
                    "product": p.get("product", ""),
                    "days_since_close": days_since,
                    "nudge_type": "second",
                })
        except (ValueError, IndexError):
            pass

    return candidates


# ── Ranked Call List ──

def get_ranked_call_list(limit=10):
    """Get the top prospects to call, ranked by score."""
    prospects = db.read_pipeline()
    today = date.today()

    active = [p for p in prospects if p.get("stage") not in ("Closed-Won", "Closed-Lost", "")]
    if not active:
        return []

    # Calculate average days in each stage
    avg_stage_days = {}
    stage_days_lists = {}
    for p in active:
        stage = p.get("stage", "")
        fc = p.get("first_contact", "")
        if stage and fc and fc != "None":
            try:
                fc_date = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
                days = (today - fc_date).days
                if stage not in stage_days_lists:
                    stage_days_lists[stage] = []
                stage_days_lists[stage].append(days)
            except (ValueError, IndexError):
                pass

    for stage, days_list in stage_days_lists.items():
        avg_stage_days[stage] = sum(days_list) / len(days_list)

    # Score each prospect
    scored = []
    for p in active:
        result = score_prospect(p, avg_stage_days)
        scored.append({**p, **result})

    scored.sort(key=lambda x: -x["score"])
    return scored[:limit]
```

**Step 2: Verify syntax**

Run: `python3 -c "import py_compile; py_compile.compile('scoring.py', doraise=True)"`

**Step 3: Commit**

```bash
git add scoring.py
git commit -m "feat: add lead scoring engine with cross-sell and referral nudges"
```

---

## Task 7: Upgrade Morning Briefing + Add `/priority` Command

**Files:**
- Modify: `scheduler.py` (morning briefing)
- Modify: `bot.py` (add /priority command)

**Step 1: Rewrite `morning_briefing()` in scheduler.py**

Replace the existing `morning_briefing()` function to use scoring:

```python
async def morning_briefing():
    """Send the daily Money Moves briefing at 8:00 AM ET."""
    if not _bot or not CHAT_ID:
        return

    import scoring

    today = date.today()
    lines = [f"MONEY MOVES — {today.strftime('%A, %B %d')}", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]

    # Top 5 call list
    ranked = scoring.get_ranked_call_list(5)
    if ranked:
        lines.append("TOP CALLS TODAY:")
        for i, p in enumerate(ranked, 1):
            reasons_str = " | ".join(p["reasons"][:2]) if p["reasons"] else ""
            lines.append(f"  {i}. {p['name']} (score: {p['score']})")
            if reasons_str:
                lines.append(f"     Why: {reasons_str}")
            lines.append(f"     Do: {p['action']}")
        lines.append("")

    # Cross-sell opportunities (recent wins)
    prospects = db.read_pipeline()
    won = [p for p in prospects if p.get("stage") == "Closed-Won"]
    active = [p for p in prospects if p.get("stage") not in ("Closed-Won", "Closed-Lost", "")]
    for p in won:
        fc = p.get("first_contact", "")
        if fc and fc != "None":
            try:
                close_date = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
                if (today - close_date).days <= 30:
                    suggestions = scoring.get_cross_sell_suggestions(p.get("product", ""))
                    if suggestions:
                        lines.append(f"CROSS-SELL: {p['name']} has {p.get('product', '?')} — suggest {', '.join(suggestions[:2])}")
            except (ValueError, IndexError):
                pass

    # Referral nudges
    referral_candidates = scoring.get_referral_candidates()
    if referral_candidates:
        lines.append("")
        lines.append("REFERRAL OPPORTUNITIES:")
        for c in referral_candidates:
            lines.append(f"  - Ask {c['name']} for a referral ({c['days_since_close']}d since close)")

    # Pipeline snapshot
    total_aum = sum(float(p.get("aum") or 0) for p in active)
    total_rev = sum(float(p.get("revenue") or 0) for p in active)
    hot_count = len([p for p in active if (p.get("priority") or "").lower() == "hot"])

    lines.append("")
    lines.append("PIPELINE:")
    lines.append(f"  Active: {len(active)} | AUM: ${total_aum:,.0f} | Premium: ${total_rev:,.0f} | Hot: {hot_count}")

    # Meetings today
    meetings = _read_meetings_today()
    if meetings:
        lines.append("")
        lines.append(f"MEETINGS TODAY ({len(meetings)}):")
        for m in meetings:
            lines.append(f"  - {m.get('time', '?')} — {m.get('prospect', '?')} ({m.get('type', '?')})")

    msg = "\n".join(lines)
    await _bot.send_message(chat_id=CHAT_ID, text=msg)
    logger.info("Morning briefing (Money Moves) sent.")
```

**Step 2: Add cross-sell trigger after stage change to Closed-Won in bot.py**

In `update_prospect()` in db.py or in the bot's tool-processing logic, detect when stage changes to "Closed-Won" and suggest cross-sell + schedule referral nudge.

Add to bot.py after `update_prospect` tool call returns:
```python
# After update_prospect processes, check for Closed-Won trigger
if "stage" in updates and updates["stage"] == "Closed-Won":
    import scoring
    suggestions = scoring.get_cross_sell_suggestions(updates.get("product", ""))
    if suggestions:
        reply += f"\n\nCross-sell opportunity: suggest {', '.join(suggestions[:2])} in 30 days. Want me to set a follow-up?"
```

**Step 3: Add `/priority` command to bot.py**

```python
async def cmd_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show ranked call list with scores and actions."""
    import scoring
    ranked = scoring.get_ranked_call_list(10)
    if not ranked:
        await update.message.reply_text("No active deals in pipeline.")
        return

    lines = ["YOUR CALL LIST (ranked by score):", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for i, p in enumerate(ranked, 1):
        reasons_str = " | ".join(p["reasons"][:2]) if p["reasons"] else ""
        lines.append(f"{i}. {p['name']} — score: {p['score']}")
        lines.append(f"   Stage: {p.get('stage', '?')} | {p.get('priority', '?')}")
        if reasons_str:
            lines.append(f"   Why: {reasons_str}")
        lines.append(f"   Do: {p['action']}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))
```

Register: `app.add_handler(CommandHandler("priority", cmd_priority))`

**Step 4: Verify and commit**

```bash
python3 -c "import py_compile; py_compile.compile('bot.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('scheduler.py', doraise=True)"
git add bot.py scheduler.py
git commit -m "feat: smart morning briefing, cross-sell triggers, /priority command"
```

---

## Task 8: Test End-to-End + Push

**Step 1: Run a quick local sanity check**

```bash
python3 -c "
import db
db.init_db()
db.add_prospect({'name': 'Test Person', 'stage': 'Discovery Call', 'priority': 'Hot', 'aum': 500000})
print(db.read_pipeline())
import scoring
ranked = scoring.get_ranked_call_list()
print(ranked[0]['score'], ranked[0]['action'])
db.delete_prospect('Test Person')
print('PASS')
"
```

**Step 2: Push to Railway**

```bash
git push
```

**Step 3: Verify on Railway**
- Check logs for "Database initialized" and "Migration complete: N prospects imported"
- Test `/priority` command in Telegram
- Wait for morning briefing (or trigger manually)
