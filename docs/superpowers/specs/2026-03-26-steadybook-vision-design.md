# SteadyBook CRM — Vision Design
**Date:** 2026-03-26
**Status:** Draft — pending review
**Scope:** Phase 1 feature expansion + Phase 2 roadmap

---

## Context

Marc (Cooperators) and David (brokerage partner) share the same pain: leads lost at events, multi-channel chaos (DMs, business cards, texts), and manual pipeline management that kills follow-through. SteadyBook becomes the flagship product — built for them first, then leveraged to onboard David's employees and future brokerage acquisitions. The goal is to be the best CRM purpose-built for insurance and financial services.

---

## Architecture Overview

Three new modules extend SteadyBook's existing intake pipeline. Nothing gets rebuilt — only extended.

```
CAPTURE LAYER
├── Telegram Bot (existing, extended)
│   ├── Voice memo → Whisper transcription → AI extraction → prospect
│   ├── Photo (business card) → GPT-4o Vision → OCR extraction → prospect
│   └── Text → quick-add (existing)
├── QR Landing Page (new)
│   └── Prospect self-fills name/email/phone/company → intake pipeline
└── [Phase 2: PWA mobile app]

ENRICHMENT LAYER (new: enrichment.py)
├── Fires on every new prospect creation (any source)
├── Google Search → company, title, LinkedIn URL
├── LinkedIn public profile → bio, career history, headshot
├── Instagram handle lookup → profile info
└── Writes enriched fields back to prospect record + re-triggers lead score

AUTOMATION ENGINE
├── Structured intake forms (per product line)
├── Tag-based trigger system → flows/sequences
└── AI cross-sell engine (formalized from existing logic)

[Phase 2: Social Monitoring Layer]
└── Watches for life events → auto-tags prospect → triggers flow
```

The sophisticated intake pipeline from calm-money-bot (`IntakeClassifier` + `EntityResolver` + `ActionExecutor`) gets merged into SteadyBook so every capture source deduplicates correctly. Same person via QR + Instagram DM = one record, not two.

---

## Data Model

The prospect record becomes the central intelligence file on a person. Every field write triggers a re-score and a next-action suggestion.

### Identity Layer
- Name, phone, email, company, title
- LinkedIn URL, Instagram handle, website
- Headshot (auto-pulled from enrichment)
- Source: how they came in (event/QR/DM/referral/card photo/voice)

### Relationship Layer
- How you met + context note (from voice memo or manual entry)
- Life events flagged (new job, recently married, etc.)
- Last contact date, next follow-up date
- Communication preference (text/email/phone)

### Commercial Layer
- Products of interest (life, disability, group benefits, home, auto, critical illness)
- Estimated AUM / household income tier
- Lead score (0–100, existing algorithm)
- Pipeline stage + days in stage
- Tags (drives all automation)

### Memory Layer
- Key facts extracted from every interaction (existing `client_memory` table)
- Linked to source interaction for traceability

### Signal Layer *(Phase 2)*
- Recent social activity flagged as relevant
- Life event triggers
- Engagement signals (email opens, link clicks)

---

## Capture Layer

### Telegram Bot Extensions

**Voice memo capture:**
1. User sends voice message to bot
2. Whisper API transcribes audio
3. GPT extracts: name, company, phone, email, products of interest, context note
4. Prospect created via intake pipeline (dedup runs automatically)
5. Enrichment fires in background
6. Bot replies: "Added Sarah Chen — group benefits lead, score pending enrichment"

**Business card photo capture:**
1. User sends photo to bot
2. GPT-4o Vision reads card: name, company, title, phone, email, website
3. Prospect created via intake pipeline
4. Enrichment fires
5. Bot replies with extracted fields for quick confirmation

**Text quick-add (existing, no changes needed)**

### QR Landing Page

Simple mobile-optimized web page (no login required):
- Fields: First name, last name, phone, email, company, what they're looking for (multi-select: life, disability, group benefits, home/auto, not sure)
- Submit → intake pipeline → enrichment → tagged based on selections → advisor gets Telegram notification
- Each tenant gets their own QR code linked to their page
- QR code downloadable from dashboard for use on business cards, event materials

---

## Enrichment Engine (`enrichment.py`)

Fires automatically after every new prospect creation regardless of source.

**Step 1 — Google Search**
Query: `"{name}" "{company}" site:linkedin.com OR site:instagram.com`
Extract: LinkedIn URL, Instagram handle, company website, title confirmation

**Step 2 — LinkedIn public profile**
Scrape public data (no login): headshot, bio, current role, career history, location
Store: headshot URL, bio snippet, career timeline

**Step 3 — Instagram handle**
If handle found: pull public profile bio, follower count, profile photo

