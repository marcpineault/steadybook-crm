import os
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
PIPELINE_PATH = os.environ.get("PIPELINE_PATH", "pipeline.xlsx")

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

# ── Styling constants ──
TEAL = "1ABC9C"
NAVY = "0F1B2D"
WHITE = "FFFFFF"
LIGHT_GRAY = "F0F2F5"
MED_GRAY = "DDE1E6"
TEXT_COLOR = "2C3E50"

thin_border = Border(
    left=Side(style='thin', color=MED_GRAY), right=Side(style='thin', color=MED_GRAY),
    top=Side(style='thin', color=MED_GRAY), bottom=Side(style='thin', color=MED_GRAY),
)

# ── Pipeline Excel helpers ──

DATA_START = 5  # row where data begins
MAX_ROWS = 80   # max rows to scan

PIPELINE_COLS = {
    "name": 1, "phone": 2, "email": 3, "source": 4,
    "priority": 5, "stage": 6, "product": 7,
    "aum": 8, "revenue": 9, "first_contact": 10,
    "next_followup": 11, "days_open": 12, "notes": 13,
}

LOG_COLS = {
    "date": 1, "prospect": 2, "action": 3,
    "outcome": 4, "next_step": 5, "notes": 6,
}


def read_pipeline():
    """Read all prospects from pipeline."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]
    prospects = []
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        name = ws.cell(row=r, column=1).value
        if not name:
            continue
        prospect = {"row": r}
        for field, col in PIPELINE_COLS.items():
            val = ws.cell(row=r, column=col).value
            if val is not None:
                prospect[field] = str(val)
            else:
                prospect[field] = ""
        prospects.append(prospect)
    wb.close()
    return prospects


def add_prospect(data: dict) -> str:
    """Add a new prospect to the first empty row."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    # Find first empty row
    target_row = None
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return "Pipeline is full! No empty rows available."

    field_map = {
        "name": 1, "phone": 2, "email": 3, "source": 4,
        "priority": 5, "stage": 6, "product": 7,
        "aum": 8, "revenue": 9, "first_contact": 10,
        "next_followup": 11, "notes": 13,
    }

    for field, col in field_map.items():
        if field in data and data[field]:
            val = data[field]
            if field == "aum" or field == "revenue":
                try:
                    val = float(str(val).replace("$", "").replace(",", ""))
                except ValueError:
                    pass
            ws.cell(row=target_row, column=col, value=val)

    # Default first_contact to today if not set
    if not data.get("first_contact"):
        ws.cell(row=target_row, column=10, value=date.today().strftime("%Y-%m-%d"))

    # Default stage to New Lead if not set
    if not data.get("stage"):
        ws.cell(row=target_row, column=6, value="New Lead")

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Added {data.get('name', 'prospect')} to pipeline (row {target_row})."


