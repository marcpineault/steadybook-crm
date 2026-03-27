# SteadyBook SaaS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate SteadyBook CRM from single-user SQLite to a fully multi-tenant SaaS on Neon Postgres + Railway, with real auth, per-tenant encrypted config, graceful degradation, and a working Telegram bot worker.

**Architecture:** Row-level tenant isolation enforced in `db.py` via a `contextvars.ContextVar`. All function signatures stay identical — only the connection layer and SQL dialect change. Per-tenant API keys stored encrypted in `tenant_config` table and accessed via `config_store.py`.

**Tech Stack:** Python 3.13, Flask, psycopg2-binary, bcrypt, cryptography (Fernet), Neon Postgres, Railway, Gunicorn

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `requirements.txt` | Modify | Add psycopg2-binary, bcrypt, cryptography |
| `db.py` | Rewrite | Postgres connection, all SQL updated, tenant context var |
| `config_store.py` | Create | Encrypted per-tenant config get/set |
| `dashboard.py` | Modify | Complete auth routes, graceful degradation, wire tenant config |
| `bot.py` | Modify | Multi-tenant routing by chat_id, per-tenant config |
| `Procfile` | Modify | Run bot.py as worker |
| `Dockerfile` | Modify | Remove SQLite init, use DATABASE_URL |
| `tests/test_db.py` | Create | Integration tests for db.py against test Postgres |
| `tests/test_config_store.py` | Create | Unit tests for config_store |
| `tests/test_auth.py` | Create | Auth flow integration tests |

---

## Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update requirements.txt**

Replace contents of `requirements.txt` with:

```
psycopg2-binary==2.9.10
openai>=1.40.0
openpyxl==3.1.5
python-dotenv==1.1.0
flask==3.1.1
flask-limiter>=3.5.0
apscheduler==3.10.4
pytz==2024.1
requests>=2.31.0
twilio>=9.0.0
gunicorn>=21.2.0
bcrypt==4.2.1
cryptography==44.0.2
python-telegram-bot==21.10
```

- [ ] **Step 2: Verify install locally**

```bash
pip install -r requirements.txt
```

Expected: all packages install without error.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: replace sqlite3 with psycopg2, add bcrypt and cryptography"
```

---

## Task 2: Create config_store.py

**Files:**
- Create: `config_store.py`
- Create: `tests/test_config_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_config_store.py`:

```python
"""Unit tests for config_store — no DB needed, uses monkeypatch."""
import os
import pytest
from unittest.mock import patch, MagicMock
from cryptography.fernet import Fernet


