# SteadyBook CRM — SaaS Production Design
**Date:** 2026-03-26
**Status:** Approved
**Scope:** Full SaaS rebuild — Neon Postgres, multi-tenant isolation, real auth, Railway deployment

---

## 1. Overview

SteadyBook CRM is a pipeline management and automation platform for insurance brokerages. Each brokerage (tenant) has a manager and a team of advisors. Advisors manage their own prospect pipelines; managers see across all advisors in their firm.

The system replaces a single-user SQLite-backed deployment with a fully multi-tenant SaaS product on Railway, backed by Neon Postgres.

---

## 2. Architecture

```
Railway (production)
├── web     → gunicorn dashboard:app       (Flask dashboard + API)
└── worker  → python bot.py               (Telegram bot, scheduler)

Neon Postgres (shared, row-level tenant isolation)
└── All tables scoped by tenant_id

Per-tenant config (in DB, encrypted)
└── OpenAI key, Telegram token, Twilio creds, Resend key, etc.
```

**Required env vars at deploy time (Railway):**
- `DATABASE_URL` — Neon Postgres connection string (only hard requirement)
- `SECRET_KEY` — Flask session signing key (auto-generated on first boot if absent)
- `ENCRYPTION_KEY` — Fernet key for encrypting tenant API keys (auto-generated on first boot if absent)

Everything else (OpenAI, Telegram, Twilio, Resend) lives in the `tenant_config` table, set per-brokerage via the Settings UI.

---

## 3. Database Layer

### 3.1 Connection Layer (db.py)

Replace `sqlite3` with `psycopg2` (or `psycopg2-binary`). The `get_db()` context manager stays identical — callers don't change. Neon connection string comes from `DATABASE_URL` env var.

SQLite-specific syntax to replace:
- `datetime('now')` → `NOW()`
- `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL PRIMARY KEY`
- `PRAGMA` statements → removed
- `sqlite3.Row` → psycopg2 `RealDictCursor`
- `?` placeholders → `%s` placeholders

### 3.2 Tenant Context

A Python `contextvars.ContextVar` named `_current_tenant_id` is set at the start of every authenticated request (via `@_require_auth` decorator). Every db.py query function accepts an optional `tenant_id` param that defaults to `_current_tenant_id.get()`. This means zero changes to callers — tenant scoping is automatic.

```python
# Set in auth middleware
_current_tenant_id.set(user["tenant_id"])

# Used in every query
def read_pipeline(tenant_id=None):
    tid = tenant_id or _current_tenant_id.get()
    # SELECT ... WHERE tenant_id = %s
```

### 3.3 Tables Requiring tenant_id

All of the following tables get `tenant_id INTEGER NOT NULL REFERENCES tenants(id)`:

- `prospects`, `activities`, `meetings`, `tasks`, `notes`
- `approval_queue`, `audit_log`, `brand_voice`, `trust_config`
- `campaigns`, `campaign_messages`, `outcomes`
- `nurture_sequences`, `booking_nurture_sequences`
- `sms_conversations`, `sms_agents`
- `sequences`, `sequence_enrollments`, `sequence_step_logs`
- `prospect_tags`, `enrichment_queue`, `referrals`
- `intake_form_responses`, `email_tracking`
- `insurance_book`, `win_loss_log`, `interactions`
- `client_memory`, `market_calendar`

### 3.4 New Tables

**tenant_config** — per-tenant encrypted API keys and settings:
```sql
CREATE TABLE tenant_config (
    id          SERIAL PRIMARY KEY,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id),
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,  -- Fernet encrypted
    updated_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, key)
);
```

Keys stored: `OPENAI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TELEGRAM_WEBHOOK_SECRET`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `RESEND_API_KEY`, `INTAKE_WEBHOOK_SECRET`, `BOOKING_URL`, `COMPANY_NAME`, `ADVISOR_NAME`

**Config access pattern:**
```python
def get_tenant_config(key, tenant_id=None):
    """Returns decrypted config value, falls back to env var."""
    tid = tenant_id or _current_tenant_id.get()
    row = db_query("SELECT value FROM tenant_config WHERE tenant_id=%s AND key=%s", tid, key)
    if row:
        return fernet.decrypt(row["value"])
    return os.environ.get(key, "")
```

---

## 4. Authentication & Roles

### 4.1 Roles
- `owner` — created at signup, full access including billing and team management
- `manager` — sees all advisors' pipelines within their tenant, cannot change billing
- `advisor` — sees only prospects assigned to them (`assigned_to = user.id`)

### 4.2 Auth Flow
- Email + password, bcrypt hashed (`bcrypt` library)
- Session stored in signed httpOnly cookie (`SECRET_KEY`)
- Session contains: `user_id`, `tenant_id`, `role`, `name`
- Every request: load user from DB → set `_current_tenant_id` context var → proceed

