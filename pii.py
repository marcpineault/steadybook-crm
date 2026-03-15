"""PII redaction and restoration for OpenAI API calls.

Strips personally identifiable information from text before sending to
external LLM APIs, and optionally restores reversible tokens (names) in
AI-generated output that must contain real client names.
"""

import re

# ---------------------------------------------------------------------------
# Regex patterns for PII detection
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
_PHONE_RE = re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_ACCOUNT_RE = re.compile(r"\b\d{8,16}\b")  # Generic long number sequences (account/policy numbers)


# ---------------------------------------------------------------------------
# Atomic redactors
# ---------------------------------------------------------------------------

def redact_email(email: str) -> str:
    """Mask an email address: john.doe@example.com -> j***@e***.com"""
    try:
        local, domain = email.split("@", 1)
        domain_parts = domain.split(".", 1)
        masked_local = local[0] + "***" if local else "***"
        masked_domain = domain_parts[0][0] + "***" if domain_parts[0] else "***"
        tld = "." + domain_parts[1] if len(domain_parts) > 1 else ""
        return f"{masked_local}@{masked_domain}{tld}"
    except (ValueError, IndexError):
        return "[EMAIL_REDACTED]"


def redact_phone(phone: str) -> str:
    """Mask a phone number, keeping last 4 digits: 519-555-1234 -> ***-***-1234"""
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 4:
        return f"***-***-{digits[-4:]}"
    return "[PHONE_REDACTED]"


def _dollar_to_range(match: re.Match) -> str:
    """Convert a dollar amount to an approximate range."""
    raw = match.group(0)
    cleaned = raw.replace("$", "").replace(",", "")
    try:
        amount = float(cleaned)
    except ValueError:
        return "[AMOUNT_REDACTED]"
    if amount < 1_000:
        return "[under $1K]"
    if amount < 10_000:
        return f"[${int(amount // 1_000)}K-${int(amount // 1_000) + 1}K range]"
    if amount < 100_000:
        lower = int(amount // 10_000) * 10
        return f"[${lower}K-${lower + 10}K range]"
    if amount < 1_000_000:
        lower = int(amount // 100_000) * 100
        return f"[${lower}K-${lower + 100}K range]"
    lower = int(amount // 1_000_000)
    return f"[${lower}M-${lower + 1}M range]"


# ---------------------------------------------------------------------------
# Name redaction (reversible via redaction map)
# ---------------------------------------------------------------------------

def _build_name_pattern(name: str) -> re.Pattern | None:
    """Build a regex to match a name (case-insensitive, word-boundary)."""
    name = name.strip()
    if not name or len(name) < 2:
        return None
    escaped = re.escape(name)
    return re.compile(r"\b" + escaped + r"\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Full-text redaction
# ---------------------------------------------------------------------------

def redact_text(text: str, known_names: list[str] | None = None,
                redaction_map: dict | None = None) -> str:
    """Apply all redaction passes to free text.

    Args:
        text: The text to redact.
        known_names: List of prospect/client names to replace with tokens.
        redaction_map: Dict to populate with {token: original_name} mappings.
                       Pass the same dict to restore_names() later.

    Returns:
        Redacted text.
    """
    if not text:
        return text

    if redaction_map is None:
        redaction_map = {}

    # 1. SSNs first (most specific pattern)
    text = _SSN_RE.sub("[SSN_REDACTED]", text)

    # 2. Names (reversible tokenization) — do before emails so names inside
    #    email addresses don't cause partial matches
    if known_names:
        # Sort by length (longest first) to avoid partial replacements
        sorted_names = sorted(known_names, key=len, reverse=True)
        counter = len(redaction_map)
        for name in sorted_names:
            pattern = _build_name_pattern(name)
            if pattern is None:
                continue
            token = f"[CLIENT_{counter + 1:02d}]"
            if pattern.search(text):
                redaction_map[token] = name
                text = pattern.sub(token, text)
                counter += 1

    # 3. Emails (irreversible)
    text = _EMAIL_RE.sub(lambda m: redact_email(m.group(0)), text)

    # 4. Phones (irreversible)
    text = _PHONE_RE.sub(lambda m: redact_phone(m.group(0)), text)

    # 5. Dollar amounts → ranges
    text = _DOLLAR_RE.sub(_dollar_to_range, text)

    return text


def restore_names(text: str, redaction_map: dict) -> str:
    """Replace [CLIENT_XX] tokens back to real names in AI output."""
    if not text or not redaction_map:
        return text
    for token, real_name in redaction_map.items():
        text = text.replace(token, real_name)
    return text


# ---------------------------------------------------------------------------
# RedactionContext — convenience wrapper for a single AI call
# ---------------------------------------------------------------------------

class RedactionContext:
    """Context manager for a redaction/restoration cycle.

    Usage:
        with RedactionContext(prospect_names=["John Smith", "Jane Doe"]) as ctx:
            safe_prompt = ctx.redact(prompt_text)
            # ... call OpenAI with safe_prompt ...
            restored_output = ctx.restore(ai_response)
    """

    def __init__(self, prospect_names: list[str] | None = None):
        self._names = prospect_names or []
        self._map: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def redact(self, text: str) -> str:
        """Redact PII from text, building the internal name map."""
        return redact_text(text, known_names=self._names, redaction_map=self._map)

    def restore(self, text: str) -> str:
        """Restore [CLIENT_XX] tokens to real names in AI output."""
        return restore_names(text, self._map)

    @property
    def redaction_map(self) -> dict:
        return dict(self._map)


# ---------------------------------------------------------------------------
# Logging helpers — use these instead of raw PII in log statements
# ---------------------------------------------------------------------------

def safe_log_name(name: str) -> str:
    """Return initials for logging: 'John Smith' -> 'J.S.'"""
    if not name:
        return "?"
    parts = name.strip().split()
    return ".".join(p[0].upper() for p in parts if p) + "."


def safe_log_email(email: str) -> str:
    """Return masked email for logging: 'john@example.com' -> 'j***@e***.com'"""
    return redact_email(email)


# ---------------------------------------------------------------------------
# Prompt injection sanitization
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts?|rules?)", re.IGNORECASE),
    re.compile(r"(system|assistant)\s*:", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|above|prior)", re.IGNORECASE),
]


def sanitize_for_prompt(text: str) -> str:
    """Remove common prompt injection patterns from user-supplied text.

    This is defense-in-depth — the primary defense is system/user message
    separation in OpenAI calls (Phase 3).
    """
    if not text:
        return text
    for pattern in _INJECTION_PATTERNS:
        text = pattern.sub("[FILTERED]", text)
    return text
