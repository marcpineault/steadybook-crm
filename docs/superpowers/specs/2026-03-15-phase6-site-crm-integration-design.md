# Phase 6: Site-CRM Integration Design

## Goal

Connect calmmoney.ca (pineault-wealth repo) to the Calm Money CRM bot (calm-money-bot repo) so that website leads automatically enter the CRM pipeline with scoring, nurture sequences, and follow-up drafts — and approved emails to website leads auto-send via Resend instead of requiring Outlook copy-paste.

## Context

- Both services run on Railway in the same project, communicating over Railway internal networking (zero latency, no public exposure).
- calmmoney.ca is a Next.js site with 4 lead capture points (contact form, retirement quiz, tool capture, newsletter signup), PostgreSQL on Railway (used for newsletter subscribers only), Resend for email delivery and form notifications, and an admin dashboard. Note: contact, quiz, and tool capture forms do NOT save to PostgreSQL — they send Resend notification emails to Marc. Only the newsletter subscribe form writes to PostgreSQL.
- calm-money-bot is a Python Telegram bot with SQLite, OpenAI GPT-4, APScheduler, approval queue, compliance checking, nurture sequences, campaigns, and analytics.
- Co-operators branding constraint: website leads (calmmoney.ca source) can receive emails from `marc@info.calmmoney.ca` via Resend. Co-operators clients (voice notes, Outlook bookings, insurance book) must go through Outlook with Co-operators branding (copy-paste stays).

## Architecture

Two independent services, connected by HTTP webhooks over Railway private network. No shared database. The site's PostgreSQL stores newsletter subscribers only; the contact/quiz/tool forms are notification-only (Resend email to Marc). The CRM webhook becomes the primary persistent record for these leads, creating prospect records in SQLite.

```
calmmoney.ca (Next.js)                    calm-money-bot (Python)
┌─────────────────────┐                   ┌─────────────────────┐
│ Contact form        │                   │                     │
│ Quiz                │── POST ──────────▶│ /api/intake         │
│ Tool capture        │  (Railway         │  type=website_lead  │
│                     │   internal)       │                     │
│ Resend webhooks     │── POST ──────────▶│ /api/intake         │
│ (open/click/bounce) │                   │  type=email_event   │
│                     │                   │                     │
│                     │                   │ resend_sender.py    │
│                     │                   │  (sends approved    │
│ Resend API ◀────────│───────────────────│   drafts for        │
│ (delivery)          │                   │   website leads)    │
└─────────────────────┘                   └─────────────────────┘
```

## Section 1: Site → CRM Webhook

### What Happens

After each site form handler sends its Resend notification email to Marc (existing behavior unchanged), it fires a POST to the CRM's `/api/intake` endpoint over Railway internal networking. The contact, quiz, and tool forms do not save to PostgreSQL — the CRM webhook is the primary persistent record for these leads.

### Form Type Mapping

| Site Form | CRM Intake Type | Data Sent | Prospect Priority |
|-----------|----------------|-----------|-------------------|
| Contact form (`/api/contact`) | `website_contact` | name, email, phone, service, message | Hot |
| Retirement quiz (`/api/quiz`) | `website_quiz` | email, score, answers (`[{questionId, optionLabel, points}]`), tier (computed server-side from score via `getTier()`) | Warm |
| Tool capture (`/api/lead`) | `website_tool` | email, toolName (camelCase in site, mapped to `tool_name` by CRM) | Cool |

Newsletter signup is excluded — too low-intent and high-volume. Can be added later.

### Deduplication Strategy

Before creating a new prospect, the CRM checks for an existing prospect with the same email address:

- **Match found**: Update the existing prospect — merge any new data (e.g., phone from contact form onto a quiz-only record), bump priority if new source is higher priority (Hot > Warm > Cool), add a timeline note ("Also submitted contact form on 2026-03-15"). Do NOT overwrite `send_channel` if already set.
- **No match**: Create a new prospect.

**Handling nameless leads**: Quiz and tool capture forms only collect email (no name). When creating a prospect without a name, use the email local part as a placeholder name (e.g., `sarah@example.com` → `"sarah"`). If a contact form submission later matches the same email, the name gets upgraded to the real name.

### CRM Processing for Website Leads

- Create prospect (or update existing — see deduplication above) with `source = "website"`, `send_channel = "resend"`
- `website_contact`: product mapped from service dropdown, priority = Hot, follow-up draft auto-generated, Telegram alert to Marc
- `website_quiz`: score stored in client memory ("Scored 72/100, weak areas: tax planning, insurance"), nurture sequence auto-started, Telegram alert to Marc
- `website_tool`: minimal — just creates the prospect record, no alert (too noisy)

### Failure Handling

Site fire-and-forgets the webhook call. If the CRM is down, the lead data is lost (contact/quiz/tool forms do not persist to PostgreSQL — they only send a Resend notification email to Marc). Marc still receives the Resend email and can manually add the lead. This is acceptable for now; a retry queue or site-side PostgreSQL persistence for these forms can be added later if volume warrants it.

