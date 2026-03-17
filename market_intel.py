"""Market intelligence module — pre-loaded calendars and seasonal context.

Provides market event awareness for content generation and morning briefings:
- Bank of Canada rate decision dates
- Tax deadline calendar (RRSP, TFSA, filing)
- Seasonal financial planning topics
- Custom events (product updates, local news)
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

# Pre-loaded calendar events (static, published annually)
DEFAULT_EVENTS = [
    # Bank of Canada rate decisions (8 per year, 2026 dates)
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-01-29", "description": "Bank of Canada interest rate announcement — impacts mortgage and investment conversations", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-03-12", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-04-15", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-06-03", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-07-15", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-09-09", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-10-28", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},
    {"event_type": "rate_decision", "title": "BoC Rate Decision", "date": "2026-12-09", "description": "Bank of Canada interest rate announcement", "relevance_products": "Wealth Management", "recurring": 0},

    # Tax deadlines
    {"event_type": "tax_deadline", "title": "RRSP Contribution Deadline", "date": "2026-03-02", "description": "Last day to contribute to RRSP for 2025 tax year", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "tax_deadline", "title": "Tax Filing Deadline", "date": "2026-04-30", "description": "Personal income tax filing deadline", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "tax_deadline", "title": "TFSA Contribution Room Resets", "date": "2026-01-01", "description": "New TFSA contribution room available for 2026", "relevance_products": "Wealth Management", "recurring": 1},

    # Seasonal topics
    {"event_type": "seasonal", "title": "RRSP Season Starts", "date": "2026-01-15", "description": "Prime time for retirement savings conversations — RRSP season runs Jan-Mar", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "seasonal", "title": "Tax Season Starts", "date": "2026-03-15", "description": "Tax preparation season — good time for financial review conversations", "relevance_products": "Wealth Management", "recurring": 1},
    {"event_type": "seasonal", "title": "Spring Home Insurance Reviews", "date": "2026-04-01", "description": "Spring season — homeowners reviewing coverage after winter", "relevance_products": "Home Insurance", "recurring": 1},
    {"event_type": "seasonal", "title": "Back-to-School Life Insurance", "date": "2026-08-15", "description": "Back-to-school season — families thinking about protection and education savings", "relevance_products": "Life Insurance,Wealth Management", "recurring": 1},
    {"event_type": "seasonal", "title": "Year-End Financial Planning", "date": "2026-10-15", "description": "Year-end planning season — tax optimization, RRSP top-ups, coverage reviews", "relevance_products": "Wealth Management,Life Insurance", "recurring": 1},
    {"event_type": "seasonal", "title": "Winter Driving Safety", "date": "2026-11-15", "description": "Winter tire season — good time for auto insurance conversations", "relevance_products": "Auto Insurance", "recurring": 1},
]

# Seasonal context by month (always available, no DB needed)
SEASONAL_CONTEXT = {
    1: "RRSP season (Jan-Mar). New year financial resolutions. TFSA room reset.",
    2: "RRSP season continues. Valentine's — couples financial planning angle.",
    3: "RRSP deadline approaching. Tax season starting. Spring forward.",
    4: "Tax filing deadline Apr 30. Spring home insurance reviews. Moving season starts.",
    5: "Post-tax season. Summer planning. Home & auto coverage reviews.",
    6: "Summer travel insurance. Mid-year financial check-ups. New grads entering workforce.",
    7: "Summer vacations. Travel insurance. Mid-year portfolio reviews.",
    8: "Back-to-school. Life insurance for families. Education savings (RESP).",
    9: "Fall renewal season. Back to routine. Business insurance reviews.",
    10: "Year-end planning starts. RRSP catch-up. Coverage gap reviews.",
    11: "Winter tire season. Auto insurance. Year-end tax moves.",
    12: "Year-end wrap-up. Holiday insurance considerations. New year planning preview.",
}


def seed_default_calendar():
    """Seed the market_calendar table with pre-loaded events. Idempotent."""
    with db.get_db() as conn:
        for event in DEFAULT_EVENTS:
            existing = conn.execute(
                "SELECT id FROM market_calendar WHERE title = ? AND date = ?",
                (event["title"], event["date"]),
            ).fetchone()
            if not existing:
                conn.execute(
                    """INSERT INTO market_calendar (event_type, title, date, description, relevance_products, recurring)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (event["event_type"], event["title"], event["date"], event["description"], event["relevance_products"], event["recurring"]),
                )


def get_upcoming_events(days_ahead=14):
    """Get market calendar events in the next N days."""
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM market_calendar WHERE date >= ? AND date <= ? ORDER BY date ASC",
            (today, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]


def get_seasonal_context():
    """Get the current month's seasonal context string."""
    month = datetime.now().month
    return SEASONAL_CONTEXT.get(month, "No specific seasonal context.")


def get_content_angles(days_ahead=14):
    """Get content angle suggestions based on upcoming events and seasonal context.

    Returns list of dicts with: topic, angle, relevance, source.
    """
    angles = []

    # Upcoming events
    events = get_upcoming_events(days_ahead=days_ahead)
    for event in events:
        angles.append({
            "topic": event["title"],
            "angle": event["description"],
            "relevance": event.get("relevance_products", ""),
            "source": f"market_calendar ({event['event_type']})",
        })

    # Seasonal context
    seasonal = get_seasonal_context()
    if seasonal:
        angles.append({
            "topic": "Seasonal relevance",
            "angle": seasonal,
            "relevance": "all",
            "source": "seasonal_calendar",
        })

    return angles


def add_event(event_type, title, date, description="", relevance_products=""):
    """Add a custom event to the market calendar."""
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO market_calendar (event_type, title, date, description, relevance_products, recurring)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (event_type, title, date, description, relevance_products),
        )
    logger.info("Added market event: %s on %s", title, date)


def format_for_briefing(days_ahead=7):
    """Format upcoming market events for inclusion in the morning briefing.

    Includes prospect relevance — which prospects each event is relevant to.
    Returns a string summary, or empty string if no events.
    """
    events = get_upcoming_events(days_ahead=days_ahead)
    if not events:
        return ""

    # Cross-reference events with prospect pipeline for relevance
    try:
        prospects = db.read_pipeline()
        active_prospects = [p for p in prospects if p.get("stage") not in ("Closed-Won", "Closed-Lost", "")]
    except Exception:
        active_prospects = []

    lines = ["UPCOMING MARKET EVENTS:"]
    for event in events[:5]:
        event_date = datetime.strptime(event["date"], "%Y-%m-%d").date()
        days_until = (event_date - datetime.now().date()).days
        timing = f"in {days_until} days" if days_until > 1 else ("tomorrow" if days_until == 1 else "today")
        lines.append(f"  - {event['title']} ({timing}): {event['description'][:100]}")

        # Find relevant prospects by matching product interest
        relevance_products = [p.strip().lower() for p in (event.get("relevance_products") or "").split(",") if p.strip()]
        if relevance_products and active_prospects:
            relevant_names = []
            for p in active_prospects:
                prospect_product = (p.get("product") or "").lower()
                if any(rp in prospect_product or prospect_product in rp for rp in relevance_products):
                    relevant_names.append(p["name"])
            if relevant_names:
                lines.append(f"    Relevant prospects: {', '.join(relevant_names[:5])}")

    seasonal = get_seasonal_context()
    if seasonal:
        lines.append(f"  Season: {seasonal}")

    return "\n".join(lines)
