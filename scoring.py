"""
Lead scoring engine for Calm Money Pipeline Bot.

Scores prospects 0-100 based on deal size, urgency, stage probability,
and priority. Also provides cross-sell suggestions, referral candidates,
and a ranked call list.
"""

import re
import logging
from datetime import date, datetime

import db

logger = logging.getLogger(__name__)

# ── Stage probability map ──

STAGE_PROBABILITY = {
    "New Lead": 0.05,
    "Contacted": 0.10,
    "Discovery Call": 0.20,
    "Needs Analysis": 0.35,
    "Plan Presentation": 0.50,
    "Proposal Sent": 0.65,
    "Negotiation": 0.80,
    "Nurture": 0.05,
}

# ── Cross-sell matrix ──

CROSS_SELL_MATRIX = {
    "Life Insurance": ["Disability Insurance", "Critical Illness", "Wealth Management"],
    "Wealth Management": ["Life Insurance", "Estate Planning"],
    "Disability Insurance": ["Critical Illness", "Life Insurance"],
    "Critical Illness": ["Disability Insurance", "Life Insurance"],
    "Group Benefits": ["Life Insurance", "Wealth Management"],
    "Estate Planning": ["Life Insurance", "Wealth Management"],
}

# ── Stale action recommendations ──

_STALE_ACTIONS = {
    "New Lead": "Try a different channel — call instead of email",
    "Contacted": "Try a different channel — call instead of email",
    "Discovery Call": "Send a relevant article or rate comparison to re-engage",
    "Needs Analysis": "Offer to run fresh numbers — get a new quote",
    "Plan Presentation": "Ask if they had time to review, offer a quick recap call",
    "Proposal Sent": "Follow up with urgency — rates may change",
    "Negotiation": "Direct ask — what's holding you back?",
    "Nurture": "Share a market update or relevant content piece",
}

# ── Standard action recommendations ──

_STANDARD_ACTIONS = {
    "New Lead": "Make first contact — introduce yourself and book a discovery call",
    "Contacted": "Follow up on initial contact",
    "Discovery Call": "Prepare questions and book the discovery call",
    "Needs Analysis": "Complete the needs analysis and prepare recommendations",
    "Plan Presentation": "Present the financial plan",
    "Proposal Sent": "Follow up on the proposal",
    "Negotiation": "Address objections and close the deal",
    "Nurture": "Stay in touch with valuable content",
}


# ── Helper: parse a date string ──

