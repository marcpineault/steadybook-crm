# Calm Money AI — Autonomous Business Partner

## Overview

Transform calm-money-bot from a reactive Telegram CRM assistant into an autonomous AI business partner for Marc's financial planning practice at Co-operators in London, Ontario. The system will think about the business 24/7 — generating content, managing admin, preparing outreach, and learning what works — while maintaining compliance guardrails appropriate for the financial services industry.

**Goal**: Become THE financial planner people think of in London, Ontario, by automating lead generation, eliminating admin overhead, and building a self-improving growth engine.

**Core Principle**: Autonomous for routine operations, with compliance guardrails, audit trails, and a trust ladder that keeps Marc in control.

## Current State

- Python 3.13 Telegram bot (webhook-based) with Flask web dashboard
- GPT-4 tool calling with 25+ tools, Whisper voice transcription
- SQLite database with prospects, activities, meetings, insurance book, tasks, interactions, win/loss log
- Multi-channel intake: voice notes, Otter.ai transcripts, email (CloudMailin), calendar (Power Automate), Outlook Bookings
- Lead scoring engine with cross-sell suggestions and referral nudges
- Task/reminder system with morning briefings and auto-nag
- Insurance quoting (term life via term4sale.ca, disability via Edge Benefits)
- Deployed on Railway via Docker

## Constraints

- **Outlook email is locked down** — cannot automate sending/reading from work email programmatically
- **Co-operators handles client portal and investment platform** — no need to build client-facing infrastructure
- **Financial services compliance** — no return promises, no misleading claims, proper disclaimers
- **PIPEDA (Canadian privacy law)** — PII must be handled appropriately
- **Social media publishing** — Marc uses Publer for scheduling; AI generates content, Marc drops it into Publer
- **Outreach safety** — AI drafts and queues, Marc approves before anything goes to a client (trust ladder model)
- **Microsoft Bookings** — existing booking system, already integrated via Power Automate webhooks

## Architecture

```
+---------------------------------------------------+
|                 CALM MONEY AI                      |
|            "The Business Brain"                    |
|                                                    |
|  +----------+  +----------+  +---------------+    |
|  | Memory   |  | Strategy |  | Learning      |    |
|  | Engine   |  | Engine   |  | Engine        |    |
|  |          |  |          |  |               |    |
|  | Client   |  | Pipeline |  | What works?   |    |
|  | profiles |  | analysis |  | What doesn't? |    |
|  | Context  |  | Timing   |  | Adapt & opt.  |    |
|  | History  |  | Scoring  |  |               |    |
|  +----------+  +----------+  +---------------+    |
|                                                    |
|  +--------------------------------------------+   |
|  |         Autonomous Agent Layer              |   |
|  |                                             |   |
|  |  Marketing  |  Admin  |  Outreach  | Intel  |   |
|  |  Agent      |  Agent  |  Agent     | Agent  |   |
|  +--------------------------------------------+   |
|                                                    |
|  +--------------------------------------------+   |
|  |         Compliance & Audit Layer            |   |
|  |  Financial Regs  |  PII Encryption  | Logs  |   |
|  +--------------------------------------------+   |
+-------------------------+-------------------------+
                          |
            +-------------+----------------+
            |             |                |
      +-----v---+  +-----v------+  +------v--------+
      |Telegram |  |Social/SMS  |  | Web Dashboard |
      |(HQ)     |  |LinkedIn    |  | Analytics     |
      |         |  |FB/IG/Text  |  | Approvals     |
      +---------+  +------------+  +---------------+
```

## Phased Build Plan

Each phase is independently valuable and gets its own implementation plan. Later phases build on earlier ones but each delivers standalone benefit.

---

## Phase 1: "The Brain" (Intelligence Foundation)

### Client Memory Engine

**Purpose**: Transform flat CRM records into rich relationship profiles that power every other system.

