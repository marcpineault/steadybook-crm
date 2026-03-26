# Onboarding Wizard — Design Spec
Date: 2026-03-26

## Goal
Let a non-technical client configure the SteadyBook CRM themselves — no developer needed.
They open `/setup`, follow numbered steps, paste keys, hit "Test", and end up with a
ready-to-paste env var block for Railway.

## Scope
- Wizard UI at `/setup` (GET)
- Live-test endpoints at `/api/setup/test/<service>` (POST)
- Key generator at `/api/setup/generate-key` (GET)
- Wizard auto-locks when `ONBOARDING_COMPLETE=true` env var is set

Out of scope: writing env vars to Railway automatically, multi-tenant onboarding.

## Route: `/setup`
- No auth required
- Returns 404 if `ONBOARDING_COMPLETE=true`
- Renders `templates/onboarding.html` (standalone — does NOT extend base.html)

## Steps

| # | Label | Required | What the user does |
|---|-------|----------|-------------------|
| 1 | Dashboard API Key | Yes | Click "Generate" — copies a secure token |
| 2 | Telegram Bot Token | Yes | Follow @BotFather instructions (linked), paste token, click Test |
| 3 | Telegram Chat ID | Yes | Instructions to message the bot + use @userinfobot, paste ID, click Test (sends "hello") |
| 4 | OpenAI API Key | Yes | Link to platform.openai.com, paste key, click Test |
| 5 | Resend (Email) | Yes | Link to resend.com, paste API key + from email, click Test |
| 6 | Twilio (SMS) | Optional | Link to twilio.com, paste SID + token + number, click Test |
| 7 | n8n Webhook Secret | Optional | Explain: any random string, used to verify n8n payloads, click Generate |
| 8 | Done | — | Shows all collected values as a copyable env block for Railway |

## API Endpoints

### GET `/api/setup/generate-key`
Returns `{"key": "<secrets.token_urlsafe(32)>"}`. No auth.

### POST `/api/setup/test/<service>`
Body: JSON with the relevant credentials for that service.
Returns `{"ok": true}` or `{"ok": false, "error": "<message>"}`.

Services:
- `telegram-token` — calls `https://api.telegram.org/bot{token}/getMe`
- `telegram-chat` — calls `sendMessage` with text "SteadyBook setup test ✓"
- `openai` — lists models via OpenAI client
- `resend` — GET `https://api.resend.com/domains` with the API key
- `twilio` — GET Twilio account status via `https://api.twilio.com/2010-04-01/Accounts/{SID}.json`

No credentials are stored server-side. All tests use the values POSTed in the request body.

## UI Design

### Layout
- Standalone page, full-screen white, centered card (max-width 640px)
- Inter font (already loaded via CDN in base.html — replicate the link tag)
- Header: SteadyBook logo/name + "Setup Wizard"
- Step progress bar: numbered circles (1–8), filled green when complete

### Per-step card
```
[ Step N of 8 ]
Title
Short plain-English description (1–2 sentences max, no jargon)
[ Optional: "How to get this" link/accordion with numbered instructions ]
[ Input field(s) ]
[ Test button ]  →  ✓ Connected  /  ✗ Error message inline
[ Next → ]  (enabled only after test passes, or if step is optional)
```

### Step 8 (Done)
- Green checkmark header
- Textarea with all env vars, pre-filled with collected values
- "Copy All" button
- Instructions: "Paste these into your Railway service → Variables tab"
- "Mark setup complete" button — POSTs to `/api/setup/complete` which sets a flag in DB

### Tone
Plain English. No acronyms without explanation. Every step has a one-line "Why you need this."
Example: "This lets the CRM send you messages on Telegram when new leads come in."

## Security
- Test endpoints accept credentials in POST body only (never query string)
- Wizard is disabled (404) once `ONBOARDING_COMPLETE=true`
- No credentials are logged or stored server-side during testing
- `DASHBOARD_API_KEY` generation uses `secrets.token_urlsafe(32)`

## Production Fixes (same PR)
1. Rate limiter: add `storage_uri=os.environ.get("REDIS_URL", "memory://")` to Limiter init
2. `sms_conversations.py:165`: replace `datetime.utcnow()` with `datetime.now(datetime.UTC)`
3. `Dockerfile`: switch from `flask run` to `gunicorn`; add `gunicorn` to `requirements.txt`

## Files Changed
- `dashboard.py` — add `/setup`, `/api/setup/test/<service>`, `/api/setup/generate-key`, `/api/setup/complete` routes + rate limiter fix
- `sms_conversations.py` — utcnow fix
- `Dockerfile` — gunicorn
- `requirements.txt` — add gunicorn
- `templates/onboarding.html` — new wizard template
