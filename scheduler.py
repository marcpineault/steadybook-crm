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

# ── Job run tracking (watchdog) ──

_job_last_run = {}

# Track failed reminder sends to prevent infinite spam
_reminder_failures: dict[int, int] = {}
_REMINDER_MAX_RETRIES = 3


def _mark_job_run(job_name):
    """Record that a job ran successfully."""
    _job_last_run[job_name] = datetime.now(ET)


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
            await _bot.send_message(chat_id=CHAT_ID, text="Morning briefing error — check logs.")
        except Exception:
            pass


async def _morning_briefing_inner():
    import briefing as briefing_module
    text = briefing_module.generate_briefing_text()
    if len(text) > 4096:
        text = text[:4076] + "\n...(truncated)"
    await _bot.send_message(chat_id=CHAT_ID, text=text)
    logger.info("Morning briefing (strategic) sent.")
    _mark_job_run("morning_briefing")


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

    # 5. Overdue tasks
    try:
        overdue_tasks = db.get_overdue_tasks()
        for t in overdue_tasks:
            task_key = f"task_{t['id']}"
            if _can_nag(nag_state, task_key, "overdue_task"):
                days_late = (today - datetime.strptime(t["due_date"], "%Y-%m-%d").date()).days
                prospect_str = f" ({t['prospect']})" if t.get("prospect") else ""
                alerts.append(f"  TASK OVERDUE: {t['title']}{prospect_str} — {days_late} days late")
                _mark_nagged(nag_state, task_key, "overdue_task")
    except Exception as e:
        logger.warning(f"Could not check overdue tasks for nag: {e}")

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
            await _bot.send_message(chat_id=CHAT_ID, text="Weekly report error — check logs.")
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

    # AI-powered weekly insights
    try:
        import analytics
        stats = analytics.get_weekly_stats()
        if stats["total_actions"] > 0:
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("AI INSIGHTS:")
            lines.append(analytics.format_stats_for_telegram(stats))

            insights = analytics.generate_insights()
            if insights:
                lines.append("")
                lines.append(insights[:1500])
    except Exception:
        logger.exception("Insights section failed — sending report without it")

    msg = "\n".join(lines)
    await _bot.send_message(chat_id=CHAT_ID, text=msg)
    logger.info("Weekly report sent.")


# ── Midday Check-in ──

async def midday_checkin():
    """Send a midday progress check at 12:30 PM ET."""
    if not _bot or not CHAT_ID:
        return

    try:
        today = date.today()
        prospects = _read_prospects()
        active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]

        # Count today's activities
        activities_today = 0
        calls_today = 0
        for a in db.read_activities():
            ad = a.get("date", "")
            if ad and ad != "None":
                try:
                    activity_date = datetime.strptime(ad.split(" ")[0], "%Y-%m-%d").date()
                    if activity_date == today:
                        activities_today += 1
                        if "call" in a["action"].lower() or "phone" in a["action"].lower():
                            calls_today += 1
                except (ValueError, IndexError):
                    pass

        for b in db.read_insurance_book():
            lc = b.get("last_called", "")
            if lc and lc != "None":
                try:
                    if datetime.strptime(lc.split(" ")[0], "%Y-%m-%d").date() == today:
                        calls_today += 1
                except (ValueError, IndexError):
                    pass

        # Follow-ups due today
        due_today = []
        for p in active:
            fu = p["_next_followup_date"]
            if fu and fu == today:
                due_today.append(p["name"])

        # Overdue
        overdue = [p for p in active if p["_next_followup_date"] and p["_next_followup_date"] < today]

        lines = ["MIDDAY CHECK-IN", "━━━━━━━━━━━━━━━━", ""]
        lines.append(f"So far today: {calls_today} calls, {activities_today} total activities")

        if calls_today < 10:
            lines.append(f"  {10 - calls_today} more calls to hit your daily target")

        if due_today:
            lines.append(f"\nDue today ({len(due_today)}):")
            for name in due_today[:5]:
                lines.append(f"  {name}")

        if overdue:
            lines.append(f"\nOverdue ({len(overdue)}):")
            for p in overdue[:3]:
                days_late = (today - p["_next_followup_date"]).days
                lines.append(f"  {p['name']} — {days_late}d late")

        # Afternoon meetings
        meetings = _read_meetings_today()
        afternoon = [m for m in meetings if _is_afternoon(m.get("time", ""))]
        if afternoon:
            lines.append(f"\nThis afternoon:")
            for m in afternoon:
                lines.append(f"  {m.get('time', '?')} — {m.get('prospect', '?')} ({m.get('type', '?')})")

        if not due_today and not overdue and calls_today >= 10:
            lines.append("\nYou're crushing it today. Keep going.")

        await _bot.send_message(chat_id=CHAT_ID, text="\n".join(lines))
        logger.info("Midday check-in sent.")
    except Exception as e:
        logger.error(f"Midday check-in failed: {e}")


