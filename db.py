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


# ── Numeric parsing ──

def _parse_numeric(val):
    """Parse a numeric value, stripping $ and commas. Returns float, or None if empty/invalid."""
    if val is None or val == "":
        return None
    try:
        return float(str(val).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return None


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


def normalize_phone(phone: str) -> str:
    """Return the last 10 digits of a phone number with all non-digits stripped.

    Safe for numbers with 1s in the middle — strips by taking the last 10 chars
    of the digit string, not by removing the character '1' everywhere.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    return digits[-10:] if len(digits) >= 10 else digits


def get_prospect_by_phone(phone: str, tenant_id: int = None):
    """Look up a prospect by phone number. Matches on last 10 digits. Returns dict or None."""
    tid = tenant_id or _current_tenant_id.get()
    last10 = normalize_phone(phone)
    if not last10:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM prospects WHERE phone != '' AND tenant_id = %s", (tid,))
        rows = cur.fetchall()
    for row in rows:
        if normalize_phone(row["phone"]) == last10:
            return _row_to_dict(row)
    return None


# ── Schema ──

def init_db():
    """Create all tables and run migrations. Safe to call repeatedly."""
    _create_core_tables()
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
                notes       TEXT DEFAULT '',
                created_at  TIMESTAMPTZ DEFAULT NOW()
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
            CREATE TABLE IF NOT EXISTS campaign_messages (
                id          SERIAL PRIMARY KEY,
                tenant_id   INTEGER NOT NULL DEFAULT 1 REFERENCES tenants(id),
                campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
                prospect    TEXT DEFAULT '',
                channel     TEXT DEFAULT '',
                message     TEXT DEFAULT '',
                status      TEXT DEFAULT 'pending',
                sent_at     TIMESTAMPTZ,
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
        cur = conn.cursor()
        cur.execute("SELECT id FROM tenants LIMIT 1")
        row = cur.fetchone()
        if not row:
            cur.execute("""
                INSERT INTO tenants (name, slug, company, timezone, plan, status)
                VALUES ('Default', 'default', '', 'America/Toronto', 'pro', 'active')
            """)
            logger.info("Created default tenant")


# ── Prospects CRUD ──

def read_pipeline(tenant_id: int = None):
    """Return all prospects as a list of dicts."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM prospects WHERE tenant_id = %s ORDER BY id", (tid,))
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def add_prospect(data: dict, tenant_id: int = None) -> str:
    """Insert a new prospect. Returns status string."""
    tid = tenant_id or _current_tenant_id.get()
    name = data.get("name", "").strip()
    if not name:
        return "No name provided for prospect."

    aum = _parse_numeric(data.get("aum"))
    revenue = _parse_numeric(data.get("revenue"))
    first_contact = data.get("first_contact") or date.today().strftime("%Y-%m-%d")
    stage = data.get("stage") or "New Lead"

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
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


def update_prospect(name: str, updates: dict, tenant_id: int = None) -> str:
    """Update a prospect by partial name match (case insensitive).
    Skips empty values. Returns status string."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name FROM prospects WHERE LOWER(name) LIKE %s AND tenant_id = %s LIMIT 1",
            (f"%{name.lower()}%", tid),
        )
        row = cur.fetchone()

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

        # Cap notes at 2000 chars — truncate oldest content from the front
        if "notes" in safe_fields and safe_fields["notes"]:
            notes_val = safe_fields["notes"]
            if len(notes_val) > 2000:
                safe_fields["notes"] = "..." + notes_val[-1997:]

        # Build SET clause using only validated field names from the allowlist
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = %s' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [prospect_id]
        cur.execute(
            f"UPDATE prospects SET {set_clauses}, updated_at = NOW() WHERE id = %s",
            values,
        )

    return f"Updated {matched_name}: {', '.join(f'{f} → {v}' for f, v in safe_fields.items())}"


def delete_prospect(name: str, tenant_id: int = None) -> str:
    """Delete a prospect by partial name match. Returns status string."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name FROM prospects WHERE LOWER(name) LIKE %s AND tenant_id = %s LIMIT 1",
            (f"%{name.lower()}%", tid),
        )
        row = cur.fetchone()

        if not row:
            return f"Could not find prospect matching '{name}'."

        matched_name = row["name"]
        pid = row["id"]
        # Delete related records first to avoid foreign key constraint failures
        cur.execute("DELETE FROM client_memory WHERE prospect_id = %s", (pid,))
        cur.execute("DELETE FROM approval_queue WHERE prospect_id = %s", (pid,))
        cur.execute("DELETE FROM nurture_sequences WHERE prospect_id = %s", (pid,))
        # Clean up sequence enrollments and their step logs
        cur.execute("SELECT id FROM sequence_enrollments WHERE prospect_id = %s", (pid,))
        enrollment_ids = [r["id"] for r in cur.fetchall()]
        for eid in enrollment_ids:
            cur.execute("DELETE FROM sequence_step_logs WHERE enrollment_id = %s", (eid,))
        cur.execute("DELETE FROM sequence_enrollments WHERE prospect_id = %s", (pid,))
        cur.execute("DELETE FROM activities WHERE LOWER(prospect) = %s", (matched_name.lower(),))
        cur.execute("DELETE FROM interactions WHERE LOWER(prospect) = %s", (matched_name.lower(),))
        cur.execute("DELETE FROM prospects WHERE id = %s", (pid,))

    return f"Deleted {matched_name} from pipeline."


