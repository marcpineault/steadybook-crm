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

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OPENAI_KEY = os.environ["OPENAI_API_KEY"]

# DATA_DIR kept for migration path reference
DATA_DIR = os.environ.get("DATA_DIR", "")

client = OpenAI(api_key=OPENAI_KEY)

# DEPRECATED — kept for scheduler import compat
pipeline_lock = threading.RLock()


def read_pipeline():
    """Read all prospects from pipeline (via SQLite)."""
    return db.read_pipeline()


def add_prospect(data: dict) -> str:
    """Add a new prospect (via SQLite)."""
    return db.add_prospect(data)


def update_prospect(name: str, updates: dict) -> str:
    """Update a prospect by name (via SQLite)."""
    return db.update_prospect(name, updates)


def delete_prospect(name: str) -> str:
    """Delete a prospect by name (via SQLite)."""
    return db.delete_prospect(name)


def add_activity(data: dict) -> str:
    """Add entry to activity log (via SQLite)."""
    return db.add_activity(data)


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
    birth_year = 2026 - age
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


def get_disability_quote(age: int, gender: str, occupation: str, income: int,
                         benefit: int = 0, wait_days: str = "30",
                         benefit_period: str = "5", coverage_type: str = "24hour") -> str:
    """Look up Edge Benefits disability insurance rates."""
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

    sex_code = "0" if gender.lower().startswith("m") else "1"
    cov_code = "0" if coverage_type == "24hour" else "1"
    gender_label = "Male" if sex_code == "0" else "Female"

    matched_title = occ_lower if occ_lower != occupation.lower().strip() else occupation.title()
    lines = [
        f"EDGE BENEFITS DISABILITY QUOTE",
        f"━━━━━━━━━━━━━━━━",
        f"  {age}{gender_label[0]}, {matched_title}",
        f"  Risk Class: {risk_class} | Income: ${income:,}/yr",
        f"  Max eligible benefit: ${max_benefit:,}/mo",
        "",
    ]

    # Injury rate (not age-dependent)
    inj_key = f"DIPR-{risk_class}-{benefit}-{sex_code}-{wait_days}-{benefit_period}-{cov_code}-0"
    inj_rate = rates.get(inj_key)

    # Illness rate (age-banded)
    age_band = _get_age_band(age)
    ill_key = f"DIPR_ILL-{risk_class}-{benefit}-{age_band}-{sex_code}-{wait_days}-{benefit_period}"
    ill_rate = rates.get(ill_key)

    wait_label = EDGE_WAIT_LABELS.get(wait_days, f"{wait_days} days")
    period_label = EDGE_PERIOD_LABELS.get(benefit_period, benefit_period)
    cov_label = "24-Hour" if cov_code == "0" else "Non-Occupational"

    lines.append(f"  ${benefit:,}/mo benefit | {wait_label} wait | {period_label} | {cov_label}")
    lines.append("")

    if inj_rate:
        lines.append(f"  Injury Only: ${inj_rate:.2f}/mo")
    else:
        lines.append(f"  Injury rate not found for this combination.")

    # Show comparison table for different benefit amounts (injury only)
    lines.append("")
    lines.append("Other benefit amounts (Injury Only):")
    for alt in EDGE_BENEFITS:
        if alt == benefit:
            continue
        if alt > max_benefit:
            break
        alt_inj = rates.get(f"DIPR-{risk_class}-{alt}-{sex_code}-{wait_days}-{benefit_period}-{cov_code}-0")
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

    hot = len([p for p in active if p["priority"].lower() == "hot"])

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
        model="gpt-4.1",
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
        model="gpt-4.1",
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
    _tool("get_disability_quote", "Look up Edge Benefits disability insurance quotes. Returns injury-only rates.", {
        "age": {"type": "integer", "description": "Age of the person"},
        "gender": {"type": "string", "description": "M or F"},
        "occupation": {"type": "string", "description": "Job title (e.g. office worker, nurse, teacher)"},
        "income": {"type": "integer", "description": "Annual income in dollars (e.g. 50000, NOT monthly)"},
        "benefit": {"type": "integer", "description": "Desired monthly benefit amount in dollars (e.g. 3000 for $3,000/mo). 0 = auto-calculate max eligible."},
        "wait_days": {"type": "string", "description": "Waiting period: 0, 30, or 112 days. Default 30."},
        "benefit_period": {"type": "string", "description": "Benefit period: 2 (2yr), 5 (5yr), or 70 (to age 70). Default 5."},
        "coverage_type": {"type": "string", "description": "24hour or non-occupational. Default 24hour."},
    }, ["age", "gender", "occupation", "income"]),
]