### Site-Side Implementation

- New env var: `CRM_INTERNAL_URL` (e.g., `http://calm-money-bot.railway.internal:8080`)
- New helper: `src/lib/crm.ts` — single async function that POSTs to the CRM, catches errors silently
- Three form handlers modified: `/api/contact/route.ts`, `/api/quiz/route.ts`, `/api/lead/route.ts` — each adds a `crmNotify()` call after existing Resend notification send

## Section 2: Lead Source Routing (Resend vs Outlook)

### The Rule

Each prospect has a `send_channel` field that determines how approved messages are delivered.

| Prospect Source | `send_channel` | Delivery Method |
|----------------|----------------|-----------------|
| Website (any calmmoney.ca form) | `resend` | Auto-send via Resend API on approval |
| Voice note, Outlook booking, calendar event, email lead, manual | `outlook` | Copy-paste text shown in Telegram (current behavior) |

### How send_channel Gets Set

- Website leads: automatically set to `resend` by the website_lead intake handler
- All other sources: default to `outlook`
- Marc can override naturally via voice notes: "she found me on calmmoney" → bot detects and tags `resend`
- Manual override: `/lead Sarah Chen channel resend` or `/lead Sarah Chen channel outlook`

### Approval Flow Changes

When Marc taps **Approve** on a draft in Telegram:

1. Look up the prospect's `send_channel`
2. If `resend`:
   - Send email via Resend API (`marc@info.calmmoney.ca` sender)
   - Mark draft as sent
   - Confirm in Telegram: "Sent via Resend to sarah@example.com"
   - Record outcome for tracking
3. If `outlook`:
   - Show copy-paste text (current behavior, unchanged)
   - Record outcome for tracking

The Telegram draft notification shows which channel will be used: `"DRAFT — send via Resend"` or `"DRAFT — copy to Outlook"`.

### Resend Configuration on CRM Side