def update_prospect(name: str, updates: dict) -> str:
    """Update a prospect by name (partial match)."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    target_row = None
    matched_name = None
    name_lower = name.lower()

    for r in range(DATA_START, DATA_START + MAX_ROWS):
        cell_val = ws.cell(row=r, column=1).value
        if cell_val and name_lower in str(cell_val).lower():
            target_row = r
            matched_name = cell_val
            break

    if not target_row:
        wb.close()
        return f"Could not find prospect matching '{name}'."

    field_map = {
        "name": 1, "phone": 2, "email": 3, "source": 4,
        "priority": 5, "stage": 6, "product": 7,
        "aum": 8, "revenue": 9, "first_contact": 10,
        "next_followup": 11, "notes": 13,
    }

    changes = []
    for field, value in updates.items():
        if field in field_map and value:
            col = field_map[field]
            if field in ("aum", "revenue"):
                try:
                    value = float(str(value).replace("$", "").replace(",", ""))
                except ValueError:
                    pass
            ws.cell(row=target_row, column=col, value=value)
            changes.append(f"{field} → {value}")

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Updated {matched_name}: {', '.join(changes)}"


def add_activity(data: dict) -> str:
    """Add entry to Activity Log sheet."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Activity Log"]

    target_row = None
    for r in range(3, 103):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return "Activity log is full!"

    field_map = {"date": 1, "prospect": 2, "action": 3, "outcome": 4, "next_step": 5, "notes": 6}

    for field, col in field_map.items():
        if field in data and data[field]:
            ws.cell(row=target_row, column=col, value=data[field])

    if not data.get("date"):
        ws.cell(row=target_row, column=1, value=date.today().strftime("%Y-%m-%d"))

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Logged activity for {data.get('prospect', 'unknown')}."


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
    """Log why a deal was won or lost."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)

    # Ensure Win/Loss sheet exists
    if "Win Loss Log" not in wb.sheetnames:
        ws = wb.create_sheet("Win Loss Log")
        ws.merge_cells('A1:E1')
        c = ws['A1']
        c.value = "WIN / LOSS LOG"
        c.font = Font(name='Aptos', size=18, bold=True, color=WHITE)
        c.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type='solid')
        c.alignment = Alignment(horizontal='left', vertical='center', indent=1)
        ws.row_dimensions[1].height = 50
        headers = ["Date", "Prospect", "Outcome", "Reason", "Product"]
        for i, h in enumerate(headers, 1):
            cell = ws.cell(row=2, column=i, value=h)
            cell.font = Font(name='Aptos', size=10, bold=True, color=WHITE)
            cell.fill = PatternFill(start_color=TEAL, end_color=TEAL, fill_type='solid')
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.border = thin_border
        ws.column_dimensions['A'].width = 14
        ws.column_dimensions['B'].width = 24
        ws.column_dimensions['C'].width = 14
        ws.column_dimensions['D'].width = 40
        ws.column_dimensions['E'].width = 20
        ws.freeze_panes = 'A3'
    else:
        ws = wb["Win Loss Log"]

    # Find prospect's product from pipeline
    ps = wb["Pipeline"]
    product = ""
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        cell_val = ps.cell(row=r, column=1).value
        if cell_val and prospect_name.lower() in str(cell_val).lower():
            product = str(ps.cell(row=r, column=7).value or "")
            break

    # Find first empty row
    target_row = None
    for r in range(3, 103):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return "Win/Loss log is full!"

    ws.cell(row=target_row, column=1, value=date.today().strftime("%Y-%m-%d"))
    ws.cell(row=target_row, column=2, value=prospect_name)
    ws.cell(row=target_row, column=3, value=outcome)
    ws.cell(row=target_row, column=4, value=reason)
    ws.cell(row=target_row, column=5, value=product)

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Logged {outcome} for {prospect_name}: {reason}"


def get_win_loss_stats() -> str:
    """Get win/loss analysis: patterns, reasons, conversion by product."""
    wb = openpyxl.load_workbook(PIPELINE_PATH, data_only=True)
    if "Win Loss Log" not in wb.sheetnames:
        wb.close()
        return "No win/loss data yet. Close some deals first!"

    ws = wb["Win Loss Log"]
    wins = []
    losses = []
    for r in range(3, 103):
        outcome = ws.cell(row=r, column=3).value
        if not outcome:
            continue
        entry = {
            "date": str(ws.cell(row=r, column=1).value or ""),
            "prospect": str(ws.cell(row=r, column=2).value or ""),
            "reason": str(ws.cell(row=r, column=4).value or ""),
            "product": str(ws.cell(row=r, column=5).value or ""),
        }
        if outcome.lower() in ("won", "closed-won"):
            wins.append(entry)
        else:
            losses.append(entry)

    wb.close()

    total = len(wins) + len(losses)
    if total == 0:
        return "No win/loss data yet."

    win_rate = len(wins) / total * 100

    # Reason tallies
    win_reasons = {}
    for w in wins:
        r = w["reason"]
        if r:
            win_reasons[r] = win_reasons.get(r, 0) + 1

    loss_reasons = {}
    for l in losses:
        r = l["reason"]
        if r:
            loss_reasons[r] = loss_reasons.get(r, 0) + 1

    # Product breakdown
    product_wins = {}
    product_losses = {}
    for w in wins:
        p = w["product"] or "Unknown"
        product_wins[p] = product_wins.get(p, 0) + 1
    for l in losses:
        p = l["product"] or "Unknown"
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

RATE_FILE = "cooperators_rates.json"
EDGE_RATE_FILE = "edge_benefits_rates.json"
_rate_cache = None
_edge_cache = None


def _load_rates():
    global _rate_cache
    if _rate_cache is None and Path(RATE_FILE).exists():
        with open(RATE_FILE, "r") as f:
            _rate_cache = json.load(f)
    return _rate_cache or {}


def _load_edge_rates():
    global _edge_cache
    if _edge_cache is None and Path(EDGE_RATE_FILE).exists():
        with open(EDGE_RATE_FILE, "r") as f:
            _edge_cache = json.load(f)
    return _edge_cache or {}


RATE_AMOUNTS = [100000, 250000, 500000, 750000, 1000000]


def _closest_amount(amount: int) -> int:
    return min(RATE_AMOUNTS, key=lambda x: abs(x - amount))


def get_term_quote(age: int, gender: str, smoker: bool, term: str, amount: int, health: str = "regular") -> str:
    """Look up Co-operators term life insurance rates."""
    sex = "M" if gender.lower().startswith("m") else "F"
    smoke = "Y" if smoker else "N"
    face = _closest_amount(amount)
    term_str = str(term).strip()

    rates = _load_rates()
    key = f"{age}_{sex}_{smoke}_{term_str}_{face}"

    if key in rates:
        r = rates[key]
        sex_name = "Male" if sex == "M" else "Female"
        smoke_name = "Smoker" if smoker else "Non-Smoker"
        lines = [
            f"CO-OPERATORS QUOTE — {r.get('product', 'Versatile Term ' + term_str)}",
            f"━━━━━━━━━━━━━━━━",
            f"  {age}{sex_name[0]} {smoke_name}, ${face:,} coverage",
            f"  Annual: ${r['annual']}/yr",
            f"  Monthly: ${r['monthly']}/mo",
            "",
            f"Health class: Regular (standard rates)",
        ]

        # Also show nearby amounts if available
        other_lines = []
        for alt_face in RATE_AMOUNTS:
            if alt_face == face:
                continue
            alt_key = f"{age}_{sex}_{smoke}_{term_str}_{alt_face}"
            if alt_key in rates:
                ar = rates[alt_key]
                other_lines.append(f"  ${alt_face:,}: ${ar['annual']}/yr (${ar['monthly']}/mo)")

        if other_lines:
            lines.append("\nOther coverage amounts:")
            lines.extend(other_lines)

        return "\n".join(lines)
    else:
        # Rate not in table — give lookup instructions
        sex_name = "Male" if sex == "M" else "Female"
        smoke_name = "Smoker" if smoker else "Non-Smoker"
        return (
            f"Rate not found for {age}{sex_name[0]} {smoke_name}, ${amount:,} Term {term_str}.\n"
            f"Check term4sale.ca → N6A 1A1, {age}{sex_name[0]}, {smoke_name}, Regular, "
            f"${amount:,}, Term {term_str}\n"
            f"Co-operators product: Versatile Term {term_str}"
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

    # Determine risk class from occupation
    occ_lower = occupation.lower().strip()
    risk_class = None

    # Direct lookup
    if occ_lower in occupations:
        occ_code = str(occupations[occ_lower])
        rate_key = f"OCCR-RATE-{occ_code}"
        risk_class = rates.get(rate_key)

    # Fuzzy match
    if not risk_class:
        matches = [(k, v) for k, v in occupations.items() if occ_lower in k.lower()]
        if matches:
            occ_code = str(matches[0][1])
            rate_key = f"OCCR-RATE-{occ_code}"
            risk_class = rates.get(rate_key)
            occ_lower = matches[0][0]

    if not risk_class:
        return f"Occupation '{occupation}' not found in Edge Benefits database. Try a different title."

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

    lines = [
        f"EDGE BENEFITS DISABILITY QUOTE",
        f"━━━━━━━━━━━━━━━━",
        f"  {age}{gender_label[0]}, {occupation.title()}",
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
        lines.append(f"  Injury Only:      ${inj_rate:.2f}/mo")
    if ill_rate:
        lines.append(f"  Illness Only:     ${ill_rate:.2f}/mo")
    if inj_rate and ill_rate:
        lines.append(f"  Injury + Illness: ${inj_rate + ill_rate:.2f}/mo")

    if not inj_rate and not ill_rate:
        lines.append(f"  Rate not found for this combination.")

    # Show comparison table for different benefit amounts
    lines.append("")
    lines.append("Other benefit amounts (Injury+Illness):")
    for alt in EDGE_BENEFITS:
        if alt == benefit:
            continue
        if alt > max_benefit:
            break
        alt_inj = rates.get(f"DIPR-{risk_class}-{alt}-{sex_code}-{wait_days}-{benefit_period}-{cov_code}-0")
        alt_ill = rates.get(f"DIPR_ILL-{risk_class}-{alt}-{age_band}-{sex_code}-{wait_days}-{benefit_period}")
        if alt_inj and alt_ill:
            lines.append(f"  ${alt:,}/mo: ${alt_inj + alt_ill:.2f}/mo")

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


# ── Ensure sheets exist ──

def ensure_sheet(sheet_name, headers, col_widths=None):
    """Create a sheet if it doesn't exist in the pipeline."""
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    if sheet_name not in wb.sheetnames:
        ws = wb.create_sheet(sheet_name)
        # Title row
        ws.merge_cells(f'A1:{chr(64+len(headers))}1')
        c = ws['A1']
        c.value = sheet_name.upper()
        c.font = Font(name='Aptos', size=18, bold=True, color=WHITE)
        c.fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type='solid')
        c.alignment = Alignment(horizontal='left', vertical='center', indent=1)
        ws.row_dimensions[1].height = 50
        # Headers
        for i, h in enumerate(headers, 1):
            cell = ws.cell(row=2, column=i, value=h)
            cell.font = Font(name='Aptos', size=10, bold=True, color=WHITE)
            cell.fill = PatternFill(start_color=TEAL, end_color=TEAL, fill_type='solid')
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = thin_border
            if col_widths and i <= len(col_widths):
                ws.column_dimensions[chr(64+i)].width = col_widths[i-1]
        ws.freeze_panes = 'A3'
        wb.save(PIPELINE_PATH)
    wb.close()


