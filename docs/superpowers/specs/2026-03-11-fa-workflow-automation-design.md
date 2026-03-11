# FA Workflow Automation Design

**Date:** 2026-03-11
**Status:** Approved

## Problem

As a financial advisor, two major time sinks eat into selling hours: post-call admin (no consistent notes, manual CRM updates, forgotten follow-ups) and prospecting/outreach (leads go cold due to lack of systematic follow-up). Calls happen across cell, VoIP, and video — making consistent capture difficult.

## Solution

Seven automations built into the existing Telegram bot, organized in three phases. The bot becomes the central hub: all data flows in, AI processes it, actions flow out. No new apps to check.

## Architecture

**Inputs:** Otter.ai transcripts (via Zapier), Telegram voice notes, forwarded lead emails, Outlook Bookings calendar (via Power Automate)

**Brain:** Existing Telegram bot + OpenAI API — transcript analysis, pipeline management, scheduling, message drafting

**Outputs:** Auto-updated pipeline, draft follow-up emails, Telegram reminders/nudges, pre-call briefings

**Connectors:** Zapier (Otter → bot), Power Automate (Outlook Bookings → bot), webhook endpoint on Railway, OpenAI Whisper + GPT

## Phase 1: Quick Wins

### 1A — Voice Notes → Pipeline

User sends a voice message to the Telegram bot after any interaction. The bot:

1. Transcribes via OpenAI Whisper API
2. AI extracts: prospect name, key details, action items, next steps, cross-sell signals
3. Matches to existing prospect or creates new entry
4. Updates pipeline and confirms via Telegram

Handles referral mentions — e.g., "his brother needs commercial insurance" auto-creates a new prospect.

### 1B — Lead Intake Automation

Referral emails or Co-operators lead notifications forwarded to the bot (via Zapier email monitoring or pasted into Telegram). AI parses the lead info, creates prospect, scores them, schedules first follow-up.

### 1C — Auto-Intake from Outlook Bookings

Power Automate triggers on new Outlook Bookings appointments. Sends prospect data (name, email, phone, booking reason) to the bot's webhook. Bot creates prospect, scores them, kicks off follow-up cadence. Zero manual data entry for booked meetings.

**Technical changes:**
- New: Voice message handler (Telegram voice → Whisper API)
- New: AI extraction prompts (structured data from transcripts/emails)
- New: Webhook endpoint (receives Zapier + Power Automate payloads)
- Enhanced: db.py — interaction logging (notes, timestamps, source)
- Enhanced: bot.py — voice + webhook handlers

## Phase 2: Call Intelligence

### 2A — Otter Transcript → Bot Pipeline

Zapier detects new Otter.ai transcript → sends full text to bot webhook. AI analyzes the conversation: identifies prospect, extracts summary, concerns, products discussed, objections, commitments, next steps, cross-sell opportunities, referral mentions. Updates pipeline and messages user with summary.

### 2B — Post-Call Auto-Pilot

After every call, the bot automatically:
- Updates pipeline (status, notes, interaction log)
- Drafts personalized follow-up email referencing specific call details
- Schedules next touchpoint based on what was discussed
- Flags cross-sell opportunities and new referral leads

User reviews draft emails via `/review_email` command in Telegram.

**Technical changes:**
- New: Webhook receiver for Zapier/Otter payloads
- New: Transcript analyzer (AI prompt chain: summarize → extract → match → generate actions)
- New: Email drafter (AI generates follow-ups, stores for review)
- New: `/review_email` command
- Enhanced: Scheduler — accepts dynamic follow-up dates from calls
- Enhanced: db.py — store call transcripts, drafted emails, interaction history

## Phase 3: Proactive Outreach

### 3A — Smart Follow-Up Engine

Every prospect gets a follow-up cadence based on their score:
- **Hot (8-10):** Day 1 call, day 2 email, day 4 text, day 7 call
- **Warm (5-7):** Day 1 call, day 3 email, day 7 check-in, day 14 value-add
- **Cool (1-4):** Day 1 call attempt, day 5 email, day 14 follow-up, day 30 final

Morning briefing includes today's follow-ups. Missed follow-ups get escalated. User manages via `/snooze`, `/skip`, `/done` commands.

### 3B — Pre-Call Briefing

15 minutes before a scheduled call, bot sends a brief: prospect profile, last interaction, policies, talking points, cross-sell opportunities. AI synthesizes all pipeline data, past notes, and call transcripts into a 30-second read.

### 3C — Pre-Meeting Personal Touch (No-Show Reducer)

Morning of a meeting, bot sends a Telegram nudge with a suggested personal message based on client history. User records and sends their own video/voice/text to the client. Outlook Bookings already handles generic email reminders — this adds the personal layer that reduces no-shows.

**Technical changes:**
- New: Cadence engine (db table for schedules, templates by score)
- New: `/snooze`, `/skip`, `/done` commands
- New: Power Automate → webhook (daily upcoming bookings)
- New: Pre-call brief generator
- New: Personal touch message suggester
- Enhanced: Morning briefing — includes follow-ups, meetings, nudge reminders
- Enhanced: Scheduler — manages cadences, escalates missed follow-ups

## External Dependencies

| Service | Purpose | Integration Method |
|---|---|---|
| Otter.ai | Call recording & transcription | Zapier trigger → webhook |
| Zapier | Otter + email forwarding bridge | Zaps → bot webhook |
| Power Automate | Outlook Bookings → bot | Flow → bot webhook |
| OpenAI Whisper | Voice note transcription | API (already have key) |
| OpenAI GPT | All AI analysis & drafting | API (already in use) |
| Outlook Bookings | Scheduling + email reminders | Existing, unchanged |

## Recording & Privacy

User notifies clients at start of calls (Canada one-party consent). Otter.ai handles recording. Transcripts stored in bot's SQLite database on Railway.

## Build Order

Phase 1 → Phase 2 → Phase 3. Each phase builds on the data the previous phase collects. Phase 1 is self-contained and delivers immediate value.