def _is_afternoon(time_str: str) -> bool:
    """Check if a time string is afternoon (12 PM or later)."""
    if not time_str:
        return False
    t = time_str.lower()
    if "pm" in t:
        return True
    try:
        hour = int(t.split(":")[0])
        return hour >= 12
    except (ValueError, IndexError):
        return False


# ── End of Day Wrap-up ──

async def eod_wrapup():
    """Send end-of-day summary at 5:30 PM ET on weekdays."""
    if not _bot or not CHAT_ID:
        return

    try:
        today = date.today()
        # Skip weekends
        if today.weekday() >= 5:
            return

        prospects = _read_prospects()
        active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]

        # Count today's activities
        activities_today = 0
        calls_today = 0
        for a in db.read_activities():
            ad = a.get("date", "")
            if ad and ad != "None":
                try:
                    activity_date = datetime.strptime(ad.split(" ")[0], "%Y-%m-%d").date()
                    if activity_date == today:
                        activities_today += 1
                        if "call" in a["action"].lower() or "phone" in a["action"].lower():
                            calls_today += 1
                except (ValueError, IndexError):
                    pass

        for b in db.read_insurance_book():
            lc = b.get("last_called", "")
            if lc and lc != "None":
                try:
                    if datetime.strptime(lc.split(" ")[0], "%Y-%m-%d").date() == today:
                        calls_today += 1
                except (ValueError, IndexError):
                    pass

        # Overdue still unresolved
        overdue = [p for p in active if p["_next_followup_date"] and p["_next_followup_date"] < today]

        # Tomorrow's meetings
        tomorrow_meetings = _read_meetings_tomorrow()

        lines = ["END OF DAY", "━━━━━━━━━━━━━━━━", ""]
        lines.append(f"Today: {calls_today} calls, {activities_today} total activities")

        if calls_today >= 10:
            lines.append("Hit your call target today.")
        else:
            lines.append(f"Missed call target by {10 - calls_today}.")

        if overdue:
            lines.append(f"\nStill overdue ({len(overdue)}):")
            for p in overdue[:5]:
                days_late = (today - p["_next_followup_date"]).days
                lines.append(f"  {p['name']} — {days_late}d late")

        # Tomorrow preview
        tomorrow = today + timedelta(days=1)
        due_tomorrow = [p for p in active if p["_next_followup_date"] and p["_next_followup_date"] == tomorrow]

        if tomorrow_meetings or due_tomorrow:
            lines.append(f"\nTomorrow:")
            for m in tomorrow_meetings:
                lines.append(f"  Meeting: {m.get('time', '?')} — {m.get('prospect', '?')} ({m.get('type', '?')})")
            for p in due_tomorrow[:3]:
                lines.append(f"  Follow-up: {p['name']}")

        lines.append("\nGood work today, Marc.")

        await _bot.send_message(chat_id=CHAT_ID, text="\n".join(lines))
        logger.info("EOD wrap-up sent.")
    except Exception as e:
        logger.error(f"EOD wrap-up failed: {e}")


# ── Task Reminders ──

