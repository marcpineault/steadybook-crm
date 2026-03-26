# Capture Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add business card photo capture, QR landing page, and a unified `/api/social-intake` webhook that receives normalized payloads from n8n for Instagram, LinkedIn, WhatsApp, Gmail, Outlook, and Calendar.

**Architecture:** Extend the existing Telegram bot with a photo handler using GPT-4o Vision. Add a Flask QR landing page route per tenant. Add a single `/api/social-intake` webhook endpoint that all n8n workflows POST to with a normalized schema — SteadyBook never calls Meta/LinkedIn/Google APIs directly.

**Tech Stack:** Python 3.13, Flask, python-telegram-bot, OpenAI GPT-4o Vision, SQLite via `db.py`, existing `intake.py` dedup logic, HMAC webhook validation.

**Note:** Voice memo capture is already complete in `voice_handler.py`. Do not modify it.

**Cross-plan dependency:** Plans B, C, D depend on the DB schema additions in Task 1 of this plan. Run Task 1 first before parallel execution with other plans.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `db.py` | Modify | Add `prospect_tags`, `referrals`, `intake_forms`, `enrichment_queue` tables |
| `photo_handler.py` | Create | GPT-4o Vision business card extraction |
| `bot.py` | Modify | Register photo handler, add confirmation flow |
| `social_intake.py` | Create | `/api/social-intake` and `/api/calendar-intake` webhook handlers |
| `dashboard.py` | Modify | Register new blueprints, add `/qr/<tenant_id>` route |
| `templates/qr_landing.html` | Create | Mobile-optimized QR landing page |
| `tests/test_photo_handler.py` | Create | Unit tests for card extraction |
| `tests/test_social_intake.py` | Create | Unit tests for n8n webhook parsing |

---

## Task 1: DB Schema — New Tables

**Files:**
- Modify: `db.py:122` (inside `init_db()`)

- [ ] **Step 1: Add new tables to `init_db()`**

Open `db.py`. Inside `init_db()`, after the last `CREATE TABLE` block (after `sequence_step_logs`), add:

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prospect_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prospect_id INTEGER NOT NULL,
                    tag TEXT NOT NULL,
                    applied_by TEXT DEFAULT 'system',
                    applied_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE,
                    UNIQUE(prospect_id, tag)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS enrichment_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prospect_id INTEGER NOT NULL UNIQUE,
                    status TEXT DEFAULT 'pending',
                    attempts INTEGER DEFAULT 0,
                    last_attempt TEXT,
                    linkedin_url TEXT,
                    instagram_handle TEXT,
                    headshot_url TEXT,
                    bio TEXT,
                    company_website TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_prospect_id INTEGER,
                    referred_prospect_id INTEGER NOT NULL,
                    referral_date TEXT DEFAULT (datetime('now')),
                    notes TEXT,
                    FOREIGN KEY (referrer_prospect_id) REFERENCES prospects(id),
                    FOREIGN KEY (referred_prospect_id) REFERENCES prospects(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intake_form_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prospect_id INTEGER NOT NULL,
                    form_type TEXT NOT NULL,
                    responses TEXT NOT NULL,
                    submitted_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE CASCADE
                )
            """)
```

- [ ] **Step 2: Add helper functions at bottom of `db.py`**

```python
def apply_tag(prospect_id: int, tag: str, applied_by: str = "system") -> bool:
    """Apply a tag to a prospect. Returns True if new, False if already existed."""
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO prospect_tags (prospect_id, tag, applied_by) VALUES (?,?,?)",
                (prospect_id, tag, applied_by)
            )
            return True
        except sqlite3.IntegrityError:
            return False

def remove_tag(prospect_id: int, tag: str) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM prospect_tags WHERE prospect_id=? AND tag=?", (prospect_id, tag))

def get_tags(prospect_id: int) -> list[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT tag FROM prospect_tags WHERE prospect_id=?", (prospect_id,)).fetchall()
        return [r["tag"] for r in rows]

def get_prospects_by_tag(tag: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT p.* FROM prospects p
            JOIN prospect_tags t ON t.prospect_id = p.id
            WHERE t.tag = ?
        """, (tag,)).fetchall()
        return _rows_to_dicts(rows)