**New database table — `client_memory`**:

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| prospect_id | INTEGER FK | Links to prospects table |
| category | TEXT | One of: life_context, financial_context, communication_prefs, relationship_signals, conversation_history, key_dates |
| fact | TEXT | The extracted fact (e.g., "daughter starts university Sept 2027") |
| source | TEXT | Where this was learned (e.g., "voice_note_2026-03-10", "meeting_transcript") |
| needs_review | BOOLEAN | Whether Marc should confirm this fact (set for ambiguous or contradictory extractions) |
| extracted_at | TEXT | Timestamp |

**Auto-extraction pipeline**:
1. Every voice note transcription, chat message, meeting transcript, and logged activity passes through a GPT extraction step
2. GPT receives the existing client profile + new interaction and returns structured facts
3. New facts are merged into client_memory — duplicates are updated, contradictions flagged with `needs_review = true`
4. Facts marked `needs_review` are sent to Marc via Telegram for confirmation ("I think Sarah's daughter starts university in 2027 — is that right?")

**Extraction prompt strategy**:
- System prompt defines the categories and expected fact types
- Few-shot examples for each category
- Instruction to extract only what's explicitly stated or strongly implied — no speculation
- Instruction to flag when new information contradicts existing facts

**Data migration**: On first run, backfill `client_memory` by processing all existing `prospects.notes` and `interactions.raw_text` through the extraction pipeline. This bootstraps the Memory Engine with years of embedded client knowledge.

### Approval Queue (Database-Backed)

All drafted messages (follow-ups, outreach, content) are persisted in a database table, not just sent as Telegram messages. This ensures nothing is lost if the bot restarts or Marc misses a Telegram notification.

**New database table — `approval_queue`**:

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| type | TEXT | follow_up, outreach, content, nurture_touch |
| prospect_id | INTEGER FK | Optional — links to prospects table |
| channel | TEXT | email_draft, sms, linkedin_dm, content_post |
| content | TEXT | The drafted message |
| context | TEXT | Why this was generated (e.g., "post-call follow-up for discovery call") |
| status | TEXT | pending, approved, edited, dismissed, snoozed, sent |
| created_at | TEXT | When the draft was generated |
| acted_on_at | TEXT | When Marc took action |
| telegram_message_id | TEXT | For linking back to the Telegram notification |

### GPT API Failure Handling

All GPT-dependent flows must handle API failures gracefully:
- **Compliance filter fails**: Message is held in approval_queue with status "compliance_pending" — never sent without compliance check. Marc is notified.
- **Content generation fails**: Content calendar shows gap; Marc is notified to write manually or retry.
- **Memory extraction fails**: Interaction is logged normally; extraction retried on next scheduler run.
- **Briefing generation fails**: Falls back to current simple morning briefing format (overdue tasks + follow-ups).

### Strategic Morning Briefing

**Replaces current morning briefing** (scheduler.py `send_morning_briefing`) with a comprehensive daily brief sent at 8AM ET.

**Briefing structure**:

1. **Pipeline health score** (0-100) with week-over-week trend
   - Calculated from: deal velocity, stage distribution, follow-up compliance, win rate trend
2. **Revenue forecast** for the month
   - Sum of (`revenue` field x stage probability) for all active prospects (uses existing `STAGE_PROBABILITIES` from scoring.py)
3. **Priority moves** (top 2-3)
   - AI analyzes the full pipeline and recommends the highest-impact actions for today
   - Each recommendation includes reasoning: "Mike has been in Needs Analysis for 6 days — win rate drops 40% after day 5"
4. **Risk alerts**
   - Deals going cold (no activity in X days by stage)
   - Follow-ups overdue
   - Prospects who were hot but have gone silent
5. **Opportunity alerts**
   - Market events mapped to specific prospects (rate changes, tax deadlines, seasonal relevance)
   - Cross-sell opportunities surfaced from Memory Engine data
6. **Today's call list**
   - Ranked by impact score
   - Each entry includes: name, context summary, talking points, recommended approach
7. **Queued actions**
   - Follow-up emails/messages drafted overnight, ready for review
   - Number of items in approval queue

