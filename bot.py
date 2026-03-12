import asyncio
import os
import json
import logging
import signal
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from openai import OpenAI
import db
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from voice_handler import handle_voice_message

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]
ADMIN_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# DATA_DIR kept for migration path reference
DATA_DIR = os.environ.get("DATA_DIR", "")

client = OpenAI(api_key=OPENAI_KEY)

# DEPRECATED — kept for scheduler import compat
pipeline_lock = threading.RLock()


def _is_admin(update) -> bool:
    """Check if the message sender is the admin (Marc)."""
    return str(update.effective_chat.id) == str(ADMIN_CHAT_ID)


async def _require_admin(update) -> bool:
    """Check admin access. Sends denial message if not admin. Returns True if authorized."""
    if _is_admin(update):
        return True
    await update.message.reply_text(
        "You have access to /quote, /add, /status, /msg, /todo, /tasks, and /done.\n"
        "Try: /quote disability office worker 50k income 3k benefit\n"
        "Or: /add John Smith, interested in life insurance\n"
        "Or: /msg Hey Marc, can we chat about the Johnson file?\n"
        "Or: /todo send brochure to John by Friday"
    )
    return False


def read_pipeline():
    """Read all prospects from pipeline (via SQLite)."""
    return db.read_pipeline()


def add_prospect(data: dict) -> str:
    """Add a new prospect (via SQLite)."""
    return db.add_prospect(data)


def lookup_prospect(name: str) -> str:
    """Look up a single prospect by name."""
    p = db.get_prospect_by_name(name)
    if not p:
        return f"No prospect found matching '{name}'."
    return json.dumps(p, default=str)


def message_marc(sender: str, message: str) -> str:
    """Send a message to Marc via Telegram. Returns confirmation."""
    import asyncio

    if not ADMIN_CHAT_ID:
        return "Could not send — Marc's chat ID not configured."

    # Access bot from the running app
    import sys
    main_mod = sys.modules.get("__main__")
    bot = getattr(main_mod, "telegram_app", None)
    if bot:
        bot = bot.bot
    else:
        return "Message queued — Marc will see it when the bot restarts."

    text = f"Message from {sender}:\n\n{message}"

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(bot.send_message(chat_id=ADMIN_CHAT_ID, text=text))
        else:
            loop.run_until_complete(bot.send_message(chat_id=ADMIN_CHAT_ID, text=text))
        return f"Message sent to Marc."
    except Exception as e:
        logger.error(f"Failed to message Marc: {e}")
        return f"Could not send message right now. Try again or call Marc directly."


def update_prospect(name: str, updates: dict) -> str:
    """Update a prospect by name (via SQLite)."""
    return db.update_prospect(name, updates)


def delete_prospect(name: str) -> str:
    """Delete a prospect by name (via SQLite)."""
    return db.delete_prospect(name)


def add_activity(data: dict) -> str:
    """Add entry to activity log (via SQLite)."""
    return db.add_activity(data)


def get_activities(date_filter: str = "", prospect: str = "") -> str:
    """Get logged activities, optionally filtered by date or prospect."""
    activities = db.read_activities(limit=50)
    if date_filter:
        activities = [a for a in activities if date_filter in str(a.get("date", ""))]
    if prospect:
        activities = [a for a in activities if prospect.lower() in str(a.get("prospect", "")).lower()]
    if not activities:
        return "No activities found."
    lines = [f"Activity Log ({len(activities)} entries):"]
    for a in activities:
        line = f"{a.get('date', '?')} - {a.get('prospect', '?')}: {a.get('action', '?')}"
        if a.get("outcome"):
            line += f" -> {a['outcome']}"
        if a.get("next_step"):
            line += f" | Next: {a['next_step']}"
        lines.append(line)
    return "\n".join(lines)


def get_overdue():
    """Get prospects with overdue follow-ups."""
    prospects = read_pipeline()
    today = date.today()
    overdue = []

    for p in prospects:
        if p["next_followup"] and p["stage"] not in ("Closed-Won", "Closed-Lost", ""):
            try:
                fu_date = datetime.strptime(p["next_followup"].split(" ")[0], "%Y-%m-%d").date()
                if fu_date < today:
                    days_late = (today - fu_date).days
                    overdue.append(f"• {p['name']} — {days_late} days overdue (was {p['next_followup']})")
            except (ValueError, IndexError):
                pass

    if not overdue:
        return "No overdue follow-ups. You're on top of it."
    return f"Overdue follow-ups ({len(overdue)}):\n" + "\n".join(overdue)


# ── Follow-up sequences ──

FOLLOW_UP_SEQUENCES = {
    "Discovery Call": [
        (1, "Send thank-you email + summary of discussion"),
        (3, "Check-in — any questions about what we discussed?"),
        (7, "Share a relevant article or insight"),
    ],
    "Plan Presentation": [
        (1, "Send plan summary + next steps"),
        (3, "Follow up — any questions about the plan?"),
        (7, "Gentle nudge — ready to move forward?"),
    ],
    "Proposal Sent": [
        (1, "Confirm they received the proposal"),
        (3, "Check if they have questions"),
        (5, "Ask if they need anything else to decide"),
        (10, "Final follow-up — still interested?"),
    ],
    "Needs Analysis": [
        (1, "Send recap of what you learned"),
        (5, "Share that you're working on their plan"),
    ],
    "Contacted": [
        (2, "Follow up if no response"),
        (5, "Try different channel (call vs email)"),
        (10, "Last attempt before moving to Nurture"),
    ],
}


def get_follow_up_sequence(prospect_name: str, stage: str) -> str:
    """Get the recommended follow-up sequence for a prospect's current stage."""
    seq = FOLLOW_UP_SEQUENCES.get(stage)
    if not seq:
        return f"No follow-up sequence defined for stage '{stage}'. Just stay in touch!"

    today = date.today()
    lines = [f"Follow-up sequence for {prospect_name} ({stage}):"]
    for day_offset, action in seq:
        target = today + timedelta(days=day_offset)
        lines.append(f"  Day {day_offset} ({target.strftime('%b %d')}): {action}")

    lines.append(f"\nWant me to set the first follow-up ({(today + timedelta(days=seq[0][0])).strftime('%Y-%m-%d')}) now?")
    return "\n".join(lines)


def auto_set_follow_up(prospect_name: str, stage: str) -> str:
    """Automatically set the next follow-up date based on stage sequence."""
    seq = FOLLOW_UP_SEQUENCES.get(stage)
    if not seq:
        return ""

    next_date = date.today() + timedelta(days=seq[0][0])
    result = update_prospect(prospect_name, {"next_followup": next_date.strftime("%Y-%m-%d")})
    return f"Auto-set follow-up to {next_date.strftime('%b %d')} — {seq[0][1]}"


# ── Win/Loss Analysis ──

def log_win_loss(prospect_name: str, outcome: str, reason: str) -> str:
    """Log why a deal was won or lost (via SQLite)."""
    p = db.get_prospect_by_name(prospect_name)
    product = p.get("product", "") if p else ""
    return db.log_win_loss(prospect_name, outcome, reason, product)