def queue_enrichment(prospect_id: int) -> None:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO enrichment_queue (prospect_id) VALUES (?)
            ON CONFLICT(prospect_id) DO NOTHING
        """, (prospect_id,))
```

- [ ] **Step 3: Run existing tests to confirm no regression**

```bash
cd /Users/map98/Projects/steadybook-crm
python -m pytest tests/ -x -q 2>&1 | tail -20
```

Expected: all existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add db.py
git commit -m "feat: add prospect_tags, enrichment_queue, referrals, intake_form_responses tables"
```

---

## Task 2: Business Card Photo Capture

**Files:**
- Create: `photo_handler.py`
- Modify: `bot.py`
- Create: `tests/test_photo_handler.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_photo_handler.py`:

```python
"""Tests for business card photo extraction."""
import pytest
from unittest.mock import patch, MagicMock


def test_parse_card_response_full():
    from photo_handler import parse_card_response
    raw = '{"name":"Sarah Chen","title":"CFO","company":"Maple Ridge Construction","phone":"519-555-0123","email":"sarah@maple.ca","website":"maple.ca"}'
    result = parse_card_response(raw)
    assert result["name"] == "Sarah Chen"
    assert result["email"] == "sarah@maple.ca"
    assert result["company"] == "Maple Ridge Construction"


def test_parse_card_response_partial():
    from photo_handler import parse_card_response
    raw = '{"name":"John Smith","company":"ABC Corp","phone":"","email":"","website":""}'
    result = parse_card_response(raw)
    assert result["name"] == "John Smith"
    assert result["email"] == ""


def test_parse_card_response_with_markdown():
    from photo_handler import parse_card_response
    raw = '```json\n{"name":"Jane Doe","company":"XYZ","phone":"416-555-9999","email":"jane@xyz.com","website":""}\n```'
    result = parse_card_response(raw)
    assert result["name"] == "Jane Doe"


def test_parse_card_response_empty_returns_none():
    from photo_handler import parse_card_response
    assert parse_card_response("") is None
    assert parse_card_response("{}") is None
    assert parse_card_response('{"name":""}') is None


def test_format_confirmation_message():
    from photo_handler import format_confirmation_message
    card = {"name": "Sarah Chen", "title": "CFO", "company": "Maple Ridge Construction",
            "phone": "519-555-0123", "email": "sarah@maple.ca", "website": "maple.ca"}
    msg = format_confirmation_message(card)
    assert "Sarah Chen" in msg
    assert "CFO" in msg
    assert "519-555-0123" in msg
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python -m pytest tests/test_photo_handler.py -v 2>&1 | tail -15
```

Expected: `ModuleNotFoundError: No module named 'photo_handler'`

- [ ] **Step 3: Create `photo_handler.py`**

```python
"""
Business card photo handler for the Telegram bot.
Sends the image to GPT-4o Vision, extracts contact fields,
creates/updates the prospect via existing intake dedup logic.
"""

import base64
import json
import logging
import os
import tempfile

from openai import OpenAI

import db
from intake import _score_and_schedule

logger = logging.getLogger(__name__)
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

CARD_EXTRACTION_PROMPT = """You are extracting contact information from a business card image.

Return ONLY a JSON object with these exact keys:
{
  "name": "Full name on the card",
  "title": "Job title",
  "company": "Company name",
  "phone": "Phone number (first one if multiple)",
  "email": "Email address",
  "website": "Website URL"
}