TOOL_FUNCTIONS = {
    "read_pipeline": lambda _: json.dumps(read_pipeline(), default=str),
    "add_prospect": lambda args: add_prospect(args),
    "update_prospect": lambda args: update_prospect(args["name"], args.get("updates") or {k: v for k, v in args.items() if k != "name"}),
    "delete_prospect": lambda args: delete_prospect(args["name"]),
    "add_activity": lambda args: add_activity(args),
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
    "get_disability_quote": lambda args: get_disability_quote(args["age"], args["gender"], args["occupation"], args["income"], args.get("benefit", 0), args.get("wait_days", "30"), args.get("benefit_period", "5"), args.get("coverage_type", "24hour")),
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

def _build_prompt(template):
    return template.format(today=date.today().strftime("%Y-%m-%d"), formatting=FORMATTING_RULE)


# ── Conversation history ──
MAX_HISTORY = 20
_chat_histories = {}


async def _llm_respond(update, messages, tools=None):
    """Send messages to LLM, process tool calls, return reply."""
    response = client.chat.completions.create(
        model="gpt-4.1",
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
            tool_input = json.loads(tool_call.function.arguments)
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
            model="gpt-4.1",
            max_completion_tokens=1024,
            tools=tools or TOOLS,
            messages=messages,
        )
        msg = response.choices[0].message

    return msg.content or "Done!"


def _get_history(chat_id):
    if chat_id not in _chat_histories:
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
            "Usage: /quote disability female 30 office worker 50k income 3k benefit\n"
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

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command — add prospect to pipeline."""
    user_msg = update.message.text.replace("/add", "", 1).strip()
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


# ── /pipeline, /overdue, /meetings, /calls — direct tool commands ──

async def cmd_pipeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = get_pipeline_summary()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_overdue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = get_overdue()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_meetings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = get_meetings()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_calls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = get_next_calls()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        result = get_win_loss_stats()
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:200]}")


async def cmd_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


# ── Free-form message handler (still works for everything else) ──

# ── Free-form message handler ──

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-form text messages."""
    user_msg = update.message.text
    if not user_msg:
        return

    chat_id = update.effective_chat.id
    logger.info(f"Received: {user_msg}")

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
    await update.message.reply_text(
        "Hey Marc! Here are your commands:\n\n"
        "/quote — insurance quotes\n"
        "  /quote disability female 30 office worker 50k income 3k benefit\n"
        "  /quote term 35 male nonsmoker 500k 20yr\n\n"
        "/add — add a prospect\n"
        "  /add John Smith, 300k AUM, wealth management, hot, referral\n\n"
        "/pipeline — see your deals\n"
        "/priority — ranked call list with scores\n"
        "/overdue — who needs follow-up\n"
        "/meetings — view meetings\n"
        "/calls — next prospects to call\n"
        "/stats — win/loss stats\n"
        "/export — download pipeline database\n\n"
        "You can also type anything and I'll figure it out:\n"
        "  move Sarah to discovery call\n"
        "  meeting with John Thursday 2pm\n"
        "  called Mike, booked meeting\n"
        "  draft follow-up email for Sarah\n\n"
        "Let's close some deals."
    )


def main():
    # Initialize SQLite database
    db.init_db()

    # One-time migration from Excel (skips if DB already has data)
    excel_path = os.path.join(DATA_DIR, "pipeline.xlsx") if DATA_DIR else "pipeline.xlsx"
    if os.path.exists(excel_path):
        result = db.migrate_from_excel(excel_path)
        logger.info(f"Migration check: {result}")

    # Start web dashboard in background thread
    from dashboard import start_dashboard_thread
    start_dashboard_thread()
    logger.info("Web dashboard started.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("export", export))
    app.add_handler(CommandHandler("quote", cmd_quote))
    app.add_handler(CommandHandler("q", cmd_quote))  # shortcut
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("pipeline", cmd_pipeline))
    app.add_handler(CommandHandler("overdue", cmd_overdue))
    app.add_handler(CommandHandler("meetings", cmd_meetings))
    app.add_handler(CommandHandler("calls", cmd_calls))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("priority", cmd_priority))
    app.add_handler(CommandHandler("p", cmd_priority))  # shortcut
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Error handler to suppress 409 Conflict spam during redeploys
    async def error_handler(update, context):
        if "Conflict" in str(context.error):
            return  # ignore 409s during redeploy overlap
        logger.error(f"Bot error: {context.error}")

    app.add_error_handler(error_handler)

    # Start scheduler for morning briefings and auto-nags
    try:
        from scheduler import start_scheduler
        start_scheduler(app)
        logger.info("Scheduler started (morning briefing + auto-nags).")
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}. Bot will run without scheduled messages.")

    # Handle SIGTERM from Railway so old instance stops polling immediately
    def handle_sigterm(signum, frame):
        logger.info("SIGTERM received — shutting down polling...")
        os._exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    logger.info("Bot started. Listening for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