async def check_task_reminders():
    """Check for tasks with remind_at <= now and send reminders."""
    if not _bot or not CHAT_ID:
        return

    try:
        now_str = datetime.now(ET).strftime("%Y-%m-%d %H:%M")
        tasks = db.get_reminder_tasks(now_str)
        if tasks:
            logger.info(f"Task reminders: found {len(tasks)} tasks due at {now_str}")
        else:
            # Log all pending tasks with remind_at for debugging
            all_with_reminders = db.get_tasks(status="pending")
            reminders = [(t["id"], t["title"], t["remind_at"]) for t in all_with_reminders if t.get("remind_at")]
            if reminders:
                logger.debug(f"Task reminders: now={now_str}, no matches. Pending reminders: {reminders}")

        for t in tasks:
            task_id = t["id"]

            # Skip if we've already failed too many times — clear it so it stops spamming
            fail_count = _reminder_failures.get(task_id, 0)
            if fail_count >= _REMINDER_MAX_RETRIES:
                logger.error(f"Task reminder #{task_id} failed {fail_count} times, clearing to stop spam")
                db.clear_reminder(task_id)
                _reminder_failures.pop(task_id, None)
                continue

            due_str = f" (due {t['due_date']})" if t.get("due_date") else ""
            prospect_str = f" [{t['prospect']}]" if t.get("prospect") else ""
            msg = f"Reminder: {t['title']}{prospect_str}{due_str}"

            # Always send to admin CHAT_ID (most reliable)
            try:
                await _bot.send_message(chat_id=CHAT_ID, text=msg)
                db.clear_reminder(task_id)
                _reminder_failures.pop(task_id, None)
                logger.info(f"Task reminder sent: #{task_id} to admin {CHAT_ID}")
            except Exception as e:
                _reminder_failures[task_id] = fail_count + 1
                logger.error(f"Could not send task reminder #{task_id} to {CHAT_ID} (attempt {fail_count + 1}/{_REMINDER_MAX_RETRIES}): {e}")
                continue

            # Also notify the assignee if different from admin
            assignee = t.get("assigned_to", "")
            if assignee and assignee != CHAT_ID:
                try:
                    await _bot.send_message(chat_id=assignee, text=msg)
                    logger.info(f"Task reminder also sent to assignee {assignee}")
                except Exception as e:
                    logger.warning(f"Could not send task reminder to assignee {assignee}: {e}")

    except Exception as e:
        logger.error(f"Task reminder check failed: {e}")


# ── Nudge Stale Drafts ──

async def nudge_stale_drafts():
    """Check for pending drafts that haven't been acted on and send reminders."""
    if not _bot or not CHAT_ID:
        return

    try:
        import follow_up as fu
        import approval_queue

        # First, re-surface snoozed drafts older than 1 hour
        with db.get_db() as conn:
            snoozed = conn.execute(
                """SELECT id FROM approval_queue
                   WHERE status = 'snoozed'
                   AND acted_on_at <= datetime('now', '-1 hour')""",
            ).fetchall()
        for row in snoozed:
            try:
                approval_queue.update_draft_status(row["id"], "pending")
            except Exception:
                logger.warning("Failed to re-surface snoozed draft #%s", row["id"])

        # Then check for stale pending drafts (computed AFTER re-surfacing)
        stale = fu.get_stale_drafts()
        if not stale:
            return

        count = len(stale)
        if count == 1:
            draft = stale[0]
            name = draft.get("prospect_name", "Unknown")
            text = f"NUDGE: You have a pending {draft['type']} draft for {name} (#{draft['id']}).\n/drafts to review."
        else:
            text = f"NUDGE: You have {count} pending drafts awaiting review.\n/drafts to review them."

        await _bot.send_message(chat_id=CHAT_ID, text=text)
        logger.info("Sent nudge for %d stale drafts", count)

    except Exception:
        logger.exception("Nudge stale drafts failed")


# ── Meeting Prep Docs ──

async def send_meeting_prep_docs():
    """Check for meetings in the next 1-2 hours and send prep docs."""
    if not _bot or not CHAT_ID:
        return

    try:
        import json as _json
        import meeting_prep

        now = datetime.now(ET)
        today = now.strftime("%Y-%m-%d")
        meetings = meeting_prep.get_meetings_needing_prep(today)

        state_file = os.path.join(os.environ.get("DATA_DIR", "."), "meeting_prep_state.json")
        try:
            with open(state_file) as f:
                _state = _json.load(f)
        except (FileNotFoundError, ValueError):
            _state = {}
        sent_preps = set(_state.get("sent_preps", []))

        for m in meetings:
            meeting_time = m.get("time", "")
            prospect_name = m.get("prospect", "")

            if not meeting_time or not prospect_name:
                continue

            # Parse meeting time and check if it's 1-2 hours from now
            try:
                hour, minute = int(meeting_time.split(":")[0]), int(meeting_time.split(":")[1])
                meeting_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                delta = (meeting_dt - now).total_seconds() / 3600

                if 0.5 <= delta <= 2.0:
                    # Check if we already sent prep for this meeting (avoid duplicates)
                    meeting_id = m.get("id", "")
                    if meeting_id in sent_preps:
                        continue

                    doc = meeting_prep.generate_prep_doc(prospect_name, m.get("type", "Meeting"), meeting_time)
                    if doc:
                        if len(doc) > 4096:
                            doc = doc[:4076] + "\n...(truncated)"
                        await _bot.send_message(chat_id=CHAT_ID, text=doc)
                        sent_preps.add(meeting_id)
                        _state["sent_preps"] = list(sent_preps)[-50:]
                        with open(state_file, "w") as f:
                            _json.dump(_state, f)
                        logger.info("Meeting prep sent for %s at %s", prospect_name, meeting_time)
            except (ValueError, IndexError):
                logger.warning("Could not parse meeting time '%s'", meeting_time)
                continue

    except Exception:
        logger.exception("Meeting prep doc check failed")


