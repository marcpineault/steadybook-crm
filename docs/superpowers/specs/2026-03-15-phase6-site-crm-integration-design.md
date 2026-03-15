# Phase 6: Site-CRM Integration Design

## Goal

Connect calmmoney.ca (pineault-wealth repo) to the Calm Money CRM bot (calm-money-bot repo) so that website leads automatically enter the CRM pipeline with scoring, nurture sequences, and follow-up drafts — and approved emails to website leads auto-send via Resend instead of requiring Outlook copy-paste.

## Context

- Both services run on Railway in the same project, communicating over Railway internal networking (zero latency, no public exposure).
- calmmoney.ca is a Next.js site with 4 lead capture points (contact form, retirement quiz, tool capture, newsletter signup), PostgreSQL on Railway, Resend for email delivery, and an admin dashboard.
- calm-money-bot is a Python Telegram bot with SQLite, OpenAI GPT-4, APScheduler, approval queue, compliance checking, nurture sequences, campaigns, and analytics.
- Co-operators branding constraint: website leads (calmmoney.ca source) can receive emails from `marc@info.calmmoney.ca` via Resend. Co-operators clients (voice notes, Outlook bookings, insurance book) must go through Outlook with Co-operators branding (copy-paste stays).

## Architecture

Two independent services, connected by HTTP webhooks over Railway private network. No shared database. The site continues to save all data to its own PostgreSQL. The CRM receives webhook notifications and creates its own prospect records in SQLite.

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

After each site form handler saves to PostgreSQL (existing behavior unchanged), it fires a POST to the CRM's `/api/intake` endpoint over Railway internal networking.

### Form Type Mapping

| Site Form | CRM Intake Type | Data Sent | Prospect Priority |
|-----------|----------------|-----------|-------------------|
| Contact form (`/api/contact`) | `website_contact` | name, email, phone, service, message | Hot |
| Retirement quiz (`/api/quiz`) | `website_quiz` | email, score, answers, tier | Warm |
| Tool capture (`/api/lead`) | `website_tool` | email, tool_name | Cool |

Newsletter signup is excluded — too low-intent and high-volume. Can be added later.

### CRM Processing for Website Leads

- Create prospect with `source = "website"`, `send_channel = "resend"`
- `website_contact`: product mapped from service dropdown, priority = Hot, follow-up draft auto-generated, Telegram alert to Marc
- `website_quiz`: score stored in client memory ("Scored 72/100, weak areas: tax planning, insurance"), nurture sequence auto-started, Telegram alert to Marc
- `website_tool`: minimal — just creates the prospect record, no alert (too noisy)

### Failure Handling

Site fire-and-forgets the webhook call. If the CRM is down, the lead still exists in the site's PostgreSQL. No retry mechanism for now.

### Site-Side Implementation

- New env var: `CRM_INTERNAL_URL` (e.g., `http://calm-money-bot.railway.internal:8080`)
- New helper: `src/lib/crm.ts` — single async function that POSTs to the CRM, catches errors silently
- Three form handlers modified: `/api/contact/route.ts`, `/api/quiz/route.ts`, `/api/lead/route.ts` — each adds a `crmNotify()` call after existing PostgreSQL save

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

### Implementation

- Site: add CRM webhook call in existing `/api/webhooks/resend/route.ts` handler, after existing bounce/complaint processing
- CRM: new intake type `email_event` in `intake.py`, matches events to outcomes by email address + recent sent_at timestamp

## Section 4: Database Changes

### calm-money-bot (SQLite)

Add `send_channel` column to `prospects` table:

```sql
ALTER TABLE prospects ADD COLUMN send_channel TEXT DEFAULT 'outlook';
```

Values: `'resend'` or `'outlook'`. Default `'outlook'` so all existing prospects are unaffected.

### pineault-wealth (PostgreSQL)

No schema changes. The site continues using its existing tables unchanged.

## Section 5: Changes Summary

### pineault-wealth (site) — 4 files touched

1. **New: `src/lib/crm.ts`** — CRM webhook helper (fire-and-forget POST)
2. **Modify: `src/app/api/contact/route.ts`** — add `crmNotify()` after save
3. **Modify: `src/app/api/quiz/route.ts`** — add `crmNotify()` after save
4. **Modify: `src/app/api/lead/route.ts`** — add `crmNotify()` after save
5. **Modify: `src/app/api/webhooks/resend/route.ts`** — forward open/click/bounce to CRM

### calm-money-bot (CRM) — 4 files touched

1. **New: `resend_sender.py`** — Resend API wrapper for sending approved drafts
2. **Modify: `intake.py`** — new intake types: `website_contact`, `website_quiz`, `website_tool`, `email_event`
3. **Modify: `db.py`** — add `send_channel` column to prospects table
4. **Modify: `bot.py`** — approval flow: auto-send via Resend when `send_channel = 'resend'`

### What Does NOT Change

- All existing site behavior (PostgreSQL saves, confirmation emails, admin dashboard, drip sequence)
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

- Verify `crmNotify()` is called after form save
- Verify CRM failure doesn't break form submission
- Verify Resend webhook forwards events to CRM

## Environment Variables

### New for calm-money-bot

- `RESEND_API_KEY` — Resend API key for sending emails
- `RESEND_FROM_EMAIL` — sender address (default: `marc@info.calmmoney.ca`)
- `RESEND_REPLY_TO` — reply-to address (default: `mpineault1@gmail.com`)

### New for pineault-wealth

- `CRM_INTERNAL_URL` — Railway internal URL of calm-money-bot (e.g., `http://calm-money-bot.railway.internal:8080`)
- `CRM_WEBHOOK_SECRET` — shared secret for authenticating CRM webhooks (uses existing `INTAKE_WEBHOOK_SECRET`)

## Success Criteria

1. A contact form submission on calmmoney.ca creates a Hot prospect in the CRM within seconds, with a follow-up draft ready for Marc to approve
2. A quiz completion creates a Warm prospect with score in memory and nurture sequence started
3. Approving a website-lead draft sends the email via Resend automatically (no copy-paste)
4. Email opens/clicks flow back to CRM outcome tracking
5. Existing Co-operators client workflows are completely unaffected
6. Site continues to work normally even if CRM is down
