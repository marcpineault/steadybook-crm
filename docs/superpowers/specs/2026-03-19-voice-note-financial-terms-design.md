# Voice Note Financial Terms — Design Spec
**Date:** 2026-03-19
**Status:** Approved

## Problem

The voice note processing system transcribes and extracts prospect data correctly in general, but fails to understand three financially significant terms:

- **AUM** (Assets Under Management) — total investment portfolio Marc manages for a client
- **Insurance premium** — what the client pays monthly/annually for their policy
- **Insurance commission** — what Marc earns on a policy (his revenue)

Symptoms: dollar amounts for these are ignored, the AI confuses one for another in ambiguous speech, and the DB fields `aum` and `revenue` are never populated from voice notes. Memory facts for these values are missed or misclassified.

## Goal

Marc speaks naturally into a voice note ("she has $400K in investments, her premium is $180 a month, I'll earn about $2,400 commission on this") and the system captures all of it — in the right DB fields and in the client's memory profile — with zero extra steps.

## Approach: Prompt Enhancement + Domain Glossary (Approach B)

Add a Co-operators-specific domain glossary to both AI prompts so the system understands these terms in Marc's context. Wire the new extracted fields into the DB write logic.

## Changes

### 1. `voice_handler.py` — `VOICE_EXTRACTION_SYSTEM_PROMPT`

**Add a domain glossary block** near the top of the prompt:

> AUM (Assets Under Management) = total investment/wealth portfolio Marc manages for this client.
> Insurance premium = what the client pays monthly or annually for their policy.
> Insurance commission = what Marc earns on this policy (his revenue from the sale).

**Add three new fields** to the JSON schema:

```json
"aum": 450000,
"insurance_premium": 180,
"insurance_commission": 2400
```

- All three are nullable (null if not mentioned in the voice note)
- Dollar amounts extracted as plain numbers (e.g. "four hundred K" → 400000, "$180/month" → 180)

**Wire into DB write** in `extract_and_update()`:

- `aum` → `db.update_prospect(name, {"aum": value})`
- `insurance_commission` → `db.update_prospect(name, {"revenue": value})`
- `insurance_premium` → appended to notes as "Premium: $X/month" (no dedicated DB column)

Only update the DB field if a value was extracted (not null). Existing values are not overwritten if the voice note doesn't mention them.

### 2. `memory_engine.py` — `EXTRACTION_SYSTEM_PROMPT`

**Update the `financial_context` category description** from:

> risk tolerance, income bracket, assets, debts, retirement timeline, coverage gaps

To:

> risk tolerance, income bracket, AUM (total investments Marc manages for this client), insurance premium (what the client pays for their policy), insurance commission (what Marc earns on this policy), assets, debts, retirement timeline, coverage gaps

This ensures the memory engine stores descriptive facts like:
- *"Has $450K AUM with RBC, looking to transfer to Co-operators"*
- *"Life insurance premium is $180/month"*
- *"Commission on this policy approximately $2,400"*

These facts surface in meeting prep briefs and client profiles automatically.

## What Does NOT Change

- No new DB columns (existing `aum` and `revenue` fields are sufficient)
- No new parsing logic (`_parse_numeric()` already handles string-to-float conversion)
- No changes to the transcription pipeline, scoring, or follow-up generation
- No changes to the memory engine's fact storage or category structure

## Out of Scope

- Reporting or totaling commission across all clients
- A dedicated `insurance_premium` DB column (notes is sufficient for now)
- Detecting premium frequency (monthly vs annual) — raw amount is enough

## Success Criteria

- Marc says "she has $400K in AUM" → `aum` field on prospect record = 400000
- Marc says "his premium is $180/month" → notes contain "Premium: $180/month"
- Marc says "I'll earn $2,400 commission" → `revenue` field = 2400
- All three values appear as `financial_context` facts in the client memory profile
- No regressions on existing voice note extraction (names, product, stage, priority)
