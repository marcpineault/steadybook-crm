"""
Scheduler module for Calm Money Pipeline Bot.

Provides:
- Morning briefing at 8:00 AM ET daily
- Auto-nag system every 2 hours (9AM-5PM ET)

Usage:
    from scheduler import start_scheduler
    start_scheduler(telegram_app)
"""

import json
import logging
import os
from datetime import date, datetime, timedelta
import db
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
if DATA_DIR:
    NAG_STATE_FILE = os.path.join(DATA_DIR, "nag_state.json")
else:
    NAG_STATE_FILE = "nag_state.json"
ET = pytz.timezone("America/Toronto")

INSURANCE_DAILY_LIMIT = 5

# ── Bot reference ──

_bot = None


# ── Nag state persistence ──

def _load_nag_state() -> dict:
    if os.path.exists(NAG_STATE_FILE):
        try:
            with open(NAG_STATE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def _save_nag_state(state: dict):
    try:
        with open(NAG_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        logger.error(f"Failed to save nag state: {e}")


def _can_nag(state: dict, prospect_name: str, nag_type: str) -> bool:
    """Check if we can nag about this prospect (not within 24 hours of last nag)."""
    key = f"{prospect_name}::{nag_type}"
    last = state.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
        return datetime.now() - last_dt > timedelta(hours=24)
    except (ValueError, TypeError):
        return True


def _mark_nagged(state: dict, prospect_name: str, nag_type: str):
    key = f"{prospect_name}::{nag_type}"
    state[key] = datetime.now().isoformat()


# ── Data readers ──

def _parse_date(val) -> date | None:
    """Parse a date value (could be None, datetime, date, or string)."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        return datetime.strptime(str(val).strip().split(" ")[0], "%Y-%m-%d").date()
    except (ValueError, IndexError):
        pass
    # Try other common formats
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(str(val).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _read_prospects():
    """Read pipeline prospects."""
    prospects = db.read_pipeline()
    # Add parsed date fields for compatibility
    for p in prospects:
        p["_next_followup_date"] = _parse_date(p.get("next_followup"))
        p["_first_contact_date"] = _parse_date(p.get("first_contact"))
    return prospects


def _read_meetings_today():
    """Read meetings for today."""
    today_str = date.today().strftime("%Y-%m-%d")
    return [m for m in db.read_meetings() if (m.get("date") or "").startswith(today_str)]


def _read_meetings_tomorrow():
    """Read meetings for tomorrow."""
    tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return [m for m in db.read_meetings() if (m.get("date") or "").startswith(tomorrow_str)]


def _read_insurance_calls():
    """Read insurance book entries that need calling today."""
    today = date.today()
    calls = []
    for entry in db.read_insurance_book():
        status = (entry.get("status") or "").strip().lower()
        eligible = status in ("not called", "")
        if not eligible and entry.get("retry_date"):
            try:
                rd = datetime.strptime(entry["retry_date"].split(" ")[0], "%Y-%m-%d").date()
                eligible = rd <= today
            except (ValueError, IndexError):
                pass
        if eligible:
            calls.append(entry)
            if len(calls) >= INSURANCE_DAILY_LIMIT:
                break
    return calls


# ── Morning Briefing ──

async def morning_briefing():
    """Send the daily Money Moves briefing at 8:00 AM ET."""
    if not _bot or not CHAT_ID:
        return

    try:
        await _morning_briefing_inner()
    except Exception as e:
        logger.error(f"Morning briefing failed: {e}")
        try:
            await _bot.send_message(chat_id=CHAT_ID, text=f"Morning briefing error — check logs. ({str(e)[:100]})")
        except Exception:
            pass


async def _morning_briefing_inner():
    import scoring

    today = date.today()
    lines = [f"MONEY MOVES — {today.strftime('%A, %B %d')}", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]

    # Top 5 ranked call list
    ranked = scoring.get_ranked_call_list(5)
    if ranked:
        lines.append("TOP CALLS TODAY:")
        for i, p in enumerate(ranked, 1):
            reasons_str = " | ".join(p.get("reasons", [])[:2])
            lines.append(f"  {i}. {p['name']} (score: {p['score']})")
            if reasons_str:
                lines.append(f"     Why: {reasons_str}")
            lines.append(f"     Do: {p.get('action', 'Follow up')}")
        lines.append("")

    # Cross-sell opportunities on recent wins (last 30 days)
    prospects = db.read_pipeline()
    active = [p for p in prospects if p.get("stage") not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p.get("stage") == "Closed-Won"]

    cross_sell_lines = []
    for p in won:
        fc = p.get("first_contact", "")
        if fc and fc != "None":
            try:
                close_date = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
                if (today - close_date).days <= 30:
                    suggestions = scoring.get_cross_sell_suggestions(p.get("product", ""))
                    if suggestions:
                        cross_sell_lines.append(f"  {p['name']} has {p.get('product', '?')} — suggest {', '.join(suggestions[:2])}")
            except (ValueError, IndexError):
                pass

    if cross_sell_lines:
        lines.append("CROSS-SELL OPPORTUNITIES:")
        lines.extend(cross_sell_lines)
        lines.append("")

    # Referral nudges
    referral_candidates = scoring.get_referral_candidates()
    if referral_candidates:
        lines.append("REFERRAL OPPORTUNITIES:")
        for c in referral_candidates:
            lines.append(f"  Ask {c['name']} for a referral ({c['days_since_close']}d since close)")
        lines.append("")

    # Meetings today
    meetings = _read_meetings_today()
    if meetings:
        lines.append(f"MEETINGS TODAY ({len(meetings)}):")
        for m in meetings:
            lines.append(f"  {m.get('time', '?')} — {m.get('prospect', '?')} ({m.get('type', '?')})")
        lines.append("")

    # Pipeline snapshot
    total_aum = sum(float(p.get("aum") or 0) for p in active)
    total_rev = sum(float(p.get("revenue") or 0) for p in active)
    hot_count = len([p for p in active if (p.get("priority") or "").lower() == "hot"])
    lines.append("PIPELINE:")
    lines.append(f"  Active: {len(active)} | AUM: ${total_aum:,.0f} | Premium: ${total_rev:,.0f} | Hot: {hot_count}")

    msg = "\n".join(lines)
    await _bot.send_message(chat_id=CHAT_ID, text=msg)
    logger.info("Morning briefing (Money Moves) sent.")


# ── Auto-Nag System ──

async def auto_nag():
    """Check for items that need attention and send nag messages."""
    if not _bot or not CHAT_ID:
        logger.warning("Bot or CHAT_ID not configured, skipping auto-nag.")
        return

    today = date.today()
    nag_state = _load_nag_state()
    alerts = []

    try:
        prospects = _read_prospects()
    except Exception as e:
        logger.error(f"Error reading pipeline for nag: {e}")
        return

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]

    for p in active:
        name = p["name"]
        fu = p["_next_followup_date"]
        fc = p["_first_contact_date"]
        stage = p["stage"]
        priority = (p.get("priority") or "").lower()

        # 1. No activity for 7+ days
        ref_date = fu or fc
        if ref_date and (today - ref_date).days >= 7:
            if _can_nag(nag_state, name, "stale"):
                days_idle = (today - ref_date).days
                alerts.append(f"  STALE: {name} — no activity for {days_idle} days")
                _mark_nagged(nag_state, name, "stale")

        # 2. Follow-up 2+ days overdue
        if fu and (today - fu).days >= 2:
            if _can_nag(nag_state, name, "overdue"):
                days_late = (today - fu).days
                alerts.append(f"  OVERDUE: {name} — follow-up is {days_late} days late")
                _mark_nagged(nag_state, name, "overdue")

        # 3. Hot lead stuck in New Lead/Contacted for 5+ days
        if priority == "hot" and stage in ("New Lead", "Contacted"):
            if fc and (today - fc).days >= 5:
                if _can_nag(nag_state, name, "hot_stuck"):
                    alerts.append(f"  HOT STUCK: {name} — hot lead in '{stage}' for {(today - fc).days} days")
                    _mark_nagged(nag_state, name, "hot_stuck")

    # 4. Meeting tomorrow prep reminder
    try:
        tomorrow_meetings = _read_meetings_tomorrow()
        for m in tomorrow_meetings:
            mkey = f"{m['prospect']}_{m['time']}"
            if _can_nag(nag_state, mkey, "meeting_prep"):
                prep = f" — Prep: {m['prep_notes']}" if m["prep_notes"] else ""
                alerts.append(f"  MEETING TOMORROW: {m['prospect']} at {m['time']} ({m['type']}){prep}")
                _mark_nagged(nag_state, mkey, "meeting_prep")
    except Exception as e:
        logger.warning(f"Could not check tomorrow's meetings for nag: {e}")

    _save_nag_state(nag_state)

    if alerts:
        header = f"Hey Marc, heads up ({len(alerts)} item{'s' if len(alerts) != 1 else ''}):\n"
        msg = header + "\n".join(alerts)
        await _bot.send_message(chat_id=CHAT_ID, text=msg)
        logger.info(f"Auto-nag sent with {len(alerts)} alerts.")


# ── Weekly Performance Report ──

async def weekly_report():
    """Send weekly performance report Sunday at 7 PM ET."""
    if not _bot or not CHAT_ID:
        return

    try:
        await _weekly_report_inner()
    except Exception as e:
        logger.error(f"Weekly report failed: {e}")
        try:
            await _bot.send_message(chat_id=CHAT_ID, text=f"Weekly report error — check logs. ({str(e)[:100]})")
        except Exception:
            pass


async def _weekly_report_inner():
    today = date.today()
    week_start = today - timedelta(days=7)

    try:
        prospects = _read_prospects()
    except Exception as e:
        logger.error(f"Error reading pipeline for weekly report: {e}")
        return

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p["stage"] == "Closed-Won"]

    # Pipeline value
    total_aum = 0
    total_rev = 0
    for p in active:
        try:
            total_aum += float(p["aum"] or 0)
        except (ValueError, TypeError):
            pass
        try:
            total_rev += float(p["revenue"] or 0)
        except (ValueError, TypeError):
            pass

    won_rev = 0
    for p in won:
        try:
            won_rev += float(p["revenue"] or 0)
        except (ValueError, TypeError):
            pass

    hot_count = len([p for p in active if (p.get("priority") or "").lower() == "hot"])

    # Activity log this week
    raw_activities = []
    for a in db.read_activities():
        raw_activities.append((_parse_date(a.get("date")), (a.get("action") or "").lower()))

    raw_book = []
    for e in db.read_insurance_book():
        raw_book.append((_parse_date(e.get("last_called")), (e.get("status") or "").lower()))

    raw_wl = []
    for w in db.get_win_loss_stats():
        raw_wl.append((_parse_date(w.get("date")), (w.get("outcome") or "").lower()))

    # Compute stats
    week_activities = 0
    calls_made = 0
    emails_sent = 0
    meetings_held = 0
    for activity_date, action in raw_activities:
        if activity_date and activity_date >= week_start:
            week_activities += 1
            if "call" in action or "phone" in action:
                calls_made += 1
            if "email" in action:
                emails_sent += 1
            if "meeting" in action or "discovery" in action or "presentation" in action:
                meetings_held += 1

    book_calls_week = 0
    book_booked = 0
    for last_called, status in raw_book:
        if last_called and last_called >= week_start:
            book_calls_week += 1
            if status == "booked meeting":
                book_booked += 1

    wins_week = 0
    losses_week = 0
    for wl_date, outcome in raw_wl:
        if wl_date and wl_date >= week_start:
            if outcome in ("won", "closed-won"):
                wins_week += 1
            elif outcome in ("lost", "closed-lost"):
                losses_week += 1

    # Overdue count
    overdue_count = 0
    for p in active:
        fu = p["_next_followup_date"]
        if fu and fu < today:
            overdue_count += 1

    # Build the report
    lines = [
        f"WEEKLY REPORT — {week_start.strftime('%b %d')} to {today.strftime('%b %d')}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "ACTIVITY:",
        f"  Total touchpoints: {week_activities}",
        f"  Calls: {calls_made} | Emails: {emails_sent} | Meetings: {meetings_held}",
    ]

    if book_calls_week > 0:
        lines.append(f"  Book calls: {book_calls_week} | Booked: {book_booked}")

    lines.extend([
        "",
        "RESULTS:",
        f"  Deals won: {wins_week} | Lost: {losses_week}",
        f"  Won revenue: ${won_rev:,.0f}",
        "",
        "PIPELINE:",
        f"  Active: {len(active)} | Value: ${total_aum:,.0f} | Hot: {hot_count}",
        f"  Est. revenue: ${total_rev:,.0f}",
        f"  Overdue follow-ups: {overdue_count}",
        "",
        "MONDAY PRIORITIES:",
    ])

    # Top 3 priorities for Monday
    priorities = []
    # Overdue hot leads first
    for p in active:
        fu = p["_next_followup_date"]
        if (p.get("priority") or "").lower() == "hot" and fu and fu < today:
            priorities.append(f"  {len(priorities)+1}. Follow up with {p['name']} (Hot, {(today - fu).days}d overdue)")
    # Then other overdue
    for p in active:
        fu = p["_next_followup_date"]
        if (p.get("priority") or "").lower() != "hot" and fu and fu < today:
            priorities.append(f"  {len(priorities)+1}. Follow up with {p['name']} ({(today - fu).days}d overdue)")
        if len(priorities) >= 3:
            break

    if not priorities:
        priorities.append("  All caught up! Focus on prospecting.")

    lines.extend(priorities[:3])
    lines.append(f"\nKeep grinding, Marc. 💪")

    msg = "\n".join(lines)
    await _bot.send_message(chat_id=CHAT_ID, text=msg)
    logger.info("Weekly report sent.")


# ── Scheduler entry point ──

def start_scheduler(telegram_app, event_loop=None):
    """
    Start the scheduler with the Telegram application.

    Args:
        telegram_app: The python-telegram-bot Application object.
        event_loop: Optional asyncio event loop to use for async jobs.
    """
    global _bot
    _bot = telegram_app.bot

    if not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not set — scheduler will not send messages.")
        return

    kwargs = {"timezone": ET}
    if event_loop:
        kwargs["event_loop"] = event_loop
    scheduler = AsyncIOScheduler(**kwargs)

    # Morning briefing at 8:00 AM ET every day
    scheduler.add_job(
        morning_briefing,
        "cron",
        hour=8,
        minute=0,
        id="morning_briefing",
        name="Daily Morning Briefing",
    )

    # Auto-nag every 2 hours from 9 AM to 5 PM ET
    scheduler.add_job(
        auto_nag,
        "cron",
        hour="9,11,13,15,17",
        minute=0,
        id="auto_nag",
        name="Auto-Nag Check",
    )

    # Weekly performance report Sunday at 7 PM ET
    scheduler.add_job(
        weekly_report,
        "cron",
        day_of_week="sun",
        hour=19,
        minute=0,
        id="weekly_report",
        name="Weekly Performance Report",
    )

    scheduler.start()
    logger.info("Scheduler started — morning briefing 8AM, auto-nag 9-5, weekly report Sun 7PM ET.")