def _parse_date(val):
    """Parse a YYYY-MM-DD string to a date object. Returns None on failure."""
    if not val:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        return datetime.strptime(str(val).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── FYC calculation ──

def _calc_fyc(premium, product):
    """Calculate First Year Commission from premium and product term.

    Term 20/25/30: premium * 11.11 * 0.5
    Term 10/15:    premium * 11.11 * 0.4
    """
    if premium is None:
        return 0.0
    try:
        prem = float(premium)
    except (ValueError, TypeError):
        return 0.0
    if prem <= 0:
        return 0.0
    term_match = re.search(r"(\d+)", str(product or ""))
    if not term_match:
        return 0.0
    term = int(term_match.group(1))
    if term in (20, 25, 30):
        return prem * 11.11 * 0.5
    elif term in (10, 15):
        return prem * 11.11 * 0.4
    return 0.0


# ── Stage action recommendation ──

def _get_stage_action(prospect, today, avg_stage_days=None):
    """Return a recommended next action based on stage and staleness."""
    stage = prospect.get("stage", "")
    first_contact = _parse_date(prospect.get("first_contact"))

    days_in_stage = 0
    if first_contact:
        days_in_stage = (today - first_contact).days

    # Determine if stale
    is_stale = days_in_stage > 14
    if avg_stage_days and stage in avg_stage_days:
        avg = avg_stage_days[stage]
        if avg > 0 and days_in_stage > avg * 1.5:
            is_stale = True

    if is_stale:
        return _STALE_ACTIONS.get(stage, "Review this prospect and decide on next steps")
    return _STANDARD_ACTIONS.get(stage, "Review this prospect and decide on next steps")


# ── Main scoring function ──

def score_prospect(prospect, avg_stage_days=None):
    """Score a prospect 0-100 based on four weighted factors.

    Args:
        prospect: dict with prospect fields from SQLite
        avg_stage_days: optional dict of stage -> average days in stage

    Returns:
        dict with: score, reasons, action, deal_size_score, urgency_score,
                   stage_score, priority_score
    """
    today = date.today()
    reasons = []

    # ── Deal Size (40% weight) ──
    aum = float(prospect.get("aum") or 0)
    revenue = float(prospect.get("revenue") or 0)
    product = prospect.get("product", "")
    fyc = _calc_fyc(revenue, product)

    aum_norm = min(aum / 1_000_000, 1.0) if aum > 0 else 0.0
    rev_norm = min(revenue / 10_000, 1.0) if revenue > 0 else 0.0
    fyc_norm = min(fyc / 5_000, 1.0) if fyc > 0 else 0.0
    deal_size_score = max(aum_norm, rev_norm, fyc_norm) * 40

    if aum >= 500_000:
        reasons.append(f"High AUM (${aum:,.0f})")
    elif aum >= 100_000:
        reasons.append(f"Solid AUM (${aum:,.0f})")
    if fyc >= 2_000:
        reasons.append(f"Strong FYC potential (${fyc:,.0f})")

    # ── Urgency (30% weight) ──
    next_followup = _parse_date(prospect.get("next_followup"))
    first_contact = _parse_date(prospect.get("first_contact"))

    urgency_score = 0.0
    if next_followup:
        days_overdue = (today - next_followup).days
        if days_overdue > 0:
            urgency_score = min(days_overdue / 14, 1.0) * 30
            reasons.append(f"Follow-up overdue by {days_overdue} day{'s' if days_overdue != 1 else ''}")

    # Stage velocity check
    stage = prospect.get("stage", "")
    if first_contact and avg_stage_days and stage in avg_stage_days:
        days_in_stage = (today - first_contact).days
        avg = avg_stage_days[stage]
        if avg > 0 and days_in_stage > avg * 1.5:
            urgency_score = max(urgency_score, 20)
            reasons.append(f"Stale in {stage} ({days_in_stage}d vs {avg:.0f}d avg)")

    # ── Stage Probability (20% weight) ──
    prob = STAGE_PROBABILITY.get(stage, 0.0)
    stage_score = prob * 20

    if prob >= 0.5:
        reasons.append(f"Late stage ({stage}, {prob:.0%} probability)")

    # ── Priority (10% weight) ──
    priority = (prospect.get("priority") or "").strip().lower()
    priority_map = {"hot": 10, "warm": 6, "cold": 2}
    priority_score = priority_map.get(priority, 3)

    if priority == "hot":
        reasons.append("Hot priority")

    # ── Total ──
    raw_score = deal_size_score + urgency_score + stage_score + priority_score
    total_score = int(min(raw_score, 100))

    # Get action recommendation
    action = _get_stage_action(prospect, today, avg_stage_days)

    return {
        "score": total_score,
        "reasons": reasons,
        "action": action,
        "deal_size_score": round(deal_size_score, 1),
        "urgency_score": round(urgency_score, 1),
        "stage_score": round(stage_score, 1),
        "priority_score": round(priority_score, 1),
    }


# ── Cross-sell suggestions ──

def get_cross_sell_suggestions(product):
    """Return cross-sell product suggestions based on current product.

    Uses case-insensitive partial matching against the cross-sell matrix keys.
    """
    if not product:
        return []
    product_lower = product.lower()
    for key, suggestions in CROSS_SELL_MATRIX.items():
        if key.lower() in product_lower or product_lower in key.lower():
            return suggestions
    return []


# ── Referral candidates ──

def get_referral_candidates():
    """Find Closed-Won prospects eligible for referral nudges.

    Returns list of dicts with: name, product, days_since_close, nudge_type.
    First nudge: 14-30 days since first_contact.
    Second nudge: 90-120 days since first_contact.
    Skips prospects with 'referral' in notes.
    """
    prospects = db.read_pipeline()
    today = date.today()
    candidates = []

    for p in prospects:
        stage = (p.get("stage") or "").strip()
        if stage != "Closed-Won":
            continue

        notes = (p.get("notes") or "").lower()
        if "referral" in notes:
            continue

        first_contact = _parse_date(p.get("first_contact"))
        if not first_contact:
            continue

        days_since = (today - first_contact).days

        nudge_type = None
        if 14 <= days_since <= 30:
            nudge_type = "first"
        elif 90 <= days_since <= 120:
            nudge_type = "second"

        if nudge_type:
            candidates.append({
                "name": p.get("name", ""),
                "product": p.get("product", ""),
                "days_since_close": days_since,
                "nudge_type": nudge_type,
            })

    return candidates


# ── Ranked call list ──

def get_ranked_call_list(limit=10):
    """Get a scored and ranked list of active prospects for today's calls.

    Args:
        limit: max number of prospects to return (default 10)

    Returns:
        List of dicts (prospect fields + score fields), sorted by score desc.
    """
    prospects = db.read_pipeline()
    today = date.today()

    # Filter to active prospects
    inactive_stages = {"Closed-Won", "Closed-Lost", ""}
    active = [p for p in prospects if (p.get("stage") or "").strip() not in inactive_stages]

    if not active:
        return []

    # Calculate average days per stage
    avg_stage_days = {}
    stage_days_accum = {}

    for p in active:
        stage = (p.get("stage") or "").strip()
        first_contact = _parse_date(p.get("first_contact"))
        if not stage or not first_contact:
            continue
        days = (today - first_contact).days
        if stage not in stage_days_accum:
            stage_days_accum[stage] = []
        stage_days_accum[stage].append(days)

    for stage, days_list in stage_days_accum.items():
        avg_stage_days[stage] = sum(days_list) / len(days_list) if days_list else 0

    # Score each prospect
    results = []
    for p in active:
        score_data = score_prospect(p, avg_stage_days)
        merged = {**p, **score_data}
        results.append(merged)

    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:limit]
