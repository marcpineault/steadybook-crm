"""Multi-tenant management for Calm Money CRM SaaS.

Provides tenant isolation, user authentication, and per-tenant configuration.
Backwards-compatible: existing single-tenant deployments keep working via
a default tenant (id=1) and legacy API key auth.

Usage:
    from tenants import get_current_tenant, require_tenant_auth
"""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps

import db

logger = logging.getLogger(__name__)

# ── Password hashing (bcrypt-like with hashlib fallback) ──

_HASH_ITERATIONS = 400_000  # OWASP recommendation for PBKDF2-SHA256


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-SHA256. Returns 'salt$hash'."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _HASH_ITERATIONS)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash."""
    if "$" not in stored:
        return False
    salt, hash_hex = stored.split("$", 1)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _HASH_ITERATIONS)
    return hmac.compare_digest(dk.hex(), hash_hex)


# ── Session tokens ──

_sessions: dict = {}  # token -> {tenant_id, user_id, expires}
_SESSION_TTL = 86400  # 24 hours


def create_session(tenant_id: int, user_id: int) -> str:
    """Create a session token for a user. Returns the token."""
    token = secrets.token_urlsafe(48)
    _sessions[token] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "expires": time.time() + _SESSION_TTL,
    }
    # Purge expired sessions (keep memory bounded)
    expired = [t for t, s in _sessions.items() if s["expires"] < time.time()]
    for t in expired:
        del _sessions[t]
    return token


def validate_session(token: str) -> dict | None:
    """Validate a session token. Returns {tenant_id, user_id} or None."""
    session = _sessions.get(token)
    if not session:
        return None
    if session["expires"] < time.time():
        del _sessions[token]
        return None
    return {"tenant_id": session["tenant_id"], "user_id": session["user_id"]}


def destroy_session(token: str):
    """Invalidate a session token."""
    _sessions.pop(token, None)


# ── Tenant CRUD ──