def init_extra_sheets():
    """Initialize Meetings and Insurance Book sheets if they don't exist."""
    ensure_sheet("Meetings",
                 ["Date", "Time", "Prospect", "Type", "Prep Notes", "Status"],
                 [14, 10, 24, 18, 40, 14])
    ensure_sheet("Insurance Book",
                 ["Name", "Phone", "Address", "Policy Start", "Status", "Last Called", "Notes", "Retry Date"],
                 [24, 16, 30, 14, 14, 14, 35, 14])


# ── Meeting helpers ──

def add_meeting(data: dict) -> str:
    """Add a meeting to the Meetings sheet."""
    init_extra_sheets()
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Meetings"]

    target_row = None
    for r in range(3, 103):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return "Meetings sheet is full!"

    fields = {"date": 1, "time": 2, "prospect": 3, "type": 4, "prep_notes": 5, "status": 6}
    for field, col in fields.items():
        if field in data and data[field]:
            ws.cell(row=target_row, column=col, value=data[field])

    if not data.get("status"):
        ws.cell(row=target_row, column=6, value="Scheduled")

    # Auto-fill prep notes from pipeline
    if data.get("prospect") and not data.get("prep_notes"):
        prospects = read_pipeline()
        for p in prospects:
            if data["prospect"].lower() in p["name"].lower():
                notes = f"{p['product']} | {p['stage']}"
                if p["notes"]:
                    notes += f" | {p['notes'][:100]}"
                ws.cell(row=target_row, column=5, value=notes)
                break

    wb.save(PIPELINE_PATH)
    wb.close()
    return f"Meeting added: {data.get('prospect', '?')} on {data.get('date', '?')} at {data.get('time', '?')}"