def merge_prospects(keep_name: str, merge_name: str) -> str:
    """Merge one prospect into another. Keeps keep_name, deletes merge_name.

    Transfers all activities, interactions, memory, approvals, and nurture
    sequences from merge_name to keep_name. Merges notes.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM prospects WHERE LOWER(name) LIKE %s LIMIT 1",
            (f"%{keep_name.lower()}%",),
        )
        keep = cur.fetchone()
        cur.execute(
            "SELECT * FROM prospects WHERE LOWER(name) LIKE %s LIMIT 1",
            (f"%{merge_name.lower()}%",),
        )
        merge = cur.fetchone()

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
        cur.execute(
            "UPDATE activities SET prospect = %s WHERE LOWER(prospect) = %s",
            (keep_real, merge_real.lower()),
        )
        cur.execute(
            "UPDATE interactions SET prospect = %s WHERE LOWER(prospect) = %s",
            (keep_real, merge_real.lower()),
        )

        # Transfer FK-based records
        cur.execute(
            "UPDATE client_memory SET prospect_id = %s WHERE prospect_id = %s",
            (keep_id, merge_id),
        )
        cur.execute(
            "UPDATE approval_queue SET prospect_id = %s WHERE prospect_id = %s",
            (keep_id, merge_id),
        )
        cur.execute(
            "UPDATE nurture_sequences SET prospect_id = %s, prospect_name = %s WHERE prospect_id = %s",
            (keep_id, keep_real, merge_id),
        )

        # Merge notes
        keep_notes = keep["notes"] or ""
        merge_notes = merge["notes"] or ""
        if merge_notes:
            combined = f"{keep_notes} | Merged from {merge_real}: {merge_notes}".strip(" |")
            cur.execute("UPDATE prospects SET notes = %s WHERE id = %s", (combined, keep_id))

        # Fill empty fields on keep from merge
        for field in ("phone", "email", "product", "aum", "revenue"):
            if not keep[field] and merge[field]:
                cur.execute(f"UPDATE prospects SET {field} = %s WHERE id = %s", (merge[field], keep_id))

        # Delete the merged prospect
        cur.execute("DELETE FROM prospects WHERE id = %s", (merge_id,))

    return f"Merged {merge_real} into {keep_real}."


def get_all_prospect_names(tenant_id: int = None) -> list[str]:
    """Return a list of all prospect names (for duplicate checking)."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM prospects WHERE tenant_id = %s ORDER BY name", (tid,))
        rows = cur.fetchall()
    return [row["name"] for row in rows]