# ── Nurture Sequence Check ──

async def check_nurture_sequences():
    """Check for due nurture touches, generate them, and send a single batched notification."""
    if not _bot or not CHAT_ID:
        return

    try:
        import nurture

        due = nurture.get_due_touches()
        if not due:
            return

        touches = []
        for seq in due:
            try:
                touch = nurture.generate_touch(seq["id"])
                if touch:
                    touches.append(touch)
            except Exception:
                logger.exception("Nurture touch failed for sequence #%s", seq["id"])

        if not touches:
            return

        if len(touches) == 1:
            t = touches[0]
            text = (
                f"NURTURE TOUCH — {t['prospect_name']}\n"
                f"Touch {t['touch_number']}/{t['total_touches']}\n\n"
                f"{t['content'][:500]}\n\n"
                f"Queue #{t['queue_id']} — /drafts to review"
            )
        else:
            lines = [f"NURTURE TOUCHES — {len(touches)} due today\n"]
            for t in touches:
                lines.append(
                    f"  {t['prospect_name']} (touch {t['touch_number']}/{t['total_touches']}) "
                    f"— Queue #{t['queue_id']}"
                )
            lines.append(f"\nUse /drafts to review all {len(touches)} drafts.")
            text = "\n".join(lines)

        await _bot.send_message(chat_id=CHAT_ID, text=text)
        logger.info("Generated %d nurture touches (batched)", len(touches))

    except Exception:
        logger.exception("Nurture sequence check failed")


# ── Booking Nurture Touch Check ──

async def check_booking_nurture_touches():
    """Check for due pre-call nurture touches, generate SMS drafts, queue to Telegram."""
    if not _bot or not CHAT_ID:
        return
    try:
        import booking_nurture
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        due = booking_nurture.get_due_touches()
        for touch_row in due:
            try:
                result = booking_nurture.generate_touch(touch_row)
                if not result:
                    continue
                text = booking_nurture.format_touch_for_telegram(touch_row, result["content"])
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("Approve", callback_data=f"draft_approve_{result['queue_id']}"),
                    InlineKeyboardButton("Skip", callback_data=f"draft_dismiss_{result['queue_id']}"),
                    InlineKeyboardButton("Snooze 1h", callback_data=f"draft_snooze_{result['queue_id']}"),
                ]])
                msg = await _bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=keyboard)
                import approval_queue as aq
                aq.set_telegram_message_id(result["queue_id"], str(msg.message_id))
            except Exception:
                logger.exception("Booking nurture touch failed seq #%s", touch_row["id"])
    except Exception:
        logger.exception("check_booking_nurture_touches failed")