def get_meetings(date_filter: str = "") -> str:
    """Get upcoming meetings, optionally filtered by date."""
    init_extra_sheets()
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Meetings"]

    meetings = []
    for r in range(3, 103):
        d = ws.cell(row=r, column=1).value
        if not d:
            continue
        status = str(ws.cell(row=r, column=6).value or "Scheduled")
        if status == "Cancelled":
            continue
        meetings.append({
            "date": str(d),
            "time": str(ws.cell(row=r, column=2).value or ""),
            "prospect": str(ws.cell(row=r, column=3).value or ""),
            "type": str(ws.cell(row=r, column=4).value or ""),
            "prep_notes": str(ws.cell(row=r, column=5).value or ""),
            "status": status,
            "row": r,
        })
    wb.close()

    if date_filter:
        meetings = [m for m in meetings if date_filter in m["date"]]

    if not meetings:
        return "No meetings scheduled."

    return json.dumps(meetings, default=str)


def cancel_meeting(prospect: str) -> str:
    """Cancel a meeting by prospect name."""
    init_extra_sheets()
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Meetings"]

    for r in range(3, 103):
        name = ws.cell(row=r, column=3).value
        if name and prospect.lower() in str(name).lower():
            ws.cell(row=r, column=6, value="Cancelled")
            wb.save(PIPELINE_PATH)
            wb.close()
            return f"Cancelled meeting with {name}."

    wb.close()
    return f"No meeting found for '{prospect}'."


# ── Insurance Book helpers ──

def upload_insurance_book(file_path: str) -> str:
    """Process an uploaded insurance book CSV/Excel into the Insurance Book sheet."""
    # This is handled via the document handler - just a placeholder for the tool
    return "Use the file upload feature to send your insurance book."


def get_next_calls(count: int = 5) -> str:
    """Get next prospects to call from the insurance book."""
    init_extra_sheets()
    wb = openpyxl.load_workbook(PIPELINE_PATH)

    if "Insurance Book" not in wb.sheetnames:
        wb.close()
        return "No insurance book uploaded yet. Send me the file."

    ws = wb["Insurance Book"]
    today = date.today()
    calls = []

    for r in range(3, 503):
        name = ws.cell(row=r, column=1).value
        if not name:
            continue

        status = str(ws.cell(row=r, column=5).value or "Not Called")
        if status in ("Not Interested", "Client", "Booked Meeting"):
            continue

        # Check retry date for "No Answer" / "Callback"
        if status in ("No Answer", "Callback"):
            retry = ws.cell(row=r, column=8).value
            if retry:
                try:
                    retry_date = datetime.strptime(str(retry).split(" ")[0], "%Y-%m-%d").date()
                    if retry_date > today:
                        continue
                except (ValueError, IndexError):
                    pass

        calls.append({
            "row": r,
            "name": str(name),
            "phone": str(ws.cell(row=r, column=2).value or ""),
            "address": str(ws.cell(row=r, column=3).value or ""),
            "policy_start": str(ws.cell(row=r, column=4).value or ""),
            "notes": str(ws.cell(row=r, column=7).value or ""),
        })

        if len(calls) >= count:
            break

    wb.close()

    if not calls:
        return "No more calls in the book. You've been through everyone!"

    return json.dumps(calls, default=str)


def log_book_call(name: str, outcome: str, notes: str = "", retry_days: int = 3) -> str:
    """Log a call outcome in the insurance book."""
    init_extra_sheets()
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Insurance Book"]

    target_row = None
    matched_name = None
    for r in range(3, 503):
        cell_val = ws.cell(row=r, column=1).value
        if cell_val and name.lower() in str(cell_val).lower():
            target_row = r
            matched_name = cell_val
            break

    if not target_row:
        wb.close()
        return f"Could not find '{name}' in the insurance book."

    today_str = date.today().strftime("%Y-%m-%d")
    ws.cell(row=target_row, column=6, value=today_str)  # Last Called

    result_msg = f"Logged call with {matched_name}: {outcome}"

    if outcome.lower() in ("not interested", "declined", "remove"):
        ws.cell(row=target_row, column=5, value="Not Interested")
    elif outcome.lower() in ("no answer", "voicemail", "no pick up"):
        ws.cell(row=target_row, column=5, value="No Answer")
        retry = (date.today() + timedelta(days=retry_days)).strftime("%Y-%m-%d")
        ws.cell(row=target_row, column=8, value=retry)
        result_msg += f". Retry in {retry_days} days."
    elif "meeting" in outcome.lower() or "booked" in outcome.lower():
        ws.cell(row=target_row, column=5, value="Booked Meeting")
        result_msg += ". Added to pipeline as New Lead."
        # Also add to pipeline
        phone = str(ws.cell(row=target_row, column=2).value or "")
        wb.save(PIPELINE_PATH)
        wb.close()
        add_prospect({"name": str(matched_name), "phone": phone, "source": "Insurance Book", "stage": "New Lead", "priority": "Warm"})
        return result_msg
    elif "callback" in outcome.lower():
        ws.cell(row=target_row, column=5, value="Callback")
        retry = (date.today() + timedelta(days=retry_days)).strftime("%Y-%m-%d")
        ws.cell(row=target_row, column=8, value=retry)
        result_msg += f". Callback set for {retry}."
    else:
        ws.cell(row=target_row, column=5, value=outcome)

    if notes:
        ws.cell(row=target_row, column=7, value=notes)

    wb.save(PIPELINE_PATH)
    wb.close()
    return result_msg


