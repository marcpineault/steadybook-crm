# SteadyBook Pitch Deck — Design Spec
Date: 2026-03-26

## Overview
A single self-contained HTML pitch deck at `docs/pitch-deck.html`. Replaces the existing version.

## Audience
Insurance advisors / potential customers. This is a product sales deck, not a fundraising deck.

## Visual Style
- Attio.com inspired: light & clean
- Background: warm off-white (#f8f7f4)
- Accent: indigo (#6366f1)
- Typography: Inter (Google Fonts)
- Card shadows, clean borders, generous whitespace
- Scroll-snap 100vh slides, keyboard nav, progress bar, slide counter

## Story Arc — "The Advisor's Day"
Narrative follows one advisor from 7am chaos to 7pm clarity, with SteadyBook as the turning point.

| # | Slide | Emotional Beat |
|---|-------|----------------|
| 1 | Hook: 7am — "3 leads went cold overnight" | Urgency |
| 2 | The Advisor's Juggle — full-day timeline | Recognition |
| 3 | The Breaking Point — $12K commission lost | Gut punch |
| 4 | There's a Better Way — intro to SteadyBook | Relief |
| 5 | Feature: Know Who to Call (AI scoring) | Clarity |
| 6 | Feature: Follow-Up That Sounds Like You (SMS) | Trust |
| 7 | Feature: Your Pipeline, Always Clean (Kanban) | Control |
| 8 | The Transformation: 7pm — same day, different result | Payoff |
| 9 | Pricing — Solo / Growth / Agency | Commitment |
| 10 | CTA — "See your pipeline in 20 minutes" | Action |

## Technical
- Single `docs/pitch-deck.html` file, all CSS/JS inline
- External: Google Fonts only
- Keyboard navigation (arrow keys), dot nav, prev/next buttons
- Progress bar, slide counter (1/10)
- No external icon libraries — inline SVG