async def check_cold_outreach_followups():
    """Draft follow-up texts for cold outreach that got no reply.

    Up to 3 follow-ups with varied angles:
    - Follow-up 1 (day 3):  Gentle nudge — "just in case you missed it"
    - Follow-up 2 (day 7):  Value angle — why it's worth a quick chat
    - Follow-up 3 (day 14): Final soft close — "door's always open"

    Only fires if:
    - At least 1 outbound text sent to this phone (from cold outreach)
    - Zero inbound replies ever received from this phone
    - Fewer than 4 total outbound texts (1 initial + 3 follow-ups max)
    - Not opted out
    """
    if not _bot or not CHAT_ID:
        return
    try:
        import db as _db
        import sms_conversations
        import approval_queue as aq
        from openai import OpenAI
        import os

        # Follow-up schedule: (outbound_count_threshold, days_since_first, prompt_key)
        FOLLOWUP_SCHEDULE = [
            (1, 3, "nudge"),
            (2, 7, "value"),
            (3, 14, "final"),
        ]

        FOLLOWUP_PROMPTS = {
            "nudge": (
                "You are writing a brief follow-up text for Marc Pineault, a financial advisor at Co-operators "
                "in London, Ontario. Marc texted this person a few days ago and never got a reply. "
                "Write ONE sentence — low pressure, casual, leaves the door open. "
                "First name only, sign '- Marc'. No mentioning products or Co-operators. "
                "Good example: 'Hey Sarah, just leaving this here in case you missed it — happy to chat whenever works for you. - Marc'"
            ),
            "value": (
                "You are writing a second follow-up text for Marc Pineault, a financial advisor at Co-operators "
                "in London, Ontario. Marc has texted this person before with no reply. "
                "Write ONE sentence — offer a reason why a quick chat is worth their time (e.g. saving money, "
                "getting a second opinion, making sure they're covered). Keep it casual and genuine. "
                "First name only, sign '- Marc'. No specific products, rates, or numbers. "
                "Good example: 'Hey Sarah, a lot of people I sit down with find they're overpaying or have gaps they didn't know about — happy to take a look if you're curious. - Marc'"
            ),
            "final": (
                "You are writing a final follow-up text for Marc Pineault, a financial advisor at Co-operators "
                "in London, Ontario. Marc has texted this person a couple times with no reply. "
                "This is the LAST text — make it respectful, zero pressure, and leave the door wide open. "
                "Write ONE sentence. First name only, sign '- Marc'. No products or Co-operators. "
                "Good example: 'Hey Sarah, I won't keep bugging you — just know the door's always open if you ever want to chat. - Marc'"
            ),
        }

        with _db.get_db() as conn:
            # Phones with outbound texts, no inbound ever, ≤3 total outbound
            rows = conn.execute(
                """
                SELECT phone, MIN(created_at) as first_outbound, COUNT(*) as outbound_count,
                       MAX(prospect_id) as prospect_id, MAX(prospect_name) as prospect_name
                FROM sms_conversations
                WHERE direction = 'outbound'
                GROUP BY phone
                HAVING outbound_count <= 3
                   AND phone NOT IN (
                       SELECT DISTINCT phone FROM sms_conversations WHERE direction = 'inbound'
                   )
                """,
            ).fetchall()

        now = datetime.now(ET)

        for row in rows:
            phone = row["phone"]
            prospect_id = row["prospect_id"]
            prospect_name = row["prospect_name"] or ""
            outbound_count = row["outbound_count"]
            first_outbound = row["first_outbound"]

            # Determine which follow-up is due
            prompt_key = None
            followup_label = ""
            for threshold, days, key in FOLLOWUP_SCHEDULE:
                if outbound_count == threshold:
                    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
                    if first_outbound <= cutoff:
                        prompt_key = key
                        followup_label = f"{days}-day"
                    break

            if not prompt_key:
                continue

            # Check opt-out
            prospect = None
            if prospect_id:
                with _db.get_db() as conn:
                    p = conn.execute("SELECT * FROM prospects WHERE id = ?", (prospect_id,)).fetchone()
                    if p:
                        prospect = dict(p)
            if sms_conversations.is_opted_out(prospect):
                continue

            try:
                from pii import RedactionContext, sanitize_for_prompt
                openai_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
                first_name = (prospect_name or "there").split()[0]
                if first_name.startswith("Contact"):
                    first_name = "there"

                with RedactionContext(prospect_names=[prospect_name] if prospect_name and not prospect_name.startswith("Contact ") else []) as pii_ctx:
                    user_content = pii_ctx.redact(sanitize_for_prompt(
                        f"Prospect first name: {first_name}"
                    ))
                    resp = openai_client.chat.completions.create(
                        model="gpt-4.1-mini",
                        messages=[
                            {"role": "system", "content": FOLLOWUP_PROMPTS[prompt_key]},
                            {"role": "user", "content": user_content},
                        ],
                        max_completion_tokens=100,
                        temperature=0.7,
                    )
                    content = pii_ctx.restore(resp.choices[0].message.content.strip())
                    if first_name and prospect_name and first_name != prospect_name:
                        content = content.replace(prospect_name, first_name)
            except Exception:
                logger.exception("Cold follow-up generation failed for %s", phone[-4:])
                continue

            attempt_num = outbound_count  # 1, 2, or 3
            draft = aq.add_draft(
                draft_type="cold_followup",
                channel="sms_draft",
                content=content,
                context=f"{followup_label} no-reply follow-up #{attempt_num} for {prospect_name or phone} ({phone})",
                prospect_id=prospect_id,
            )

            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Send", callback_data=f"draft_approve_{draft['id']}"),
                InlineKeyboardButton("Skip", callback_data=f"draft_dismiss_{draft['id']}"),
            ]])
            display = prospect_name or phone
            attempt_labels = {1: "1st", 2: "2nd", 3: "3rd (final)"}
            text = (
                f"NO REPLY FOLLOW-UP — {display}\n"
                f"{attempt_labels.get(attempt_num, '')} attempt · {followup_label} since first text\n\n"
                f"{content}"
            )
            try:
                msg = await _bot.send_message(chat_id=CHAT_ID, text=text, reply_markup=keyboard)
                aq.set_telegram_message_id(draft["id"], str(msg.message_id))
            except Exception:
                logger.exception("Could not send cold follow-up draft to Telegram for %s", phone[-4:])

    except Exception:
        logger.exception("check_cold_outreach_followups failed")