**Implementation**: New function in scheduler.py that:
1. Queries pipeline, activities, tasks, and client_memory
2. Sends all data to GPT with a strategic briefing prompt
3. GPT returns the structured briefing
4. Formatted and sent via Telegram

### Compliance & Audit Layer

**New database table — `audit_log`**:

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| timestamp | TEXT | ISO 8601 |
| action_type | TEXT | e.g., "email_draft", "sms_sent", "prospect_updated", "content_generated" |
| target | TEXT | Who/what was affected (prospect name, platform, etc.) |
| content | TEXT | The actual content generated/sent |
| compliance_check | TEXT | Result of compliance filter (passed/flagged + details) |
| approved_by | TEXT | "auto" or "marc" or null (if pending) |
| outcome | TEXT | What happened after (sent, edited, dismissed) |

**Compliance filter** — a GPT pass on every outgoing client-facing message:
- No promises of returns or guaranteed outcomes
- No misleading claims about products or coverage
- Appropriate disclaimers where needed
- No sharing of other clients' information
- Professional tone appropriate for financial services
- Returns: pass/fail + specific issues if flagged

---

## Phase 2: "The Admin" (Autopilot Operations)

### Auto-Drafted Follow-Ups

**Trigger**: Any logged activity (call, meeting, voice note recap) automatically generates a follow-up draft.

**Flow**:
1. Activity is logged (via voice note, chat, or manual entry)
2. AI pulls: activity details + full client memory profile + conversation history + stage-appropriate templates
3. GPT generates a follow-up email/message tailored to the specific conversation
4. Draft is sent to Marc's Telegram with prospect name, context summary, and the draft
5. Marc can: Approve (copy-paste to Outlook), Edit (tell AI what to change), Dismiss, or Snooze
6. All drafts persisted in `approval_queue` table — survives bot restarts
7. If no action within configurable window (default 4 hours, stored in environment variable `FOLLOW_UP_NUDGE_HOURS`), a nudge is sent
8. Action and outcome logged to audit_log

**Draft quality signals** (feeds into Phase 5 Learning):
- Track which drafts Marc sends as-is vs edits heavily vs dismisses
- Over time, adapt tone, length, and style to match Marc's preferences

### Meeting Prep Docs

**Trigger**: Scheduled meeting detected (from meetings table or Microsoft Bookings webhook).

**Timing**: Sent to Telegram 1 hour before the meeting (configurable).

**Prep doc contents**:
1. **Client snapshot** — all Memory Engine data for this prospect, organized by category
2. **Interaction history** — last 3-5 interactions summarized
3. **Where we left off** — last conversation's key points, promises made, open questions
4. **Recommended agenda** — based on stage and prospect needs
5. **Objection prep** — likely concerns based on profile and what similar prospects have raised
6. **Product recommendation** — what to pitch and why, with talking points
7. **Cross-sell opportunities** — based on current holdings vs gaps
8. **Personal touch** — something to ask about from their life context (kids, hobbies, recent events)

### Enhanced Voice-Note Pipeline

**Expands existing voice_handler.py** to do more with post-call voice notes.

**Current flow**: Voice note → Whisper transcription → GPT extraction → add prospect + log activity + log interaction. This already handles prospect creation and basic activity logging.