def get_book_stats() -> str:
    """Get insurance book calling stats."""
    init_extra_sheets()
    wb = openpyxl.load_workbook(PIPELINE_PATH)

    if "Insurance Book" not in wb.sheetnames:
        wb.close()
        return "No insurance book uploaded yet."

    ws = wb["Insurance Book"]

    total = 0
    not_called = 0
    no_answer = 0
    not_interested = 0
    booked = 0
    callback = 0
    client = 0

    for r in range(3, 503):
        name = ws.cell(row=r, column=1).value
        if not name:
            continue
        total += 1
        status = str(ws.cell(row=r, column=5).value or "Not Called")
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

    wb.close()

    called = total - not_called
    conversion = f"{booked/called*100:.1f}%" if called > 0 else "0%"

    return (
        f"Insurance Book Stats:\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Total in book: {total}\n"
        f"Called: {called} | Remaining: {not_called}\n"
        f"No answer (retry queued): {no_answer}\n"
        f"Callbacks pending: {callback}\n"
        f"Meetings booked: {booked}\n"
        f"Not interested: {not_interested}\n"
        f"Conversion rate: {conversion}\n"
        f"Progress: {called}/{total} ({called/total*100:.0f}%)" if total > 0 else "Insurance book is empty."
    )


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

    prompt = f"""Draft a short, casual email for Marc (financial planner at Calm Money, London Ontario) to send to a prospect.

Prospect info: {context}
Email type: {email_type}
Additional details: {details}

Marc's style:
- Very casual and direct, like texting a friend
- Short sentences, no fluff
- Signs off as "Marc" or "Marc / Calm Money"
- For quotes, just lists prices simply (e.g., "$81/mo for $500K")
- No formal language, no "I hope this finds you well"

Return ONLY the email (subject line + body). No commentary."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


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

Marc's email style: casual, direct, short. Signs off as "Marc / Calm Money"."""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text


# ── Available tools for Claude ──