# ── Database Backup ──

async def backup_database():
    """Create a daily backup of the SQLite database."""
    import shutil
    import glob

    data_dir = os.environ.get("DATA_DIR", ".")
    db_path = os.path.join(data_dir, "pipeline.db")
    backup_dir = os.path.join(data_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)

    today = datetime.now(ET).strftime("%Y-%m-%d")
    backup_path = os.path.join(backup_dir, f"pipeline-{today}.db")

    try:
        # Use SQLite backup API for safe copy
        import sqlite3
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(backup_path)
        src.backup(dst)
        dst.close()
        src.close()
        logger.info(f"Database backed up to {backup_path}")

        # Remove backups older than 7 days
        all_backups = sorted(glob.glob(os.path.join(backup_dir, "pipeline-*.db")))
        while len(all_backups) > 7:
            old = all_backups.pop(0)
            os.remove(old)
            logger.info(f"Removed old backup: {old}")
    except Exception:
        logger.exception("Database backup failed")
        # Try to alert Marc
        if _bot and CHAT_ID:
            try:
                await _bot.send_message(chat_id=CHAT_ID, text="Database backup failed. Check logs.")
            except Exception:
                pass


# ── System Watchdog ──

async def watchdog_check():
    """Check if critical jobs fired on schedule. Alert if not."""
    if not _bot or not CHAT_ID:
        return

    now = datetime.now(ET)
    alerts = []

    # Check morning briefing ran today (should have fired at 8 AM)
    last_briefing = _job_last_run.get("morning_briefing")
    if last_briefing is None or last_briefing.date() != now.date():
        alerts.append("Morning briefing did not fire today")

    if alerts:
        try:
            msg = "SYSTEM WATCHDOG ALERT:\n" + "\n".join(f"- {a}" for a in alerts)
            await _bot.send_message(chat_id=CHAT_ID, text=msg)
            logger.warning(f"Watchdog alerts: {alerts}")
        except Exception:
            logger.exception("Watchdog alert failed")


# ── Webhook Health Check ──

