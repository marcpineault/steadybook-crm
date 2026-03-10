# Smart Sales Assistant + SQLite Migration — Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate from Excel to SQLite and transform the bot from a record keeper into a proactive sales coach that maximizes revenue per prospect.

**Architecture:** SQLite database replaces Excel. New scoring engine scores prospects daily. Morning briefing upgraded to ranked call list with cross-sell and referral nudges. All existing functionality preserved.

**Tech Stack:** Python 3.13, SQLite3 (stdlib), python-telegram-bot, OpenAI gpt-4.1, Flask, APScheduler

---

## Part 1: SQLite Migration

### Database Schema

```sql
CREATE TABLE prospects (
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

CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    prospect TEXT DEFAULT '',
    action TEXT DEFAULT '',
    outcome TEXT DEFAULT '',
    next_step TEXT DEFAULT '',
    notes TEXT DEFAULT ''
);

CREATE TABLE meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT DEFAULT '',
    time TEXT DEFAULT '',
    prospect TEXT DEFAULT '',
    type TEXT DEFAULT '',
    prep_notes TEXT DEFAULT '',
    status TEXT DEFAULT 'Scheduled'
);

CREATE TABLE insurance_book (
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
```

### Migration Strategy

1. New `db.py` module with all database operations
2. One-time migration script reads existing Excel, inserts into SQLite
3. `bot.py`, `dashboard.py`, `scheduler.py` all switch from openpyxl to db.py
4. Remove openpyxl from requirements.txt
5. Pipeline lock replaced by SQLite's built-in concurrency
6. DATA_DIR logic preserved (db file at `$DATA_DIR/pipeline.db` or local `pipeline.db`)

### What changes per file

- **db.py** (NEW): All CRUD operations, migration function, connection management
- **bot.py**: Replace all openpyxl read/write with db.py calls. Remove pipeline_lock, Excel styling constants
- **dashboard.py**: Replace read_data() with db queries. Remove openpyxl import. Simplify API endpoints
- **scheduler.py**: Replace Excel reads with db queries. Remove openpyxl import

---

## Part 2: Smart Sales Assistant

### 2a. Lead Scoring Engine

Score every active prospect 0-100:
- Deal size (AUM + premium + FYC potential) — 40% weight
- Urgency (days overdue, days in stage vs average) — 30% weight
- Stage probability (closer to close = higher) — 20% weight
- Priority (Hot/Warm/Cold) — 10% weight

Lives in `scoring.py`. Called by morning briefing and `/priority` command.

### 2b. Morning Briefing v2 ("Money Moves")

Daily at 8AM ET, replaces current briefing:
- Top 5 ranked prospects with score, reason, and suggested action
- Stale deal alerts (deals above average days in their stage)
- Cross-sell opportunities on recent wins
- Referral nudge on clients won 14-30 days ago
- Quick stats: pipeline value, FYC in play

### 2c. Cross-Sell Matrix

When a deal closes, bot suggests next product:
- Life Insurance -> Disability, Critical Illness, Wealth Management
- Wealth Management -> Life Insurance, Estate Planning
- Disability -> Critical Illness, Life Insurance
- Critical Illness -> Disability, Life Insurance
- Group Benefits -> Life Insurance (personal), Wealth Management

Auto-offers to schedule 30-day follow-up for cross-sell.

### 2d. Referral Nudge System

- 14 days after Closed-Won: suggest asking for referral
- 90 days: second nudge if none logged
- Tracked via notes or activity log

### 2e. Stale Deal Interventions

Stage-specific suggested actions:
- New Lead/Contacted (stale): "Try a different channel"
- Discovery Call (stale): "Send a relevant article or rate comparison"
- Needs Analysis (stale): "Offer to run numbers — get a fresh quote"
- Proposal Sent (stale): "Follow up with urgency — rates may change"
- Negotiation (stale): "Direct ask — what's holding you back?"

### 2f. New `/priority` Command

On-demand ranked call list with scores and suggested actions.

---

## Implementation Order

1. SQLite migration (db.py + migrate existing data)
2. Update bot.py to use db.py
3. Update dashboard.py to use db.py
4. Update scheduler.py to use db.py
5. Scoring engine (scoring.py)
6. Morning briefing v2
7. Cross-sell + referral nudges
8. `/priority` command