**Step 4 — Write back + re-score**
Enriched fields written to prospect record. Lead score recalculated. If high-value signals found (large company, senior title), score bumps and advisor gets notified.

**Design rules:**
- Enrichment runs async (never blocks the capture flow)
- Partial enrichment is fine — write what you find, leave the rest blank
- Re-runs on demand from dashboard ("Re-enrich" button)
- All data sourced from public information only

---

## Automation Engine

### Structured Intake Forms

Product-specific forms activated when a prospect is tagged for a product line. Asks the right questions for each line:

**Life Insurance:** beneficiaries, coverage amount needed, existing policies, smoker status, health flags, reason for interest
**Disability:** occupation, income, existing group coverage, waiting period preference
**Group Benefits:** number of employees, current provider, renewal date, decision-maker
**Home/Auto:** property type, current insurer, renewal date, vehicles
**Critical Illness:** family health history, existing coverage

Form data → populates prospect record → triggers product-specific lead score adjustment → may auto-tag for additional flows.

Forms are sent via SMS or email link after advisor tags the prospect. Prospect fills at their own pace.

### Tag-Based Trigger System

Tags are the nervous system of the platform. Any event can apply a tag. Tags trigger flows.

**Auto-applied tags (system):**
```
new_lead               → 48hr follow-up reminder task
source_event           → "met at event" nurture sequence
source_qr              → "thanks for connecting" auto-SMS
closed_life            → cross-sell: disability flow (30-day delay)
closed_disability      → cross-sell: critical illness flow
policy_renewal_90      → renewal prep email + advisor task
policy_renewal_30      → urgent renewal task
job_change             → flag: group benefits conversation
life_event_baby        → flag: life insurance review
```

**Manual tags (advisor applies):**
```
hot                    → priority score boost, daily briefing highlight
referral_source        → referral appreciation sequence
vip                    → white-glove nurture, no automated SMS
do_not_contact         → all automations suppressed
```

**Flow structure:** Tag applied → check eligibility → enroll in sequence → sequence steps fire on schedule → tag removed when sequence completes or prospect advances stage.

### AI Cross-Sell Engine

Formalized from existing SteadyBook logic. Fires after every closed deal.

1. Look at client's current product holdings
2. Apply product matrix to identify gaps (e.g., has life but no disability)
3. Check household (spouse, dependents) for additional opportunities
4. Apply timing rules (minimum 30-day cooldown between cross-sell attempts)
5. Generate recommended next product + suggested talking point
6. Create advisor task with the recommendation
7. Optionally enroll in cross-sell nurture sequence

**Product matrix (insurance-specific):**
- Life → Disability (income protection)
- Life → Critical Illness (living benefits)
- Life → Group Benefits (if business owner)
- Disability → Critical Illness
- Group Benefits → Key Person Life (if business owner)
- Any → Annual review reminder (12-month tag)

---

## Phase 1 Scope (Build Now)

1. Telegram voice memo capture (Whisper + GPT extraction)
2. Telegram business card photo capture (GPT-4o Vision)
3. QR landing page (per-tenant, downloadable QR)
4. Enrichment engine (`enrichment.py`) — Google + LinkedIn + Instagram
5. calm-money-bot intake pipeline merger (dedup + entity resolution)
6. Tag-based trigger system (core tags + flow enrollment)
7. Life + disability + group benefits intake forms
8. Formalized cross-sell engine with product matrix + timing rules

## Phase 2 Scope (After Dog-Fooding)

1. Social monitoring for life events → auto-tagging
2. PWA mobile capture app
3. Home/auto intake forms
4. Flow builder UI (drag-and-drop sequences)
5. Manager/team dashboard for David's employees
6. Email open + click tracking
7. Full GoHighLevel feature parity

---

## What Already Exists in SteadyBook (No Rebuild Needed)

- Multi-tenant isolation and auth (`tenants.py`)
- Lead scoring algorithm (`scoring.py`)
- Nurture sequences and campaigns (`nurture.py`, `campaigns.py`, `sequences.py`)
- SMS agent + approval queue
- Client memory extraction (`memory_engine.py`)
- Compliance filter + audit log (`compliance.py`)
- Morning briefing with top prospects
- Activity logging + meeting management
- Cross-sell suggestions (lightweight — being formalized above)
- Flask dashboard with pipeline view

---

## Success Criteria

- A lead captured at an event via voice memo is in SteadyBook with enriched profile within 60 seconds
- Zero manual data entry required for a new prospect from a business card photo
- Every new prospect is automatically enriched, scored, tagged, and has a next action assigned
- Advisors spend less than 2 minutes per day on CRM admin (everything else is automated or AI-drafted)
- David can onboard his team as users and see aggregate pipeline without code changes