def create_tenant(name: str, slug: str, owner_email: str, owner_password: str,
                  owner_name: str = "", company: str = "", timezone: str = "America/Toronto",
                  products: list | None = None) -> dict:
    """Create a new tenant with an owner user. Returns tenant dict.

    Args:
        name: Display name for the tenant/workspace
        slug: URL-safe identifier (lowercase, alphanumeric + hyphens)
        owner_email: Email for the owner account
        owner_password: Password for the owner account
        owner_name: Display name for the owner
        company: Company/brokerage name
        timezone: Default timezone
        products: List of product names this tenant sells
    """
    # Validate slug
    slug = slug.lower().strip()
    if not re.match(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$", slug):
        raise ValueError("Slug must be 3-50 chars, lowercase alphanumeric and hyphens only")

    default_config = {
        "booking_url": "",
        "brand_name": name,
        "sender_name": owner_name or name,
        "sender_signature": f"- {owner_name.split()[0] if owner_name else name}",
        "compliance_enabled": True,
        "approval_required": True,
        "trust_level": 1,
        "stage_labels": list(db.STAGE_PROBABILITY.keys()) if hasattr(db, "STAGE_PROBABILITY") else [
            "New Lead", "Contacted", "Discovery Call", "Needs Analysis",
            "Plan Presentation", "Proposal Sent", "Negotiation", "Nurture",
            "Closed-Won", "Closed-Lost",
        ],
        "cross_sell_matrix": {},
    }

    products_json = json.dumps(products or [
        "Life Insurance", "Disability Insurance", "Critical Illness",
        "Wealth Management", "Group Benefits", "Estate Planning",
    ])

    with db.get_db() as conn:
        # Check slug uniqueness
        existing = conn.execute("SELECT id FROM tenants WHERE slug = ?", (slug,)).fetchone()
        if existing:
            raise ValueError(f"Slug '{slug}' is already taken")

        # Check email uniqueness
        existing_email = conn.execute("SELECT id FROM users WHERE LOWER(email) = ?",
                                      (owner_email.lower(),)).fetchone()
        if existing_email:
            raise ValueError(f"Email '{owner_email}' is already registered")

        # Create tenant
        cursor = conn.execute(
            """INSERT INTO tenants (name, slug, company, timezone, products, config, plan, status)
               VALUES (?, ?, ?, ?, ?, ?, 'starter', 'active')""",
            (name, slug, company, timezone, products_json, json.dumps(default_config)),
        )
        tenant_id = cursor.lastrowid

        # Create owner user
        password_hash = hash_password(owner_password)
        conn.execute(
            """INSERT INTO users (tenant_id, email, password_hash, name, role, status)
               VALUES (?, ?, ?, ?, 'owner', 'active')""",
            (tenant_id, owner_email.lower(), password_hash, owner_name),
        )

        # Generate default API key
        api_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        conn.execute(
            """INSERT INTO api_keys (tenant_id, key_hash, name, scopes)
               VALUES (?, ?, 'Default', '["all"]')""",
            (tenant_id, key_hash),
        )

        tenant = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()

    result = dict(tenant)
    result["api_key"] = api_key  # Only returned on creation
    return result


def get_tenant(tenant_id: int) -> dict | None:
    """Get a tenant by ID."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
    return dict(row) if row else None


def get_tenant_by_slug(slug: str) -> dict | None:
    """Get a tenant by slug."""
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (slug.lower(),)).fetchone()
    return dict(row) if row else None


def get_tenant_config(tenant_id: int) -> dict:
    """Get parsed config for a tenant. Returns empty dict if not found."""
    tenant = get_tenant(tenant_id)
    if not tenant:
        return {}
    try:
        return json.loads(tenant.get("config") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def update_tenant_config(tenant_id: int, updates: dict) -> dict:
    """Merge updates into tenant config. Returns updated config."""
    current = get_tenant_config(tenant_id)
    current.update(updates)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE tenants SET config = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(current), tenant_id),
        )
    return current


# ── User CRUD ──

def authenticate_user(email: str, password: str) -> dict | None:
    """Authenticate a user by email/password. Returns user dict with tenant info, or None."""
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT u.*, t.name as tenant_name, t.slug as tenant_slug, t.status as tenant_status
               FROM users u JOIN tenants t ON u.tenant_id = t.id
               WHERE LOWER(u.email) = ? AND u.status = 'active' AND t.status = 'active'""",
            (email.lower(),),
        ).fetchone()

    if not row:
        return None

    user = dict(row)
    if not verify_password(password, user["password_hash"]):
        return None

    # Update last login
    with db.get_db() as conn:
        conn.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user["id"],))

    user.pop("password_hash", None)
    return user


def create_user(tenant_id: int, email: str, password: str, name: str = "",
                role: str = "agent") -> dict:
    """Create a new user for a tenant. Returns user dict."""
    if role not in ("owner", "admin", "agent"):
        raise ValueError("Role must be owner, admin, or agent")

    with db.get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE LOWER(email) = ?",
                                (email.lower(),)).fetchone()
        if existing:
            raise ValueError(f"Email '{email}' is already registered")

        password_hash = hash_password(password)
        cursor = conn.execute(
            """INSERT INTO users (tenant_id, email, password_hash, name, role, status)
               VALUES (?, ?, ?, ?, ?, 'active')""",
            (tenant_id, email.lower(), password_hash, name, role),
        )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()

    user = dict(row)
    user.pop("password_hash", None)
    return user


def get_tenant_users(tenant_id: int) -> list[dict]:
    """Get all users for a tenant."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT id, tenant_id, email, name, role, status, created_at, last_login "
            "FROM users WHERE tenant_id = ? ORDER BY created_at",
            (tenant_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── API Key auth ──

def authenticate_api_key(key: str) -> dict | None:
    """Authenticate an API key. Returns {tenant_id, key_id, scopes} or None."""
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT ak.*, t.status as tenant_status
               FROM api_keys ak JOIN tenants t ON ak.tenant_id = t.id
               WHERE ak.key_hash = ? AND t.status = 'active'
               AND (ak.expires_at IS NULL OR ak.expires_at > datetime('now'))""",
            (key_hash,),
        ).fetchone()

    if not row:
        return None

    result = dict(row)
    try:
        result["scopes"] = json.loads(result.get("scopes") or '["all"]')
    except json.JSONDecodeError:
        result["scopes"] = ["all"]
    return result


# ── Flask auth decorator (multi-tenant) ──