**Incremental changes** (what's NEW beyond existing voice_handler.py):
1. **Update existing prospect** — detect when voice note is about an existing prospect (not just new leads) and update their stage, notes, priority
2. **Create task** — extract follow-up commitments and create tasks with dates (extends existing task creation)
3. **Draft follow-up** — auto-generate follow-up email/message, queue in `approval_queue`
4. **Update Memory Engine** — extract relationship facts into `client_memory` (NEW)
5. **Alert on urgency** — if the voice note indicates something time-sensitive, send immediate Telegram alert (NEW)

The extraction prompt is redesigned to identify ALL actionable items and execute them in one pass, using GPT tool calling with the expanded tool set.

### Microsoft Bookings Integration Enhancement

**Current state**: Power Automate sends booking webhooks to intake.py, which creates meeting records.

**Enhanced flow**:
1. Booking received → create/update meeting record (existing)
2. Match to existing prospect or create new one (existing, enhanced matching)
3. Trigger Memory Engine lookup — pull all known context
4. Schedule prep doc generation for 1 hour before meeting
5. If new prospect: start building profile from any available data (booking form fields, email domain, etc.)
6. Send Marc a Telegram notification: "New booking: [Name] on [Date] for [Type]. I'll have prep ready before the meeting."

---

## Phase 3: "The Marketer" (Content & Brand Machine)

### Content Generation Engine

**New module: `content_engine.py`**

Generates social media content in Marc's voice for LinkedIn, Facebook, and Instagram.

**Content types and mix** (weekly default):
- 2x Educational posts (financial planning tips, insurance education)
- 1x Local angle (London, Ontario context)
- 1x Story post (anonymized client win or scenario)
- 1x Timely/reactive post (market events, rate changes, seasonal)

**Voice calibration**:
- Initial setup: Marc provides 10-20 existing social posts as examples. These are stored in a `brand_voice` table and included in every content generation prompt as few-shot examples.
- Ongoing: when Marc edits a draft before posting, the edited version replaces the original in the brand voice examples. Over time, the examples converge on Marc's actual voice.
- Tone targets: approachable, confident, not salesy, locally rooted, plain language

**Content generation prompt includes**:
- Marc's brand voice profile
- Current market context (rate environment, seasonal relevance)
- Recent high-performing content patterns (from Phase 5)
- Prospect pipeline context (what products/topics are most relevant right now)
- Local London, Ontario news/events when relevant

### Weekly Content Calendar

**Trigger**: Every Sunday at 6PM ET.

**Flow**:
1. AI reviews: upcoming week's market calendar, seasonal relevance, recent content performance, pipeline composition
2. Generates a 5-post content plan with: day, platform, topic, angle, and why this topic now
3. Sends plan to Marc's Telegram
4. Marc approves, swaps, or requests changes
5. On approval, AI generates all posts (text + suggested image descriptions)
6. Marc drops them into Publer for scheduling

### Market Intelligence Feed

**New scheduled job**: Monitors relevant signals daily.

**Sources** (pre-loaded calendars — no web scraping):
- Bank of Canada rate decision dates (published annually, 8 per year — pre-loaded)
- Tax deadline calendar (RRSP, TFSA, filing deadlines — pre-loaded)
- Seasonal financial planning topics (pre-loaded: RRSP season Jan-Mar, tax season Mar-Apr, back-to-school Aug-Sep for life insurance, year-end planning Oct-Dec)
- Co-operators product updates (Marc inputs manually when he learns of them, or via a simple `/news` command)

Note: Local London news and web scraping are deferred. Marc can manually surface local angles via chat ("housing prices in London dropped, make a post about that").

**Output**: Relevant items included in morning briefing with:
- The event/news
- Which prospects it's relevant to (from Memory Engine)
- A draft content angle if it's post-worthy
- A draft outreach message if it's relevant to specific prospects

---

## Phase 4: "The Outreach Rep" (Draft & Approve)

### Trust Ladder

Core safety mechanism. All outreach operates on a trust level that Marc controls:

| Level | AI Behavior | Marc's Role |
|-------|------------|-------------|
| **1 — Training wheels** (default) | Drafts everything, queues in Telegram for approval | Reviews and approves each message individually |
| **2 — Trusted on routine** | Sends standard reminders and confirmations autonomously | Reviews only non-standard or first-contact messages |
| **3 — Full autonomy** | Handles all routine outreach, escalates exceptions only | Spot-checks weekly, handles escalations |

**Level transitions**: Marc explicitly tells the bot to change level (e.g., `/trust 2`). The system never auto-escalates.

**Compliance filter runs at ALL trust levels.** Even at Level 3, every outgoing message passes through the compliance check. The trust ladder controls whether Marc reviews messages, not whether the system does.

**Available output channels at launch**:
- **Email drafts** — AI drafts the message, Marc copy-pastes into Outlook (primary channel)
- **SMS via Twilio** — added when Marc is ready; requires Twilio account setup and CASL compliance (deferred)
- **LinkedIn DM drafts** — AI drafts, Marc copy-pastes into LinkedIn (manual initially)
- **Phone call prep** — not a message channel, but AI prepares talking points and adds to call list

Note: Auto-send via SMS/email is a future capability. At launch, all channels are "draft and copy-paste."

**Approval UX in Telegram**:
- Message shows: recipient, channel, the message content, and context
- Inline buttons: Approve | Edit | Skip | Snooze
- All drafts backed by `approval_queue` table — nothing lost if bot restarts
- Batch mode for campaigns: "Here are 8 messages for your disability cross-sell campaign. Approve all / Review each"

### Insurance Book Campaign System

**New module: `campaigns.py`**

**Campaign creation flow**:
1. Marc says: "I want to reach out to my life insurance clients who don't have disability"
2. AI queries insurance_book + prospects, segments by criteria
3. AI presents: "Found 42 clients matching. I'd suggest 3 waves of outreach over 2 weeks. Here's the approach for wave 1..."
4. For each client, AI generates a personalized message referencing their specific policy, tenure, and situation
5. Messages queued in batches for Marc's approval
6. Approved messages sent via chosen channel
7. Responses tracked — interested clients escalated to call list with context

**Campaign tracking**:
- Messages sent, delivered, responded
- Positive responses vs no response vs opt-out
- Conversion to meetings and deals
- All logged to audit_log

### Lead Nurture Sequences

**Replaces existing `FOLLOW_UP_SEQUENCES` in bot.py** (lines 204-230) which currently define static follow-up timing by stage. The new system is dynamic, personalized, and content-driven rather than just time-based.

**For prospects who enter the pipeline but aren't meeting-ready**:

1. Prospect enters pipeline (from social media, referral, etc.)
2. AI builds a nurture sequence: 3-5 value touches over 2-4 weeks
   - Touch 1: Relevant educational content
   - Touch 2: Specific insight related to their situation
   - Touch 3: Soft ask (booking link)
   - Touch 4+: Additional value or re-engagement angle
3. Each touch queued for Marc's approval before sending
4. If prospect engages (responds, books), sequence stops and they move to active pipeline
5. If no engagement after full sequence, AI suggests: park, try different angle, or move to long-term nurture

### Inbound Lead Funnel

**When social content generates interest** (Phase 3):

- Marc receives DM or comment → AI drafts a response that acknowledges their interest and guides toward booking link
- Response queued for Marc's approval
- If they book → full pipeline kicks in (prospect created, Memory Engine starts, prep doc scheduled)
- If they engage but don't book → enters nurture sequence

---

## Phase 5: "The Analyst" (Learning Loop)

### Outcome Tracking

**New database table — `outcomes`**:

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| action_id | INTEGER FK | Links to audit_log |
| action_type | TEXT | email_draft, content_post, outreach_message, call_recommendation |
| target | TEXT | Prospect name or platform |
| sent_at | TEXT | When the action was executed |
| response_received | BOOLEAN | Did they respond? |
| response_at | TEXT | When they responded |
| response_type | TEXT | positive, neutral, negative, no_response |
| converted | BOOLEAN | Did this lead to a meeting or deal? |
| notes | TEXT | Additional context |

**Tracking by system**:
- **Follow-up emails**: Marc marks whether prospect responded (via quick Telegram buttons after a few days)
- **Content posts**: Marc inputs engagement metrics periodically, or eventual API integration
- **Outreach campaigns**: Response tracking built into campaign flow
- **Call recommendations**: Did Marc call? What happened? (Voice note capture)

### Weekly Insights Digest

**Trigger**: Every Sunday at 6PM ET — combined into a single "Weekly Review" message alongside the content calendar. Replaces the existing `weekly_report` in scheduler.py (currently Sunday 7PM) to avoid notification fatigue.

**Contents**:
1. **This week's numbers**: Calls made, meetings booked, deals progressed, deals closed
2. **What worked**: Top-performing content, most effective follow-up styles, best outreach messages
3. **What didn't**: Content that flopped, outreach with low response rates, lost deals and why
4. **Patterns spotted**: Timing patterns, messaging patterns, prospect type patterns
5. **Recommendations**: Specific adjustments to content strategy, outreach approach, or follow-up timing
6. **Month-over-month trends**: Pipeline growth, conversion rates, revenue trajectory

### Feedback Loops

**How learning feeds back into each phase**:

| Learning | Feeds Into | How |
|----------|-----------|-----|
| Which draft styles Marc sends as-is | Admin (Phase 2) | Adjust follow-up draft tone and length |
| Which content topics drive engagement | Marketer (Phase 3) | Shift content calendar toward high-performers |
| Which outreach messages get responses | Outreach (Phase 4) | Evolve message templates by segment |
| Which prospects convert fastest | Brain (Phase 1) | Improve lead scoring weights |
| Best call times and days | Brain (Phase 1) | Optimize call list ranking and timing |
| Common objections by product | Admin (Phase 2) | Improve meeting prep objection section |

**Implementation**: Learning is primarily prompt-driven. Each system's GPT prompts include a "what's working" context block that gets updated weekly from the outcomes data. No custom ML models needed — just smart aggregation and prompt engineering.

---

## Preliminary Refactoring

Before Phase 1 implementation, `bot.py` (2,387 lines) should be decomposed to prevent it from growing past 4,000 lines. Extract into:
- `tools.py` — GPT tool definitions and dispatch logic
- `handlers.py` — Telegram command handlers
- `bot.py` — Application setup, webhook config, and orchestration only

This refactoring is a prerequisite for Phase 1 and should be the first task in the implementation plan.

## GPT Model Strategy

- **Content generation, strategic briefings, meeting prep**: Use `gpt-4.1` (highest quality for client-facing and strategic output)
- **Memory extraction, compliance filtering**: Use `gpt-4.1-mini` (fast, cheap, structured extraction — consistent with current codebase usage)
- **Voice transcription**: Continue using Whisper API (existing)

## Data Model Changes Summary

New tables to add to `db.py`:

1. **`client_memory`** — Enriched client facts extracted from interactions
2. **`approval_queue`** — Persisted draft messages awaiting Marc's review (survives bot restarts)
3. **`audit_log`** — Every AI action logged for compliance and review
4. **`outcomes`** — Tracks results of AI actions for the learning loop
5. **`campaigns`** — Campaign definitions (name, segment criteria, status, type)
6. **`campaign_messages`** — Individual messages within a campaign (recipient, content, status, response)
7. **`content_calendar`** — Planned and generated content (date, platform, topic, content, status)
8. **`brand_voice`** — Example social posts for voice calibration
9. **`market_calendar`** — Pre-loaded financial events (rate decisions, tax deadlines, seasonal topics)

## New Modules

| Module | Purpose |
|--------|---------|
| `memory_engine.py` | Client fact extraction, storage, retrieval, and conflict resolution |
| `briefing.py` | Strategic morning briefing generation |
| `compliance.py` | Compliance filter for outgoing messages, audit logging |
| `follow_up.py` | Auto-draft follow-ups, approval queue, nudging |
| `meeting_prep.py` | Pre-meeting briefing document generation |
| `content_engine.py` | Content generation, calendar planning, voice calibration |
| `market_intel.py` | Market monitoring and opportunity mapping |
| `campaigns.py` | Campaign creation, segmentation, message generation, tracking |
| `nurture.py` | Lead nurture sequence management |
| `analytics.py` | Outcome tracking, pattern recognition, weekly insights |

## Non-Goals (Explicitly Out of Scope)

- Client-facing portal (Co-operators handles this)
- Investment platform or financial plan generation (Co-operators handles this)
- Auto-publishing to social media (Marc uses Publer)
- CASL consent management (deferred to later)
- Autonomous email sending from Outlook (locked down)
- Multi-user/team features beyond current coworker access
- Mobile app (Telegram is the interface)