### 4.3 Existing Routes to Complete
The following routes exist but need to be wired to the Postgres-backed user/tenant system:
- `POST /api/auth/register` — create tenant + owner user
- `POST /api/auth/login` — email/password → session cookie
- `POST /api/auth/logout` — clear session
- `GET /api/auth/me` — return current user info
- `GET /register` — registration page
- `GET /login` — login page (already exists)

### 4.4 First-Boot Redirect
If `SELECT COUNT(*) FROM tenants` returns 0, redirect all requests to `/register`. No blank dashboard, no crash.

### 4.5 Advisor Invites
- Owner generates invite link: `/invite/<signed_token>`
- Token encodes `tenant_id` + `role` + expiry (signed with `SECRET_KEY`)
- Recipient sets name + password → creates user under tenant
- No email required (link can be shared via Slack, WhatsApp, etc.)

---

## 5. Graceful Degradation

When a tenant hasn't configured an integration, the feature is disabled in the UI — not a 500 error.

| Integration | Unconfigured behavior |
|---|---|
| OpenAI | AI chat, drafts, recommendations show "Connect OpenAI to enable" |
| Telegram | Bot commands disabled; dashboard-only mode |
| Twilio | SMS tab shows "Connect Twilio to enable" |
| Resend | Email sequences paused; shown in sequence editor |
| All | Core pipeline, tasks, clients, reporting always work |

Implementation: `get_tenant_config()` returns `""` when not set. Each module checks at call time:
```python
key = get_tenant_config("OPENAI_API_KEY")
if not key:
    return {"error": "not_configured", "message": "OpenAI not connected"}
```

---

## 6. Self-Serve Signup & Onboarding

### 6.1 Signup (`/register`)
1. Enter: firm name, your name, email, password
2. Creates: `tenants` row + `users` row (role=owner)
3. Redirects to: `/setup` (onboarding wizard)

### 6.2 Onboarding Wizard (`/setup`)
Already built at `/setup` + `templates/onboarding.html`. Wire to:
- Step 1: Firm details (name, timezone, products offered)
- Step 2: Connect OpenAI (test button → `/api/setup/test/openai`)
- Step 3: Connect Telegram bot (test button → `/api/setup/test/telegram`)
- Step 4: Connect Twilio SMS (test button → `/api/setup/test/twilio`)
- Step 5: Connect Resend email (test button → `/api/setup/test/resend`)
- Step 6: Invite first advisor (optional, skippable)

Each step saves to `tenant_config` via `POST /api/tenant/config`. Already implemented.

### 6.3 Settings Page (`/settings`)
Already built. Wire "Save" to `PUT /api/tenant/config`. Shows connected/disconnected status per integration.

---

## 7. Telegram Bot Multi-Tenancy

The bot runs as a single process. Tenants are identified by their registered `TELEGRAM_CHAT_ID` in `tenant_config`.

On each incoming Telegram message:
1. Look up `chat_id` across `tenant_config WHERE key='TELEGRAM_CHAT_ID'`
2. Identify tenant → set context → process message with that tenant's OpenAI key etc.
3. If `chat_id` not registered → reply with registration instructions

Per-tenant bot tokens (each brokerage has their own `@BotFather` token) are supported: on startup, the worker process loads all active tenant Telegram tokens and registers each as a separate bot application.

---

## 8. Railway Deployment

### 8.1 Procfile
```
web: gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 dashboard:app
worker: python bot.py
```

### 8.2 Dockerfile
```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD python -c "import db; db.init_db()" && gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 dashboard:app
```

### 8.3 Required Railway Environment Variables
```
DATABASE_URL      (Neon connection string — required)
SECRET_KEY        (auto-generated if absent)
ENCRYPTION_KEY    (auto-generated if absent, stored as Railway var after first boot)
```

### 8.4 Health Check
`GET /health` returns `{"status": "ok", "db": "connected"}` — used by Railway for deployment health checks.

---

## 9. Implementation Order

Build in this sequence to maintain a working app throughout:

1. **Add dependencies** — `psycopg2-binary`, `bcrypt`, `cryptography` to requirements.txt
2. **Rewrite db.py** — Postgres connection, same function signatures, tenant context var
3. **Migrate init_db()** — PostgreSQL DDL, all tables with tenant_id
4. **Rewrite auth** — complete `/api/auth/*` routes with bcrypt + session
5. **Wire tenant_config** — `get_tenant_config()` helper, encrypt/decrypt
6. **Update all db functions** — add `tenant_id` scoping to every query
7. **Fix graceful degradation** — every integration checks config before use
8. **Wire onboarding/settings** — connect `/setup` and `/settings` to tenant_config
9. **Fix bot.py** — multi-tenant routing by chat_id, per-tenant config
10. **Fix Procfile** — run bot.py as worker
11. **Update Dockerfile** — remove SQLite init, add health check
12. **End-to-end test** — register → onboard → invite advisor → use all features

---

## 10. Out of Scope (Phase 2)

- Stripe billing / subscription management
- Per-tenant Neon database branches (enterprise isolation)
- Custom subdomains (`firm.steadybook.com`)
- White-label branding per tenant
- Mobile app