async def check_webhook_health():
    """Verify Telegram webhook is properly registered."""
    if not _bot or not CHAT_ID:
        return

    try:
        import httpx
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not bot_token:
            return

        async with httpx.AsyncClient() as client:
            resp = await client.get(f"https://api.telegram.org/bot{bot_token}/getWebhookInfo")
            data = resp.json()

        if not data.get("ok"):
            logger.error("Webhook health check: API returned not ok")
            return

        result = data.get("result", {})
        webhook_url = result.get("url", "")
        pending = result.get("pending_update_count", 0)
        last_error = result.get("last_error_date")

        # Alert if webhook URL is empty or wrong
        expected_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        if expected_domain and expected_domain not in webhook_url:
            await _bot.send_message(
                chat_id=CHAT_ID,
                text=f"Webhook URL mismatch! Expected {expected_domain} but got: {webhook_url}"
            )
            logger.error(f"Webhook URL mismatch: {webhook_url}")

        # Alert if too many pending updates (messages not being processed)
        if pending > 10:
            await _bot.send_message(
                chat_id=CHAT_ID,
                text=f"Webhook has {pending} pending updates — messages may not be processing."
            )
            logger.warning(f"Webhook pending updates: {pending}")

        logger.info(f"Webhook health: url={webhook_url}, pending={pending}")
    except Exception:
        logger.exception("Webhook health check failed")


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

    # Morning briefing at 8:00 AM ET weekdays only
    scheduler.add_job(
        morning_briefing,
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=0,
        id="morning_briefing",
        name="Daily Morning Briefing",
    )

    # Auto-nag at 9 AM and 2 PM ET weekdays
    scheduler.add_job(
        auto_nag,
        "cron",
        hour="9,14",
        minute=0,
        id="auto_nag",
        name="Auto-Nag Check",
    )

    # Midday check-in at 12:30 PM ET weekdays
    scheduler.add_job(
        midday_checkin,
        "cron",
        day_of_week="mon-fri",
        hour=12,
        minute=30,
        id="midday_checkin",
        name="Midday Check-In",
    )

    # End of day wrap-up at 5:30 PM ET weekdays
    scheduler.add_job(
        eod_wrapup,
        "cron",
        day_of_week="mon-fri",
        hour=17,
        minute=30,
        id="eod_wrapup",
        name="End of Day Wrap-Up",
    )

    # Weekly performance report Sunday at 6:30 PM ET (offset from content plan at 6PM)
    scheduler.add_job(
        weekly_report,
        "cron",
        day_of_week="sun",
        hour=18,
        minute=30,
        id="weekly_report",
        name="Weekly Performance Report",
    )

    # Task reminders — check every 60 seconds
    scheduler.add_job(
        check_task_reminders,
        "interval",
        seconds=60,
        id="task_reminders",
        name="Task Reminder Check",
    )

    # Nudge for stale drafts once daily at 2:30 PM ET
    scheduler.add_job(
        nudge_stale_drafts,
        "cron",
        day_of_week="mon-fri",
        hour="14",
        minute=30,
        id="nudge_stale_drafts",
        name="Nudge Stale Drafts",
    )

    # Meeting prep docs — check every hour during business hours
    scheduler.add_job(
        send_meeting_prep_docs,
        "cron",
        day_of_week="mon-fri",
        hour="7,8,9,10,11,12,13,14,15,16",
        minute=0,
        id="meeting_prep_docs",
        name="Meeting Prep Docs",
    )

    # Daily nurture check — 9:15AM ET weekdays (offset from 9AM auto_nag)
    scheduler.add_job(
        check_nurture_sequences,
        "cron",
        day_of_week="mon-fri",
        hour=9,
        minute=15,
        id="check_nurture_sequences",
        name="Nurture Sequence Check",
    )

    # Pre-call text nurture — check every 15 minutes
    scheduler.add_job(
        check_booking_nurture_touches,
        "interval",
        minutes=15,
        id="check_booking_nurture_touches",
        name="Pre-Call Text Nurture Check",
    )

    scheduler.add_job(
        check_cold_outreach_followups,
        "cron",
        hour=9,
        minute=30,
        id="check_cold_outreach_followups",
        name="Cold Outreach No-Reply Follow-ups",
    )

    # Daily database backup at 11 PM ET
    scheduler.add_job(
        backup_database,
        "cron",
        hour=23,
        minute=0,
        id="backup_database",
        name="Daily Database Backup",
    )

    # System watchdog at 8:45 AM ET weekdays
    scheduler.add_job(
        watchdog_check,
        "cron",
        day_of_week="mon-fri",
        hour=8,
        minute=45,
        id="watchdog_check",
        name="System Watchdog",
    )

    # Webhook health check every 6 hours
    scheduler.add_job(
        check_webhook_health,
        "interval",
        hours=6,
        id="check_webhook_health",
        name="Webhook Health Check",
    )

    scheduler.start()
    logger.info("Scheduler started — briefing 8AM (weekdays), nag 9AM+2PM, midday 12:30PM, EOD 5:30PM, weekly Sun 6:30PM, task reminders every 60s, meeting prep hourly, backup 11PM, watchdog 8:45AM, webhook check every 6h, booking nurture every 15min, cold follow-ups 9:30AM daily ET.")
