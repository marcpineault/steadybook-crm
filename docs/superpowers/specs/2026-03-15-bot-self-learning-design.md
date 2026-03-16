# Phase 3: Bot Self-Learning — Design Spec

## Overview

Close the feedback loops in the bot so it actually learns from Marc's behavior and prospect responses. Currently the bot collects outcome data but barely uses it — learning context is shallow aggregate rates that feed only into the morning briefing. This phase makes the bot genuinely self-improving.

**Goal:** The bot gets measurably better at drafting follow-ups, scoring leads, and timing outreach — without Marc having to tune anything manually.

## Current State (Gaps)

| What Exists | What's Missing |
|---|---|
| Outcomes table tracks approved drafts | Dismissed/snoozed drafts are not recorded as negative signal |
| Resend open/click webhooks update outcomes | No tracking of which draft CONTENT drives opens/clicks |
| `get_learning_context()` returns aggregate rates | No per-topic, per-tone, per-style breakdown |
| Learning context injected into briefing | NOT injected into follow_up.py, nurture.py, or campaigns.py |
| Brand voice examples update on content approval | Follow-up style does NOT update based on Marc's edits |
| Win/loss log exists | Completely disconnected from learning system |
| Scoring is fully hardcoded | No feedback from outcomes into scoring weights |

## Changes

### 1. Record Negative Signals (bot.py)

When Marc dismisses or snoozes a draft, record it:

```python
# In handle_draft_callback, elif action == "dismiss":
analytics.record_outcome(
    action_type=draft["type"],
    target=prospect_name,
    sent_at=today,
    response_type="dismissed",  # Negative signal
)

# In handle_draft_callback, elif action == "snooze":
analytics.record_outcome(
    action_type=draft["type"],
    target=prospect_name,
    sent_at=today,
    response_type="snoozed",  # Weak negative signal
)
```

### 2. Enrich Learning Context (analytics.py)

Replace the shallow `get_learning_context()` with a richer analysis:

```python
def get_learning_context():
    """Generate learning context from outcome data for prompt injection."""
    # Existing: aggregate response rates by action_type
    # NEW: Add these dimensions:

    # 1. Approval rate — what % of drafts does Marc approve vs dismiss?
    #    High dismiss rate = drafts aren't matching his expectations

    # 2. Response rate by prospect priority (Hot/Warm/Cool)
    #    Helps calibrate outreach intensity

    # 3. Best-performing draft types
    #    Which action_types get the most positive responses?

    # 4. Time-of-day patterns
    #    When do approved emails get the most opens/clicks?

    # 5. Win/loss integration
    #    Which products have the highest win rate? Inform scoring.
```

Output format (injected into prompts):

```
LEARNING CONTEXT:
- Draft approval rate: 78% (22% dismissed — drafts may be too formal)
- Best performing: follow_up emails (45% response rate)
- Weakest: nurture touches (12% response rate — consider shorter, more personal)
- Hot prospects respond 3x more than Warm — prioritize accordingly
- Emails sent before 10 AM get 2x more opens
- Top converting products: RRSP (42% win rate), Life Insurance (38%)
- Least converting: Disability (18% win rate — may need different approach)
```

### 3. Inject Learning Into All Prompts

Currently `learning_context` only goes into the morning briefing. Add it to:

- **follow_up.py** `FOLLOW_UP_SYSTEM_PROMPT` — so drafts adapt to what's working
- **nurture.py** `generate_touch()` prompt — so nurture messages improve
- **scoring.py** — adjust stage probabilities based on actual win rates (not hardcoded)

Implementation for follow_up.py:
```python
def generate_follow_up_draft(prospect_name, activity_summary, ...):
    import analytics
    learning = analytics.get_learning_context()

    # Inject learning into the follow-up prompt
    prompt = FOLLOW_UP_SYSTEM_PROMPT + f"\n\nLEARNING FROM PAST PERFORMANCE:\n{learning}"
```

### 4. Adaptive Scoring (scoring.py)

Replace hardcoded `STAGE_PROBABILITY` with data-driven probabilities:

```python
def get_stage_probabilities():
    """Calculate stage probabilities from actual win/loss data."""
    # Query win_loss_log for conversion rates by stage
    # Fall back to hardcoded defaults if insufficient data (<20 data points)
    # Update weekly via the nightly autonomous run
```

Add outcome-informed scoring factors:
- Products with higher win rates get a scoring boost
- Prospects similar to past winners (by source, product, priority) score higher
- Time-since-last-contact decay should match actual response patterns, not assumptions

### 5. Weekly Self-Tuning Report

Add a new function to analytics.py that the autonomous nightly run calls weekly:

```python
def generate_self_tuning_report():
    """Analyze all outcome data and generate recommendations for system tuning."""
    # Compare current prompt performance vs last month
    # Identify specific improvements to make
    # Save to logs/autonomous/tuning/YYYY-MM-DD.md
```

This report is consumed by the nightly Claude Code run, which can then make actual changes to prompts and parameters.

## Files Modified

| File | Change |
|---|---|
| `analytics.py` | Enrich `get_learning_context()`, add `generate_self_tuning_report()` |
| `bot.py` | Record dismiss/snooze as negative outcomes |
| `follow_up.py` | Inject learning context into draft generation prompt |
| `nurture.py` | Inject learning context into touch generation prompt |
| `scoring.py` | Add `get_stage_probabilities()` that reads from win_loss_log |
| `AUTONOMOUS.md` | Add weekly self-tuning task |

## Data Requirements

- Needs ~20+ approved drafts before learning context becomes meaningful
- Needs ~10+ win/loss records per product before adaptive scoring activates
- Falls back to hardcoded defaults when data is insufficient
- All thresholds are configurable

## Constraints

- No custom ML models — all learning is prompt-driven and rule-based
- No external data sources — uses only what's in the SQLite database
- Changes must be backward-compatible — the bot works fine with empty outcome data
- The nightly autonomous run handles the actual tuning — the bot itself just collects data and uses the learning context