TOOLS = [
    {
        "name": "read_pipeline",
        "description": "Read all prospects from the sales pipeline. Returns a list of all prospects with their details.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_prospect",
        "description": "Add a new prospect to the pipeline. Use 'New Lead' as default stage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prospect's full name"},
                "phone": {"type": "string", "description": "Phone number"},
                "email": {"type": "string", "description": "Email address"},
                "source": {"type": "string", "enum": ["Referral", "Website", "Social Media", "Seminar", "Cold Outreach", "LinkedIn", "Podcast", "Networking", "Centre of Influence", "Other"]},
                "priority": {"type": "string", "enum": ["Hot", "Warm", "Cold"]},
                "stage": {"type": "string", "enum": ["New Lead", "Contacted", "Discovery Call", "Needs Analysis", "Plan Presentation", "Proposal Sent", "Negotiation", "Closed-Won", "Closed-Lost", "Nurture"]},
                "product": {"type": "string", "enum": ["Life Insurance", "Wealth Management", "Life Insurance + Wealth", "Disability Insurance", "Critical Illness", "Group Benefits", "Estate Planning", "Other"]},
                "aum": {"type": "string", "description": "Estimated premium or AUM value"},
                "revenue": {"type": "string", "description": "Estimated annual revenue from this client"},
                "next_followup": {"type": "string", "description": "Next follow-up date in YYYY-MM-DD format"},
                "notes": {"type": "string", "description": "Any notes about the prospect"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_prospect",
        "description": "Update an existing prospect's information. Search by name (partial match works).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Prospect name to search for (partial match)"},
                "updates": {
                    "type": "object",
                    "description": "Fields to update",
                    "properties": {
                        "stage": {"type": "string"},
                        "priority": {"type": "string"},
                        "next_followup": {"type": "string"},
                        "notes": {"type": "string"},
                        "phone": {"type": "string"},
                        "email": {"type": "string"},
                        "product": {"type": "string"},
                        "aum": {"type": "string"},
                        "revenue": {"type": "string"},
                        "source": {"type": "string"},
                    },
                },
            },
            "required": ["name", "updates"],
        },
    },
    {
        "name": "add_activity",
        "description": "Log an activity/touchpoint in the Activity Log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect": {"type": "string", "description": "Prospect name"},
                "action": {"type": "string", "description": "What was done (e.g., Phone Call, Email, Meeting)"},
                "outcome": {"type": "string", "description": "Result of the activity"},
                "next_step": {"type": "string", "description": "What to do next"},
                "notes": {"type": "string", "description": "Additional notes"},
            },
            "required": ["prospect", "action"],
        },
    },
    {
        "name": "get_overdue",
        "description": "Get all prospects with overdue follow-up dates.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_pipeline_summary",
        "description": "Get a full summary of the current pipeline: active deals, value, stages, overdue items.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "add_meeting",
        "description": "Schedule a meeting with a prospect. Auto-fills prep notes from pipeline.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Meeting date in YYYY-MM-DD format"},
                "time": {"type": "string", "description": "Meeting time (e.g., '2:00 PM')"},
                "prospect": {"type": "string", "description": "Prospect name"},
                "type": {"type": "string", "enum": ["Discovery Call", "Plan Presentation", "Review", "Follow-Up", "Closing", "Other"]},
            },
            "required": ["date", "prospect"],
        },
    },
    {
        "name": "get_meetings",
        "description": "Get scheduled meetings. Optionally filter by date (YYYY-MM-DD) or 'this week'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_filter": {"type": "string", "description": "Date to filter by (YYYY-MM-DD), or leave empty for all"},
            },
            "required": [],
        },
    },
    {
        "name": "cancel_meeting",
        "description": "Cancel a meeting by prospect name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect": {"type": "string", "description": "Prospect name to cancel meeting for"},
            },
            "required": ["prospect"],
        },
    },
    {
        "name": "get_next_calls",
        "description": "Get the next prospects to call from the insurance book.",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of calls to get (default 5)"},
            },
            "required": [],
        },
    },
    {
        "name": "log_book_call",
        "description": "Log the outcome of a call from the insurance book. Outcomes: 'not interested', 'no answer', 'booked meeting [date]', 'callback [date]'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Person's name from the book"},
                "outcome": {"type": "string", "description": "Call outcome"},
                "notes": {"type": "string", "description": "Any notes"},
                "retry_days": {"type": "integer", "description": "Days until retry for no answer/callback (default 3)"},
            },
            "required": ["name", "outcome"],
        },
    },
    {
        "name": "get_book_stats",
        "description": "Get insurance book calling statistics: total, called, remaining, meetings booked, conversion rate.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_email",
        "description": "Draft an email for a prospect. Types: 'follow-up', 'quote', 'intro', 'check-in', 'referral request'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {"type": "string", "description": "Prospect name"},
                "email_type": {"type": "string", "description": "Type of email to draft"},
                "details": {"type": "string", "description": "Additional details (e.g., quote prices, context)"},
            },
            "required": ["prospect_name", "email_type"],
        },
    },
    {
        "name": "process_transcript",
        "description": "Process a meeting transcript (from Otter or pasted). Extracts summary, needs, next steps, and drafts follow-up email. Use when receiving a long message that looks like a meeting transcript.",
        "input_schema": {
            "type": "object",
            "properties": {
                "transcript": {"type": "string", "description": "The meeting transcript text"},
            },
            "required": ["transcript"],
        },
    },
    {
        "name": "get_follow_up_sequence",
        "description": "Get the recommended follow-up cadence for a prospect based on their current stage. Shows what to do on which day.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {"type": "string", "description": "Prospect name"},
                "stage": {"type": "string", "description": "Current pipeline stage"},
            },
            "required": ["prospect_name", "stage"],
        },
    },
    {
        "name": "auto_set_follow_up",
        "description": "Automatically set the next follow-up date based on the stage's follow-up sequence. Call this after moving a prospect to a new stage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {"type": "string", "description": "Prospect name"},
                "stage": {"type": "string", "description": "Current pipeline stage"},
            },
            "required": ["prospect_name", "stage"],
        },
    },
    {
        "name": "log_win_loss",
        "description": "Log why a deal was won or lost. Call this whenever a prospect is moved to Closed-Won or Closed-Lost. Ask Marc for the reason.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {"type": "string", "description": "Prospect name"},
                "outcome": {"type": "string", "enum": ["Won", "Lost"], "description": "Whether the deal was won or lost"},
                "reason": {"type": "string", "description": "Why the deal was won or lost (e.g., 'great rapport', 'went with competitor', 'price too high', 'referral trust')"},
            },
            "required": ["prospect_name", "outcome", "reason"],
        },
    },
    {
        "name": "get_win_loss_stats",
        "description": "Get win/loss analysis: win rate, top reasons for winning and losing, breakdown by product.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_term_quote",
        "description": "Look up term life insurance quotes from term4sale.ca. Returns competitive rates from multiple carriers including Co-operators. Use when Marc says 'quote' or asks for life insurance rates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "age": {"type": "integer", "description": "Client's age"},
                "gender": {"type": "string", "enum": ["male", "female"], "description": "Client's gender"},
                "smoker": {"type": "boolean", "description": "Whether client smokes/uses tobacco"},
                "term": {"type": "string", "enum": ["10", "15", "20", "25", "30"], "description": "Term length in years"},
                "amount": {"type": "integer", "description": "Coverage amount in dollars (e.g., 500000)"},
                "health": {"type": "string", "enum": ["regular"], "description": "Health class. Always use 'regular'."},
            },
            "required": ["age", "gender", "smoker", "term", "amount"],
        },
    },
    {
        "name": "get_disability_quote",
        "description": "Look up disability insurance quotes from Edge Benefits (insured by Co-operators). Returns monthly premiums for injury and illness income protection. Use when Marc says 'disability quote' or asks for DI rates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "age": {"type": "integer", "description": "Client's age (18-69)"},
                "gender": {"type": "string", "enum": ["male", "female"], "description": "Client's gender"},
                "occupation": {"type": "string", "description": "Client's occupation (e.g., 'accountant', 'nurse', 'teacher')"},
                "income": {"type": "integer", "description": "Client's annual employment income"},
                "benefit": {"type": "integer", "description": "Desired monthly benefit amount ($1000-$6000 in $500 increments). 0 = auto-calculate max eligible."},
                "wait_days": {"type": "string", "enum": ["0", "30", "112"], "description": "Waiting period in days. Default 30."},
                "benefit_period": {"type": "string", "enum": ["2", "5", "70"], "description": "Benefit period: 2=2yr, 5=5yr, 70=to age 70. Default 5."},
                "coverage_type": {"type": "string", "enum": ["24hour", "non-occupational"], "description": "Coverage type. Default 24hour."},
            },
            "required": ["age", "gender", "occupation", "income"],
        },
    },
]

