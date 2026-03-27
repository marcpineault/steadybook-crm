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

_fernet_instance: Fernet | None = None
_fernet_key_used: str = ""

def _get_fernet() -> Fernet:
    """Get or create a cached Fernet instance. Ensures encryption/decryption consistency."""
    global _fernet_instance, _fernet_key_used
    key = os.environ.get("ENCRYPTION_KEY", "")
    if not key:
        if _fernet_instance is None:
            key = Fernet.generate_key().decode()
            logger.warning(
                "ENCRYPTION_KEY not set — generated ephemeral key. "
                "Set ENCRYPTION_KEY env var to persist encrypted config across restarts."
            )
            _fernet_key_used = key
            _fernet_instance = Fernet(key.encode())
        return _fernet_instance
    if key != _fernet_key_used:
        _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
        _fernet_key_used = key
    return _fernet_instance


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
    try:
        _upsert_row(tenant_id, key, encrypted)
    except Exception:
        logger.error("config_store: failed to persist key=%s for tenant_id=%s", key, tenant_id)
        raise


def get_all_config(tenant_id: int) -> dict[str, str]:
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