def get_prospect_by_name(name: str, tenant_id: int = None):
    """Lookup by exact match first, then fuzzy partial match. Returns single dict or None."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        # Try exact match first (case-insensitive)
        cur.execute(
            "SELECT * FROM prospects WHERE LOWER(name) = %s AND tenant_id = %s LIMIT 1",
            (name.lower(), tid),
        )
        row = cur.fetchone()
        if row is None:
            # Fall back to partial match — fetch ALL candidates and pick best
            cur.execute(
                "SELECT * FROM prospects WHERE LOWER(name) LIKE %s AND tenant_id = %s",
                (f"%{name.lower()}%", tid),
            )
            candidates = cur.fetchall()
            if len(candidates) == 1:
                row = candidates[0]
                logger.info(f"Prospect fuzzy match: '{name}' → '{dict(row)['name']}'")
            elif len(candidates) > 1:
                other_names = [dict(r)["name"] for r in candidates]
                logger.warning(
                    "Prospect fuzzy match ambiguous for '%s' — matched: %s. Returning None.",
                    name, other_names,
                )
                row = None
    return _row_to_dict(row)


def get_prospect_by_email(email: str, tenant_id: int = None):
    """Lookup prospect by exact email match (case-insensitive). Returns dict or None."""
    tid = tenant_id or _current_tenant_id.get()
    if not email:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM prospects WHERE LOWER(email) = %s AND tenant_id = %s LIMIT 1",
            (email.lower().strip(), tid),
        )
        row = cur.fetchone()
    return _row_to_dict(row)


def get_prospect_by_id(prospect_id: int):
    """Look up a prospect by primary key. Returns dict or None."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM prospects WHERE id = %s", (prospect_id,))
        row = cur.fetchone()
    return _row_to_dict(row)


# ── Activities CRUD ──

def add_activity(data: dict, tenant_id: int = None) -> str:
    """Add an entry to the activity log. Defaults date to today."""
    tid = tenant_id or _current_tenant_id.get()
    activity_date = data.get("date") or date.today().strftime("%Y-%m-%d")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO activities (date, prospect, action, outcome, next_step, notes, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                activity_date,
                data.get("prospect", ""),
                data.get("action", ""),
                data.get("outcome", ""),
                data.get("next_step", ""),
                data.get("notes", ""),
                tid,
            ),
        )
    return f"Logged activity for {data.get('prospect', 'unknown')}."


def read_activities(limit: int = 100, tenant_id: int = None):
    """Return recent activities as list of dicts, newest first."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM activities WHERE tenant_id = %s ORDER BY id DESC LIMIT %s", (tid, limit)
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


# ── Prospect Notes ──

def add_prospect_note(prospect_id: int, content: str, created_by: str = "") -> dict | None:
    """Add a note to a prospect's timeline. Returns the note dict."""
    content = content.strip()
    if not content:
        return None
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO prospect_notes (prospect_id, content, created_by) VALUES (%s, %s, %s) RETURNING id",
            (prospect_id, content, created_by),
        )
        note_id = cur.fetchone()["id"]
        cur.execute("SELECT * FROM prospect_notes WHERE id = %s", (note_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_prospect_notes(prospect_id: int, limit: int = 50) -> list[dict]:
    """Get notes for a prospect, newest first."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM prospect_notes WHERE prospect_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (prospect_id, limit),
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def delete_prospect_note(note_id: int) -> str:
    """Delete a note by ID."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT id FROM prospect_notes WHERE id = %s", (note_id,))
        row = cur.fetchone()
        if not row:
            return "Note not found."
        cur.execute("DELETE FROM prospect_notes WHERE id = %s", (note_id,))
    return "Note deleted."


# ── Meetings CRUD ──

def add_meeting(data: dict, tenant_id: int = None) -> str:
    """Add a meeting. Defaults status to 'Scheduled'."""
    tid = tenant_id or _current_tenant_id.get()
    status = data.get("status") or "Scheduled"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO meetings (date, time, prospect, type, prep_notes, status, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                data.get("date", ""),
                data.get("time", ""),
                data.get("prospect", ""),
                data.get("type", ""),
                data.get("prep_notes", ""),
                status,
                tid,
            ),
        )
    return f"Meeting added: {data.get('prospect', '?')} on {data.get('date', '?')} at {data.get('time', '?')}"