def get_win_loss_stats() -> str:
    """Get win/loss analysis: patterns, reasons, conversion by product."""
    entries = db.get_win_loss_stats()

    wins = []
    losses = []
    for entry in entries:
        outcome = entry.get("outcome", "")
        if not outcome:
            continue
        if outcome.lower() in ("won", "closed-won"):
            wins.append(entry)
        else:
            losses.append(entry)

    total = len(wins) + len(losses)
    if total == 0:
        return "No win/loss data yet. Close some deals first!"

    win_rate = len(wins) / total * 100

    # Reason tallies
    win_reasons = {}
    for w in wins:
        r = w.get("reason", "")
        if r:
            win_reasons[r] = win_reasons.get(r, 0) + 1

    loss_reasons = {}
    for l in losses:
        r = l.get("reason", "")
        if r:
            loss_reasons[r] = loss_reasons.get(r, 0) + 1

    # Product breakdown
    product_wins = {}
    product_losses = {}
    for w in wins:
        p = w.get("product") or "Unknown"
        product_wins[p] = product_wins.get(p, 0) + 1
    for l in losses:
        p = l.get("product") or "Unknown"
        product_losses[p] = product_losses.get(p, 0) + 1

    lines = [
        f"Win/Loss Analysis ({total} deals):",
        f"━━━━━━━━━━━━━━━━",
        f"Won: {len(wins)} | Lost: {len(losses)} | Win rate: {win_rate:.0f}%",
        "",
    ]

    if win_reasons:
        lines.append("Why you WIN:")
        for reason, count in sorted(win_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  • {reason} ({count}x)")
        lines.append("")

    if loss_reasons:
        lines.append("Why you LOSE:")
        for reason, count in sorted(loss_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"  • {reason} ({count}x)")
        lines.append("")

    all_products = set(list(product_wins.keys()) + list(product_losses.keys()))
    if all_products:
        lines.append("By product:")
        for p in all_products:
            w = product_wins.get(p, 0)
            l = product_losses.get(p, 0)
            rate = w / (w + l) * 100 if (w + l) > 0 else 0
            lines.append(f"  • {p}: {w}W/{l}L ({rate:.0f}%)")

    return "\n".join(lines)


# ── Quote Helper ──

EDGE_RATE_FILE = "edge_benefits_rates.json"
_edge_cache = None


def _load_edge_rates():
    global _edge_cache
    if _edge_cache is None and Path(EDGE_RATE_FILE).exists():
        with open(EDGE_RATE_FILE, "r") as f:
            _edge_cache = json.load(f)
    return _edge_cache or {}


TERM_MAP = {"10": "3", "15": "4", "20": "5", "25": "6", "30": "7"}


def _fetch_term4sale(age, sex, smoke, term_str, face):
    """Hit term4sale.ca API live for Co-operators rates."""
    birth_year = date.today().year - age
    cat_code = TERM_MAP.get(term_str)
    if not cat_code:
        return None

    params = {
        "requestType": "request", "ModeUsed": "M", "SortOverride1": "A",
        "ErrOnMissingZipCode": "ON", "State": "0", "ZipCode": "N6A1A1",
        "BirthMonth": "6", "BirthDay": "15", "BirthYear": str(birth_year),
        "Sex": sex, "Smoker": smoke, "Health": "R",
        "NewCategory": cat_code, "FaceAmount": str(face), "CompRating": "4",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://www.term4sale.ca/",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = requests.get("https://www.term4sale.ca/apit4sc/compulifeapi/api.php/",
                           params=params, headers=headers, timeout=10)
        logger.info(f"term4sale API status={resp.status_code}, body={resp.text[:100]}")
        if "scraping" in resp.text.lower() and len(resp.text) < 50:
            logger.warning("term4sale blocked us (returned 'scraping')")
            return None
        data = resp.json()
        results = data.get("Compulife_ComparisonResults", {}).get("Compulife_Results", [])
        for r in results:
            if "Co-operators" in r.get("Compulife_company", ""):
                return {
                    "annual": r["Compulife_premiumAnnual"].strip(),
                    "monthly": r["Compulife_premiumM"].strip(),
                    "product": r["Compulife_product"].strip(),
                }
        logger.info(f"Co-operators not found in {len(results)} results")
    except Exception as e:
        logger.error(f"term4sale API error: {e}")
    return None


def get_term_quote(age: int, gender: str, smoker: bool, term: str, amount: int, health: str = "regular") -> str:
    """Look up Co-operators term life insurance rates via live API."""
    sex = "M" if gender.lower().startswith("m") else "F"
    smoke = "Y" if smoker else "N"
    term_str = str(term).strip()
    sex_name = "Male" if sex == "M" else "Female"
    smoke_name = "Smoker" if smoker else "Non-Smoker"

    # Try live API
    r = _fetch_term4sale(age, sex, smoke, term_str, amount)

    if r:
        lines = [
            f"CO-OPERATORS QUOTE — {r.get('product', 'Versatile Term ' + term_str)}",
            f"━━━━━━━━━━━━━━━━",
            f"  {age}{sex_name[0]} {smoke_name}, ${amount:,} coverage",
            f"  Annual: ${r['annual']}/yr",
            f"  Monthly: ${r['monthly']}/mo",
            "",
            f"Health class: Regular (standard rates)",
        ]
        return "\n".join(lines)
    else:
        return (
            f"Couldn't get live rate for {age}{sex_name[0]} {smoke_name}, ${amount:,} Term {term_str}.\n"
            f"API may be temporarily unavailable. Check term4sale.ca manually.\n"
            f"Postal: N6A 1A1, Regular health, Co-operators Versatile Term {term_str}"
        )


EDGE_AGE_BANDS = {
    (18, 29): "18-29", (30, 39): "30-39", (40, 49): "40-49",
    (50, 59): "50-59", (60, 69): "60-69",
}

EDGE_BENEFITS = [1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000]
EDGE_WAIT_LABELS = {"0": "0 days", "30": "30 days", "112": "112 days"}
EDGE_PERIOD_LABELS = {"2": "2 Year", "5": "5 Year", "70": "To Age 70"}


def _get_age_band(age: int) -> str:
    for (lo, hi), label in EDGE_AGE_BANDS.items():
        if lo <= age <= hi:
            return label
    return ""


def get_disability_quote(age: int = 0, gender: str = "", occupation: str = "", income: int = 0,
                         benefit: int = 0, wait_days: str = "30",
                         benefit_period: str = "5", coverage_type: str = "24hour") -> str:
    """Look up Edge Benefits disability insurance rates. Age and gender optional (needed for illness rates only)."""
    data = _load_edge_rates()
    if not data:
        return "Edge Benefits rate data not loaded. Check edge_benefits_rates.json."

    rates = data.get("rates", data)
    occupations = data.get("occupations", {})

    # Common occupation aliases → Edge Benefits database titles
    OCC_ALIASES = {
        "administrative assistant": "office worker (general clerical duties only)",
        "administrative assistance": "office worker (general clerical duties only)",
        "admin assistant": "office worker (general clerical duties only)",
        "admin": "office worker (general clerical duties only)",
        "secretary": "office worker (general clerical duties only)",
        "receptionist": "office worker (general clerical duties only)",
        "office admin": "office worker (general clerical duties only)",
        "office administrator": "office worker (general clerical duties only)",
        "office clerk": "office worker (general clerical duties only)",
        "office worker": "office worker (general clerical duties only)",
        "clerk": "office worker (general clerical duties only)",
        "clerical": "office worker (general clerical duties only)",
        "data entry": "office worker (general clerical duties only)",
        "bookkeeper": "office worker (general clerical duties only)",
        "financial advisor": "insurance - financial planner (more than 2 years experience)",
        "financial planner": "insurance - financial planner (more than 2 years experience)",
        "financial consultant": "insurance - financial planner (more than 2 years experience)",
        "insurance agent": "insurance - financial planner (more than 2 years experience)",
        "insurance broker": "insurance - financial planner (more than 2 years experience)",
        "teacher": "education - teacher (permanent)",
        "nurse": "registered nurse (rn)",
        "engineer": "engineer - professional (office and consulting duties only)",
        "lawyer": "lawyer/attorney",
        "doctor": "physician/surgeon",
        "dentist": "dentist",
        "pharmacist": "pharmacist",
        "realtor": "real estate agent/broker",
        "real estate agent": "real estate agent/broker",
        "construction worker": "construction - general labourer",
        "electrician": "electrician - licensed",
        "plumber": "plumber/pipefitter",
        "truck driver": "truck driver (long haul)",
        "programmer": "computer programmer/analyst/operator/consultant",
        "software developer": "computer programmer/analyst/operator/consultant",
        "it professional": "computer programmer/analyst/operator/consultant",
    }

    # Determine risk class from occupation
    occ_lower = occupation.lower().strip()
    risk_class = None

    # Check aliases first (also try partial/fuzzy alias matching)
    if occ_lower in OCC_ALIASES:
        occ_lower = OCC_ALIASES[occ_lower]
    else:
        # Try matching alias keys as substrings or vice versa
        for alias_key, alias_val in OCC_ALIASES.items():
            if alias_key in occ_lower or occ_lower in alias_key:
                occ_lower = alias_val
                break

    # Direct lookup
    if occ_lower in occupations:
        occ_code = str(occupations[occ_lower])
        rate_key = f"OCCR-RATE-{occ_code}"
        risk_class = rates.get(rate_key)

    # Fuzzy match — try substring match
    if not risk_class:
        matches = [(k, v) for k, v in occupations.items() if occ_lower in k.lower()]
        if matches:
            occ_code = str(matches[0][1])
            rate_key = f"OCCR-RATE-{occ_code}"
            risk_class = rates.get(rate_key)
            occ_lower = matches[0][0]

    # Try matching by word overlap — prefer entries with more matching words
    if not risk_class:
        words = [w for w in occ_lower.split() if len(w) >= 4]
        if words:
            scored = []
            for k, v in occupations.items():
                k_lower = k.lower()
                score = sum(1 for w in words if w in k_lower)
                if score > 0:
                    scored.append((score, k, v))
            if scored:
                scored.sort(key=lambda x: -x[0])
                best = scored[0]
                occ_code = str(best[2])
                rate_key = f"OCCR-RATE-{occ_code}"
                risk_class = rates.get(rate_key)
                occ_lower = best[1]

    if not risk_class:
        # Suggest close matches
        suggestions = [k for k in occupations.keys() if any(w in k.lower() for w in occ_lower.split() if len(w) >= 4)][:5]
        if suggestions:
            return f"Occupation '{occupation}' not found. Try one of these: {', '.join(suggestions)}"
        return f"Occupation '{occupation}' not found in Edge Benefits database. Try a more general title like 'office worker' or 'clerk'."

    if risk_class in ("UI", "IC"):
        return f"Occupation '{occupation}' is rated '{risk_class}' (uninsurable/individual consideration) by Edge Benefits."

    # Calculate max eligible benefit (income / 12 * 0.69, rounded to nearest $500)
    max_monthly = int(income / 12 * 0.69)
    max_benefit = min(6000, max(1000, (max_monthly // 500) * 500))

    if benefit <= 0:
        benefit = max_benefit
    benefit = min(benefit, max_benefit)

    has_gender = bool(gender and gender.strip())
    has_age = bool(age and age > 0)
    sex_code = "0" if has_gender and gender.lower().startswith("m") else "1"
    cov_code = "0" if coverage_type == "24hour" else "1"
    gender_label = "Male" if sex_code == "0" else "Female"

    matched_title = occ_lower if occ_lower != occupation.lower().strip() else occupation.title()
    demo_str = f"{age}{gender_label[0]}, " if has_age and has_gender else ""
    lines = [
        f"EDGE BENEFITS DISABILITY QUOTE",
        f"━━━━━━━━━━━━━━━━",
        f"  {demo_str}{matched_title}",
        f"  Risk Class: {risk_class} | Income: ${income:,}/yr",
        f"  Max eligible benefit: ${max_benefit:,}/mo",
        "",
    ]

    # Injury rate (not age-dependent, but needs gender for rate key)
    # Show both male and female rates if gender not provided
    if has_gender:
        inj_key = f"DIPR-{risk_class}-{benefit}-{sex_code}-{wait_days}-{benefit_period}-{cov_code}-0"
        inj_rate = rates.get(inj_key)
    else:
        inj_key_m = f"DIPR-{risk_class}-{benefit}-0-{wait_days}-{benefit_period}-{cov_code}-0"
        inj_key_f = f"DIPR-{risk_class}-{benefit}-1-{wait_days}-{benefit_period}-{cov_code}-0"
        inj_rate_m = rates.get(inj_key_m)
        inj_rate_f = rates.get(inj_key_f)
        inj_rate = None  # handled below

    # Illness rate (age-banded) — only if age and gender provided
    ill_rate = None
    if has_age and has_gender:
        age_band = _get_age_band(age)
        ill_key = f"DIPR_ILL-{risk_class}-{benefit}-{age_band}-{sex_code}-{wait_days}-{benefit_period}"
        ill_rate = rates.get(ill_key)

    cov_label = "24-Hour" if cov_code == "0" else "Non-Occupational"

    lines.append(f"  ${benefit:,}/mo benefit | {cov_label}")
    lines.append("")

    # Build pricing table across wait period / benefit period combos
    # Common combos: 112-day/5yr (most common), 112-day/to-70, 30-day/5yr, 30-day/to-70
    COMMON_COMBOS = [
        ("112", "5", "112-day wait / 5-yr benefit"),
        ("112", "70", "112-day wait / to age 70"),
        ("30", "5", "30-day wait / 5-yr benefit"),
        ("30", "70", "30-day wait / to age 70"),
        ("30", "2", "30-day wait / 2-yr benefit"),
        ("0", "2", "0-day wait / 2-yr benefit"),
    ]

    # Determine if user specified wait/benefit or we should show all combos
    user_specified_combo = (wait_days != "30" or benefit_period != "5")
    show_multi = not user_specified_combo

    if show_multi:
        lines.append("PRICING OPTIONS (Injury Only):")
        lines.append("")
        for combo_wait, combo_period, combo_label in COMMON_COMBOS:
            if has_gender:
                inj_k = f"DIPR-{risk_class}-{benefit}-{sex_code}-{combo_wait}-{combo_period}-{cov_code}-0"
                inj_r = rates.get(inj_k)
                if inj_r:
                    lines.append(f"  {combo_label}: ${inj_r:.2f}/mo")
            else:
                inj_k_m = f"DIPR-{risk_class}-{benefit}-0-{combo_wait}-{combo_period}-{cov_code}-0"
                inj_k_f = f"DIPR-{risk_class}-{benefit}-1-{combo_wait}-{combo_period}-{cov_code}-0"
                r_m = rates.get(inj_k_m)
                r_f = rates.get(inj_k_f)
                if r_m or r_f:
                    parts = []
                    if r_m:
                        parts.append(f"M ${r_m:.2f}")
                    if r_f:
                        parts.append(f"F ${r_f:.2f}")
                    lines.append(f"  {combo_label}: {' / '.join(parts)}/mo")

        # Add illness+injury combos if age and gender provided
        if has_age and has_gender:
            age_band = _get_age_band(age)
            lines.append("")
            lines.append("ILLNESS + INJURY (combined):")
            lines.append("")
            for combo_wait, combo_period, combo_label in COMMON_COMBOS:
                inj_k = f"DIPR-{risk_class}-{benefit}-{sex_code}-{combo_wait}-{combo_period}-{cov_code}-0"
                ill_k = f"DIPR_ILL-{risk_class}-{benefit}-{age_band}-{sex_code}-{combo_wait}-{combo_period}"
                inj_r = rates.get(inj_k)
                ill_r = rates.get(ill_k)
                if inj_r and ill_r:
                    total = inj_r + ill_r
                    lines.append(f"  {combo_label}: ${total:.2f}/mo")
                elif ill_r:
                    lines.append(f"  {combo_label}: illness ${ill_r:.2f}/mo (injury rate N/A)")
    else:
        # User specified a specific combo — show just that one
        wait_label = EDGE_WAIT_LABELS.get(wait_days, f"{wait_days} days")
        period_label = EDGE_PERIOD_LABELS.get(benefit_period, benefit_period)
        lines.append(f"  {wait_label} wait | {period_label}")
        lines.append("")

        if has_gender:
            if inj_rate:
                lines.append(f"  Injury Only: ${inj_rate:.2f}/mo")
            else:
                lines.append(f"  Injury rate not found for this combination.")
        else:
            if inj_rate_m or inj_rate_f:
                if inj_rate_m:
                    lines.append(f"  Injury Only (Male): ${inj_rate_m:.2f}/mo")
                if inj_rate_f:
                    lines.append(f"  Injury Only (Female): ${inj_rate_f:.2f}/mo")
            else:
                lines.append(f"  Injury rate not found for this combination.")

        if has_age and has_gender and ill_rate and inj_rate:
            total = inj_rate + ill_rate
            lines.append(f"  Illness + Injury: ${total:.2f}/mo")
        elif has_age and has_gender and ill_rate:
            lines.append(f"  Illness Only: ${ill_rate:.2f}/mo")

    if not has_age or not has_gender:
        lines.append("")
        lines.append("(Add age and gender for illness + injury combined rates)")

    # Show other benefit amounts for the most common combo (112-day/5yr)
    lines.append("")
    ref_wait = "112" if show_multi else wait_days
    ref_period = "5" if show_multi else benefit_period
    lines.append(f"Other benefit amounts ({EDGE_WAIT_LABELS.get(ref_wait, ref_wait)} / {EDGE_PERIOD_LABELS.get(ref_period, ref_period)}, Injury Only):")
    lookup_sex = sex_code if has_gender else "0"
    for alt in EDGE_BENEFITS:
        if alt == benefit:
            continue
        if alt > max_benefit:
            break
        alt_inj = rates.get(f"DIPR-{risk_class}-{alt}-{lookup_sex}-{ref_wait}-{ref_period}-{cov_code}-0")
        if alt_inj:
            lines.append(f"  ${alt:,}/mo: ${alt_inj:.2f}/mo")

    lines.append("")
    lines.append("Insured by Co-operators Life Insurance Company via Edge Benefits.")

    return "\n".join(lines)


def get_pipeline_summary():
    """Get a summary of the current pipeline."""
    prospects = read_pipeline()

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p["stage"] == "Closed-Won"]

    total_aum = 0
    total_rev = 0
    for p in active:
        try:
            total_aum += float(p["aum"].replace("$", "").replace(",", "")) if p["aum"] else 0
        except ValueError:
            pass
        try:
            total_rev += float(p["revenue"].replace("$", "").replace(",", "")) if p["revenue"] else 0
        except ValueError:
            pass

    hot = len([p for p in active if (p.get("priority") or "").lower() == "hot"])

    stages = {}
    for p in active:
        s = p["stage"]
        stages[s] = stages.get(s, 0) + 1

    stage_breakdown = "\n".join(f"  • {s}: {c}" for s, c in sorted(stages.items()))
    overdue_info = get_overdue()

    return (
        f"Pipeline Summary:\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Active deals: {len(active)}\n"
        f"Pipeline value: ${total_aum:,.0f}\n"
        f"Est. revenue: ${total_rev:,.0f}\n"
        f"Hot leads: {hot}\n"
        f"Deals won: {len(won)}\n\n"
        f"By stage:\n{stage_breakdown}\n\n"
        f"{overdue_info}"
    )


def init_extra_sheets():
    """No-op — tables are created by db.init_db()."""
    pass


# ── Meeting helpers ──

def add_meeting(data: dict) -> str:
    """Add a meeting (via SQLite)."""
    # Auto-generate prep notes from pipeline if not provided
    if data.get("prospect") and not data.get("prep_notes"):
        prospects = read_pipeline()
        for p in prospects:
            if data["prospect"].lower() in p["name"].lower():
                prep_notes_auto = f"{p.get('product', '')} | {p.get('stage', '')}"
                if p.get("notes"):
                    prep_notes_auto += f" | {p['notes'][:100]}"
                data["prep_notes"] = prep_notes_auto
                break

    return db.add_meeting(data)


def get_meetings(date_filter: str = "") -> str:
    """Get upcoming meetings, optionally filtered by date."""
    all_meetings = db.read_meetings()

    meetings = [m for m in all_meetings if m.get("status", "Scheduled") != "Cancelled"]

    if date_filter:
        meetings = [m for m in meetings if date_filter in str(m.get("date", ""))]

    if not meetings:
        return "No meetings scheduled."

    lines = [f"Meetings ({len(meetings)}):"]
    for m in meetings:
        line = f"{m.get('date', '')}"
        if m.get("time"):
            line += f" {m['time']}"
        line += f" - {m.get('prospect', '')}"
        if m.get("type"):
            line += f" ({m['type']})"
        if m.get("prep_notes"):
            line += f" | {m['prep_notes']}"
        lines.append(line)
    return "\n".join(lines)


def cancel_meeting(prospect: str) -> str:
    """Cancel a meeting by prospect name."""
    all_meetings = db.read_meetings()
    for m in all_meetings:
        name = m.get("prospect", "")
        if name and prospect.lower() in name.lower():
            return db.update_meeting(m["id"], {"status": "Cancelled"})
    return f"No meeting found for '{prospect}'."


# ── Insurance Book helpers ──

def upload_insurance_book(file_path: str) -> str:
    """Process an uploaded insurance book CSV/Excel into the Insurance Book sheet."""
    # This is handled via the document handler - just a placeholder for the tool
    return "Use the file upload feature to send your insurance book."


def get_next_calls(count: int = 5) -> str:
    """Get next prospects to call from the insurance book."""
    all_entries = db.read_insurance_book()
    if not all_entries:
        return "No insurance book uploaded yet. Send me the file."

    today = date.today()
    calls = []

    for entry in all_entries:
        status = entry.get("status", "Not Called")
        if status in ("Not Interested", "Client", "Booked Meeting"):
            continue

        # Check retry date for "No Answer" / "Callback"
        if status in ("No Answer", "Callback"):
            retry = entry.get("retry_date")
            if retry:
                try:
                    retry_date = datetime.strptime(str(retry).split(" ")[0], "%Y-%m-%d").date()
                    if retry_date > today:
                        continue
                except (ValueError, IndexError):
                    pass

        calls.append({
            "id": entry["id"],
            "name": entry.get("name", ""),
            "phone": entry.get("phone", ""),
            "address": entry.get("address", ""),
            "policy_start": entry.get("policy_start", ""),
            "notes": entry.get("notes", ""),
        })

        if len(calls) >= count:
            break

    if not calls:
        return "No more calls in the book. You've been through everyone!"

    return json.dumps(calls, default=str)


def log_book_call(name: str, outcome: str, notes: str = "", retry_days: int = 3) -> str:
    """Log a call outcome in the insurance book (via SQLite)."""
    all_entries = db.read_insurance_book()

    # Find matching entry
    matched = None
    for entry in all_entries:
        if entry.get("name") and name.lower() in entry["name"].lower():
            matched = entry
            break

    if not matched:
        return f"Could not find '{name}' in the insurance book."

    entry_id = matched["id"]
    matched_name = matched["name"]
    today_str = date.today().strftime("%Y-%m-%d")

    updates = {"last_called": today_str}
    result_msg = f"Logged call with {matched_name}: {outcome}"

    if outcome.lower() in ("not interested", "declined", "remove"):
        updates["status"] = "Not Interested"
    elif outcome.lower() in ("no answer", "voicemail", "no pick up"):
        updates["status"] = "No Answer"
        retry = (date.today() + timedelta(days=retry_days)).strftime("%Y-%m-%d")
        updates["retry_date"] = retry
        result_msg += f". Retry in {retry_days} days."
    elif "meeting" in outcome.lower() or "booked" in outcome.lower():
        updates["status"] = "Booked Meeting"
        result_msg += ". Added to pipeline as New Lead."
        if notes:
            existing_notes = matched.get("notes", "")
            note_date = date.today().strftime("%m/%d")
            new_note = f"[{note_date}] {notes}"
            updates["notes"] = f"{existing_notes} | {new_note}" if existing_notes else new_note
        db.update_insurance_entry(entry_id, updates)
        # Also add to pipeline
        add_prospect({"name": matched_name, "phone": matched.get("phone", ""), "source": "Insurance Book", "stage": "New Lead", "priority": "Warm", "notes": notes or ""})
        return result_msg
    elif "callback" in outcome.lower():
        updates["status"] = "Callback"
        retry = (date.today() + timedelta(days=retry_days)).strftime("%Y-%m-%d")
        updates["retry_date"] = retry
        result_msg += f". Callback set for {retry}."
    else:
        updates["status"] = outcome

    if notes:
        existing_notes = matched.get("notes", "")
        note_date = date.today().strftime("%m/%d")
        new_note = f"[{note_date}] {notes}"
        updates["notes"] = f"{existing_notes} | {new_note}" if existing_notes else new_note

    db.update_insurance_entry(entry_id, updates)
    return result_msg


def get_book_stats() -> str:
    """Get insurance book calling stats."""
    all_entries = db.read_insurance_book()

    if not all_entries:
        return "No insurance book uploaded yet."

    total = 0
    not_called = 0
    no_answer = 0
    not_interested = 0
    booked = 0
    callback = 0
    client = 0
    other = 0

    for entry in all_entries:
        total += 1
        status = entry.get("status", "Not Called")
        if status == "Not Called":
            not_called += 1
        elif status == "No Answer":
            no_answer += 1
        elif status == "Not Interested":
            not_interested += 1
        elif status == "Booked Meeting":
            booked += 1
        elif status == "Callback":
            callback += 1
        elif status == "Client":
            client += 1
        else:
            other += 1

    if total == 0:
        return "Insurance book is empty."

    # Exclude pre-existing clients and not-called from "called" count
    called = total - not_called - client
    conversion = f"{booked/called*100:.1f}%" if called > 0 else "0%"

    lines = [
        f"Insurance Book Stats:",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Total in book: {total}",
        f"Called: {called} | Remaining: {not_called}",
        f"No answer (retry queued): {no_answer}",
        f"Callbacks pending: {callback}",
        f"Meetings booked: {booked}",
        f"Not interested: {not_interested}",
    ]
    if client:
        lines.append(f"Existing clients: {client}")
    if other:
        lines.append(f"Other: {other}")
    lines.append(f"Conversion rate: {conversion}")
    lines.append(f"Progress: {called}/{total - client} ({called/(total - client)*100:.0f}%)" if total > client else f"Progress: 0/0")

    return "\n".join(lines)


# ── Email drafting helper ──

def draft_email(prospect_name: str, email_type: str, details: str = "") -> str:
    """Draft an email for a prospect using Claude. Returns the drafted email text."""
    # Get prospect context
    prospects = read_pipeline()
    context = ""
    for p in prospects:
        if prospect_name.lower() in p["name"].lower():
            context = json.dumps(p, default=str)
            break

    prompt = f"""Draft a short, casual email for Marc Pineault (Financial Advisor at Co-operators, London Ontario) to send to a prospect.

Prospect info: {context}
Email type: {email_type}
Additional details: {details}

Marc's style:
- Very casual and direct, like texting a friend
- Short sentences, no fluff
- Signs off as "Marc Pineault" or "Marc Pineault, Financial Advisor | Co-operators"
- For quotes, just lists prices simply (e.g., "$81/mo for $500K")
- No formal language, no "I hope this finds you well"

Return ONLY the email (subject line + body). No commentary."""

    response = client.chat.completions.create(
        model="gpt-5",
        max_completion_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.choices[0].message.content


# ── Otter transcript processing ──

def process_transcript(transcript: str) -> str:
    """Process an Otter meeting transcript. Returns structured summary."""
    prompt = f"""You are a sales assistant for Marc, a financial planner who sells life insurance and wealth management.

Analyze this meeting transcript and return a structured summary:

TRANSCRIPT:
{transcript[:4000]}

Return in this EXACT format:
PROSPECT: [name]
SUMMARY: [2-3 sentence summary of key discussion points]
FINANCIAL SITUATION: [income, assets, debts mentioned]
NEEDS: [what they need - insurance, investments, retirement, etc.]
NEXT STEPS: [specific action items with dates if mentioned]
FOLLOW-UP EMAIL: [draft a short casual follow-up email in Marc's style]

Marc's email style: casual, direct, short. Signs off as "Marc Pineault, Financial Advisor | Co-operators"."""

    response = client.chat.completions.create(
        model="gpt-5",
        max_completion_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.choices[0].message.content


# ── Helper to build OpenAI tool defs ──

def _tool(name, desc, props, required=None):
    params = {"type": "object", "properties": props}
    if required:
        params["required"] = required
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}


TOOLS = [
    _tool("read_pipeline", "Read all prospects from the sales pipeline.", {}, []),
    _tool("lookup_prospect", "Look up a single prospect by name. Returns their details.", {
        "name": {"type": "string", "description": "Prospect name to search for"},
    }, ["name"]),
    _tool("message_marc", "Send a message to Marc via Telegram. Use when a coworker wants to tell Marc something.", {
        "sender": {"type": "string", "description": "Name of the person sending the message"},
        "message": {"type": "string", "description": "The message to send to Marc"},
    }, ["sender", "message"]),
    _tool("add_prospect", "Add a new prospect to the pipeline.", {
        "name": {"type": "string", "description": "Prospect's full name"},
        "phone": {"type": "string"}, "email": {"type": "string"},
        "source": {"type": "string"}, "priority": {"type": "string"},
        "stage": {"type": "string"}, "product": {"type": "string"},
        "aum": {"type": "string"}, "revenue": {"type": "string"},
        "next_followup": {"type": "string"}, "notes": {"type": "string"},
    }, ["name"]),
    _tool("update_prospect", "Update an existing prospect (partial name match).", {
        "name": {"type": "string", "description": "Prospect name to find"},
        "updates": {"type": "object", "description": "Fields to update: stage, priority, next_followup, notes, phone, email, product, aum, revenue, source, name"},
    }, ["name", "updates"]),
    _tool("delete_prospect", "Delete a prospect from the pipeline by name.", {
        "name": {"type": "string", "description": "Prospect name to delete"},
    }, ["name"]),
    _tool("add_activity", "Log an activity in the Activity Log.", {
        "prospect": {"type": "string"}, "action": {"type": "string"},
        "outcome": {"type": "string"}, "next_step": {"type": "string"}, "notes": {"type": "string"},
    }, ["prospect", "action"]),
    _tool("get_activities", "Get logged activities from the Activity Log. Use to show what's been done.", {
        "date_filter": {"type": "string", "description": "Filter by date (YYYY-MM-DD or partial)"},
        "prospect": {"type": "string", "description": "Filter by prospect name"},
    }, []),
    _tool("get_overdue", "Get prospects with overdue follow-ups.", {}, []),
    _tool("get_pipeline_summary", "Get pipeline summary: active deals, value, stages, overdue.", {}, []),
    _tool("add_meeting", "Schedule a meeting with a prospect.", {
        "date": {"type": "string", "description": "YYYY-MM-DD"}, "time": {"type": "string"},
        "prospect": {"type": "string"}, "type": {"type": "string"},
    }, ["date", "prospect"]),
    _tool("get_meetings", "Get scheduled meetings.", {
        "date_filter": {"type": "string", "description": "YYYY-MM-DD or 'this week' or empty for all"},
    }, []),
    _tool("cancel_meeting", "Cancel a meeting by prospect name.", {
        "prospect": {"type": "string"},
    }, ["prospect"]),
    _tool("get_next_calls", "Get next prospects to call from insurance book.", {
        "count": {"type": "integer", "description": "How many (default 5)"},
    }, []),
    _tool("log_book_call", "Log outcome of a call from insurance book.", {
        "name": {"type": "string"}, "outcome": {"type": "string"},
        "notes": {"type": "string"}, "retry_days": {"type": "integer"},
    }, ["name", "outcome"]),
    _tool("get_book_stats", "Get insurance book calling stats.", {}, []),
    _tool("draft_email", "Draft an email for a prospect.", {
        "prospect_name": {"type": "string"}, "email_type": {"type": "string"},
        "details": {"type": "string"},
    }, ["prospect_name", "email_type"]),
    _tool("process_transcript", "Process a meeting transcript. Extract summary, needs, next steps.", {
        "transcript": {"type": "string"},
    }, ["transcript"]),
    _tool("get_follow_up_sequence", "Get recommended follow-up cadence for a prospect's stage.", {
        "prospect_name": {"type": "string"}, "stage": {"type": "string"},
    }, ["prospect_name", "stage"]),
    _tool("auto_set_follow_up", "Auto-set next follow-up date after stage change.", {
        "prospect_name": {"type": "string"}, "stage": {"type": "string"},
    }, ["prospect_name", "stage"]),
    _tool("log_win_loss", "Log why a deal was won or lost.", {
        "prospect_name": {"type": "string"},
        "outcome": {"type": "string", "enum": ["Won", "Lost"]},
        "reason": {"type": "string"},
    }, ["prospect_name", "outcome", "reason"]),
    _tool("get_win_loss_stats", "Get win/loss analysis: win rate, reasons, product breakdown.", {}, []),
    _tool("get_term_quote", "Look up Co-operators term life insurance quotes.", {
        "age": {"type": "integer"}, "gender": {"type": "string"},
        "smoker": {"type": "boolean"}, "term": {"type": "string"},
        "amount": {"type": "integer"}, "health": {"type": "string"},
    }, ["age", "gender", "smoker", "term", "amount"]),
    _tool("get_disability_quote", "Look up Edge Benefits disability insurance quotes. Omit wait_days and benefit_period to show all common pricing options. Age/gender optional (only needed for illness rates).", {
        "age": {"type": "integer", "description": "Age of the person (optional — only needed for illness rates)"},
        "gender": {"type": "string", "description": "M or F (optional — only needed for illness rates)"},
        "occupation": {"type": "string", "description": "Job title (e.g. office worker, nurse, teacher)"},
        "income": {"type": "integer", "description": "Annual income in dollars (e.g. 50000, NOT monthly)"},
        "benefit": {"type": "integer", "description": "Desired monthly benefit amount in dollars (e.g. 3000 for $3,000/mo). 0 = auto-calculate max eligible."},
        "wait_days": {"type": "string", "description": "Waiting period: 0, 30, or 112 days. OMIT to show all options."},
        "benefit_period": {"type": "string", "description": "Benefit period: 2 (2yr), 5 (5yr), or 70 (to age 70). OMIT to show all options."},
        "coverage_type": {"type": "string", "description": "24hour or non-occupational. Default 24hour."},
    }, ["occupation", "income"]),
]

# Task management tools (used by /todo command)
TASK_TOOLS = [
    _tool("create_task", "Create a new task/to-do item.", {
        "title": {"type": "string", "description": "The task title — what needs to be done"},
        "prospect": {"type": "string", "description": "Prospect name if this task is related to a prospect. Empty string if general task."},
        "due_date": {"type": "string", "description": "Due date in YYYY-MM-DD format. Null if no due date."},
        "remind_at": {"type": "string", "description": "Reminder datetime in YYYY-MM-DD HH:MM format. Null if no reminder."},
    }, ["title"]),
    _tool("lookup_prospect", "Look up a single prospect by name. Returns their details.", {
        "name": {"type": "string", "description": "Prospect name to search for"},
    }, ["name"]),
]

TOOL_FUNCTIONS = {
    "read_pipeline": lambda _: json.dumps(read_pipeline(), default=str),
    "lookup_prospect": lambda args: lookup_prospect(args["name"]),
    "message_marc": lambda args: message_marc(args["sender"], args["message"]),
    "add_prospect": lambda args: add_prospect(args),
    "update_prospect": lambda args: update_prospect(args["name"], args.get("updates") or {k: v for k, v in args.items() if k != "name"}),
    "delete_prospect": lambda args: delete_prospect(args["name"]),
    "add_activity": lambda args: add_activity(args),
    "get_activities": lambda args: get_activities(args.get("date_filter", ""), args.get("prospect", "")),
    "get_overdue": lambda _: get_overdue(),
    "get_pipeline_summary": lambda _: get_pipeline_summary(),
    "add_meeting": lambda args: add_meeting(args),
    "get_meetings": lambda args: get_meetings(args.get("date_filter", "")),
    "cancel_meeting": lambda args: cancel_meeting(args["prospect"]),
    "get_next_calls": lambda args: get_next_calls(args.get("count", 5)),
    "log_book_call": lambda args: log_book_call(args["name"], args["outcome"], args.get("notes", ""), args.get("retry_days", 3)),
    "get_book_stats": lambda _: get_book_stats(),
    "draft_email": lambda args: draft_email(args["prospect_name"], args["email_type"], args.get("details", "")),
    "process_transcript": lambda args: process_transcript(args["transcript"]),
    "get_follow_up_sequence": lambda args: get_follow_up_sequence(args["prospect_name"], args["stage"]),
    "auto_set_follow_up": lambda args: auto_set_follow_up(args["prospect_name"], args["stage"]),
    "log_win_loss": lambda args: log_win_loss(args["prospect_name"], args["outcome"], args["reason"]),
    "get_win_loss_stats": lambda _: get_win_loss_stats(),
    "get_term_quote": lambda args: get_term_quote(args["age"], args["gender"], args.get("smoker", False), args["term"], args["amount"], args.get("health", "regular")),
    "get_disability_quote": lambda args: get_disability_quote(args.get("age", 0), args.get("gender", ""), args["occupation"], args["income"], args.get("benefit", 0), args.get("wait_days", "30"), args.get("benefit_period", "5"), args.get("coverage_type", "24hour")),
}

FORMATTING_RULE = "Reply in plain text only. No markdown, no bold, no italic, no bullet points, no numbered lists, no emojis. Write like texting. Keep it short."

# ── Focused system prompts per command context ──

PROMPT_QUOTE = """You help Marc get insurance quotes. Today is {today}.

{formatting}

Marc wants a quote. Parse his message and call the right tool.

For disability: call get_disability_quote. Calculate age from DOB if given. "3k benefit" = $3,000/mo monthly benefit amount. Income is always annual. If he says "multiple prices" or "all periods", call the tool 3 times with benefit_period 2, 5, and 70.

For term life: call get_term_quote.

If something critical is missing (age, gender, occupation, income), ask for it. Do NOT add prospects or draft emails. Just get quotes."""

PROMPT_ADD = """You help Marc add prospects to his CRM pipeline. Today is {today}.

{formatting}

Parse Marc's message and call add_prospect, then auto_set_follow_up.

Guess fields from context:
- stage: PHQ/paperwork = "Proposal Sent", just met = "Discovery Call", wants quote = "Needs Analysis", done = "Closed-Won", else = "New Lead"
- product: insurance = "Life Insurance", disability = "Disability Insurance", investments = "Wealth Management"
- revenue auto-calc: AUM → revenue = AUM x 0.009. FYC → premium = FYC / 5.555 (T20/25/30) or FYC / 4.444 (T10/15). Premium → revenue = premium.

After adding, ask ONE follow-up if product type or dollar amount is missing. Don't ask for phone or email."""

PROMPT_GENERAL = """You are Marc's sales CRM assistant. He is a financial planner in London, Ontario. Today is {today}.

{formatting}

Use context from earlier messages. When Marc gives a short reply, figure out what he means from conversation history. Never claim you did something you didn't actually do via a tool call.

After completing an action, you may ask ONE follow-up if something important is missing. Don't ask for phone or email.

Commands Marc might use:
- move/update prospect stages
- delete prospects
- pipeline summary, overdue follow-ups
- schedule/view/cancel meetings
- log calls, activities
- draft emails
- process meeting transcripts
- mark priorities"""

PROMPT_COWORKER = """You are an assistant for Marc's insurance team at Co-operators in London, Ontario. Today is {today}.

{formatting}

You are chatting with {coworker_name}, one of Marc's coworkers. Be friendly and helpful.

You can help them with:
- Looking up a prospect's status (use lookup_prospect)
- Adding new leads/prospects to Marc's pipeline (use add_prospect, then auto_set_follow_up)
- Getting disability or term life insurance quotes (use get_disability_quote or get_term_quote)
- Sending a message to Marc (use message_marc) — use this when they want to tell Marc something, ask him a question, or give him an update
- Answering general insurance questions

When they add a new prospect, set source to "Referral from {coworker_name}" and add "Added by {coworker_name}" to notes.

If they say things like "tell Marc...", "can you let Marc know...", "message Marc...", "ask Marc..." — use message_marc to relay the message.

You CANNOT help with: editing/deleting prospects, viewing the full pipeline, managing meetings, exporting data, or anything else admin-only. If they ask, let them know Marc handles that.

Keep it conversational and brief."""

# Tools available to coworkers
COWORKER_TOOL_NAMES = {"lookup_prospect", "add_prospect", "auto_set_follow_up", "get_disability_quote", "get_term_quote", "message_marc"}

def _build_prompt(template):
    return template.format(today=date.today().strftime("%Y-%m-%d"), formatting=FORMATTING_RULE)


# ── Conversation history ──
MAX_HISTORY = 20
_chat_histories = {}


async def _llm_respond(update, messages, tools=None):
    """Send messages to LLM, process tool calls, return reply."""
    response = client.chat.completions.create(
        model="gpt-5",
        max_completion_tokens=1024,
        tools=tools or TOOLS,
        tool_choice="auto",
        messages=messages,
    )

    msg = response.choices[0].message

    # Process tool calls in a loop (max 8 rounds)
    tool_rounds = 0
    while msg.tool_calls and tool_rounds < 8:
        tool_rounds += 1
        messages.append(msg)

        for tool_call in msg.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_input = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError as e:
                logger.error(f"Bad tool args for {tool_name}: {e}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": f"Error: could not parse tool arguments: {e}",
                })
                continue
            logger.info(f"Tool call: {tool_name}({json.dumps(tool_input)})")

            func = TOOL_FUNCTIONS.get(tool_name)
            if func:
                with pipeline_lock:
                    result = func(tool_input)
            else:
                result = f"Unknown tool: {tool_name}"

            # Check for cross-sell trigger on Closed-Won
            if tool_name == "update_prospect" and isinstance(tool_input, dict):
                updates = tool_input.get("updates") or {k: v for k, v in tool_input.items() if k != "name"}
                if isinstance(updates, dict) and "stage" in updates:
                    import scoring
                    stage_val = updates.get("stage", "")
                    if "closed-won" in str(stage_val).lower() or "won" in str(stage_val).lower():
                        prospect_name = tool_input.get("name", "")
                        prospect = db.get_prospect_by_name(prospect_name)
                        if prospect:
                            suggestions = scoring.get_cross_sell_suggestions(prospect.get("product", ""))
                            if suggestions:
                                result += f"\n\nCross-sell: {prospect['name']} has {prospect.get('product', '?')}. Suggest {', '.join(suggestions[:2])} in 30 days."

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })

        response = client.chat.completions.create(
            model="gpt-5",
            max_completion_tokens=1024,
            tools=tools or TOOLS,
            messages=messages,
        )
        msg = response.choices[0].message

    if not msg.content and msg.tool_calls:
        logger.warning("Hit tool loop limit without text response")
        return "I ran into a loop processing that. Please try again or rephrase."
    return msg.content or "Done!"


MAX_CHAT_IDS = 100

def _get_history(chat_id):
    if chat_id not in _chat_histories:
        if len(_chat_histories) >= MAX_CHAT_IDS:
            # Evict oldest chat history to prevent unbounded memory growth
            oldest = next(iter(_chat_histories))
            del _chat_histories[oldest]
        _chat_histories[chat_id] = []
    return _chat_histories[chat_id]


def _save_history(chat_id, user_msg, reply):
    history = _get_history(chat_id)
    history.append({"role": "user", "content": user_msg})
    history.append({"role": "assistant", "content": reply})
    if len(history) > MAX_HISTORY * 2:
        _chat_histories[chat_id] = history[-(MAX_HISTORY * 2):]


# ── /quote command ──

async def cmd_quote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /quote command — disability or term life quotes."""
    user_msg = update.message.text.replace("/quote", "", 1).strip()
    if not user_msg:
        await update.message.reply_text(
            "Usage: /quote disability office worker 50k income 3k benefit\n"
            "Or: /quote term 35 male nonsmoker 500k 20yr"
        )
        return

    chat_id = update.effective_chat.id
    logger.info(f"/quote: {user_msg}")

    try:
        # Quote-only tools
        quote_tools = [t for t in TOOLS if t["function"]["name"] in ("get_disability_quote", "get_term_quote")]

        messages = [{"role": "system", "content": _build_prompt(PROMPT_QUOTE)}]
        messages.extend(_get_history(chat_id))
        messages.append({"role": "user", "content": user_msg})

        reply = await _llm_respond(update, messages, tools=quote_tools)
        _save_history(chat_id, f"[quote] {user_msg}", reply)
        await update.message.reply_text(reply)
        logger.info(f"/quote replied: {reply[:100]}")

    except Exception as e:
        logger.error(f"/quote error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


# ── /add command ──

PROMPT_ADD_COWORKER = """You help add prospects to Marc's CRM pipeline. Today is {{today}}.

{{formatting}}

The prospect is being added by a coworker: {coworker_name}.

Parse their message and call add_prospect with:
- product: guess from context — "Life Insurance", "Disability Insurance", "Wealth Management", "Home Insurance", "Auto Insurance", "Commercial Insurance", etc.
- source: "Referral from {coworker_name}"
- stage: guess from context — just met = "Discovery Call", wants quote = "Needs Analysis", else = "New Lead"
- priority: guess — interested = "Hot", mentioned = "Warm", else = "Cold"
- notes: include any details from the message, plus "Added by {coworker_name}"

Then call auto_set_follow_up.

After adding, confirm the prospect was added. Don't ask follow-up questions."""


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command — add prospect to pipeline."""
    is_admin = _is_admin(update)
    user_msg = update.message.text.replace("/add", "", 1).strip()

    if not is_admin:
        # Coworkers can add prospects
        if not user_msg:
            await update.message.reply_text(
                "Add a prospect:\n"
                "/add John Smith, interested in life insurance, 35 years old"
            )
            return

        chat_id = update.effective_chat.id
        coworker = update.effective_user.first_name or "Coworker"
        logger.info(f"/add (coworker {coworker}): {user_msg}")

        try:
            add_tools = [t for t in TOOLS if t["function"]["name"] in ("add_prospect", "auto_set_follow_up")]
            prompt = PROMPT_ADD_COWORKER.format(coworker_name=coworker)
            # Double-brace placeholders become single for _build_prompt
            prompt = prompt.replace("{{today}}", "{today}").replace("{{formatting}}", "{formatting}")
            messages = [{"role": "system", "content": _build_prompt(prompt)}]
            messages.append({"role": "user", "content": user_msg})

            reply = await _llm_respond(update, messages, tools=add_tools)
            _save_history(chat_id, f"[add-coworker:{coworker}] {user_msg}", reply)
            await update.message.reply_text(reply)
            logger.info(f"/add coworker replied: {reply[:100]}")

            # Notify Marc about the new prospect
            if ADMIN_CHAT_ID:
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"New lead added by {coworker}:\n{user_msg}"
                    )
                except Exception as e:
                    logger.warning(f"Could not notify admin: {e}")

        except Exception as e:
            logger.error(f"/add coworker error: {e}")
            await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")
        return

    # Admin flow — full access
    if not user_msg:
        await update.message.reply_text(
            "Usage: /add John Smith, 500k AUM, wealth management, hot, referral from Sarah"
        )
        return

    chat_id = update.effective_chat.id
    logger.info(f"/add: {user_msg}")

    try:
        add_tools = [t for t in TOOLS if t["function"]["name"] in ("add_prospect", "auto_set_follow_up")]

        messages = [{"role": "system", "content": _build_prompt(PROMPT_ADD)}]
        messages.append({"role": "user", "content": user_msg})

        reply = await _llm_respond(update, messages, tools=add_tools)
        _save_history(chat_id, f"[add] {user_msg}", reply)
        await update.message.reply_text(reply)
        logger.info(f"/add replied: {reply[:100]}")

    except Exception as e:
        logger.error(f"/add error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


# ── /status command — available to everyone ──

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command — look up a prospect by name. Available to all users."""
    name = update.message.text.replace("/status", "", 1).strip()
    if not name:
        await update.message.reply_text("Usage: /status John Smith")
        return

    prospect = db.get_prospect_by_name(name)
    if not prospect:
        await update.message.reply_text(f"No prospect found matching '{name}'.")
        return

    lines = [
        f"{prospect['name']}",
        f"━━━━━━━━━━━━━━━━",
        f"  Stage: {prospect.get('stage', 'N/A')}",
        f"  Priority: {prospect.get('priority', 'N/A')}",
        f"  Product: {prospect.get('product', 'N/A')}",
    ]
    if prospect.get("next_followup"):
        lines.append(f"  Next follow-up: {prospect['next_followup']}")
    if prospect.get("notes"):
        # Show last 200 chars of notes to keep it brief
        notes = prospect["notes"]
        if len(notes) > 200:
            notes = "..." + notes[-200:]
        lines.append(f"  Notes: {notes}")

    await update.message.reply_text("\n".join(lines))


# ── /msg command — coworkers can message Marc ──

async def cmd_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /msg command — send a message to Marc. Available to coworkers."""
    msg_text = update.message.text.replace("/msg", "", 1).strip()
    sender = update.effective_user.first_name or "Coworker"

    if _is_admin(update):
        await update.message.reply_text("You're Marc! This command is for your coworkers to message you.")
        return

    if not msg_text:
        await update.message.reply_text("Usage: /msg Hey Marc, can we chat about the Johnson file?")
        return

    result = message_marc(sender, msg_text)
    await update.message.reply_text(result)


# ── /call command — quick call logging ──

PROMPT_CALL = """You help Marc log call outcomes quickly. Today is {today}.

{formatting}

Marc just told you about a call. Parse the message and:

1. Use lookup_prospect to find the prospect (if name given)
2. Use add_activity to log the call with:
   - prospect: the person's name
   - action: "Phone call" or "Call attempt"
   - outcome: what happened (connected, voicemail, no answer, booked meeting, etc.)
   - next_step: what to do next (if mentioned or obvious)
3. If they mentioned a follow-up date or next step, use update_prospect to set next_followup
4. If the call outcome changes the stage (e.g. booked a meeting = Discovery Call, sent proposal = Proposal Sent), use update_prospect to update stage

Common outcomes to recognize:
- "no answer" / "VM" / "voicemail" → log as no answer, set follow-up 2-3 days
- "left message" → log as voicemail, set follow-up 3 days
- "booked" / "meeting" → log as booked meeting, advance stage
- "not interested" → log as declined, move to Nurture or Closed-Lost
- "great call" / "interested" → log as positive call, keep stage or advance
- "callback" → log as callback requested, set follow-up as mentioned

Reply with a SHORT confirmation. One or two lines max. Example: "Logged call with John Smith — voicemail, follow-up Friday."
Do NOT ask follow-up questions."""


async def cmd_call(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /call command — quick call logging."""
    if not await _require_admin(update):
        return
    user_msg = update.message.text
    # Strip /call or /log prefix
    for prefix in ("/call", "/log"):
        if user_msg.lower().startswith(prefix):
            user_msg = user_msg[len(prefix):].strip()
            break

    if not user_msg:
        await update.message.reply_text(
            "Quick call log:\n"
            "/call John Smith - voicemail\n"
            "/call Sarah Jones - booked discovery call\n"
            "/call Mike - no answer\n"
            "/call Lisa - great call, sending proposal Friday"
        )
        return

    chat_id = update.effective_chat.id
    logger.info(f"/call: {user_msg}")

    try:
        call_tools = [t for t in TOOLS if t["function"]["name"] in (
            "lookup_prospect", "add_activity", "update_prospect", "auto_set_follow_up", "add_prospect"
        )]

        messages = [{"role": "system", "content": _build_prompt(PROMPT_CALL)}]
        messages.append({"role": "user", "content": user_msg})

        reply = await _llm_respond(update, messages, tools=call_tools)
        _save_history(chat_id, f"[call] {user_msg}", reply)
        await update.message.reply_text(reply)
        logger.info(f"/call replied: {reply[:100]}")

    except Exception as e:
        logger.error(f"/call error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


# ── /todo, /tasks, /done — task management ──

PROMPT_TODO = """You help create tasks and to-do items. Today is {today}.

{formatting}

The user wants to create a task. Parse their message to extract:
1. title — the core task (required)
2. prospect — a prospect/client name if mentioned (use lookup_prospect to verify). Empty string if not prospect-related.
3. due_date — in YYYY-MM-DD format if a date is mentioned ("by Friday", "March 15", "tomorrow", "next week" = next Monday)
4. remind_at — in YYYY-MM-DD HH:MM format if they want a reminder ("remind me Thursday 9am"). Default to 09:00 if time not specified.

If the user says "@marc" or "for marc", note that in your response — the caller will handle assignment.

Call create_task with the parsed fields. Reply with a SHORT confirmation showing what was created. One or two lines max."""


async def cmd_todo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /todo command — create a task."""
    user_msg = update.message.text
    for prefix in ("/todo", "/td"):
        if user_msg.lower().startswith(prefix):
            user_msg = user_msg[len(prefix):].strip()
            break

    if not user_msg:
        await update.message.reply_text(
            "Create a task:\n"
            "/todo send John the brochure by Friday\n"
            "/todo renew E&O insurance by March 20 remind me March 19 9am\n"
            "/todo @marc review Sarah's application"
        )
        return

    chat_id = str(update.effective_chat.id)
    is_admin = _is_admin(update)
    logger.info(f"/todo from {chat_id}: {user_msg}")

    try:
        messages = [{"role": "system", "content": _build_prompt(PROMPT_TODO)}]
        messages.append({"role": "user", "content": user_msg})

        response = client.chat.completions.create(
            model="gpt-5",
            max_completion_tokens=512,
            tools=TASK_TOOLS,
            tool_choice="auto",
            messages=messages,
        )

        msg = response.choices[0].message

        # Process tool calls (max 4 rounds)
        tool_rounds = 0
        while msg.tool_calls and tool_rounds < 4:
            tool_rounds += 1
            messages.append(msg)

            for tool_call in msg.tool_calls:
                tool_name = tool_call.function.name
                try:
                    tool_input = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError as e:
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"Error: {e}"})
                    continue

                logger.info(f"/todo tool: {tool_name}({json.dumps(tool_input)})")

                if tool_name == "create_task":
                    assigned_to = chat_id
                    if "@marc" in user_msg.lower() or "for marc" in user_msg.lower():
                        assigned_to = ADMIN_CHAT_ID
                    elif not is_admin:
                        assigned_to = chat_id

                    task_data = {
                        "title": tool_input.get("title", user_msg),
                        "prospect": tool_input.get("prospect", ""),
                        "due_date": tool_input.get("due_date"),
                        "remind_at": tool_input.get("remind_at"),
                        "assigned_to": assigned_to,
                        "created_by": chat_id,
                    }
                    result = db.add_task(task_data)
                    if result:
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"Task #{result['id']} created successfully."})
                    else:
                        messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": "Error: could not create task (missing title?)."})

                elif tool_name == "lookup_prospect":
                    with pipeline_lock:
                        p = TOOL_FUNCTIONS["lookup_prospect"](tool_input)
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": str(p)})
                else:
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": f"Unknown tool: {tool_name}"})

            response = client.chat.completions.create(
                model="gpt-5",
                max_completion_tokens=512,
                tools=TASK_TOOLS,
                messages=messages,
            )
            msg = response.choices[0].message

        reply = msg.content or "Task created!"
        _save_history(chat_id, f"[todo] {user_msg}", reply)
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"/todo error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /tasks command — list pending tasks."""
    chat_id = str(update.effective_chat.id)
    is_admin = _is_admin(update)

    if is_admin:
        tasks = db.get_tasks()
    else:
        tasks = db.get_tasks(assigned_to=chat_id)

    if not tasks:
        await update.message.reply_text("No pending tasks. Use /todo to add one!")
        return

    today = date.today()
    lines = ["Your tasks:\n"] if not is_admin else ["All tasks:\n"]

    for t in tasks:
        due = t.get("due_date")
        if due:
            try:
                due_dt = datetime.strptime(due, "%Y-%m-%d").date()
                days_diff = (due_dt - today).days
                if days_diff < 0:
                    emoji = "\U0001f534"  # red circle
                    due_str = f"due {due} — {abs(days_diff)}d overdue"
                elif days_diff == 0:
                    emoji = "\U0001f7e1"  # yellow circle
                    due_str = "due today"
                else:
                    emoji = "\U0001f4cb"  # clipboard
                    due_str = f"due {due}"
            except ValueError:
                emoji = "\U0001f4cb"
                due_str = f"due {due}"
        else:
            emoji = "\U0001f4cb"
            due_str = "no due date"

        prospect_str = f" [{t['prospect']}]" if t.get("prospect") else ""
        lines.append(f"{emoji} #{t['id']} {t['title']}{prospect_str} ({due_str})")

    lines.append("\nReply /done <id> to complete")
    await update.message.reply_text("\n".join(lines))


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /done command — complete a task."""
    user_msg = update.message.text.replace("/done", "", 1).strip()
    chat_id = str(update.effective_chat.id)
    is_admin = _is_admin(update)

    if not user_msg:
        await update.message.reply_text("Usage: /done <task id>\nExample: /done 12")
        return

    try:
        task_id = int(user_msg.split()[0])
    except ValueError:
        await update.message.reply_text("Please provide a task ID number. Use /tasks to see your tasks.")
        return

    result = db.complete_task(task_id, chat_id, is_admin=is_admin)
    await update.message.reply_text(result)


# ── /pipeline, /overdue, /meetings, /calls — direct tool commands ──

async def cmd_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    try:
        result = get_pipeline_summary()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    try:
        result = get_overdue()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    try:
        result = get_meetings()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_calls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    try:
        result = get_next_calls()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    try:
        result = get_win_loss_stats()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    """Show ranked call list with scores and actions."""
    import scoring
    ranked = scoring.get_ranked_call_list(10)
    if not ranked:
        await update.message.reply_text("No active deals in pipeline.")
        return

    lines = ["YOUR CALL LIST (ranked by score):", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━", ""]
    for i, p in enumerate(ranked, 1):
        reasons_str = " | ".join(p.get("reasons", [])[:2])
        lines.append(f"{i}. {p['name']} — score: {p['score']}")
        lines.append(f"   Stage: {p.get('stage', '?')} | {p.get('priority', '?')}")
        if reasons_str:
            lines.append(f"   Why: {reasons_str}")
        lines.append(f"   Do: {p.get('action', 'Follow up')}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


# ── /lead command ──

async def cmd_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /lead command — paste in a lead email or referral info."""
    if not await _require_admin(update):
        return
    user_msg = update.message.text.replace("/lead", "", 1).strip()
    if not user_msg:
        await update.message.reply_text(
            "Paste a lead email or referral info after /lead:\n"
            "/lead Mike Johnson, 35, looking for life insurance, referred by his neighbor. 519-555-5678"
        )
        return

    logger.info(f"/lead: {user_msg[:100]}")
    await update.message.reply_text("Processing lead...")

    try:
        from intake import process_email_lead
        result = process_email_lead({
            "from": "Telegram paste",
            "subject": "",
            "body": user_msg,
        })
        await update.message.reply_text(result)
    except Exception as e:
        logger.error(f"/lead error: {e}")
        await update.message.reply_text(f"Error processing lead: {str(e)[:200]}")


# ── Free-form message handler ──

def _is_otter_transcript(text: str) -> bool:
    """Detect if a message is an Otter.ai transcript from Zapier."""
    markers = ["Title:", "Abstract summary:", "Outline:", "Action items:"]
    matches = sum(1 for m in markers if m.lower() in text.lower())
    return matches >= 2


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form text messages — admin gets full access, coworkers get limited assistant."""
    is_admin = _is_admin(update)
    user_msg = update.message.text
    if not user_msg:
        return

    chat_id = update.effective_chat.id

    # Coworker chat flow
    if not is_admin:
        coworker = update.effective_user.first_name or "Coworker"
        logger.info(f"Coworker {coworker}: {user_msg}")

        try:
            coworker_tools = [t for t in TOOLS if t["function"]["name"] in COWORKER_TOOL_NAMES]
            prompt = PROMPT_COWORKER.replace("{coworker_name}", coworker)
            history = _get_history(chat_id)
            messages = [{"role": "system", "content": _build_prompt(prompt)}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_msg})

            reply = await _llm_respond(update, messages, tools=coworker_tools)
            _save_history(chat_id, user_msg, reply)
            await update.message.reply_text(reply)
            logger.info(f"Coworker {coworker} reply: {reply[:100]}")

            # Notify Marc if the coworker added a prospect or did something actionable
            action_keywords = ["added", "new prospect", "prospect:", "pipeline"]
            if ADMIN_CHAT_ID and any(kw in reply.lower() for kw in action_keywords):
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"Update from {coworker}:\n{reply}"
                    )
                except Exception as e:
                    logger.warning(f"Could not notify admin: {e}")

        except Exception as e:
            logger.error(f"Coworker chat error: {e}")
            await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")
        return

    # Admin flow
    logger.info(f"Received: {user_msg}")

    # Detect Otter.ai transcripts from Zapier and process as call transcripts
    if _is_otter_transcript(user_msg):
        logger.info("Detected Otter.ai transcript — processing as call transcript")
        try:
            await update.message.reply_text("Got an Otter transcript, processing...")
            from voice_handler import extract_and_update
            db.add_interaction({
                "prospect": "",
                "source": "otter_transcript",
                "raw_text": user_msg[:5000],
            })
            result = await extract_and_update(user_msg, source="otter_transcript")
            await update.message.reply_text(result)
        except Exception as e:
            logger.error(f"Otter transcript error: {e}")
            await update.message.reply_text(f"Error processing transcript: {str(e)[:200]}")
        return

    try:
        history = _get_history(chat_id)
        messages = [{"role": "system", "content": _build_prompt(PROMPT_GENERAL)}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_msg})

        reply = await _llm_respond(update, messages)
        _save_history(chat_id, user_msg, reply)
        await update.message.reply_text(reply)
        logger.info(f"Replied: {reply[:100]}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    """Send the pipeline database file to the user."""
    try:
        db_path = db.DB_PATH
        if Path(db_path).exists():
            with open(db_path, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"Pipeline_{date.today().strftime('%Y-%m-%d')}.db",
                    caption="Here's your current pipeline database."
                )
        else:
            await update.message.reply_text("Pipeline database not found.")
    except Exception as e:
        await update.message.reply_text(f"Error sending file: {str(e)[:200]}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded files — pipeline Excel or insurance book CSV/Excel."""
    if not await _require_admin(update):
        return
    doc = update.message.document
    fname = doc.file_name.lower()

    # CSV = insurance book
    if fname.endswith('.csv'):
        try:
            import csv
            import io

            file = await doc.get_file()
            file_bytes = await file.download_as_bytearray()
            try:
                text = file_bytes.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = file_bytes.decode('latin-1')
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)

            if not rows:
                await update.message.reply_text("CSV is empty.")
                return

            # Clear existing insurance book entries via db
            # (delete all, then re-import)
            existing = db.read_insurance_book()
            with db.get_db() as conn:
                conn.execute("DELETE FROM insurance_book")

            # Import — detect if first row is a header or data
            first_row_lower = [str(c).lower().strip() for c in rows[0]] if rows else []
            has_header = any(kw in " ".join(first_row_lower) for kw in ("name", "phone", "tel", "address", "client", "first", "last", "email", "date"))
            header = first_row_lower if has_header else []
            data_rows = rows[1:] if has_header else rows

            count = 0
            for i, row in enumerate(data_rows):
                if not row or not row[0].strip():
                    continue
                entry = {"name": row[0].strip(), "phone": "", "address": "", "policy_start": "", "notes": ""}
                # Try to map other columns
                for j, val in enumerate(row[1:], 1):
                    if j < len(header):
                        h = header[j] if j < len(header) else ""
                        if "phone" in h or "tel" in h:
                            entry["phone"] = val.strip()
                        elif "address" in h or "addr" in h:
                            entry["address"] = val.strip()
                        elif "date" in h or "start" in h or "inception" in h:
                            entry["policy_start"] = val.strip()
                        else:
                            entry["notes"] = f"{entry['notes']} {val.strip()}".strip()
                    elif j == 1:
                        entry["phone"] = val.strip()
                    elif j == 2:
                        entry["address"] = val.strip()
                    elif j == 3:
                        entry["policy_start"] = val.strip()
                    else:
                        entry["notes"] = f"{entry['notes']} {val.strip()}".strip()

                entry["status"] = "Not Called"
                db.add_insurance_entry(entry)
                count += 1

            await update.message.reply_text(
                f"Insurance book loaded! {count} contacts imported.\n"
                f"Text 'calls' to get your first batch."
            )
            logger.info(f"Insurance book imported: {count} contacts from {doc.file_name}")

        except Exception as e:
            await update.message.reply_text(f"Error importing CSV: {str(e)[:200]}")
        return

    if not fname.endswith(('.xlsx', '.xls')):
        await update.message.reply_text("Send me an .xlsx or .csv file.")
        return

    try:
        # Check if it looks like a pipeline file or an insurance book
        file = await doc.get_file()

        if "insurance" in fname or "book" in fname or "home" in fname or "client" in fname:
            # Import as insurance book
            import openpyxl as _openpyxl
            await file.download_to_drive("/tmp/book_upload.xlsx")
            src_wb = _openpyxl.load_workbook("/tmp/book_upload.xlsx")
            src_ws = src_wb.active

            # Clear existing insurance book
            with db.get_db() as conn:
                conn.execute("DELETE FROM insurance_book")

            count = 0
            start = 2 if src_ws.cell(row=1, column=1).value and any(
                h in str(src_ws.cell(row=1, column=1).value).lower()
                for h in ["name", "client", "first", "last"]
            ) else 1

            for r in range(start, src_ws.max_row + 1):
                name = src_ws.cell(row=r, column=1).value
                if not name:
                    continue
                entry = {"name": str(name), "status": "Not Called"}
                col_map = {2: "phone", 3: "address", 4: "policy_start", 5: "status", 6: "last_called", 7: "notes"}
                for c in range(2, min(src_ws.max_column + 1, 8)):
                    val = src_ws.cell(row=r, column=c).value
                    if val and c in col_map:
                        entry[col_map[c]] = str(val)
                entry["status"] = "Not Called"  # Override any imported status
                db.add_insurance_entry(entry)
                count += 1

            src_wb.close()

            await update.message.reply_text(
                f"Insurance book loaded! {count} contacts imported.\n"
                f"Text 'calls' to get your first batch."
            )
        else:
            # Import Excel as pipeline migration
            tmp_path = "/tmp/pipeline_upload.xlsx"
            await file.download_to_drive(tmp_path)
            result = db.migrate_from_excel(tmp_path)

            await update.message.reply_text(
                f"Pipeline imported from your file.\n{result}\n"
                f"All changes are live now."
            )

        logger.info(f"File processed: {doc.file_name}")
    except Exception as e:
        await update.message.reply_text(f"Error processing file: {str(e)[:200]}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        await update.message.reply_text(
            "Welcome! I'm Marc's assistant. You can just chat with me naturally.\n\n"
            "Things I can help with:\n"
            "- Add a lead: just tell me about them\n"
            "- Check on a prospect: 'how's John Smith doing?'\n"
            "- Get a quote: 'disability quote for an office worker making 50k'\n"
            "- Message Marc: 'tell Marc I need to talk about the Johnson file'\n"
            "- Send a voice note about a prospect\n\n"
            "Or use commands:\n"
            "/quote — insurance quotes\n"
            "/add — add a prospect\n"
            "/status — check on a prospect\n"
            "/msg — send Marc a message\n\n"
            "Marc gets notified when you add a lead."
        )
        return

    await update.message.reply_text(
        "Hey Marc! Here are your commands:\n\n"
        "/quote — insurance quotes\n"
        "  /quote disability office worker 50k income 3k benefit\n"
        "  /quote term 35 male nonsmoker 500k 20yr\n\n"
        "/add — add a prospect\n"
        "  /add John Smith, 300k AUM, wealth management, hot, referral\n\n"
        "/call — quick call log\n"
        "  /call John Smith - voicemail\n"
        "  /call Sarah - booked discovery call\n\n"
        "/pipeline — see your deals\n"
        "/priority — ranked call list with scores\n"
        "/overdue — who needs follow-up\n"
        "/meetings — view meetings\n"
        "/calls — next prospects to call\n"
        "/stats — win/loss stats\n"
        "/export — download pipeline database\n"
        "/lead — paste in a referral or lead email\n\n"
        "Send a voice message after any call/meeting and I'll auto-update your pipeline.\n"
        "Otter.ai transcripts from Zapier are auto-processed too.\n\n"
        "You can also type anything and I'll figure it out:\n"
        "  move Sarah to discovery call\n"
        "  meeting with John Thursday 2pm\n"
        "  called Mike, booked meeting\n"
        "  draft follow-up email for Sarah\n\n"
        "Let's close some deals."
    )


def build_application():
    """Build the Telegram Application with all handlers (shared by main and webhook)."""
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("export", export))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("q", cmd_quote))  # shortcut
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("msg", cmd_msg))
    app.add_handler(CommandHandler("call", cmd_call))
    app.add_handler(CommandHandler("log", cmd_call))  # alias
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("td", cmd_todo))    # alias
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("pipeline", cmd_pipeline))
    app.add_handler(CommandHandler("overdue", cmd_overdue))
    app.add_handler(CommandHandler("meetings", cmd_meetings))
    app.add_handler(CommandHandler("calls", cmd_calls))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("priority", cmd_priority))
    app.add_handler(CommandHandler("p", cmd_priority))  # shortcut
    app.add_handler(CommandHandler("lead", cmd_lead))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    async def error_handler(update, context):
        logger.error(f"Bot error: {context.error}")

    app.add_error_handler(error_handler)
    return app


