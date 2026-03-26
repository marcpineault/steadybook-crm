"""
Referral tracking engine.
Records who referred whom, queries top referrers, and generates
referral ask messages at 14 days and 90 days post-close.
"""

import logging
from datetime import datetime, timedelta

import db

logger = logging.getLogger(__name__)

# Tolerance window in days — ask within ±2 days of target
ASK_TOLERANCE_DAYS = 2


def record_referral(referrer_id: int | None, referred_id: int, notes: str = "") -> None:
    """Record that referrer_id referred referred_id."""
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO referrals (referrer_prospect_id, referred_prospect_id, notes)
               VALUES (?, ?, ?)""",
            (referrer_id, referred_id, notes),
        )
    logger.info("Referral recorded: referrer=%s → referred=%d", referrer_id, referred_id)


def get_top_referrers(limit: int = 10) -> list[dict]:
    """Return top referrers by number of referrals sent."""
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT p.id, p.name, COUNT(r.id) as referral_count
            FROM prospects p
            JOIN referrals r ON r.referrer_prospect_id = p.id
            GROUP BY p.id, p.name
            ORDER BY referral_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_referral_source(prospect_id: int) -> dict | None:
    """Return the referral record for a prospect, if they were referred."""
    with db.get_db() as conn:
        row = conn.execute("""
            SELECT r.*, p.name as referrer_name
            FROM referrals r
            LEFT JOIN prospects p ON p.id = r.referrer_prospect_id
            WHERE r.referred_prospect_id = ?
            LIMIT 1
        """, (prospect_id,)).fetchone()
    return dict(row) if row else None


def should_send_referral_ask(prospect: dict, ask_day: int) -> bool:
    """
    Return True if it's time to ask this prospect for referrals.
    ask_day: 14 or 90 (days after closing)
    Checks if closed_date is within ±ASK_TOLERANCE_DAYS of ask_day.
    """
    closed_date_str = prospect.get("closed_date") or prospect.get("updated_at", "")
    if not closed_date_str:
        return False
    try:
        closed = datetime.strptime(closed_date_str[:10], "%Y-%m-%d")
        days_since = (datetime.now() - closed).days
        return abs(days_since - ask_day) <= ASK_TOLERANCE_DAYS
    except (ValueError, TypeError):
        return False


def format_referral_ask_message(prospect: dict) -> str:
    """Format a referral ask message personalized for the prospect."""
    name = prospect.get("name", "there")
    return (
        f"Hi {name} — it's been a while since we worked together and I hope everything "
        f"is going well! If you know anyone who might benefit from a conversation about "
        f"their financial plan or insurance needs, I'd love an introduction. "
        f"A referral from a happy client like you means the world. Thank you!"
    )


def check_referral_asks(tenant_id: int = 1) -> None:
    """
    Scan all Closed Won prospects and create tasks for referral asks
    at 14 days and 90 days post-close. Called by APScheduler daily.
    """
    try:
        with db.get_db() as conn:
            rows = conn.execute("""
                SELECT * FROM prospects WHERE stage = 'Closed Won'
            """).fetchall()

        for row in rows:
            prospect = dict(row)
            for ask_day in (14, 90):
                if should_send_referral_ask(prospect, ask_day):
                    ask_tag = f"referral_ask_{ask_day}d_sent"
                    tags = db.get_tags(prospect["id"])
                    if ask_tag in tags:
                        continue  # Already sent this ask
                    try:
                        due_date = datetime.now().strftime("%Y-%m-%d")
                        msg = format_referral_ask_message(prospect)
                        db.add_task(
                            {
                                "title": f"Ask {prospect.get('name')} for referrals ({ask_day}d)",
                                "prospect": prospect.get("name", ""),
                                "due_date": due_date,
                                "notes": msg,
                                "created_by": "referral_engine",
                            },
                            tenant_id=tenant_id,
                        )
                        db.apply_tag(prospect["id"], ask_tag)
                        logger.info("Referral ask task created for %s (%dd)", prospect.get("name"), ask_day)
                    except Exception as e:
                        logger.error("Failed to create referral ask for %s: %s", prospect.get("name"), e)
    except Exception as e:
        logger.error("check_referral_asks failed: %s", e)