def read_meetings(tenant_id: int = None):
    """Return all meetings ordered by date and time."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM meetings WHERE tenant_id = %s ORDER BY date, time", (tid,)
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def update_meeting(meeting_id: int, updates: dict) -> str:
    """Update a meeting by ID."""
    allowed = {"date", "time", "prospect", "type", "prep_notes", "status"}
    safe_fields = {f: v for f, v in updates.items() if f in allowed and v is not None}
    if not safe_fields:
        return f"No valid updates for meeting {meeting_id}."
    with get_db() as conn:
        cur = conn.cursor()
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = %s' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [meeting_id]
        cur.execute(f"UPDATE meetings SET {set_clauses} WHERE id = %s", values)
    return f"Updated meeting {meeting_id}: {', '.join(f'{f} → {v}' for f, v in safe_fields.items())}"


# ── Insurance Book CRUD ──

def read_insurance_book(tenant_id: int = None):
    """Return all insurance book entries."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM insurance_book WHERE tenant_id = %s ORDER BY id", (tid,)
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def add_insurance_entry(data: dict, tenant_id: int = None) -> str:
    """Add an entry to the insurance book."""
    tid = tenant_id or _current_tenant_id.get()
    name = data.get("name", "").strip()
    if not name:
        return "No name provided for insurance entry."

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO insurance_book
               (name, phone, address, policy_start, status, last_called, notes, retry_date, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                name,
                data.get("phone", ""),
                data.get("address", ""),
                data.get("policy_start", ""),
                data.get("status", "Not Called"),
                data.get("last_called"),
                data.get("notes", ""),
                data.get("retry_date"),
                tid,
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
        cur = conn.cursor()
        validated_fields = [f for f in safe_fields if f in allowed]
        set_clauses = ", ".join(f'"{field}" = %s' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [entry_id]
        cur.execute(f"UPDATE insurance_book SET {set_clauses} WHERE id = %s", values)
    return f"Updated insurance entry {entry_id}: {', '.join(f'{f} → {v}' for f, v in safe_fields.items())}"


# ── Win/Loss Log ──

def log_win_loss(prospect_name: str, outcome: str, reason: str, product: str = "", tenant_id: int = None) -> str:
    """Log a win or loss with reason."""
    tid = tenant_id or _current_tenant_id.get()
    # If product not provided, look it up from prospects
    if not product:
        p = get_prospect_by_name(prospect_name, tenant_id=tid)
        if p:
            product = p.get("product", "")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO win_loss_log (date, prospect, outcome, reason, product, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                date.today().strftime("%Y-%m-%d"),
                prospect_name,
                outcome,
                reason,
                product,
                tid,
            ),
        )
    return f"Logged {outcome} for {prospect_name}: {reason}"


def get_win_loss_stats(tenant_id: int = None):
    """Return all win/loss entries as list of dicts."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM win_loss_log WHERE tenant_id = %s ORDER BY id DESC", (tid,)
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


# ── Interactions CRUD ──

def add_interaction(data: dict, tenant_id: int = None) -> str:
    """Log an interaction (voice note, transcript, email, booking)."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO interactions (date, prospect, source, raw_text, summary, action_items, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                data.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M"),
                data.get("prospect", ""),
                data.get("source", ""),
                data.get("raw_text", ""),
                data.get("summary", ""),
                data.get("action_items", ""),
                tid,
            ),
        )
    return f"Logged interaction for {data.get('prospect', 'unknown')} via {data.get('source', '?')}."