TOOL_FUNCTIONS = {
    "read_pipeline": lambda _: json.dumps(read_pipeline(), default=str),
    "add_prospect": lambda args: add_prospect(args),
    "update_prospect": lambda args: update_prospect(args["name"], args["updates"]),
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

SYSTEM_PROMPT = """You are Calm Money Sales Assistant — Marc's personal sales assistant. Marc is a financial planner in London, Ontario who sells life insurance and wealth management.

You manage his sales pipeline, meetings, insurance book prospecting, and draft emails. You use tools to read/write an Excel-based CRM.

Key rules:
- Be concise. This is a text chat. Short replies.
- When adding prospects, default stage to "New Lead" and first_contact to today.
- "move X to Y" → update stage. "mark X as hot" → update priority.
- Relative dates ("friday", "next week", "tomorrow") → calculate YYYY-MM-DD. Today is """ + date.today().strftime("%Y-%m-%d") + """.
- "log:" messages → add to Activity Log AND update next_followup if applicable.
- "pipeline" / "summary" → pipeline summary.
- "overdue" / "who's late" → check overdue follow-ups.
- "meeting with X on [date] at [time]" → add_meeting.
- "what's on this week" / "my meetings" → get_meetings.
- "cancel meeting with X" → cancel_meeting.
- "calls" / "who should I call" → get_next_calls from insurance book.
- "called X, [outcome]" → log_book_call. Outcomes: not interested, no answer, booked meeting, callback.
- "book stats" → get_book_stats.
- "draft email/follow-up/quote for X" → draft_email. Include any details (prices, context).
- Long messages (500+ chars) that look like meeting transcripts → process_transcript.
- "what's the sequence for X" / "follow-up plan for X" → get_follow_up_sequence.
- IMPORTANT: When moving a prospect to a new stage, ALWAYS call auto_set_follow_up to set the next follow-up date automatically.
- IMPORTANT: When moving a prospect to Closed-Won or Closed-Lost, ALWAYS ask Marc WHY they won or lost, then call log_win_loss. Don't skip this.
- "why do I win" / "win loss stats" / "why do I lose" → get_win_loss_stats.
- "quote [name], [age][M/F] [smoker/non-smoker], $[amount] term [length]" → get_term_quote. Highlights Co-operators rates.
- "disability quote [name], [age][M/F], [occupation], $[income]" → get_disability_quote. Edge Benefits rates (insured by Co-operators). Risk class determined by occupation. Show injury-only AND injury+illness combo pricing.
- After any write action, confirm in 1-2 lines.
- Keep it casual and friendly. Use $ for money.
- If ambiguous, make your best guess and confirm.

Marc's email style: casual, direct, short. No corporate speak. Signs off as "Marc" or "Marc / Calm Money".
"""


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming Telegram messages."""
    user_msg = update.message.text
    if not user_msg:
        return

    logger.info(f"Received: {user_msg}")

    try:
        # Call Claude with tools
        messages = [{"role": "user", "content": user_msg}]

        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Process tool calls in a loop
        while response.stop_reason == "tool_use":
            tool_results = []
            assistant_content = response.content

            for block in response.content:
                if block.type == "tool_use":
                    tool_name = block.name
                    tool_input = block.input
                    logger.info(f"Tool call: {tool_name}({json.dumps(tool_input)})")

                    func = TOOL_FUNCTIONS.get(tool_name)
                    if func:
                        result = func(tool_input)
                    else:
                        result = f"Unknown tool: {tool_name}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

        # Extract final text response
        reply = ""
        for block in response.content:
            if hasattr(block, "text"):
                reply += block.text

        if not reply:
            reply = "Done! (no message returned)"

        await update.message.reply_text(reply)
        logger.info(f"Replied: {reply[:100]}")

    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Something went wrong: {str(e)[:200]}")


async def export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the pipeline Excel file to the user."""
    try:
        if Path(PIPELINE_PATH).exists():
            await update.message.reply_document(
                document=open(PIPELINE_PATH, "rb"),
                filename=f"CalmMoney_Pipeline_{date.today().strftime('%Y-%m-%d')}.xlsx",
                caption="Here's your current pipeline file."
            )
        else:
            await update.message.reply_text("Pipeline file not found.")
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
            text = file_bytes.decode('utf-8')
            reader = csv.reader(io.StringIO(text))
            rows = list(reader)

            if not rows:
                await update.message.reply_text("CSV is empty.")
                return

            init_extra_sheets()
            wb = openpyxl.load_workbook(PIPELINE_PATH)
            ws = wb["Insurance Book"]

            # Clear existing data
            for r in range(3, 503):
                for c in range(1, 9):
                    ws.cell(row=r, column=c, value=None)

            # Import — try to map columns intelligently
            header = [h.lower().strip() for h in rows[0]] if rows else []
            data_rows = rows[1:] if len(rows) > 1 else rows

            count = 0
            for i, row in enumerate(data_rows):
                if not row or not row[0].strip():
                    continue
                r = 3 + count
                # Column A: Name (first column or 'name' column)
                ws.cell(row=r, column=1, value=row[0].strip())
                # Try to map other columns
                for j, val in enumerate(row[1:], 1):
                    if j < len(header):
                        h = header[j] if j < len(header) else ""
                        if "phone" in h or "tel" in h:
                            ws.cell(row=r, column=2, value=val.strip())
                        elif "address" in h or "addr" in h:
                            ws.cell(row=r, column=3, value=val.strip())
                        elif "date" in h or "start" in h or "inception" in h:
                            ws.cell(row=r, column=4, value=val.strip())
                        else:
                            # Put remaining data in notes
                            existing = ws.cell(row=r, column=7).value or ""
                            ws.cell(row=r, column=7, value=f"{existing} {val.strip()}".strip())
                    elif j == 1:
                        ws.cell(row=r, column=2, value=val.strip())  # Assume phone
                    elif j == 2:
                        ws.cell(row=r, column=3, value=val.strip())  # Assume address

                ws.cell(row=r, column=5, value="Not Called")
                count += 1

            wb.save(PIPELINE_PATH)
            wb.close()

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
            await file.download_to_drive("/tmp/book_upload.xlsx")
            src_wb = openpyxl.load_workbook("/tmp/book_upload.xlsx")
            src_ws = src_wb.active

            init_extra_sheets()
            wb = openpyxl.load_workbook(PIPELINE_PATH)
            ws = wb["Insurance Book"]

            for r in range(3, 503):
                for c in range(1, 9):
                    ws.cell(row=r, column=c, value=None)

            count = 0
            start = 2 if src_ws.cell(row=1, column=1).value and any(
                h in str(src_ws.cell(row=1, column=1).value).lower()
                for h in ["name", "client", "first", "last"]
            ) else 1

            for r in range(start, src_ws.max_row + 1):
                name = src_ws.cell(row=r, column=1).value
                if not name:
                    continue
                target = 3 + count
                ws.cell(row=target, column=1, value=str(name))
                for c in range(2, min(src_ws.max_column + 1, 8)):
                    val = src_ws.cell(row=r, column=c).value
                    if val:
                        ws.cell(row=target, column=c, value=str(val))
                ws.cell(row=target, column=5, value="Not Called")
                count += 1

            wb.save(PIPELINE_PATH)
            wb.close()
            src_wb.close()

            await update.message.reply_text(
                f"Insurance book loaded! {count} contacts imported.\n"
                f"Text 'calls' to get your first batch."
            )
        else:
            # Replace pipeline
            await file.download_to_drive(PIPELINE_PATH)
            wb = openpyxl.load_workbook(PIPELINE_PATH)
            sheets = wb.sheetnames
            wb.close()

            await update.message.reply_text(
                f"Pipeline updated from your file.\n"
                f"Sheets: {', '.join(sheets)}\n"
                f"All changes are live now."
            )

        logger.info(f"File processed: {doc.file_name}")
    except Exception as e:
        await update.message.reply_text(f"Error processing file: {str(e)[:200]}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey Marc! I'm your Calm Money sales assistant.\n\n"
        "Pipeline:\n"
        "• \"add John Smith, $300K wealth, hot, referral\"\n"
        "• \"move Sarah to discovery call\"\n"
        "• \"pipeline update\" / \"who's overdue?\"\n\n"
        "Meetings:\n"
        "• \"meeting with Sarah Thursday 2pm\"\n"
        "• \"what's on this week?\"\n\n"
        "Insurance Book:\n"
        "• \"calls\" — get next prospects to call\n"
        "• \"called John, no answer\" / \"booked meeting\"\n"
        "• \"book stats\" — see your progress\n\n"
        "Emails:\n"
        "• \"draft follow-up for Sarah\"\n"
        "• \"draft quote for Mike, 500K at $81, 1M at $140\"\n\n"
        "Other:\n"
        "• /export — download Excel\n"
        "• Send Excel file to update pipeline\n"
        "• Paste Otter transcript for auto-processing\n\n"
        "Let's close some deals."
    )


def main():
    # Initialize pipeline file if it doesn't exist
    if not Path(PIPELINE_PATH).exists():
        logger.info(f"Pipeline file not found at {PIPELINE_PATH}. Please provide one.")
        return

    # Initialize extra sheets
    init_extra_sheets()

    # Start web dashboard in background thread
    from dashboard import start_dashboard_thread
    start_dashboard_thread()
    logger.info("Web dashboard started.")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("export", export))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start scheduler for morning briefings and auto-nags
    try:
        from scheduler import start_scheduler
        start_scheduler(app)
        logger.info("Scheduler started (morning briefing + auto-nags).")
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}. Bot will run without scheduled messages.")

    logger.info("Bot started. Listening for messages...")
    app.run_polling()


if __name__ == "__main__":
    main()