Rules:
- Use empty string "" for any field not visible on the card
- Do not invent or guess any information
- Return ONLY the JSON, no other text
- If you cannot read the card clearly, return {"name": "", "title": "", "company": "", "phone": "", "email": "", "website": ""}
"""


def parse_card_response(raw: str) -> dict | None:
    """Parse GPT response into a card dict. Returns None if no usable name."""
    if not raw:
        return None
    try:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1]) if len(lines) > 2 else text[3:]
            if text.startswith("json"):
                text = text[4:].strip()
        data = json.loads(text)
        if not data.get("name", "").strip():
            return None
        return data
    except (json.JSONDecodeError, AttributeError):
        return None


def format_confirmation_message(card: dict) -> str:
    """Format extracted card data for Telegram confirmation message."""
    lines = [f"Card read — does this look right?\n"]
    if card.get("name"):
        lines.append(f"Name: {card['name']}")
    if card.get("title"):
        lines.append(f"Title: {card['title']}")
    if card.get("company"):
        lines.append(f"Company: {card['company']}")
    if card.get("phone"):
        lines.append(f"Phone: {card['phone']}")
    if card.get("email"):
        lines.append(f"Email: {card['email']}")
    if card.get("website"):
        lines.append(f"Website: {card['website']}")
    lines.append("\nReply 'yes' to save or 'no' to discard.")
    return "\n".join(lines)


async def extract_card_from_image(image_path: str) -> dict | None:
    """Send image to GPT-4o Vision and extract business card fields."""
    with open(image_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": CARD_EXTRACTION_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_data}",
                    "detail": "high"
                }},
            ],
        }],
    )
    raw = response.choices[0].message.content
    return parse_card_response(raw)


def save_card_to_pipeline(card: dict, source: str = "business_card") -> tuple[str, int]:
    """
    Dedup and save extracted card to prospects table.
    Returns (prospect_name, prospect_id).
    """
    name = card["name"].strip()
    email = card.get("email", "")
    phone = card.get("phone", "")

    existing = None
    if email:
        existing = db.get_prospect_by_email(email)
    if not existing and phone:
        existing = db.get_prospect_by_phone(phone)
    if not existing:
        existing = db.get_prospect_by_name(name)

    notes = f"[Card] {card.get('title', '')} at {card.get('company', '')}".strip(" at")
    if card.get("website"):
        notes += f" | {card['website']}"

    if existing:
        updates = {"notes": f"{existing.get('notes', '')} | {notes}".strip(" |")}
        for field in ("email", "phone"):
            if card.get(field) and not existing.get(field):
                updates[field] = card[field]
        db.update_prospect(existing["name"], updates)
        db.queue_enrichment(existing["id"])
        db.apply_tag(existing["id"], "source_card")
        return existing["name"], existing["id"]
    else:
        db.add_prospect({
            "name": name,
            "phone": phone,
            "email": email,
            "source": source,
            "priority": "Warm",
            "stage": "New Lead",
            "product": "",
            "notes": notes,
        })
        prospect = db.get_prospect_by_name(name)
        db.queue_enrichment(prospect["id"])
        db.apply_tag(prospect["id"], "source_card")
        db.apply_tag(prospect["id"], "new_lead")
        _score_and_schedule(name)
        return name, prospect["id"]


async def handle_photo_message(update, context):
    """Telegram handler for photo messages (business card capture)."""
    admin_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not admin_id or str(update.effective_chat.id) != str(admin_id):
        await update.message.reply_text("Not authorized.")
        return

    photos = update.message.photo
    if not photos:
        return

    await update.message.reply_text("Reading the card...")

    tmp_path = None
    try:
        # Get highest resolution photo
        photo = max(photos, key=lambda p: p.file_size)
        file = await photo.get_file()

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

        card = await extract_card_from_image(tmp_path)

        if not card:
            await update.message.reply_text(
                "Couldn't read the card clearly. Try a better-lit photo or add manually with /add."
            )
            return

        # Store pending card in bot context for confirmation
        context.user_data["pending_card"] = card
        await update.message.reply_text(format_confirmation_message(card))

    except Exception:
        logger.exception("Photo handler error")
        await update.message.reply_text("Error reading the card. Please try again.")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


async def handle_card_confirmation(update, context):
    """Handle 'yes'/'no' reply after card extraction confirmation."""
    text = (update.message.text or "").strip().lower()
    card = context.user_data.get("pending_card")

    if not card:
        return False  # Not in card confirmation flow

    if text in ("yes", "y", "yep", "yeah", "correct", "save"):
        name, prospect_id = save_card_to_pipeline(card)
        context.user_data.pop("pending_card", None)
        await update.message.reply_text(
            f"Saved {name}. Enrichment queued — I'll fill in their socials and score them shortly."
        )
        return True

    if text in ("no", "n", "nope", "discard", "cancel"):
        context.user_data.pop("pending_card", None)
        await update.message.reply_text("Discarded. No changes made.")
        return True

    return False  # Not a yes/no — let normal message handler take over
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_photo_handler.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Register photo handler in `bot.py`**

Add to `bot.py` imports (near top, after `from voice_handler import handle_voice_message`):

```python
from photo_handler import handle_photo_message, handle_card_confirmation
```

Find the section in `bot.py` where `MessageHandler` filters are registered (search for `filters.VOICE`). Add photo handler registration immediately after:

```python
    # After: app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
```

Also find the text message handler in `bot.py` (the handler that processes general text). At the top of that handler function, add:

```python
    # Check if user is in card confirmation flow
    if await handle_card_confirmation(update, context):
        return
```

- [ ] **Step 6: Smoke test**

```bash
python -c "from photo_handler import parse_card_response, format_confirmation_message; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add photo_handler.py bot.py tests/test_photo_handler.py
git commit -m "feat: add business card photo capture via GPT-4o Vision"
```

---

## Task 3: QR Landing Page

**Files:**
- Create: `templates/qr_landing.html`
- Modify: `dashboard.py` (add `/qr/<tenant_id>` and `/api/qr-submit` routes)
- Create: `tests/test_qr_landing.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_qr_landing.py`:

```python
"""Tests for QR landing page intake."""
import pytest
import json


@pytest.fixture
def client():
    import os
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")
    import dashboard
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


def test_qr_page_loads(client):
    resp = client.get("/qr/test-tenant")
    assert resp.status_code == 200
    assert b"form" in resp.data.lower()


def test_qr_submit_creates_prospect(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_prospect_by_email", lambda e: None)
    monkeypatch.setattr(db, "get_prospect_by_phone", lambda p: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: None)
    monkeypatch.setattr(db, "add_prospect", lambda d: None)
    monkeypatch.setattr(db, "apply_tag", lambda *a, **k: True)
    monkeypatch.setattr(db, "queue_enrichment", lambda *a: None)

    def fake_get_by_name(name):
        return {"id": 1, "name": name}
    monkeypatch.setattr(db, "get_prospect_by_name", fake_get_by_name)

    resp = client.post("/api/qr-submit", json={
        "tenant_id": "test-tenant",
        "first_name": "Sarah",
        "last_name": "Chen",
        "email": "sarah@test.com",
        "phone": "519-555-0123",
        "company": "Maple Ridge",
        "interests": ["life", "group_benefits"]
    })
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_qr_landing.py::test_qr_page_loads -v 2>&1 | tail -10
```

Expected: FAIL (route not found)

- [ ] **Step 3: Create `templates/qr_landing.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Connect with {{ advisor_name }}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8f7f4;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#fff;border-radius:20px;padding:32px 28px;max-width:420px;width:100%;box-shadow:0 4px 24px rgba(0,0,0,.08)}
h1{font-size:22px;font-weight:700;color:#171717;margin-bottom:6px}
p{font-size:14px;color:#66635f;margin-bottom:24px;line-height:1.5}
label{display:block;font-size:12px;font-weight:600;color:#374151;margin-bottom:5px;margin-top:16px}
input{width:100%;border:1.5px solid #e5e7eb;border-radius:10px;padding:12px 14px;font-size:15px;outline:none;transition:border-color .15s}
input:focus{border-color:#6366f1}
.interests{display:flex;flex-wrap:wrap;gap:8px;margin-top:6px}
.interest-btn{padding:8px 14px;border:1.5px solid #e5e7eb;border-radius:99px;font-size:13px;font-weight:500;background:#fff;color:#374151;cursor:pointer;transition:all .15s}
.interest-btn.selected{background:#eef0ff;border-color:#6366f1;color:#4f46e5}
button[type=submit]{width:100%;margin-top:24px;padding:14px;background:#6366f1;color:#fff;border:none;border-radius:12px;font-size:16px;font-weight:600;cursor:pointer;transition:background .15s}
button[type=submit]:hover{background:#4f46e5}
.success{text-align:center;padding:20px 0}
.success h2{font-size:20px;font-weight:700;color:#0f9f6e;margin-bottom:8px}
</style>
</head>
<body>
<div class="card">
  <div id="form-view">
    <h1>Nice to meet you!</h1>
    <p>{{ advisor_name }} will follow up with you shortly. Fill in your details below:</p>
    <form id="qr-form">
      <input type="hidden" name="tenant_id" value="{{ tenant_id }}">
      <label>First name *</label>
      <input type="text" name="first_name" required placeholder="Sarah">
      <label>Last name *</label>
      <input type="text" name="last_name" required placeholder="Chen">
      <label>Email</label>
      <input type="email" name="email" placeholder="sarah@company.com">
      <label>Phone</label>
      <input type="tel" name="phone" placeholder="519-555-0123">
      <label>Company</label>
      <input type="text" name="company" placeholder="Maple Ridge Construction">
      <label>What can we help you with?</label>
      <div class="interests" id="interests">
        <button type="button" class="interest-btn" data-val="life">Life Insurance</button>
        <button type="button" class="interest-btn" data-val="disability">Disability</button>
        <button type="button" class="interest-btn" data-val="group_benefits">Group Benefits</button>
        <button type="button" class="interest-btn" data-val="critical_illness">Critical Illness</button>
        <button type="button" class="interest-btn" data-val="home_auto">Home / Auto</button>
        <button type="button" class="interest-btn" data-val="investments">Investments</button>
        <button type="button" class="interest-btn" data-val="not_sure">Not sure yet</button>
      </div>
      <button type="submit">Submit</button>
    </form>
  </div>
  <div id="success-view" class="success" style="display:none">
    <h2>Thanks!</h2>
    <p>{{ advisor_name }} will be in touch soon.</p>
  </div>
</div>
<script>
document.querySelectorAll('.interest-btn').forEach(btn => {
  btn.addEventListener('click', () => btn.classList.toggle('selected'));
});
document.getElementById('qr-form').addEventListener('submit', async e => {
  e.preventDefault();
  const form = e.target;
  const interests = [...document.querySelectorAll('.interest-btn.selected')].map(b => b.dataset.val);
  const body = {
    tenant_id: form.tenant_id.value,
    first_name: form.first_name.value,
    last_name: form.last_name.value,
    email: form.email.value,
    phone: form.phone.value,
    company: form.company.value,
    interests
  };
  const resp = await fetch('/api/qr-submit', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  if (resp.ok) {
    document.getElementById('form-view').style.display = 'none';
    document.getElementById('success-view').style.display = 'block';
  }
});
</script>
</body>
</html>
```

- [ ] **Step 4: Add routes to `dashboard.py`**

Find the Flask route definitions in `dashboard.py`. Add these two routes:

```python
import qrcode
import io
import base64

@app.route("/qr/<tenant_id>")
def qr_landing(tenant_id):
    """Mobile-optimized landing page for QR code scans."""
    # Look up tenant to get advisor name
    tenant = db.get_tenant(tenant_id) if hasattr(db, 'get_tenant') else None
    advisor_name = tenant.get("name", "Your Advisor") if tenant else "Your Advisor"
    return render_template("qr_landing.html", tenant_id=tenant_id, advisor_name=advisor_name)


@app.route("/api/qr-submit", methods=["POST"])
def qr_submit():
    """Handle QR landing page form submission."""
    data = request.get_json(force=True) or {}
    first = (data.get("first_name") or "").strip()
    last = (data.get("last_name") or "").strip()
    name = f"{first} {last}".strip()
    if not name:
        return jsonify({"status": "error", "message": "Name required"}), 400

    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    company = (data.get("company") or "").strip()
    interests = data.get("interests") or []

    # Dedup
    existing = None
    if email:
        existing = db.get_prospect_by_email(email)
    if not existing and phone:
        existing = db.get_prospect_by_phone(phone)
    if not existing:
        existing = db.get_prospect_by_name(name)

    interest_str = ", ".join(interests) if interests else "not specified"
    notes = f"[QR] Interested in: {interest_str}"
    if company:
        notes = f"[QR] {company} | {interest_str}"

    if existing:
        db.update_prospect(existing["name"], {
            "notes": f"{existing.get('notes', '')} | {notes}".strip(" |")
        })
        prospect_id = existing["id"]
    else:
        db.add_prospect({
            "name": name, "phone": phone, "email": email,
            "source": "qr_code", "priority": "Warm",
            "stage": "New Lead", "product": interests[0] if interests else "",
            "notes": notes,
        })
        prospect = db.get_prospect_by_name(name)
        prospect_id = prospect["id"]
        from intake import _score_and_schedule
        _score_and_schedule(name)

    db.apply_tag(prospect_id, "source_qr")
    db.apply_tag(prospect_id, "new_lead")
    for interest in interests:
        db.apply_tag(prospect_id, f"interest_{interest}")
    db.queue_enrichment(prospect_id)

    return jsonify({"status": "ok"})
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_qr_landing.py -v
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add templates/qr_landing.html dashboard.py tests/test_qr_landing.py
git commit -m "feat: add QR landing page with per-tenant URL and multi-select interests"
```

---

## Task 4: Social Intake Webhook (`/api/social-intake`)

This is the single endpoint all n8n workflows POST to. n8n normalizes the payload before sending — SteadyBook never calls Meta, LinkedIn, or Google APIs.

**Files:**
- Create: `social_intake.py`
- Modify: `dashboard.py` (register blueprint)
- Create: `tests/test_social_intake.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_social_intake.py`:

```python
"""Tests for the unified social intake webhook."""
import pytest
import json
import hmac
import hashlib
import os


@pytest.fixture(autouse=True)
def set_secret(monkeypatch):
    monkeypatch.setenv("SOCIAL_INTAKE_SECRET", "test-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def make_sig(body: bytes, secret: str = "test-secret") -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def client():
    import os
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")
    import dashboard
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


def _post(client, payload: dict):
    body = json.dumps(payload).encode()
    sig = make_sig(body)
    return client.post(
        "/api/social-intake",
        data=body,
        content_type="application/json",
        headers={"X-Intake-Signature": sig}
    )


def test_instagram_dm_lead(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_prospect_by_email", lambda e: None)
    monkeypatch.setattr(db, "get_prospect_by_phone", lambda p: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: None)
    monkeypatch.setattr(db, "add_prospect", lambda d: None)
    monkeypatch.setattr(db, "apply_tag", lambda *a, **k: True)
    monkeypatch.setattr(db, "queue_enrichment", lambda *a: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: {"id": 1, "name": n, "notes": ""})

    resp = _post(client, {
        "source": "instagram_dm",
        "name": "Sarah Chen",
        "message": "Hey I'm interested in group benefits for my team",
        "instagram_handle": "@sarahchen",
        "email": "",
        "phone": ""
    })
    assert resp.status_code == 200


def test_linkedin_lead_form(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_prospect_by_email", lambda e: None)
    monkeypatch.setattr(db, "get_prospect_by_phone", lambda p: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: None)
    monkeypatch.setattr(db, "add_prospect", lambda d: None)
    monkeypatch.setattr(db, "apply_tag", lambda *a, **k: True)
    monkeypatch.setattr(db, "queue_enrichment", lambda *a: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: {"id": 1, "name": n, "notes": ""})

    resp = _post(client, {
        "source": "linkedin_ad",
        "name": "John Park",
        "email": "john@parkenterprises.ca",
        "phone": "",
        "title": "President",
        "company": "Park Enterprises",
        "campaign": "group-benefits-q1"
    })
    assert resp.status_code == 200


def test_invalid_signature_rejected(client):
    body = json.dumps({"source": "instagram_dm", "name": "Hacker"}).encode()
    resp = client.post(
        "/api/social-intake",
        data=body,
        content_type="application/json",
        headers={"X-Intake-Signature": "bad-signature"}
    )
    assert resp.status_code == 401


def test_calendar_booking_creates_meeting(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_prospect_by_email", lambda e: None)
    monkeypatch.setattr(db, "get_prospect_by_phone", lambda p: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: None)
    monkeypatch.setattr(db, "add_prospect", lambda d: None)
    monkeypatch.setattr(db, "apply_tag", lambda *a, **k: True)
    monkeypatch.setattr(db, "queue_enrichment", lambda *a: None)
    monkeypatch.setattr(db, "get_prospect_by_name", lambda n: {"id": 1, "name": n, "notes": ""})
    monkeypatch.setattr(db, "add_meeting", lambda d: None)

    resp = _post(client, {
        "source": "calendly",
        "name": "Amy Liu",
        "email": "amy@liufinancial.ca",
        "phone": "",
        "meeting_datetime": "2026-04-01T14:00:00",
        "meeting_type": "Discovery Call"
    })
    assert resp.status_code == 200
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_social_intake.py -v 2>&1 | tail -10
```

Expected: FAIL

- [ ] **Step 3: Create `social_intake.py`**

```python
"""
Unified social intake webhook for n8n-normalized payloads.
All social/email/calendar sources (Instagram, LinkedIn, WhatsApp,
Gmail, Outlook, Calendly) post here via n8n workflows.