def read_interactions(limit: int = 50, prospect: str = "", tenant_id: int = None):
    """Return recent interactions, newest first. Optionally filter by prospect."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        if prospect:
            cur.execute(
                "SELECT * FROM interactions WHERE LOWER(prospect) LIKE %s AND tenant_id = %s ORDER BY id DESC LIMIT %s",
                (f"%{prospect.lower()}%", tid, limit),
            )
        else:
            cur.execute(
                "SELECT * FROM interactions WHERE tenant_id = %s ORDER BY id DESC LIMIT %s", (tid, limit)
            )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


# ── Tasks CRUD ──

def add_task(data: dict, tenant_id: int = None):
    """Add a task. Returns the created task as dict, or None if no title."""
    tid = tenant_id or _current_tenant_id.get()
    title = data.get("title", "").strip()
    if not title:
        return None

    # Normalize remind_at to "YYYY-MM-DD HH:MM" (replace T from datetime-local inputs)
    remind_at = data.get("remind_at")
    if remind_at and isinstance(remind_at, str):
        remind_at = remind_at.replace("T", " ")

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO tasks
               (title, prospect, due_date, remind_at, assigned_to, created_by, notes, tenant_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                title,
                data.get("prospect", ""),
                data.get("due_date"),
                remind_at,
                data.get("assigned_to", ""),
                data.get("created_by", ""),
                data.get("notes", ""),
                tid,
            ),
        )
        task_id = cur.fetchone()["id"]
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
    return _row_to_dict(row)


def get_tasks(assigned_to=None, status="pending", prospect=None, limit=50, tenant_id: int = None):
    """Get tasks with filters. Orders by due_date ASC (nulls last), then created_at DESC."""
    tid = tenant_id or _current_tenant_id.get()
    conditions = ["tenant_id = %s"]
    params = [tid]

    if status:
        conditions.append("status = %s")
        params.append(status)
    if assigned_to:
        conditions.append("assigned_to = %s")
        params.append(assigned_to)
    if prospect:
        conditions.append("LOWER(prospect) LIKE %s")
        params.append(f"%{prospect.lower()}%")

    where = " AND ".join(conditions)
    params.append(limit)

    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT * FROM tasks WHERE {where}
                ORDER BY
                    CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                    due_date ASC,
                    created_at DESC
                LIMIT %s""",
            params,
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def update_task(task_id: int, updates: dict, updated_by: str = "", is_admin: bool = False) -> str:
    """Update a task's fields. Only assignee or admin can update."""
    allowed = {"title", "prospect", "due_date", "remind_at", "notes"}
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
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
        set_clauses = ", ".join(f'"{field}" = %s' for field in validated_fields)
        values = [safe_fields[f] for f in validated_fields] + [task_id]
        cur.execute(f"UPDATE tasks SET {set_clauses} WHERE id = %s", values)
    return f"Updated task {task_id}."


def complete_task(task_id: int, completed_by: str, is_admin: bool = False) -> str:
    """Mark a task as completed. Only assignee or admin can complete."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != completed_by:
            return f"Not authorized to complete task {task_id}."
        cur.execute(
            "UPDATE tasks SET status = 'completed', completed_at = NOW() WHERE id = %s",
            (task_id,),
        )
    return f"Completed: {row['title']}"


def delete_task(task_id: int, deleted_by: str, is_admin: bool = False) -> str:
    """Delete a task. Only assignee or admin can delete."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE id = %s", (task_id,))
        row = cur.fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != deleted_by:
            return f"Not authorized to delete task {task_id}."
        cur.execute("DELETE FROM tasks WHERE id = %s", (task_id,))
    return f"Deleted: {row['title']}"


def get_due_tasks(date_str: str, tenant_id: int = None):
    """Get pending tasks due on a specific date."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE due_date = %s AND status = 'pending' AND tenant_id = %s ORDER BY created_at",
            (date_str, tid),
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def get_overdue_tasks(tenant_id: int = None):
    """Get pending tasks with due_date before today."""
    tid = tenant_id or _current_tenant_id.get()
    today = date.today().strftime("%Y-%m-%d")
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE due_date < %s AND status = 'pending' AND tenant_id = %s ORDER BY due_date ASC",
            (today, tid),
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def get_reminder_tasks(now_str: str, tenant_id: int = None):
    """Get pending tasks with remind_at <= now that haven't been cleared.
    Normalizes remind_at by replacing 'T' with space for consistent comparison."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM tasks WHERE remind_at IS NOT NULL AND REPLACE(remind_at::text, 'T', ' ') <= %s AND status = 'pending' AND tenant_id = %s ORDER BY remind_at",
            (now_str.replace("T", " "), tid),
        )
        rows = cur.fetchall()
    return _rows_to_dicts(rows)