- New env var: `RESEND_API_KEY` (same key as the site uses)
- Sender: `marc@info.calmmoney.ca` (same as site newsletters)
- Reply-to: `mpineault1@gmail.com`
- Emails sent as plain text (matches Marc's personal style, no heavy HTML templates)
- New module: `resend_sender.py` — thin wrapper around Resend API

## Section 3: Resend Engagement → CRM Outcomes

### What Happens

When a recipient opens or clicks a Resend-delivered email, the event flows from Resend → site webhook → CRM outcome tracking.

### Flow

```
Resend delivers email → recipient opens/clicks
  → Resend fires webhook to site (/api/webhooks/resend — already exists)
  → Site forwards relevant events to CRM → /api/intake (type: email_event)
  → CRM matches the event to an outcome record and updates it
```

### Events Forwarded to CRM

| Resend Event | CRM Action |
|-------------|------------|
| `email.opened` | Update outcome: `response_received = true` |
| `email.clicked` | Update outcome: `response_type = "clicked"` |
| `email.bounced` | Mark prospect email as bounced, pause nurture sequence |
| `email.complained` | Unsubscribe prospect, pause all outreach |

Events NOT forwarded: `email.delivered`, `email.sent` — no actionable signal.

**Important**: The site forwards ALL matching Resend events to the CRM, not just events for newsletter subscribers. The site's existing Resend webhook handler processes bounces/complaints for its own subscriber table, then additionally forwards the above 4 event types to the CRM for any email (subscriber or CRM-sent). The CRM ignores events it can't match to a known outcome.

### Implementation

- Site: add CRM forwarding call in existing `/api/webhooks/resend/route.ts` handler. The CRM forwarding must happen unconditionally for the 4 event types above (not inside the subscriber-specific switch block), since CRM-sent emails won't have matching subscriber records. Note: the handler already calls `request.text()` for svix verification, so the event data must be parsed with `JSON.parse(body)` from the verified body string (not `request.json()`). The CRM call uses the same `X-Webhook-Secret` auth as the lead webhook.
- CRM: new intake type `email_event` in `webhook_intake.py`, matches events to outcomes by `resend_email_id` (primary) or email address + recent sent_at timestamp (fallback)
- When `resend_sender.py` sends an email via Resend, it stores the returned `resend_email_id` on the outcome record. Resend engagement events include this ID, enabling exact matching. If the ID is missing (edge case), fall back to joining outcomes→prospects to match by prospect email + sent_at within a 48-hour window (the outcomes table stores prospect name in `target`, so the join through prospects is needed to get email).

## Section 4: Database Changes

### calm-money-bot (SQLite)

**Schema migration** — add `send_channel` column to `prospects` table and `resend_email_id` to `outcomes` table:

```sql
ALTER TABLE prospects ADD COLUMN send_channel TEXT DEFAULT 'outlook';
ALTER TABLE outcomes ADD COLUMN resend_email_id TEXT;
```

`send_channel` values: `'resend'` or `'outlook'`. Default `'outlook'` so all existing prospects are unaffected.

`resend_email_id` stores the Resend message ID returned when an email is sent, enabling exact matching of engagement events back to outcomes.

**Code changes in `db.py`**:
- `add_prospect()`: add `send_channel` to the hardcoded INSERT SQL column list and VALUES placeholders (this function uses explicit column names, not a dynamic allowlist — the SQL statement itself must be modified)
- `update_prospect()`: add `send_channel` to the allowed update fields whitelist
- New function: `get_prospect_by_email(email)` — returns an existing prospect by email address, used for deduplication in the website lead intake flow
- Migration in `init_db()`: add both ALTER TABLE statements with `IF NOT EXISTS`-style error handling

**Code changes in `analytics.py`**:
- `record_outcome()`: add `resend_email_id` as an optional parameter to the INSERT column list (this function lives in `analytics.py`, not `db.py`)

### pineault-wealth (PostgreSQL)

No schema changes. The site continues using its existing tables unchanged.

## Section 5: Changes Summary

### pineault-wealth (site) — 5 files touched

1. **New: `src/lib/crm.ts`** — CRM webhook helper (fire-and-forget POST)
2. **Modify: `src/app/api/contact/route.ts`** — add `crmNotify()` after Resend notification send
3. **Modify: `src/app/api/quiz/route.ts`** — add `crmNotify()` after Resend notification send
4. **Modify: `src/app/api/lead/route.ts`** — add `crmNotify()` after Resend notification send
5. **Modify: `src/app/api/webhooks/resend/route.ts`** — forward open/click/bounce to CRM

### calm-money-bot (CRM) — 6 files touched

1. **New: `resend_sender.py`** — Resend API wrapper for sending approved drafts
2. **Modify: `webhook_intake.py`** — add routing for new intake types (`website_contact`, `website_quiz`, `website_tool`, `email_event`) in the existing type-dispatch switch. This file owns the `/api/intake` HTTP route and dispatches to business logic functions.
3. **Modify: `intake.py`** — new business logic functions: `process_website_contact()`, `process_website_quiz()`, `process_website_tool()`, `process_email_event()` with deduplication, nameless lead handling, and priority merging
4. **Modify: `db.py`** — add `send_channel` to prospects schema and `add_prospect()` SQL; add `get_prospect_by_email()` function; add `send_channel` to `update_prospect()` allowlist
5. **Modify: `analytics.py`** — add `resend_email_id` column to outcomes table; update `record_outcome()` to accept and store `resend_email_id`
6. **Modify: `bot.py`** — approval flow: auto-send via Resend when `send_channel = 'resend'`

### What Does NOT Change

- All existing site behavior (Resend notification emails, admin dashboard, newsletter subscriber management, drip sequence)
- All existing CRM behavior for non-website leads
- Outlook copy-paste flow for Co-operators clients
- Compliance checking (runs on every message regardless of channel)
- Approval requirement (Marc must still tap Approve — Resend just delivers automatically after)
- Nurture, campaign, and content generation logic

## Testing Strategy

### CRM-Side Tests (Python/pytest)

- `test_website_intake.py`: website_contact, website_quiz, website_tool intake processing, prospect creation with correct source/send_channel/priority
- `test_resend_sender.py`: Resend API call with correct params, error handling on API failure
- `test_send_channel_routing.py`: approval flow routes to Resend for website leads, routes to copy-paste for Outlook leads
- `test_email_event_intake.py`: open/click/bounce events update outcomes correctly

### Site-Side Tests (if applicable)

- Verify `crmNotify()` is called after Resend notification send
- Verify CRM failure doesn't break form submission
- Verify Resend webhook forwards events to CRM

## Environment Variables

### New for calm-money-bot

- `RESEND_API_KEY` — Resend API key for sending emails
- `RESEND_FROM_EMAIL` — sender address (default: `marc@info.calmmoney.ca`)
- `RESEND_REPLY_TO` — reply-to address (default: `mpineault1@gmail.com`)

### New for pineault-wealth

- `CRM_INTERNAL_URL` — Railway internal URL of calm-money-bot (e.g., `http://calm-money-bot.railway.internal:8080`)
- `CRM_WEBHOOK_SECRET` — shared secret for authenticating CRM webhooks (must match the CRM's `INTAKE_WEBHOOK_SECRET` value)

## Success Criteria

1. A contact form submission on calmmoney.ca creates a Hot prospect in the CRM within seconds, with a follow-up draft ready for Marc to approve
2. A quiz completion creates a Warm prospect with score in memory and nurture sequence started
3. Approving a website-lead draft sends the email via Resend automatically (no copy-paste)
4. Email opens/clicks flow back to CRM outcome tracking
5. Existing Co-operators client workflows are completely unaffected
6. Site continues to work normally even if CRM is down