def _make_store(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    import importlib
    import config_store
    importlib.reload(config_store)
    return config_store


def test_encrypt_decrypt_roundtrip(monkeypatch):
    cs = _make_store(monkeypatch)
    encrypted = cs.encrypt_value("my-secret-key")
    assert encrypted != "my-secret-key"
    assert cs.decrypt_value(encrypted) == "my-secret-key"


def test_decrypt_empty_returns_empty(monkeypatch):
    cs = _make_store(monkeypatch)
    assert cs.decrypt_value("") == ""


def test_get_config_hits_db_first(monkeypatch):
    cs = _make_store(monkeypatch)
    encrypted = cs.encrypt_value("db-value")
    mock_row = {"value": encrypted}

    with patch("config_store._fetch_row", return_value=mock_row):
        result = cs.get_config(1, "OPENAI_API_KEY")
    assert result == "db-value"


def test_get_config_falls_back_to_env(monkeypatch):
    cs = _make_store(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")

    with patch("config_store._fetch_row", return_value=None):
        result = cs.get_config(1, "OPENAI_API_KEY")
    assert result == "env-key"


def test_get_config_returns_empty_when_nothing_set(monkeypatch):
    cs = _make_store(monkeypatch)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with patch("config_store._fetch_row", return_value=None):
        result = cs.get_config(1, "OPENAI_API_KEY")
    assert result == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_config_store.py -v
```

Expected: `ModuleNotFoundError: No module named 'config_store'`

- [ ] **Step 3: Create config_store.py**

Create `config_store.py`:

```python
"""Per-tenant encrypted configuration store.

Keys are stored in the tenant_config table as Fernet-encrypted values.
Falls back to environment variables when no DB row exists.

Usage:
    from config_store import get_config, set_config

    openai_key = get_config(tenant_id, "OPENAI_API_KEY")
    set_config(tenant_id, "OPENAI_API_KEY", "sk-...")
"""

import os
import logging
from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# ── Encryption setup ──

def _get_fernet() -> Fernet:
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        # Auto-generate and warn — on Railway this should be set as a var
        key = Fernet.generate_key().decode()
        logger.warning(
            "ENCRYPTION_KEY not set — generated ephemeral key. "
            "Set ENCRYPTION_KEY env var to persist encrypted config across restarts."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a plaintext string. Returns base64-encoded ciphertext."""
    if not plaintext:
        return ""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext. Returns plaintext or empty string on failure."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt config value — key may have changed")
        return ""


# ── DB access (thin wrapper so tests can monkeypatch) ──

def _fetch_row(tenant_id: int, key: str):
    """Fetch raw DB row for a tenant config key. Returns dict or None."""
    import db
    with db.get_db() as conn:
        cur = conn.execute(
            "SELECT value FROM tenant_config WHERE tenant_id = %s AND key = %s",
            (tenant_id, key),
        )
        return cur.fetchone()


def _upsert_row(tenant_id: int, key: str, encrypted_value: str) -> None:
    """Upsert an encrypted config value into tenant_config."""
    import db
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO tenant_config (tenant_id, key, value, updated_at)
               VALUES (%s, %s, %s, NOW())
               ON CONFLICT (tenant_id, key)
               DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            (tenant_id, key, encrypted_value),
        )


# ── Public API ──

def get_config(tenant_id: int, key: str) -> str:
    """Return config value for tenant. DB first, then env var, then empty string."""
    try:
        row = _fetch_row(tenant_id, key)
        if row and row.get("value"):
            return decrypt_value(row["value"])
    except Exception:
        logger.warning("config_store: DB lookup failed for key=%s, falling back to env", key)
    return os.environ.get(key, "")


def set_config(tenant_id: int, key: str, value: str) -> None:
    """Encrypt and persist a config value for a tenant."""
    encrypted = encrypt_value(value)
    _upsert_row(tenant_id, key, encrypted)


def get_all_config(tenant_id: int) -> dict:
    """Return all config keys for a tenant (decrypted). Never returns encrypted values."""
    import db
    try:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM tenant_config WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchall()
        return {row["key"]: decrypt_value(row["value"]) for row in rows}
    except Exception:
        logger.warning("config_store: get_all_config failed for tenant_id=%s", tenant_id)
        return {}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python3 -m pytest tests/test_config_store.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add config_store.py tests/test_config_store.py
git commit -m "feat: add encrypted per-tenant config store with Fernet"
```

---

## Task 3: Rewrite db.py — Connection Layer

**Files:**
- Modify: `db.py` (lines 1–50: imports, DB_PATH, get_db, helpers)

This task only touches the connection layer. No query functions change yet.

- [ ] **Step 1: Write failing connection test**

Create `tests/test_db.py`:

```python
"""Integration tests for db.py — requires DATABASE_URL env var pointing to a test Postgres DB."""
import os
import pytest

# Skip all tests if no DATABASE_URL
pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set — skipping Postgres integration tests"
)


def test_get_db_connects():
    import db
    with db.get_db() as conn:
        cur = conn.execute("SELECT 1 AS val")
        row = cur.fetchone()
    assert row["val"] == 1


def test_init_db_creates_tables():
    import db
    db.init_db()
    with db.get_db() as conn:
        cur = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        )
        tables = {r["table_name"] for r in cur.fetchall()}
    assert "prospects" in tables
    assert "tenants" in tables
    assert "tenant_config" in tables
    assert "users" in tables


def test_tenant_context_var():
    import db
    db._current_tenant_id.set(42)
    assert db._current_tenant_id.get() == 42
    db._current_tenant_id.set(1)
```

- [ ] **Step 2: Run test — verify it fails**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/test_db.py::test_get_db_connects -v
```

Expected: `ModuleNotFoundError` or `psycopg2` import error.

- [ ] **Step 3: Replace top of db.py (lines 1–50)**

Replace the entire imports + connection section with:

```python
"""
PostgreSQL database module for SteadyBook CRM.

Multi-tenant: all queries scoped by tenant_id via _current_tenant_id context var.
The context var is set by the auth middleware in dashboard.py on every request.

Usage:
    from db import init_db, add_prospect, read_pipeline
    init_db()
"""

import os
import re
import secrets
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

# ── Tenant context ──
# Set this at the start of every authenticated request.
# All query functions read it as their default tenant_id.
_current_tenant_id: ContextVar[int] = ContextVar("_current_tenant_id", default=1)

# ── Connection ──

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    logger.warning("DATABASE_URL not set — database operations will fail")


@contextmanager
def get_db():
    """Context manager for Postgres connections using RealDictCursor."""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row):
    """Convert a RealDictRow to a plain dict."""
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows):
    """Convert a list of RealDictRow to list of dicts."""
    return [dict(r) for r in rows] if rows else []
```

Also remove the `DATA_DIR` / `DB_PATH` block that follows (lines ~21–28 in old code) — it's SQLite-only.

- [ ] **Step 4: Run connection test**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/test_db.py::test_get_db_connects -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: replace sqlite3 connection layer with psycopg2 + tenant context var"
```

---

## Task 4: Rewrite db.py — init_db() PostgreSQL DDL

**Files:**
- Modify: `db.py` (init_db function and all _migrate_* functions)

All `executescript` calls, `AUTOINCREMENT`, `datetime('now')`, `INTEGER PRIMARY KEY`, and `PRAGMA` statements must be replaced. Each table becomes a separate `conn.execute()` call.

- [ ] **Step 1: Replace init_db() and all migration functions**

Replace `init_db()` (line 123 through end of `_migrate_multi_tenant()` ~line 566) with:

```python
def init_db():
    """Create all tables and run migrations. Safe to call repeatedly."""
    _create_core_tables()
    _create_tenant_tables()
    _create_sequence_tables()
    _create_sms_tables()
    _create_tracking_tables()
    _ensure_default_tenant()
    logger.info("Database initialization complete")


def _create_core_tables():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                slug        TEXT NOT NULL UNIQUE,
                company     TEXT DEFAULT '',
                timezone    TEXT DEFAULT 'America/Toronto',
                products    TEXT DEFAULT '[]',
                config      TEXT DEFAULT '{}',
                stripe_customer_id TEXT,
                plan        TEXT DEFAULT 'starter',
                status      TEXT DEFAULT 'active',
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                updated_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            SERIAL PRIMARY KEY,
                tenant_id     INTEGER NOT NULL REFERENCES tenants(id),
                email         TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                name          TEXT DEFAULT '',
                role          TEXT DEFAULT 'advisor',
                telegram_chat_id TEXT,
                status        TEXT DEFAULT 'active',
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                last_login    TIMESTAMPTZ
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(LOWER(email))")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenant_config (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                updated_at  TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(tenant_id, key)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
                key_hash    TEXT NOT NULL,
                name        TEXT DEFAULT 'Default',
                scopes      TEXT DEFAULT '["all"]',
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                expires_at  TIMESTAMPTZ
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prospects (
                id            SERIAL PRIMARY KEY,
                tenant_id     INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                name          TEXT NOT NULL,
                phone         TEXT DEFAULT '',
                email         TEXT DEFAULT '',
                source        TEXT DEFAULT '',
                priority      TEXT DEFAULT '',
                stage         TEXT DEFAULT 'New Lead',
                product       TEXT DEFAULT '',
                aum           REAL,
                revenue       REAL,
                first_contact TEXT,
                next_followup TEXT,
                notes         TEXT DEFAULT '',
                send_channel  TEXT DEFAULT 'outlook',
                assigned_to   TEXT DEFAULT '',
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                updated_at    TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prospects_tenant ON prospects(tenant_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activities (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                date        TEXT,
                prospect    TEXT DEFAULT '',
                action      TEXT DEFAULT '',
                outcome     TEXT DEFAULT '',
                next_step   TEXT DEFAULT '',
                notes       TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_prospect ON activities(prospect)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                date        TEXT,
                time        TEXT DEFAULT '',
                prospect    TEXT DEFAULT '',
                type        TEXT DEFAULT '',
                prep_notes  TEXT DEFAULT '',
                status      TEXT DEFAULT 'Scheduled'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                title       TEXT NOT NULL,
                prospect    TEXT DEFAULT '',
                due_date    TEXT,
                remind_at   TEXT,
                assigned_to TEXT DEFAULT '',
                created_by  TEXT DEFAULT '',
                notes       TEXT DEFAULT '',
                status      TEXT DEFAULT 'pending',
                completed_at TEXT,
                completed_by TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_remind ON tasks(remind_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prospect_notes (
                id          SERIAL PRIMARY KEY,
                prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
                content     TEXT NOT NULL,
                created_by  TEXT DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prospect_notes_prospect ON prospect_notes(prospect_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS approval_queue (
                id              SERIAL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                prospect_id     INTEGER REFERENCES prospects(id) ON DELETE CASCADE,
                prospect_name   TEXT DEFAULT '',
                draft_text      TEXT DEFAULT '',
                draft_type      TEXT DEFAULT '',
                status          TEXT DEFAULT 'pending',
                telegram_msg_id TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                actioned_at     TIMESTAMPTZ,
                snoozed_until   TIMESTAMPTZ
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_queue_status ON approval_queue(status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                timestamp   TIMESTAMPTZ DEFAULT NOW(),
                action      TEXT DEFAULT '',
                actor       TEXT DEFAULT '',
                target      TEXT DEFAULT '',
                details     TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trust_config (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                trust_level INTEGER DEFAULT 1,
                changed_at  TIMESTAMPTZ DEFAULT NOW(),
                changed_by  TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS campaigns (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                name        TEXT NOT NULL,
                status      TEXT DEFAULT 'draft',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS outcomes (
                id              SERIAL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                target          TEXT DEFAULT '',
                sent_at         TIMESTAMPTZ,
                resend_email_id TEXT,
                status          TEXT DEFAULT '',
                opened_at       TIMESTAMPTZ,
                clicked_at      TIMESTAMPTZ
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_resend_id ON outcomes(resend_email_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_target ON outcomes(target, sent_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS nurture_sequences (
                id              SERIAL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                prospect_id     INTEGER REFERENCES prospects(id) ON DELETE CASCADE,
                prospect_name   TEXT DEFAULT '',
                status          TEXT DEFAULT 'active',
                next_touch_date TEXT,
                touch_count     INTEGER DEFAULT 0,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nurture_status ON nurture_sequences(status, next_touch_date)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS interactions (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                date        TIMESTAMPTZ DEFAULT NOW(),
                prospect    TEXT DEFAULT '',
                source      TEXT DEFAULT '',
                raw_text    TEXT DEFAULT '',
                summary     TEXT DEFAULT '',
                action_items TEXT DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_prospect ON interactions(prospect)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS insurance_book (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                name        TEXT NOT NULL,
                phone       TEXT DEFAULT '',
                address     TEXT DEFAULT '',
                policy_start TEXT DEFAULT '',
                status      TEXT DEFAULT 'Not Called',
                last_called TEXT,
                notes       TEXT DEFAULT '',
                retry_date  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS win_loss_log (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                date        TEXT,
                prospect    TEXT DEFAULT '',
                outcome     TEXT DEFAULT '',
                reason      TEXT DEFAULT '',
                product     TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_memory (
                id          SERIAL PRIMARY KEY,
                prospect_id INTEGER REFERENCES prospects(id) ON DELETE CASCADE,
                memory_key  TEXT DEFAULT '',
                memory_val  TEXT DEFAULT '',
                extracted_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS brand_voice (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                voice_key   TEXT DEFAULT '',
                voice_val   TEXT DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_calendar (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                event_date  TEXT NOT NULL,
                title       TEXT NOT NULL,
                description TEXT DEFAULT '',
                category    TEXT DEFAULT ''
            )
        """)


def _create_tenant_tables():
    """Already created in _create_core_tables — kept for clarity."""
    pass


def _create_sequence_tables():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sequences (
                id              SERIAL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                name            TEXT NOT NULL,
                description     TEXT DEFAULT '',
                trigger_type    TEXT NOT NULL,
                trigger_config  TEXT DEFAULT '{}',
                status          TEXT DEFAULT 'active',
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sequences_tenant ON sequences(tenant_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sequences_trigger ON sequences(trigger_type, status)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sequence_steps (
                id              SERIAL PRIMARY KEY,
                sequence_id     INTEGER NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
                step_order      INTEGER NOT NULL,
                step_type       TEXT NOT NULL,
                delay_minutes   INTEGER DEFAULT 0,
                content_template TEXT DEFAULT '',
                channel         TEXT DEFAULT '',
                config          TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seq_steps_seq ON sequence_steps(sequence_id, step_order)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sequence_enrollments (
                id              SERIAL PRIMARY KEY,
                sequence_id     INTEGER NOT NULL REFERENCES sequences(id),
                prospect_id     INTEGER NOT NULL REFERENCES prospects(id),
                status          TEXT DEFAULT 'active',
                current_step    INTEGER DEFAULT 1,
                enrolled_at     TIMESTAMPTZ DEFAULT NOW(),
                last_step_at    TIMESTAMPTZ,
                next_step_at    TIMESTAMPTZ,
                completed_at    TIMESTAMPTZ,
                trigger_data    TEXT DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seq_enroll_due ON sequence_enrollments(status, next_step_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seq_enroll_prospect ON sequence_enrollments(prospect_id, status)")
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_seq_enroll_active
            ON sequence_enrollments(sequence_id, prospect_id)
            WHERE status = 'active'
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sequence_step_logs (
                id              SERIAL PRIMARY KEY,
                enrollment_id   INTEGER NOT NULL REFERENCES sequence_enrollments(id),
                step_id         INTEGER NOT NULL REFERENCES sequence_steps(id),
                status          TEXT DEFAULT 'ok',
                content         TEXT DEFAULT '',
                executed_at     TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_seq_step_logs_enrollment ON sequence_step_logs(enrollment_id, executed_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prospect_tags (
                id          SERIAL PRIMARY KEY,
                prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
                tag         TEXT NOT NULL,
                applied_by  TEXT DEFAULT 'system',
                applied_at  TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(prospect_id, tag)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS enrichment_queue (
                id              SERIAL PRIMARY KEY,
                prospect_id     INTEGER NOT NULL UNIQUE REFERENCES prospects(id) ON DELETE CASCADE,
                status          TEXT DEFAULT 'pending',
                attempts        INTEGER DEFAULT 0,
                last_attempt    TIMESTAMPTZ,
                linkedin_url    TEXT,
                instagram_handle TEXT,
                headshot_url    TEXT,
                bio             TEXT,
                company_website TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id                      SERIAL PRIMARY KEY,
                referrer_prospect_id    INTEGER REFERENCES prospects(id),
                referred_prospect_id    INTEGER NOT NULL REFERENCES prospects(id),
                referral_date           TIMESTAMPTZ DEFAULT NOW(),
                notes                   TEXT
            )
        """)


def _create_sms_tables():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sms_conversations (
                id              SERIAL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                phone           TEXT NOT NULL,
                prospect_id     INTEGER REFERENCES prospects(id) ON DELETE SET NULL,
                prospect_name   TEXT DEFAULT '',
                status          TEXT DEFAULT 'active',
                ai_enabled      BOOLEAN DEFAULT TRUE,
                last_message_at TIMESTAMPTZ DEFAULT NOW(),
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_conv_phone ON sms_conversations(tenant_id, phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sms_conv_prospect ON sms_conversations(prospect_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sms_messages (
                id              SERIAL PRIMARY KEY,
                conversation_id INTEGER NOT NULL REFERENCES sms_conversations(id) ON DELETE CASCADE,
                direction       TEXT NOT NULL,
                body            TEXT DEFAULT '',
                status          TEXT DEFAULT 'delivered',
                sent_at         TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sms_messages_conv ON sms_messages(conversation_id, sent_at)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sms_agents (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                phone       TEXT NOT NULL,
                persona     TEXT DEFAULT 'friendly',
                status      TEXT DEFAULT 'active',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sms_agents_phone ON sms_agents(tenant_id, phone)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS booking_nurture_sequences (
                id              SERIAL PRIMARY KEY,
                tenant_id       INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                prospect_id     INTEGER REFERENCES prospects(id) ON DELETE CASCADE,
                prospect_name   TEXT DEFAULT '',
                phone           TEXT DEFAULT '',
                status          TEXT DEFAULT 'active',
                current_step    INTEGER DEFAULT 1,
                next_send_at    TIMESTAMPTZ,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_booking_nurture_status_sched ON booking_nurture_sequences(status, next_send_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_booking_nurture_prospect ON booking_nurture_sequences(prospect_id)")


def _create_tracking_tables():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS intake_form_responses (
                id          SERIAL PRIMARY KEY,
                prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
                form_type   TEXT NOT NULL,
                responses   TEXT NOT NULL,
                submitted_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_tracking (
                id              SERIAL PRIMARY KEY,
                prospect_id     INTEGER REFERENCES prospects(id) ON DELETE SET NULL,
                prospect_name   TEXT,
                email_type      TEXT,
                token           TEXT UNIQUE,
                opened_at       TIMESTAMPTZ,
                link_clicked_at TIMESTAMPTZ,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
        """)


def _ensure_default_tenant():
    """Create tenant id=1 if no tenants exist (single-user migration path)."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM tenants LIMIT 1").fetchone()
        if not row:
            conn.execute("""
                INSERT INTO tenants (name, slug, company, timezone, plan, status)
                VALUES ('Default', 'default', '', 'America/Toronto', 'pro', 'active')
            """)
            logger.info("Created default tenant")
```

- [ ] **Step 2: Remove the old _migrate_* functions**

Delete these functions from db.py (they are now replaced by the above):
- `_migrate_booking_nurture()`
- `_migrate_sms_conversations()`
- `_migrate_sms_agent()`
- `_migrate_multi_tenant()`
- `_migrate_sequences()`
- `_migrate_phase6()`
- `cleanup_old_data()`

Also delete `migrate_from_excel()` (line ~1377) — Excel import is out of scope for SaaS.

- [ ] **Step 3: Run init_db test**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/test_db.py::test_init_db_creates_tables -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "feat: rewrite init_db() with PostgreSQL DDL, all tables tenant-scoped"
```

---

## Task 5: Update db.py — Replace All SQL Placeholders

**Files:**
- Modify: `db.py` (all query functions)

Replace every `?` with `%s`, every `datetime('now')` with `NOW()`, and every `cursor.lastrowid` with `RETURNING id`. Also replace `conn.execute(...).fetchone()` / `.fetchall()` pattern — psycopg2 requires calling `cur = conn.execute(...)` then `cur.fetchone()`.

- [ ] **Step 1: Global find-replace ? → %s**

In db.py, replace all SQL query placeholders. The pattern is: any `?` inside a string passed to `conn.execute()`. Do this carefully — only inside SQL strings, not Python comparisons.

Run this to verify count before:
```bash
grep -c "VALUES (" db.py
```

After replacement, verify no bare `?` remain in SQL strings:
```bash
grep -n " = ?\| IN (?" db.py
```

Expected: 0 results.

- [ ] **Step 2: Replace datetime('now') → NOW()**

```bash
grep -n "datetime('now')" db.py
```

Replace each occurrence in SQL strings: `datetime('now')` → `NOW()`.

- [ ] **Step 3: Fix RETURNING id for INSERT operations**

In Postgres, `cursor.lastrowid` doesn't work. For any INSERT that needs the new row's ID, append `RETURNING id` to the INSERT and use `cur.fetchone()["id"]`.

Find all places using `lastrowid`:
```bash
grep -n "lastrowid" db.py
```

For each one, e.g. in `add_task`:
```python
# OLD:
cursor = conn.execute("INSERT INTO tasks (...) VALUES (%s, ...)", (...))
task_id = cursor.lastrowid

# NEW:
cur = conn.execute("INSERT INTO tasks (...) VALUES (%s, ...) RETURNING id", (...))
task_id = cur.fetchone()["id"]
```

- [ ] **Step 4: Fix get_prospect_by_phone (line ~108)**

```python
def get_prospect_by_phone(phone: str, tenant_id: int = 1):
    tid = tenant_id or _current_tenant_id.get()
    normalized = normalize_phone(phone)
    with get_db() as conn:
        cur = conn.execute(
            "SELECT * FROM prospects WHERE phone != '' AND tenant_id = %s",
            (tid,),
        )
        rows = cur.fetchall()
    for row in _rows_to_dicts(rows):
        if normalize_phone(row.get("phone", "")) == normalized:
            return row
    return None
```

- [ ] **Step 5: Fix read_pipeline and add_prospect**

```python
def read_pipeline(tenant_id: int = None):
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.execute(
            "SELECT * FROM prospects WHERE tenant_id = %s ORDER BY id", (tid,)
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def add_prospect(data: dict, tenant_id: int = None) -> str:
    tid = tenant_id or _current_tenant_id.get()
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
                aum, revenue, first_contact, next_followup, notes, send_channel, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
                tid,
            ),
        )
    return f"Added {name} to pipeline."
```

- [ ] **Step 6: Fix update_prospect — SET clause uses %s not ?**

In `update_prospect`, the dynamic SET clause builder must use `%s` not `?`:

```python
set_clauses = ", ".join(f'"{field}" = %s' for field in validated_fields)
values = [safe_fields[f] for f in validated_fields] + [prospect_id]
conn.execute(
    f"UPDATE prospects SET {set_clauses}, updated_at = NOW() WHERE id = %s",
    values,
)
```

- [ ] **Step 7: Fix all remaining query functions**

For each function in db.py that calls `conn.execute(...)`, apply these rules:
- `?` → `%s`
- `datetime('now')` → `NOW()`
- `.lastrowid` → use `RETURNING id`
- `LIKE ?` → `LIKE %s` (note: `%` in LIKE patterns must be passed as params, not f-strings)

Functions to update (search for `conn.execute` in each):
- `delete_prospect`, `merge_prospects`, `get_all_prospect_names`, `get_prospect_by_name`
- `get_prospect_by_email`, `get_prospect_by_id`
- `add_activity`, `read_activities`
- `add_prospect_note`, `get_prospect_notes`, `delete_prospect_note`
- `add_meeting`, `read_meetings`, `update_meeting`
- `read_insurance_book`, `add_insurance_entry`, `update_insurance_entry`
- `log_win_loss`, `get_win_loss_stats`
- `add_interaction`, `read_interactions`
- `add_task`, `get_tasks`, `update_task`, `complete_task`, `delete_task`
- `get_due_tasks`, `get_overdue_tasks`, `get_reminder_tasks`, `clear_reminder`
- `apply_tag`, `remove_tag`, `get_tags`, `get_prospects_by_tag`
- `queue_enrichment`
- `get_conversion_by_source`, `get_pipeline_metrics`, `get_stage_funnel`, `get_fyc_by_advisor`, `get_avg_stage_time`
- `get_trust_level`
- `create_email_tracking_token`, `record_email_open`, `record_link_click`
- `add_intake_form_response`

- [ ] **Step 8: Add default tenant_id resolution to all functions**

Every function with `tenant_id: int = 1` should resolve via context var:

```python
def some_function(tenant_id: int = None):
    tid = tenant_id or _current_tenant_id.get()
    # use tid instead of tenant_id
```

Change all default values from `= 1` to `= None` and add `tid = tenant_id or _current_tenant_id.get()` as the first line.

- [ ] **Step 9: Run full test suite**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/test_db.py -v
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add db.py
git commit -m "feat: update all db.py queries to PostgreSQL syntax with tenant isolation"
```

---

## Task 6: Complete Auth in dashboard.py

**Files:**
- Modify: `dashboard.py` (auth routes ~lines 1676–1760, `_check_auth`, `_require_auth`)
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write failing auth tests**

Create `tests/test_auth.py`:

```python
"""Integration tests for auth routes."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set"
)


@pytest.fixture
def client():
    import db
    db.init_db()
    import dashboard
    dashboard.app.config["TESTING"] = True
    dashboard.app.config["SECRET_KEY"] = "test-secret"
    with dashboard.app.test_client() as c:
        yield c


def test_register_creates_tenant_and_user(client):
    resp = client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage",
        "name": "Alice Smith",
        "email": "alice@testbroker.com",
        "password": "Secure123!"
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "tenant_id" in data


def test_login_returns_session(client):
    # Register first
    client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage 2",
        "name": "Bob Jones",
        "email": "bob@testbroker2.com",
        "password": "Secure123!"
    })
    resp = client.post("/api/auth/login", json={
        "email": "bob@testbroker2.com",
        "password": "Secure123!"
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_login_wrong_password_fails(client):
    resp = client.post("/api/auth/login", json={
        "email": "bob@testbroker2.com",
        "password": "wrongpassword"
    })
    assert resp.status_code == 401


def test_me_returns_user_when_logged_in(client):
    client.post("/api/auth/register", json={
        "firm_name": "Test Brokerage 3",
        "name": "Carol Lee",
        "email": "carol@testbroker3.com",
        "password": "Secure123!"
    })
    client.post("/api/auth/login", json={
        "email": "carol@testbroker3.com",
        "password": "Secure123!"
    })
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["email"] == "carol@testbroker3.com"
    assert data["role"] == "owner"


def test_no_tenants_redirects_to_register(client):
    import db
    with db.get_db() as conn:
        conn.execute("DELETE FROM users")
        conn.execute("DELETE FROM tenants")
    resp = client.get("/")
    assert resp.status_code in (302, 303)
    assert "/register" in resp.headers["Location"]
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/test_auth.py -v
```

Expected: failures on register/login routes.

- [ ] **Step 3: Add bcrypt import to dashboard.py**

At the top of `dashboard.py`, add:

```python
import bcrypt
from config_store import get_config, set_config, get_all_config
import db as _db_module
```

- [ ] **Step 4: Replace _check_auth() and _require_auth decorator**

Find `_check_auth()` (~line 810) and replace the entire auth check section:

```python
def _check_auth():
    """Return current user dict from session, or None."""
    from flask import session
    user_id = session.get("user_id")
    if not user_id:
        return None
    try:
        with db.get_db() as conn:
            cur = conn.execute(
                "SELECT id, tenant_id, email, name, role FROM users WHERE id = %s AND status = 'active'",
                (user_id,),
            )
            row = cur.fetchone()
        if row:
            db._current_tenant_id.set(row["tenant_id"])
            return dict(row)
    except Exception:
        logger.exception("Auth check failed")
    return None


def _require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check if any tenants exist — if not, redirect to register
        try:
            with db.get_db() as conn:
                cur = conn.execute("SELECT id FROM tenants LIMIT 1")
                if not cur.fetchone():
                    return redirect("/register")
        except Exception:
            pass

        user = _check_auth()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated
```

- [ ] **Step 5: Implement /api/auth/register**

Find `@app.route("/api/auth/register", methods=["POST"])` (~line 1681) and replace its implementation:

```python
@app.route("/api/auth/register", methods=["POST"])
def api_auth_register():
    data = request.get_json() or {}
    firm_name = data.get("firm_name", "").strip()
    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not all([firm_name, name, email, password]):
        return jsonify({"error": "All fields required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    slug = re.sub(r"[^a-z0-9]+", "-", firm_name.lower()).strip("-")

    try:
        with db.get_db() as conn:
            # Check email not already registered
            cur = conn.execute("SELECT id FROM users WHERE LOWER(email) = %s", (email,))
            if cur.fetchone():
                return jsonify({"error": "Email already registered"}), 409

            # Create tenant
            cur = conn.execute(
                """INSERT INTO tenants (name, slug, status, plan)
                   VALUES (%s, %s, 'active', 'starter') RETURNING id""",
                (firm_name, slug),
            )
            tenant_id = cur.fetchone()["id"]

            # Create owner user
            cur = conn.execute(
                """INSERT INTO users (tenant_id, email, password_hash, name, role)
                   VALUES (%s, %s, %s, %s, 'owner') RETURNING id""",
                (tenant_id, email, pw_hash, name),
            )
            user_id = cur.fetchone()["id"]

        from flask import session
        session["user_id"] = user_id
        session["tenant_id"] = tenant_id
        db._current_tenant_id.set(tenant_id)

        return jsonify({"ok": True, "tenant_id": tenant_id, "user_id": user_id})
    except Exception:
        logger.exception("Registration failed")
        return jsonify({"error": "Registration failed"}), 500
```

- [ ] **Step 6: Implement /api/auth/login**

Find `@app.route("/api/auth/login", methods=["POST"])` (~line 1717) and replace:

```python
@app.route("/api/auth/login", methods=["POST"])
def api_auth_login():
    data = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    try:
        with db.get_db() as conn:
            cur = conn.execute(
                "SELECT id, tenant_id, password_hash, name, role FROM users WHERE LOWER(email) = %s AND status = 'active'",
                (email,),
            )
            user = cur.fetchone()
    except Exception:
        logger.exception("Login DB error")
        return jsonify({"error": "Login failed"}), 500

    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid email or password"}), 401

    from flask import session
    session["user_id"] = user["id"]
    session["tenant_id"] = user["tenant_id"]
    db._current_tenant_id.set(user["tenant_id"])

    # Update last_login
    try:
        with db.get_db() as conn:
            conn.execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],))
    except Exception:
        pass

    return jsonify({"ok": True, "name": user["name"], "role": user["role"]})
```

- [ ] **Step 7: Implement /api/auth/logout and /api/auth/me**

```python
@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    from flask import session
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/me")
def api_auth_me():
    user = _check_auth()
    if not user:
        return jsonify({"error": "Not authenticated"}), 401
    return jsonify({
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "role": user["role"],
        "tenant_id": user["tenant_id"],
    })
```

- [ ] **Step 8: Add SECRET_KEY to Flask app init**

Near the top of dashboard.py where `app = Flask(__name__)` is defined, add:

```python
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
```

- [ ] **Step 9: Run auth tests**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/test_auth.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 10: Commit**

```bash
git add dashboard.py tests/test_auth.py
git commit -m "feat: implement real email/password auth with bcrypt and tenant session scoping"
```

---

## Task 7: Wire Tenant Config to Settings + Graceful Degradation

**Files:**
- Modify: `dashboard.py` (settings routes, all integration call sites)
- Modify: `bot.py` (all `os.environ` calls for API keys)

- [ ] **Step 1: Replace /api/tenant/config GET**

Find `@app.route("/api/tenant/config", methods=["GET"])` (~line 2108):

```python
@app.route("/api/tenant/config", methods=["GET"])
@_require_auth
def api_tenant_config_get():
    user = _check_auth()
    all_config = get_all_config(user["tenant_id"])
    # Return which keys are set (not values) for security
    keys = [
        "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_PHONE_NUMBER",
        "RESEND_API_KEY", "INTAKE_WEBHOOK_SECRET", "BOOKING_URL",
        "COMPANY_NAME", "ADVISOR_NAME",
    ]
    return jsonify({
        k: bool(all_config.get(k)) for k in keys
    })
```

- [ ] **Step 2: Replace /api/tenant/config PUT**

Find `@app.route("/api/tenant/config", methods=["PUT"])` (~line 2121):

```python
@app.route("/api/tenant/config", methods=["PUT"])
@_require_auth
def api_tenant_config_put():
    user = _check_auth()
    data = request.get_json() or {}
    allowed_keys = {
        "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        "TELEGRAM_WEBHOOK_SECRET", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER", "RESEND_API_KEY", "INTAKE_WEBHOOK_SECRET",
        "BOOKING_URL", "COMPANY_NAME", "ADVISOR_NAME",
    }
    saved = []
    for key, value in data.items():
        if key in allowed_keys and isinstance(value, str):
            set_config(user["tenant_id"], key, value)
            saved.append(key)
    return jsonify({"ok": True, "saved": saved})
```

- [ ] **Step 3: Add _get_tenant_id() helper to dashboard.py**

Add this helper after `_check_auth`:

```python
def _tenant_id() -> int:
    """Return current request's tenant_id from session, defaulting to 1."""
    from flask import session
    return session.get("tenant_id", 1)
```

- [ ] **Step 4: Fix /api/chat graceful degradation**

Find `@app.route("/api/chat", methods=["POST"])` (~line 425). At the start of `api_chat()`:

```python
@app.route("/api/chat", methods=["POST"])
@_require_auth
def api_chat():
    from config_store import get_config
    tid = _tenant_id()
    openai_key = get_config(tid, "OPENAI_API_KEY")
    if not openai_key:
        return jsonify({"reply": "AI chat is not configured. Add your OpenAI API key in Settings."}), 200
    # ... rest of function using openai_key instead of os.environ["OPENAI_API_KEY"]
```

- [ ] **Step 5: Fix /api/conversations/<phone>/send graceful degradation**

Find `api_send_sms` (~line 444). At the start:

```python
@app.route("/api/conversations/<path:phone>/send", methods=["POST"])
@_require_auth
def api_send_sms(phone):
    from config_store import get_config
    tid = _tenant_id()
    twilio_sid = get_config(tid, "TWILIO_ACCOUNT_SID")
    if not twilio_sid:
        return jsonify({"error": "Twilio not configured. Add credentials in Settings."}), 400
```

- [ ] **Step 6: Fix onboarding setup check**

Find `onboarding_wizard()` (~line 2213) and the setup_complete check (~line 931):

```python
# In _common_context() or wherever setup_complete is computed:
from config_store import get_config
tid = _tenant_id()
setup_complete = bool(get_config(tid, "OPENAI_API_KEY"))
```

- [ ] **Step 7: Commit**

```bash
git add dashboard.py config_store.py
git commit -m "feat: wire tenant config to settings API, add graceful degradation for all integrations"
```

---

## Task 8: Fix bot.py for Multi-Tenancy

**Files:**
- Modify: `bot.py` (top-level env var access → per-tenant config)

- [ ] **Step 1: Remove hard-coded env var requirements**

At the top of `bot.py`, replace:

```python
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
```

With:

```python
import db as _db
from config_store import get_config

# Default token for single-tenant / self-hosted mode
_DEFAULT_TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_DEFAULT_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

logger = logging.getLogger(__name__)
```

- [ ] **Step 2: Add tenant resolution by chat_id**

Add this function early in bot.py:

```python
def _get_tenant_for_chat(chat_id: str) -> int:
    """Find tenant_id from a registered Telegram chat_id. Returns 1 as fallback."""
    try:
        _db.init_db()
        with _db.get_db() as conn:
            cur = conn.execute(
                "SELECT tenant_id FROM tenant_config WHERE key = 'TELEGRAM_CHAT_ID' AND value = %s LIMIT 1",
                (str(chat_id),),
            )
            row = cur.fetchone()
            if row:
                return row["tenant_id"]
    except Exception:
        logger.warning("Could not resolve tenant for chat_id=%s", chat_id)
    return 1


def _get_openai_key(tenant_id: int) -> str:
    key = get_config(tenant_id, "OPENAI_API_KEY")
    return key or _DEFAULT_OPENAI_KEY


def _get_admin_chat_id(tenant_id: int) -> str:
    return get_config(tenant_id, "TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
```

- [ ] **Step 3: Update _is_admin() to use per-tenant chat_id**

Find `_is_admin()` (~line 50):

```python
def _is_admin(update) -> bool:
    chat_id = str(update.effective_chat.id)
    tenant_id = _get_tenant_for_chat(chat_id)
    admin_id = _get_admin_chat_id(tenant_id)
    return chat_id == str(admin_id)
```

- [ ] **Step 4: Update get_trust_level calls to be tenant-aware**

Find `get_trust_level()` in bot.py (~line 80):

```python
def get_trust_level():
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "1")
    tenant_id = _get_tenant_for_chat(chat_id)
    return db.get_trust_level(tenant_id)
```

- [ ] **Step 5: Update main() to handle missing token gracefully**

Find `main()` at the bottom of bot.py:

```python
def main():
    token = _DEFAULT_TELEGRAM_TOKEN
    if not token:
        logger.warning("No TELEGRAM_BOT_TOKEN set — bot will not start. Configure via Settings.")
        # Keep process alive so Railway worker doesn't restart loop
        import time
        while True:
            time.sleep(3600)
        return
    # ... rest of main()
```

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "feat: make bot.py multi-tenant aware, graceful when token not set"
```

---

## Task 9: Fix Procfile and Dockerfile

**Files:**
- Modify: `Procfile`
- Modify: `Dockerfile`

- [ ] **Step 1: Fix Procfile**

Replace contents of `Procfile` with:

```
web: gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120 dashboard:app
worker: python bot.py
```

- [ ] **Step 2: Fix Dockerfile**

Replace contents of `Dockerfile` with:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Init DB runs at startup via dashboard:app's before_first_request or explicit call
CMD gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120 dashboard:app
```

- [ ] **Step 3: Add DB init on app startup in dashboard.py**

Near the bottom of dashboard.py, before `if __name__ == "__main__":`, add:

```python
# Initialize database on startup (idempotent)
try:
    import db as _startup_db
    _startup_db.init_db()
    logger.info("Database initialized on startup")
except Exception as _e:
    logger.error("Database init failed: %s", _e)
```

- [ ] **Step 4: Commit**

```bash
git add Procfile Dockerfile dashboard.py
git commit -m "fix: Procfile runs bot.py as worker, Dockerfile uses DATABASE_URL"
```

---

## Task 9b: Audit Other Modules for Hardcoded tenant_id=1

**Files:**
- Modify: `sms_conversations.py`, `scheduler.py`, `analytics.py`, `campaigns.py`, `nurture.py`, `booking_nurture.py`, `sequences.py`, `enrichment.py`, `referral.py`, `cross_sell.py`

- [ ] **Step 1: Find all hardcoded tenant_id=1 calls**

```bash
grep -rn "tenant_id=1\|tenant_id = 1" *.py | grep -v "db.py\|test_\|DEFAULT"
```

- [ ] **Step 2: For each match, replace with context var resolution**

For any file that calls db functions with an explicit `tenant_id=1`, update to omit the argument (letting db.py resolve from `_current_tenant_id` context var):

```python
# OLD:
db.add_prospect(data, tenant_id=1)

# NEW:
db.add_prospect(data)  # tenant_id resolved from _current_tenant_id context var
```

For background workers (scheduler, bot) that run outside a request context, pass the tenant_id explicitly from the job config:

```python
# In scheduler jobs:
tenant_id = job_config.get("tenant_id", 1)
db._current_tenant_id.set(tenant_id)
db.add_activity(data)
```

- [ ] **Step 3: Verify no regressions**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/ -v
```

Expected: all tests still pass.

- [ ] **Step 4: Commit**

```bash
git add sms_conversations.py scheduler.py analytics.py campaigns.py nurture.py booking_nurture.py sequences.py enrichment.py referral.py cross_sell.py
git commit -m "fix: remove hardcoded tenant_id=1 across all modules, use context var"
```

---

## Task 9c: Advisor Invite System

**Files:**
- Modify: `dashboard.py` (add invite routes)

- [ ] **Step 1: Add invite generation route**

In `dashboard.py`, add after the auth routes:

```python
import hmac as _hmac
import time as _time

def _make_invite_token(tenant_id: int, role: str) -> str:
    """Create a signed invite token: base64(tenant_id:role:expiry:sig)."""
    import base64
    expiry = int(_time.time()) + 7 * 24 * 3600  # 7 days
    payload = f"{tenant_id}:{role}:{expiry}"
    sig = _hmac.new(app.secret_key.encode(), payload.encode(), "sha256").hexdigest()[:16]
    token = base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).decode()
    return token


def _verify_invite_token(token: str):
    """Verify invite token. Returns (tenant_id, role) or raises ValueError."""
    import base64
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        tenant_id_str, role, expiry_str, sig = decoded.rsplit(":", 3)
        payload = f"{tenant_id_str}:{role}:{expiry_str}"
        expected_sig = _hmac.new(app.secret_key.encode(), payload.encode(), "sha256").hexdigest()[:16]
        if not _hmac.compare_digest(sig, expected_sig):
            raise ValueError("Invalid signature")
        if int(expiry_str) < int(_time.time()):
            raise ValueError("Invite link has expired")
        return int(tenant_id_str), role
    except (ValueError, KeyError):
        raise
    except Exception:
        raise ValueError("Malformed invite token")


@app.route("/api/invite/generate", methods=["POST"])
@_require_auth
def api_generate_invite():
    user = _check_auth()
    if user["role"] not in ("owner", "manager"):
        return jsonify({"error": "Not authorized"}), 403
    data = request.get_json() or {}
    role = data.get("role", "advisor")
    if role not in ("advisor", "manager"):
        return jsonify({"error": "Invalid role"}), 400
    token = _make_invite_token(user["tenant_id"], role)
    base_url = request.host_url.rstrip("/")
    return jsonify({"invite_url": f"{base_url}/invite/{token}"})


@app.route("/invite/<token>")
def invite_page(token):
    try:
        _verify_invite_token(token)
    except ValueError as e:
        return f"<h2>Invalid or expired invite link: {_esc(str(e))}</h2>", 400
    return render_template("register.html", invite_token=token)


@app.route("/api/invite/accept", methods=["POST"])
def api_accept_invite():
    data = request.get_json() or {}
    token = data.get("token", "")
    name = data.get("name", "").strip()
    password = data.get("password", "")

    if not all([token, name, password]):
        return jsonify({"error": "All fields required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    try:
        tenant_id, role = _verify_invite_token(token)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    email = data.get("email", "").strip().lower()
    if not email:
        return jsonify({"error": "Email required"}), 400

    try:
        with db.get_db() as conn:
            cur = conn.execute("SELECT id FROM users WHERE LOWER(email) = %s", (email,))
            if cur.fetchone():
                return jsonify({"error": "Email already registered"}), 409
            cur = conn.execute(
                """INSERT INTO users (tenant_id, email, password_hash, name, role)
                   VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                (tenant_id, email, pw_hash, name, role),
            )
            user_id = cur.fetchone()["id"]

        from flask import session
        session["user_id"] = user_id
        session["tenant_id"] = tenant_id
        db._current_tenant_id.set(tenant_id)
        return jsonify({"ok": True})
    except Exception:
        logger.exception("Accept invite failed")
        return jsonify({"error": "Failed to create account"}), 500
```

- [ ] **Step 2: Commit**

```bash
git add dashboard.py
git commit -m "feat: add advisor invite system with signed time-limited tokens"
```

---

## Task 9d: Wire Onboarding Test Buttons

**Files:**
- Modify: `dashboard.py` (/api/setup/test/<service> routes, ~line 2224)

- [ ] **Step 1: Implement /api/setup/test/<service>**

Find `@app.route("/api/setup/test/<service>", methods=["POST"])` and replace its implementation:

```python
@app.route("/api/setup/test/<service>", methods=["POST"])
@_require_auth
def api_setup_test(service):
    user = _check_auth()
    tid = user["tenant_id"]
    data = request.get_json() or {}

    if service == "openai":
        key = data.get("key") or get_config(tid, "OPENAI_API_KEY")
        if not key:
            return jsonify({"ok": False, "error": "No API key provided"})
        try:
            import openai
            client = openai.OpenAI(api_key=key)
            client.models.list()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    elif service == "telegram":
        token = data.get("key") or get_config(tid, "TELEGRAM_BOT_TOKEN")
        if not token:
            return jsonify({"ok": False, "error": "No token provided"})
        try:
            resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
            if resp.ok:
                return jsonify({"ok": True, "bot": resp.json().get("result", {}).get("username")})
            return jsonify({"ok": False, "error": resp.json().get("description", "Invalid token")})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    elif service == "twilio":
        sid = data.get("account_sid") or get_config(tid, "TWILIO_ACCOUNT_SID")
        auth = data.get("auth_token") or get_config(tid, "TWILIO_AUTH_TOKEN")
        if not sid or not auth:
            return jsonify({"ok": False, "error": "Account SID and Auth Token required"})
        try:
            from twilio.rest import Client as TwilioClient
            client = TwilioClient(sid, auth)
            client.api.accounts(sid).fetch()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    elif service == "resend":
        key = data.get("key") or get_config(tid, "RESEND_API_KEY")
        if not key:
            return jsonify({"ok": False, "error": "No API key provided"})
        try:
            resp = requests.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {key}"},
                timeout=5,
            )
            if resp.status_code == 200:
                return jsonify({"ok": True})
            return jsonify({"ok": False, "error": f"HTTP {resp.status_code}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    return jsonify({"ok": False, "error": f"Unknown service: {service}"}), 400
```

- [ ] **Step 2: Commit**

```bash
git add dashboard.py
git commit -m "feat: implement integration test endpoints for onboarding wizard"
```

---

## Task 10: Set Up Neon Postgres + Railway Deploy

**Files:** No code changes — infrastructure setup.

- [ ] **Step 1: Create Neon database**

Go to console.neon.tech → New Project → name it "steadybook-crm".
Copy the connection string: `postgresql://user:pass@host/dbname?sslmode=require`

- [ ] **Step 2: Set Railway environment variables**

In Railway dashboard → your project → Variables, set:

```
DATABASE_URL=<neon connection string>
SECRET_KEY=<generate: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
ENCRYPTION_KEY=<generate: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
```

- [ ] **Step 3: Deploy**

```bash
railway up
```

Or push to the linked git branch — Railway auto-deploys on push.

- [ ] **Step 4: Verify health check**

```bash
curl https://your-app.railway.app/health
```

Expected: `{"status": "ok"}`

- [ ] **Step 5: Register first brokerage**

Navigate to `https://your-app.railway.app/register` → create first owner account → complete onboarding wizard.

---

## Task 11: End-to-End Verification

- [ ] **Step 1: Run full test suite**

```bash
DATABASE_URL="postgresql://..." python3 -m pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 2: Smoke test all major pages**

Log in as owner and verify each page loads without error:
- `/` (dashboard)
- `/pipeline`
- `/tasks`
- `/conversations`
- `/clients`
- `/forecast`
- `/reporting`
- `/sequences`
- `/flows`
- `/manager`
- `/settings`

- [ ] **Step 3: Smoke test core API operations**

```bash
# Add a prospect
curl -X POST https://your-app.railway.app/api/prospect \
  -H "Content-Type: application/json" \
  -b "session=..." \
  -d '{"name": "Test Client", "stage": "New Lead"}'

# Read pipeline
curl https://your-app.railway.app/api/data -b "session=..."
```

- [ ] **Step 4: Verify tenant isolation**

Register a second brokerage in an incognito window. Confirm prospects from Brokerage 1 are not visible when logged in as Brokerage 2.

- [ ] **Step 5: Final commit**

```bash
git add .
git commit -m "feat: SteadyBook SaaS — Neon Postgres, multi-tenant isolation, real auth, Railway deploy"
```