def clear_reminder(task_id: int):
    """Clear remind_at after firing so it doesn't repeat."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE tasks SET remind_at = NULL WHERE id = %s", (task_id,))


# ── Tag helpers ──

def apply_tag(prospect_id: int, tag: str, applied_by: str = "system") -> bool:
    """Apply a tag to a prospect. Returns True if new, False if already existed."""
    with get_db() as conn:
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO prospect_tags (prospect_id, tag, applied_by) VALUES (%s, %s, %s)",
                (prospect_id, tag, applied_by)
            )
            return True
        except psycopg2.Error:
            return False

def remove_tag(prospect_id: int, tag: str) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM prospect_tags WHERE prospect_id = %s AND tag = %s", (prospect_id, tag))

def get_tags(prospect_id: int) -> list[str]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT tag FROM prospect_tags WHERE prospect_id = %s", (prospect_id,))
        rows = cur.fetchall()
        return [r["tag"] for r in rows]

def get_prospects_by_tag(tag: str) -> list[dict]:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT p.* FROM prospects p
            JOIN prospect_tags t ON t.prospect_id = p.id
            WHERE t.tag = %s
        """, (tag,))
        rows = cur.fetchall()
        return _rows_to_dicts(rows)

def queue_enrichment(prospect_id: int) -> None:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO enrichment_queue (prospect_id) VALUES (%s)
            ON CONFLICT(prospect_id) DO NOTHING
        """, (prospect_id,))


# ── Reporting queries ──

def get_conversion_by_source() -> list[dict]:
    """Conversion rate by lead source."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                source,
                COUNT(*) as total_leads,
                SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) as closed,
                ROUND(
                    100.0 * SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) / COUNT(*),
                    1
                ) as conversion_rate
            FROM prospects
            WHERE source IS NOT NULL AND source != ''
            GROUP BY source
            ORDER BY total_leads DESC
        """)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_pipeline_metrics() -> dict:
    """High-level pipeline summary metrics."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) as closed_won,
                SUM(CASE WHEN stage = 'New Lead' THEN 1 ELSE 0 END) as new_leads,
                SUM(CASE WHEN stage NOT IN ('Closed Won', 'Closed Lost') THEN 1 ELSE 0 END) as active
            FROM prospects
        """)
        row = cur.fetchone()
    if not row:
        return {"total": 0, "closed_won": 0, "new_leads": 0, "active": 0}
    return dict(row)


def get_stage_funnel() -> list[dict]:
    """Count of prospects per pipeline stage, ordered logically."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT stage, COUNT(*) as count
            FROM prospects
            GROUP BY stage
            ORDER BY count DESC
        """)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_fyc_by_advisor() -> list[dict]:
    """First-year commission summary by assigned advisor."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COALESCE(assigned_to, 'Unassigned') as advisor,
                COUNT(*) as total_closed,
                SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) as closed_won
            FROM prospects
            GROUP BY assigned_to
            ORDER BY closed_won DESC
        """)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_avg_stage_time() -> list[dict]:
    """Average days prospects spend in each stage (approximate)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT stage, COUNT(*) as count
            FROM prospects
            GROUP BY stage
        """)
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def get_trust_level(tenant_id: int = None) -> int:
    """Return the AI trust level for a tenant (1=draft only, 2=routine auto, 3=full autonomy)."""
    tid = tenant_id or _current_tenant_id.get()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT trust_level FROM tenants WHERE id = %s", (tid,)
        )
        row = cur.fetchone()
    if row and row["trust_level"]:
        return int(row["trust_level"])
    return 1  # Default to most conservative


def create_email_tracking_token(prospect_id: int, prospect_name: str, email_type: str) -> str:
    """Create a unique tracking token for an outgoing email. Returns the token."""
    token = secrets.token_urlsafe(20)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO email_tracking (prospect_id, prospect_name, email_type, token)
            VALUES (%s, %s, %s, %s)
        """, (prospect_id, prospect_name, email_type, token))
    return token


def record_email_open(token: str) -> bool:
    """Record that a tracking pixel was fired. Returns True if token existed."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE email_tracking
            SET opened_at = COALESCE(opened_at, NOW())
            WHERE token = %s AND opened_at IS NULL
        """, (token,))
        return cur.rowcount > 0


def record_link_click(token: str) -> str | None:
    """Record a link click. Returns None (destination URL stored in notes, not here)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE email_tracking
            SET link_clicked_at = COALESCE(link_clicked_at, NOW())
            WHERE token = %s
        """, (token,))
    return None


def add_intake_form_response(prospect_id: int, form_type: str, responses: str) -> None:
    """Save a completed intake form response (JSON string)."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO intake_form_responses (prospect_id, form_type, responses)
            VALUES (%s, %s, %s)
        """, (prospect_id, form_type, responses))