def require_tenant_auth(f):
    """Decorator: authenticates request and injects tenant context.

    Checks (in order):
    1. X-API-Key header (for programmatic access)
    2. Session cookie (dash_session) for logged-in users
    3. Legacy API key fallback (DASHBOARD_API_KEY env var → tenant_id=1)

    Sets request.tenant_id and request.user_id on success.
    """
    from flask import request as flask_request, jsonify as flask_jsonify

    @wraps(f)
    def decorated(*args, **kwargs):
        # 1. API key auth
        api_key = flask_request.headers.get("X-API-Key", "")
        if api_key:
            key_info = authenticate_api_key(api_key)
            if key_info:
                flask_request.tenant_id = key_info["tenant_id"]
                flask_request.user_id = None
                return f(*args, **kwargs)

        # 2. Session cookie
        session_token = flask_request.cookies.get("dash_session", "")
        if session_token:
            session = validate_session(session_token)
            if session:
                flask_request.tenant_id = session["tenant_id"]
                flask_request.user_id = session["user_id"]
                return f(*args, **kwargs)

        # 3. Legacy fallback: old DASHBOARD_API_KEY auth (maps to tenant_id=1)
        legacy_key = os.environ.get("DASHBOARD_API_KEY", "")
        if legacy_key:
            # Check X-API-Key against legacy key
            if api_key and hmac.compare_digest(api_key, legacy_key):
                flask_request.tenant_id = 1
                flask_request.user_id = None
                return f(*args, **kwargs)

            # Check CSRF token (legacy dashboard auth)
            csrf_token = flask_request.headers.get("X-CSRF-Token", "")
            if csrf_token:
                from dashboard import _validate_csrf_token
                if _validate_csrf_token(csrf_token):
                    flask_request.tenant_id = 1
                    flask_request.user_id = None
                    return f(*args, **kwargs)

            # Check login cookie (legacy)
            dash_cookie = flask_request.cookies.get("dash_auth", "")
            if dash_cookie:
                expected = hashlib.sha256(legacy_key.encode()).hexdigest()
                if hmac.compare_digest(dash_cookie, expected):
                    flask_request.tenant_id = 1
                    flask_request.user_id = None
                    return f(*args, **kwargs)

        return flask_jsonify({"error": "Unauthorized"}), 401
    return decorated


# ── Plans & Features ──

PLANS = {
    "starter": {
        "name": "Starter",
        "price_monthly": 149,
        "max_prospects": 200,
        "max_sequences": 5,
        "max_users": 1,
        "features": ["crm", "pipeline", "ai_followups", "sms", "email", "sequences"],
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 299,
        "max_prospects": 1000,
        "max_sequences": 25,
        "max_users": 5,
        "features": ["crm", "pipeline", "ai_followups", "sms", "email", "sequences",
                      "campaigns", "analytics", "compliance", "api_access"],
    },
    "agency": {
        "name": "Agency",
        "price_monthly": 599,
        "max_prospects": -1,  # unlimited
        "max_sequences": -1,
        "max_users": -1,
        "features": ["crm", "pipeline", "ai_followups", "sms", "email", "sequences",
                      "campaigns", "analytics", "compliance", "api_access",
                      "white_label", "custom_integrations", "priority_support"],
    },
}


def check_plan_limit(tenant_id: int, resource: str) -> bool:
    """Check if a tenant is within their plan limits.

    Args:
        resource: 'prospects', 'sequences', or 'users'

    Returns True if within limits, False if at capacity.
    """
    tenant = get_tenant(tenant_id)
    if not tenant:
        return False

    plan = PLANS.get(tenant.get("plan", "starter"), PLANS["starter"])
    limit_key = f"max_{resource}"
    limit = plan.get(limit_key, 0)

    if limit == -1:  # unlimited
        return True

    with db.get_db() as conn:
        if resource == "prospects":
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM prospects WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()["cnt"]
        elif resource == "sequences":
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM sequences WHERE tenant_id = ? AND status != 'archived'",
                (tenant_id,),
            ).fetchone()["cnt"]
        elif resource == "users":
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM users WHERE tenant_id = ? AND status = 'active'",
                (tenant_id,),
            ).fetchone()["cnt"]
        else:
            return True

    return count < limit