Set SOCIAL_INTAKE_SECRET in .env — n8n sends it as X-Intake-Signature.
"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime

from flask import Blueprint, request, jsonify

import db
from intake import _score_and_schedule

logger = logging.getLogger(__name__)

social_intake_bp = Blueprint("social_intake", __name__)

SOCIAL_INTAKE_SECRET = os.environ.get("SOCIAL_INTAKE_SECRET", "")

VALID_SOURCES = {
    "instagram_dm", "instagram_ad", "linkedin_ad", "whatsapp",
    "gmail", "outlook", "calendly", "cal_com", "google_calendar",
    "outlook_calendar"
}


def _verify_signature(body: bytes, signature: str) -> bool:
    if not SOCIAL_INTAKE_SECRET:
        logger.warning("SOCIAL_INTAKE_SECRET not set — accepting all requests (dev mode)")
        return True
    expected = hmac.new(SOCIAL_INTAKE_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _dedup_or_create(name: str, email: str, phone: str, source: str,
                     notes: str, product: str = "") -> dict:
    """Find existing prospect or create new one. Returns prospect dict."""
    existing = None
    if email:
        existing = db.get_prospect_by_email(email)
    if not existing and phone:
        existing = db.get_prospect_by_phone(phone)
    if not existing:
        existing = db.get_prospect_by_name(name)

    if existing:
        updated_notes = f"{existing.get('notes', '')} | {notes}".strip(" |")
        updates = {"notes": updated_notes}
        if email and not existing.get("email"):
            updates["email"] = email
        if phone and not existing.get("phone"):
            updates["phone"] = phone
        db.update_prospect(existing["name"], updates)
        return existing
    else:
        db.add_prospect({
            "name": name, "phone": phone, "email": email,
            "source": source, "priority": "Warm",
            "stage": "New Lead", "product": product, "notes": notes,
        })
        prospect = db.get_prospect_by_name(name)
        _score_and_schedule(name)
        return prospect


def _handle_social_message(data: dict) -> None:
    """Handle Instagram DM, WhatsApp, or general social message."""
    source = data["source"]
    name = (data.get("name") or "Unknown").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    message = (data.get("message") or "").strip()
    handle = data.get("instagram_handle") or data.get("whatsapp_number") or ""

    notes = f"[{source}] {message[:200]}"
    if handle:
        notes += f" | Handle: {handle}"

    prospect = _dedup_or_create(name, email, phone, source, notes)
    pid = prospect["id"]

    db.apply_tag(pid, f"source_{source}")
    db.apply_tag(pid, "new_lead")
    if handle and "instagram" in source:
        db.apply_tag(pid, "has_instagram")
    db.queue_enrichment(pid)

    db.add_interaction({
        "prospect": prospect["name"],
        "source": source,
        "raw_text": message,
        "summary": f"Inbound {source} message",
        "action_items": "",
    })


def _handle_ad_lead(data: dict) -> None:
    """Handle Instagram Lead Ad or LinkedIn Lead Gen Form submission."""
    source = data["source"]
    name = (data.get("name") or "").strip()
    if not name:
        return

    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    title = (data.get("title") or "").strip()
    company = (data.get("company") or "").strip()
    campaign = (data.get("campaign") or "").strip()
    answers = data.get("answers") or {}

    notes_parts = [f"[{source}]"]
    if campaign:
        notes_parts.append(f"Campaign: {campaign}")
    if title and company:
        notes_parts.append(f"{title} at {company}")
    elif company:
        notes_parts.append(f"Company: {company}")
    if answers:
        notes_parts.append(f"Answers: {json.dumps(answers)[:200]}")

    notes = " | ".join(notes_parts)
    prospect = _dedup_or_create(name, email, phone, source, notes)
    pid = prospect["id"]

    db.apply_tag(pid, f"source_{source}")
    db.apply_tag(pid, "new_lead")
    if campaign:
        db.apply_tag(pid, f"campaign_{campaign[:30].replace(' ', '_').lower()}")
    db.queue_enrichment(pid)


def _handle_email(data: dict) -> None:
    """Handle inbound email reply or Gmail/Outlook sync event."""
    source = data["source"]
    name = (data.get("name") or data.get("from_name") or "").strip()
    email = (data.get("email") or data.get("from_email") or "").strip()
    subject = (data.get("subject") or "").strip()
    body_text = (data.get("body") or data.get("snippet") or "").strip()
    direction = data.get("direction", "inbound")  # "inbound" or "outbound"

    if not email and not name:
        return

    prospect = None
    if email:
        prospect = db.get_prospect_by_email(email)
    if not prospect and name:
        prospect = db.get_prospect_by_name(name)

    summary = f"[Email {direction}] {subject}: {body_text[:300]}"

    if prospect:
        db.add_activity({
            "prospect": prospect["name"],
            "action": f"Email {direction}",
            "outcome": subject,
            "next_step": "",
        })
        db.add_interaction({
            "prospect": prospect["name"],
            "source": source,
            "raw_text": body_text,
            "summary": summary,
            "action_items": "",
        })
    else:
        if name and direction == "inbound":
            notes = f"[Email inbound] {subject}"
            prospect = _dedup_or_create(name, email, "", source, notes)
            db.apply_tag(prospect["id"], "source_email")
            db.apply_tag(prospect["id"], "new_lead")
            db.queue_enrichment(prospect["id"])


def _handle_calendar(data: dict) -> None:
    """Handle Calendly/Cal.com booking or calendar sync event."""
    source = data["source"]
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    phone = (data.get("phone") or "").strip()
    meeting_datetime = data.get("meeting_datetime") or ""
    meeting_type = data.get("meeting_type") or "Consultation"

    if not name:
        return

    notes = f"[{source}] Booked: {meeting_type}"
    prospect = _dedup_or_create(name, email, phone, source, notes)
    pid = prospect["id"]

    db.apply_tag(pid, "meeting_booked")
    db.apply_tag(pid, "new_lead")
    db.queue_enrichment(pid)

    # Parse meeting datetime
    meeting_date = ""
    meeting_time = ""
    if meeting_datetime:
        try:
            dt = datetime.fromisoformat(meeting_datetime.replace("Z", "+00:00"))
            meeting_date = dt.strftime("%Y-%m-%d")
            meeting_time = dt.strftime("%H:%M")
        except ValueError:
            pass

    db.add_meeting({
        "date": meeting_date,
        "time": meeting_time,
        "prospect": prospect["name"],
        "type": meeting_type,
        "prep_notes": f"Booked via {source}",
    })


@social_intake_bp.route("/api/social-intake", methods=["POST"])
def social_intake():
    """Unified webhook for all n8n social/email/calendar payloads."""
    raw_body = request.get_data()
    signature = request.headers.get("X-Intake-Signature", "")

    if not _verify_signature(raw_body, signature):
        return jsonify({"status": "unauthorized"}), 401

    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return jsonify({"status": "invalid json"}), 400

    source = (data.get("source") or "").strip()
    if source not in VALID_SOURCES:
        logger.warning(f"Unknown social intake source: {source}")
        return jsonify({"status": "unknown source"}), 400

    try:
        if source in ("instagram_dm", "whatsapp"):
            _handle_social_message(data)
        elif source in ("instagram_ad", "linkedin_ad"):
            _handle_ad_lead(data)
        elif source in ("gmail", "outlook"):
            _handle_email(data)
        elif source in ("calendly", "cal_com", "google_calendar", "outlook_calendar"):
            _handle_calendar(data)
    except Exception:
        logger.exception(f"Error processing social intake from {source}")
        return jsonify({"status": "error"}), 500

    return jsonify({"status": "ok"})
```

- [ ] **Step 4: Register blueprint in `dashboard.py`**

Near the top of `dashboard.py` where other imports are, add:

```python
from social_intake import social_intake_bp
```

After `app = Flask(__name__)` (or wherever other blueprints are registered), add:

```python
app.register_blueprint(social_intake_bp)
```

- [ ] **Step 5: Add `SOCIAL_INTAKE_SECRET` to `.env`**

```bash
echo "SOCIAL_INTAKE_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" >> .env
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/test_social_intake.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add social_intake.py dashboard.py tests/test_social_intake.py .env
git commit -m "feat: unified /api/social-intake webhook for n8n integrations (Instagram, LinkedIn, WhatsApp, Gmail, Calendar)"
```

---

## Task 5: n8n Setup Guide

**Files:**
- Create: `docs/n8n-setup.md`

- [ ] **Step 1: Write the n8n setup doc**

Create `docs/n8n-setup.md`:

```markdown
# n8n Integration Setup

SteadyBook receives normalized webhooks from n8n. n8n handles all platform OAuth so SteadyBook never touches Meta, LinkedIn, or Google APIs directly.

## Required Environment Variable

Set in `.env`:
```
SOCIAL_INTAKE_SECRET=<your-secret>
```

n8n must send this as the `X-Intake-Signature` header (HMAC-SHA256 of the request body).

## Payload Schema

Every n8n workflow POSTs to: `https://your-steadybook-url/api/social-intake`

### Instagram DM
```json
{
  "source": "instagram_dm",
  "name": "Sarah Chen",
  "message": "Hi I'm interested in group benefits",
  "instagram_handle": "@sarahchen",
  "email": "",
  "phone": ""
}
```

### Instagram / Facebook Lead Ad
```json
{
  "source": "instagram_ad",
  "name": "John Park",
  "email": "john@parkenterprises.ca",
  "phone": "416-555-0123",
  "campaign": "group-benefits-q1",
  "answers": {"employees": "35", "current_provider": "None"}
}
```

### LinkedIn Lead Gen Form
```json
{
  "source": "linkedin_ad",
  "name": "Amy Liu",
  "email": "amy@liufinancial.ca",
  "title": "CFO",
  "company": "Liu Financial Group",
  "campaign": "wealth-management-2026"
}
```

### WhatsApp Message
```json
{
  "source": "whatsapp",
  "name": "David Kim",
  "phone": "+15195550199",
  "message": "Hey I saw your post about life insurance",
  "email": ""
}
```

### Gmail / Outlook Email
```json
{
  "source": "gmail",
  "from_name": "Sarah Chen",
  "from_email": "sarah@maple.ca",
  "subject": "Re: Group Benefits Quote",
  "snippet": "Thanks for sending that over, I have a few questions...",
  "direction": "inbound"
}
```

### Calendly / Cal.com Booking
```json
{
  "source": "calendly",
  "name": "James Morrison",
  "email": "james@morrisoncorp.ca",
  "phone": "",
  "meeting_datetime": "2026-04-15T10:00:00",
  "meeting_type": "Discovery Call"
}
```

## n8n Workflow Setup

1. Install n8n (self-hosted: `npx n8n`)
2. For each platform, create a trigger node (Instagram, LinkedIn, Gmail, etc.)
3. Add an HTTP Request node pointing to your SteadyBook URL
4. Set header: `X-Intake-Signature` = HMAC-SHA256 of body using your secret
5. Map platform fields to the schema above
```

- [ ] **Step 2: Commit**

```bash
git add docs/n8n-setup.md
git commit -m "docs: add n8n integration setup guide with payload schemas"
```

---

## Final Verification

- [ ] Run full test suite: `python -m pytest tests/ -q 2>&1 | tail -5`
- [ ] Smoke test photo handler: `python -c "from photo_handler import parse_card_response; print(parse_card_response('{\"name\":\"Test\"}'))"`
- [ ] Smoke test social intake: `python -c "from social_intake import social_intake_bp; print('OK')"`
- [ ] Confirm DB tables exist: `python -c "import db; db.init_db(); print('OK')"`