# Global references for webhook mode
telegram_app = None
bot_event_loop = None


def init_bot():
    """Initialize the bot in webhook mode. Called once at startup."""
    global telegram_app, bot_event_loop

    # Initialize SQLite database
    db.init_db()

    # One-time migration from Excel (skips if DB already has data)
    excel_path = os.path.join(DATA_DIR, "pipeline.xlsx") if DATA_DIR else "pipeline.xlsx"
    if os.path.exists(excel_path):
        result = db.migrate_from_excel(excel_path)
        logger.info(f"Migration check: {result}")

    # Build the application
    telegram_app = build_application()

    # Create a dedicated event loop for the bot in a background thread
    bot_event_loop = asyncio.new_event_loop()

    def run_loop():
        asyncio.set_event_loop(bot_event_loop)
        bot_event_loop.run_forever()

    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()

    # Initialize the application and set webhook
    async def setup():
        await telegram_app.initialize()
        await telegram_app.start()

        # Set webhook URL — Railway provides RAILWAY_PUBLIC_DOMAIN
        domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
        telegram_webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        if domain:
            webhook_url = f"https://{domain}/webhook"
            webhook_kwargs = {"url": webhook_url, "drop_pending_updates": True}
            if telegram_webhook_secret:
                webhook_kwargs["secret_token"] = telegram_webhook_secret
            await telegram_app.bot.set_webhook(**webhook_kwargs)
            logger.info(f"Webhook set: {webhook_url}")
        else:
            logger.warning("RAILWAY_PUBLIC_DOMAIN not set — webhook not configured")

    future = asyncio.run_coroutine_threadsafe(setup(), bot_event_loop)
    future.result(timeout=30)  # wait for setup to complete

    # Start scheduler for morning briefings and auto-nags
    try:
        from scheduler import start_scheduler
        start_scheduler(telegram_app, event_loop=bot_event_loop)
        logger.info("Scheduler started (morning briefing + auto-nags).")
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}. Bot will run without scheduled messages.")

    logger.info("Bot initialized in webhook mode.")


def process_webhook_update(update_data: dict):
    """Process an incoming webhook update from Telegram. Non-blocking."""
    if telegram_app is None or bot_event_loop is None:
        logger.error("Bot not initialized")
        return

    async def _process():
        try:
            update = Update.de_json(update_data, telegram_app.bot)
            await telegram_app.process_update(update)
        except Exception as e:
            logger.error(f"Error processing update: {e}")

    asyncio.run_coroutine_threadsafe(_process(), bot_event_loop)


def main():
    """Start the bot + dashboard (webhook mode)."""
    from dashboard import app as flask_app, register_webhook

    # Initialize bot (sets up webhook with Telegram)
    init_bot()

    # Register webhook route on Flask app (pass callback to avoid __main__ vs bot module split)
    register_webhook(flask_app, process_webhook_update)

    # Handle SIGTERM from Railway for clean shutdown
    def handle_sigterm(signum, frame):
        logger.info("SIGTERM received — shutting down...")
        os._exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    # Run Flask as the main process (serves dashboard + webhook)
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Bot started (webhook mode). Dashboard on port {port}.")
    flask_app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
