import html as _html
import hmac
import os
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from functools import wraps

import json

import db
from flask import Flask, Response, request, jsonify


def _esc(val):
    """Escape HTML to prevent XSS."""
    return _html.escape(str(val)) if val else ""


def _esc_json_attr(val):
    """Escape a JSON string for safe embedding in an HTML attribute.

    Uses html.escape with quote=True to handle &, <, >, and quotes,
    ensuring the JSON cannot break out of the attribute context.
    """
    return _html.escape(val, quote=True)


DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")

# In-memory set of valid CSRF tokens (generated per dashboard page load)
_csrf_tokens: set = set()
_MAX_CSRF_TOKENS = 200


def _generate_csrf_token() -> str:
    """Generate a CSRF token, store it, and return it."""
    token = secrets.token_urlsafe(32)
    # Evict oldest tokens if we hit the limit
    while len(_csrf_tokens) >= _MAX_CSRF_TOKENS:
        _csrf_tokens.pop()
    _csrf_tokens.add(token)
    return token


def _require_auth(f):
    """Decorator: accepts either X-API-Key header or X-CSRF-Token header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check API key first (for external/programmatic access)
        api_key = request.headers.get("X-API-Key", "")
        if DASHBOARD_API_KEY and api_key and hmac.compare_digest(api_key, DASHBOARD_API_KEY):
            return f(*args, **kwargs)
        # Check CSRF token (for dashboard UI)
        csrf_token = request.headers.get("X-CSRF-Token", "")
        if csrf_token and csrf_token in _csrf_tokens:
            return f(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return decorated


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB max request size


@app.after_request
def _set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

STAGE_COLORS = {
    "New Lead": "#BDC3C7", "Contacted": "#3498DB", "Discovery Call": "#8E44AD",
    "Needs Analysis": "#E67E22", "Plan Presentation": "#F39C12", "Proposal Sent": "#2980B9",
    "Negotiation": "#E74C3C", "Closed-Won": "#27AE60", "Closed-Lost": "#95A5A6", "Nurture": "#1ABC9C",
}
PRIORITY_COLORS = {"Hot": "#E74C3C", "Warm": "#F39C12", "Cold": "#3498DB"}
STAGE_TEXT = {
    "New Lead": "#2C3E50", "Plan Presentation": "#2C3E50",
}


def read_data():
    prospects = db.read_pipeline()
    activities = db.read_activities()
    meetings = db.read_meetings()
    book_entries = db.read_insurance_book()
    return prospects, activities, meetings, book_entries


@app.route("/api/prospect", methods=["POST"])
@_require_auth
def api_add_prospect():
    data = request.json
    if not data or not data.get("name"):
        return jsonify({"error": "Name required"}), 400
    result = db.add_prospect(data)
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospect/<name>", methods=["PUT"])
@_require_auth
def api_update_prospect(name):
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400
    result = db.update_prospect(name, data)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospect/<name>", methods=["DELETE"])
@_require_auth
def api_delete_prospect(name):
    result = db.delete_prospect(name)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospects")
@_require_auth
def api_list_prospects():
    prospects, _, _, _ = read_data()
    return jsonify(prospects)


@app.route("/api/task", methods=["POST"])
@_require_auth
def api_add_task():
    data = request.json
    if not data or not data.get("title"):
        return jsonify({"error": "Title required"}), 400
    # Default assigned_to to admin chat ID for dashboard-created tasks
    if not data.get("assigned_to"):
        import os as _os
        data["assigned_to"] = _os.environ.get("TELEGRAM_CHAT_ID", "")
        data["created_by"] = data["assigned_to"]
    result = db.add_task(data)
    if result:
        return jsonify({"ok": True, "task": result})
    return jsonify({"error": "Could not create task"}), 400


@app.route("/api/tasks")
@_require_auth
def api_list_tasks():
    status = request.args.get("status", "pending")
    assigned_to = request.args.get("assigned_to")
    prospect = request.args.get("prospect")
    tasks = db.get_tasks(assigned_to=assigned_to, status=status, prospect=prospect)
    return jsonify(tasks)


@app.route("/api/task/<int:task_id>/complete", methods=["PUT"])
@_require_auth
def api_complete_task(task_id):
    result = db.complete_task(task_id, "", is_admin=True)
    return jsonify({"ok": True, "message": result})


@app.route("/api/task/<int:task_id>", methods=["DELETE"])
@_require_auth
def api_delete_task(task_id):
    result = db.delete_task(task_id, "", is_admin=True)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


def calc_fyc(premium, product):
    """Calculate First Year Commission from premium and product term.
    Term 20/25/30: Premium * 11.11 * 0.5
    Term 10/15:    Premium * 11.11 * 0.4
    """
    prem = parse_money(premium) if not isinstance(premium, (int, float)) else premium
    if prem <= 0:
        return 0.0
    # Extract term number from product string like "Term 20", "Versatile Term 30", etc.
    term_match = re.search(r'(\d+)', str(product or ""))
    if not term_match:
        return 0.0
    term = int(term_match.group(1))
    if term in (20, 25, 30):
        return prem * 11.11 * 0.5
    elif term in (10, 15):
        return prem * 11.11 * 0.4
    return 0.0


def parse_money(val):
    """Parse money values like '200k', '1.5M', '$500,000' into float."""
    try:
        s = str(val).strip().replace("$", "").replace(",", "").lower()
        if not s or s == "none" or s == "0":
            return 0.0
        if s.endswith("m"):
            return float(s[:-1]) * 1000000
        if s.endswith("k"):
            return float(s[:-1]) * 1000
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def fmt_money(val):
    try:
        v = float(str(val).replace("$", "").replace(",", ""))
        if v >= 1000000:
            return f"${v/1000000:.1f}M"
        if v >= 1000:
            return f"${v/1000:.0f}K"
        return f"${v:,.0f}"
    except (ValueError, TypeError):
        return "$0"


def fmt_money_full(val):
    try:
        return f"${float(str(val).replace('$','').replace(',','')):,.0f}"
    except (ValueError, TypeError):
        return "$0"


@app.route("/")
def dashboard():
    csrf_token = _generate_csrf_token()
    prospects, activities, meetings, book_entries = read_data()
    try:
        all_tasks = db.get_tasks(status="pending")
        completed_tasks_recent = db.get_tasks(status="completed", limit=10)
    except Exception:
        all_tasks, completed_tasks_recent = [], []
    today = date.today()
    now = datetime.now()

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p["stage"] == "Closed-Won"]
    lost = [p for p in prospects if p["stage"] == "Closed-Lost"]

    total_pipeline = sum(parse_money(p["aum"]) for p in active)
    total_revenue = sum(parse_money(p["revenue"]) for p in active)
    won_revenue = sum(parse_money(p["revenue"]) for p in won)
    hot_count = len([p for p in active if p["priority"] == "Hot"])

    win_rate = 0
    if len(won) + len(lost) > 0:
        win_rate = len(won) / (len(won) + len(lost)) * 100

    overdue = []
    for p in active:
        if p["next_followup"] and p["next_followup"] != "None":
            try:
                fu = datetime.strptime(p["next_followup"].split(" ")[0], "%Y-%m-%d").date()
                if fu < today:
                    overdue.append(p)
            except (ValueError, IndexError):
                pass

    # ── Revenue Forecasting ──
    PREMIUM_TARGET = 200000
    AUM_TARGET = 5000000

    # Baseline numbers (existing business not tracked in pipeline)
    # These ONLY apply to forecast targets, not main KPI cards
    BASELINE_AUM = 400000    # Current AUM as of March 2025
    BASELINE_PREMIUM = 2000  # Life premium YTD

    # Calculate won AUM from pipeline only (for KPI cards)
    won_aum_pipeline = 0
    for p in won:
        try:
            won_aum_pipeline += parse_money(p["aum"])
        except ValueError:
            pass

    # FYC calculations
    won_fyc = sum(calc_fyc(p["revenue"], p["product"]) for p in won)
    active_fyc = sum(calc_fyc(p["revenue"], p["product"]) for p in active)

    # Forecast totals include baselines
    forecast_revenue = won_revenue + BASELINE_PREMIUM
    forecast_aum = won_aum_pipeline + BASELINE_AUM

    # Days into the year / days remaining
    year_start = date(today.year, 1, 1)
    year_end = date(today.year, 12, 31)
    days_elapsed = (today - year_start).days + 1
    days_total = (year_end - year_start).days + 1
    days_remaining = days_total - days_elapsed
    pct_year = days_elapsed / days_total * 100

    # Premium progress (forecast includes baseline)
    premium_pct = (forecast_revenue / PREMIUM_TARGET * 100) if PREMIUM_TARGET else 0
    premium_pace = (forecast_revenue / days_elapsed * days_total) if days_elapsed else 0
    premium_on_pace = premium_pace >= PREMIUM_TARGET

    # AUM progress (forecast includes baseline)
    aum_pct = (forecast_aum / AUM_TARGET * 100) if AUM_TARGET else 0
    aum_pace = (forecast_aum / days_elapsed * days_total) if days_elapsed else 0
    aum_on_pace = aum_pace >= AUM_TARGET

    # Pipeline weighted forecast (probability by stage)
    stage_probability = {
        "New Lead": 0.05, "Contacted": 0.10, "Discovery Call": 0.20,
        "Needs Analysis": 0.35, "Plan Presentation": 0.50, "Proposal Sent": 0.65,
        "Negotiation": 0.80, "Nurture": 0.05,
    }
    weighted_revenue = 0
    weighted_aum = 0
    weighted_fyc = 0
    for p in active:
        prob = stage_probability.get(p["stage"], 0.10)
        try:
            weighted_revenue += parse_money(p["revenue"]) * prob
        except ValueError:
            pass
        try:
            weighted_aum += parse_money(p["aum"]) * prob
        except ValueError:
            pass
        weighted_fyc += calc_fyc(p["revenue"], p["product"]) * prob

    projected_revenue = forecast_revenue + weighted_revenue
    projected_aum = forecast_aum + weighted_aum
    projected_fyc = won_fyc + weighted_fyc

    # Monthly revenue tracking (won deals by month)
    monthly_revenue = {}
    monthly_aum = {}
    for p in won:
        fc = p.get("first_contact", "")
        if fc and fc != "None":
            try:
                m = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d")
                if m.year == today.year:
                    month_key = m.strftime("%b")
                    month_num = m.month
                    try:
                        monthly_revenue[(month_num, month_key)] = monthly_revenue.get((month_num, month_key), 0) + parse_money(p["revenue"])
                    except ValueError:
                        pass
                    try:
                        monthly_aum[(month_num, month_key)] = monthly_aum.get((month_num, month_key), 0) + parse_money(p["aum"])
                    except ValueError:
                        pass
            except (ValueError, IndexError):
                pass

    # Fill all months up to current
    all_months = []
    for m in range(1, today.month + 1):
        mk = date(today.year, m, 1).strftime("%b")
        all_months.append(mk)

    monthly_rev_values = [monthly_revenue.get((i+1, mk), 0) for i, mk in enumerate(all_months)]
    monthly_target_line = [PREMIUM_TARGET / 12] * len(all_months)

    # ── Conversion Funnel ──
    stage_order = ["New Lead", "Contacted", "Discovery Call", "Needs Analysis", "Plan Presentation", "Proposal Sent", "Negotiation", "Closed-Won"]
    funnel_counts = {}
    for s in stage_order:
        funnel_counts[s] = 0
    for p in prospects:
        s = p["stage"]
        if s in funnel_counts:
            funnel_counts[s] += 1
        # Count won deals as having passed through all prior stages
        if s == "Closed-Won":
            for prior in stage_order:
                funnel_counts[prior] += 1

    # Average days in each stage (for velocity)
    stage_days = {}
    for p in active:
        s = p["stage"]
        fc = p.get("first_contact", "")
        if fc and fc != "None" and s:
            try:
                start = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
                days_in = (today - start).days
                if s not in stage_days:
                    stage_days[s] = []
                stage_days[s].append(days_in)
            except (ValueError, IndexError):
                pass

    avg_stage_days = {}
    for s, days_list in stage_days.items():
        avg_stage_days[s] = sum(days_list) / len(days_list)

    # Conversion rates between stages
    funnel_rates = []
    for i in range(len(stage_order) - 1):
        current = funnel_counts[stage_order[i]]
        next_s = funnel_counts[stage_order[i + 1]]
        rate = (next_s / current * 100) if current > 0 else 0
        funnel_rates.append(rate)

    # Source effectiveness
    source_wins = {}
    source_totals = {}
    for p in prospects:
        src = p["source"] or "Unknown"
        if p["stage"]:
            source_totals[src] = source_totals.get(src, 0) + 1
            if p["stage"] == "Closed-Won":
                source_wins[src] = source_wins.get(src, 0) + 1

    source_conversion = {}
    for src, total in source_totals.items():
        wins = source_wins.get(src, 0)
        source_conversion[src] = {"wins": wins, "total": total, "rate": (wins / total * 100) if total > 0 else 0}

    # ── Activity Scoreboard ──
    # Count activities this week and today
    week_start = today - timedelta(days=today.weekday())  # Monday
    activities_today = 0
    activities_week = 0
    calls_today = 0
    calls_week = 0
    emails_today = 0
    emails_week = 0
    meetings_today = 0
    meetings_week = 0

    for a in activities:
        ad = a["date"]
        if ad and ad != "None":
            try:
                activity_date = datetime.strptime(ad.split(" ")[0], "%Y-%m-%d").date()
                action = a["action"].lower()
                is_today = activity_date == today
                is_week = activity_date >= week_start

                if is_today:
                    activities_today += 1
                    if "call" in action or "phone" in action:
                        calls_today += 1
                    if "email" in action:
                        emails_today += 1
                    if "meeting" in action or "discovery" in action or "presentation" in action:
                        meetings_today += 1

                if is_week:
                    activities_week += 1
                    if "call" in action or "phone" in action:
                        calls_week += 1
                    if "email" in action:
                        emails_week += 1
                    if "meeting" in action or "discovery" in action or "presentation" in action:
                        meetings_week += 1
            except (ValueError, IndexError):
                pass

    # Include insurance book calls in call counts
    for b in book_entries:
        lc = b.get("last_called", "")
        if lc and lc != "None":
            try:
                call_date = datetime.strptime(lc.split(" ")[0], "%Y-%m-%d").date()
                if call_date == today:
                    calls_today += 1
                if call_date >= week_start:
                    calls_week += 1
            except (ValueError, IndexError):
                pass

    # Daily targets
    DAILY_CALLS_TARGET = 10
    DAILY_EMAILS_TARGET = 3
    WEEKLY_MEETINGS_TARGET = 5

    # Calculate streaks (consecutive days with at least 1 activity)
    activity_dates = set()
    for a in activities:
        ad = a["date"]
        if ad and ad != "None":
            try:
                activity_dates.add(datetime.strptime(ad.split(" ")[0], "%Y-%m-%d").date())
            except (ValueError, IndexError):
                pass
    for b in book_entries:
        lc = b.get("last_called", "")
        if lc and lc != "None":
            try:
                activity_dates.add(datetime.strptime(lc.split(" ")[0], "%Y-%m-%d").date())
            except (ValueError, IndexError):
                pass

    streak = 0
    check_date = today
    while check_date in activity_dates:
        streak += 1
        check_date -= timedelta(days=1)

    # Pre-compute deals aging rows
    aging_rows = ""
    for p in sorted(active, key=lambda x: x.get("first_contact", "9999")):
        fc = p.get("first_contact", "")
        if fc and fc != "None":
            try:
                days_open = (today - datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()).days
                stale = '<span class="overdue">Stale</span>' if days_open > 30 else "OK"
            except (ValueError, IndexError):
                days_open = "?"
                stale = "?"
        else:
            days_open = "?"
            stale = "?"
        stage_bg = STAGE_COLORS.get(p["stage"], "#BDC3C7")
        aging_rows += f'<tr><td class="name-cell">{_esc(p["name"])}</td><td><span class="badge" style="background:{stage_bg}">{_esc(p["stage"])}</span></td><td>{days_open}</td><td>{stale}</td></tr>'

    # Pre-compute source effectiveness rows
    source_eff_rows = ""
    for src, data in sorted(source_conversion.items(), key=lambda x: -x[1]["rate"]):
        if data["total"] >= 1:
            rate_bg = "#27ae60" if data["rate"] >= 30 else "#f39c12" if data["rate"] >= 15 else "#e74c3c"
            source_eff_rows += f'<tr><td>{_esc(src)}</td><td>{data["total"]}</td><td>{data["wins"]}</td><td><span class="badge" style="background:{rate_bg}">{data["rate"]:.0f}%</span></td></tr>'

    # Stage counts for chart
    stage_counts = {}
    stage_revenue = {}
    stage_fyc = {}
    for p in prospects:
        s = p["stage"]
        if s:
            stage_counts[s] = stage_counts.get(s, 0) + 1
            try:
                stage_revenue[s] = stage_revenue.get(s, 0) + parse_money(p["revenue"])
            except ValueError:
                pass
            stage_fyc[s] = stage_fyc.get(s, 0) + calc_fyc(p["revenue"], p["product"])

    # Source counts
    source_counts = {}
    for p in prospects:
        s = p["source"]
        if s:
            source_counts[s] = source_counts.get(s, 0) + 1

    # Product counts
    product_counts = {}
    for p in prospects:
        pr = p["product"]
        if pr:
            product_counts[pr] = product_counts.get(pr, 0) + 1

    # Build prospect rows
    prospect_rows = ""
    for p in active:
        stage_bg = STAGE_COLORS.get(p["stage"], "#BDC3C7")
        stage_fg = STAGE_TEXT.get(p["stage"], "#fff")
        pri_bg = PRIORITY_COLORS.get(p["priority"], "#BDC3C7")

        is_overdue = p in overdue
        fu_class = "overdue" if is_overdue else ""
        fu_display = p["next_followup"].split(" ")[0] if p["next_followup"] and p["next_followup"] != "None" else ""

        p_json_escaped = _esc_json_attr(json.dumps(p))
        prospect_rows += f"""<tr class="editable-row" data-prospect="{p_json_escaped}" onclick="openEdit(JSON.parse(this.dataset.prospect))" style="cursor:pointer">
            <td class="name-cell">{_esc(p["name"])}</td>
            <td><span class="badge" style="background:{pri_bg}">{_esc(p["priority"])}</span></td>
            <td><span class="badge" style="background:{stage_bg};color:{stage_fg}">{_esc(p["stage"])}</span></td>
            <td>{_esc(p["product"])}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td class="{fu_class}">{_esc(fu_display)}</td>
            <td class="notes">{_esc(p["notes"][:60])}{'...' if len(p["notes"]) > 60 else ''}</td>
        </tr>"""

    # Won deals rows
    won_rows = ""
    for p in won:
        won_rows += f"""<tr>
            <td class="name-cell">{_esc(p["name"])}</td>
            <td>{_esc(p["product"])}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td>{_esc(p["source"])}</td>
        </tr>"""

    # Activity rows (last 10)
    activity_rows = ""
    for a in activities[:10]:
        activity_rows += f"""<tr>
            <td>{_esc(a["date"].split(" ")[0])}</td>
            <td>{_esc(a["prospect"])}</td>
            <td>{_esc(a["action"])}</td>
            <td>{_esc(a["outcome"])}</td>
            <td>{_esc(a["next_step"])}</td>
        </tr>"""

    # Overdue rows
    overdue_rows = ""
    for p in overdue:
        fu = p["next_followup"].split(" ")[0] if p["next_followup"] else ""
        try:
            days_late = (today - datetime.strptime(fu, "%Y-%m-%d").date()).days
        except (ValueError, IndexError):
            days_late = "?"
        overdue_rows += f"""<tr>
            <td class="name-cell">{_esc(p["name"])}</td>
            <td>{_esc(fu)}</td>
            <td class="overdue">{days_late} days late</td>
            <td>{_esc(p["phone"])}</td>
        </tr>"""

    # Chart data as JSON-like strings for inline JS
    stage_labels = list(stage_counts.keys())
    stage_values = list(stage_counts.values())
    stage_chart_colors = [STAGE_COLORS.get(s, "#BDC3C7") for s in stage_labels]

    source_labels = list(source_counts.keys())
    source_values = list(source_counts.values())

    product_labels = list(product_counts.keys())
    product_values = list(product_counts.values())

    # Build task data for Tasks tab
    today_str = today.strftime("%Y-%m-%d")
    week_ago_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")

    overdue_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] < today_str]
    due_today_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] == today_str]
    upcoming_tasks = [t for t in all_tasks if not t.get("due_date") or t["due_date"] > today_str]
    completed_this_week = [t for t in completed_tasks_recent if t.get("completed_at", "") >= week_ago_str]

    def _task_row(t, show_due=True):
        due = t.get("due_date") or ""
        row_class = ""
        due_display = ""
        if due:
            if due < today_str:
                row_class = "overdue"
                try:
                    days_late = (today - datetime.strptime(due, "%Y-%m-%d").date()).days
                except ValueError:
                    days_late = 0
                due_display = f'{_esc(due)} <span style="color:#E74C3C">({days_late}d late)</span>'
            elif due == today_str:
                row_class = "due-today"
                due_display = '<span style="color:#F39C12;font-weight:600">Today</span>'
            else:
                due_display = _esc(due)
        prospect_display = f'<a style="color:#3498DB" href="javascript:void(0)">{_esc(t["prospect"])}</a>' if t.get("prospect") else ""
        remind_icon = ' <span title="Reminder set" style="color:#F39C12">&#9200;</span>' if t.get("remind_at") else ""
        due_cell = f"<td>{due_display}</td>" if show_due else ""
        return f"""<tr class="{row_class}">
            <td style="text-align:center"><input type="checkbox" onchange="completeTask({t['id']}, this)" style="width:18px;height:18px;cursor:pointer"></td>
            <td>{_esc(t['title'])}{remind_icon}</td>
            <td>{prospect_display}</td>
            {due_cell}
            <td style="text-align:center"><button onclick="deleteTask({t['id']})" style="background:none;border:none;color:#E74C3C;cursor:pointer;font-size:16px">&#10005;</button></td>
        </tr>"""

    overdue_task_rows = "".join(_task_row(t) for t in overdue_tasks)
    due_today_task_rows = "".join(_task_row(t, show_due=False) for t in due_today_tasks)
    upcoming_task_rows = "".join(_task_row(t) for t in upcoming_tasks)

    completed_rows = ""
    for t in completed_tasks_recent:
        completed_rows += f"""<tr style="opacity:0.6">
            <td style="text-align:center;color:#27AE60">&#10003;</td>
            <td style="text-decoration:line-through;color:#7f8c8d">{_esc(t['title'])}</td>
            <td>{_esc(t.get('prospect', ''))}</td>
            <td>{_esc((t.get('completed_at') or '')[:10])}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="csrf-token" content="{csrf_token}">
<title>Calm Money — Pipeline Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; background: #f0f2f5; color: #2c3e50; }}

.header {{
    background: linear-gradient(135deg, #0f1b2d 0%, #1a2744 100%);
    padding: 24px 32px;
    color: white;
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.header h1 {{ font-size: 24px; font-weight: 700; }}
.header h1 span {{ color: #1abc9c; }}
.header .updated {{ font-size: 13px; color: #7f8c8d; }}

.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}

.kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.kpi-card {{
    background: white;
    border-radius: 12px;
    padding: 20px 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    border-top: 4px solid #1abc9c;
}}
.kpi-card.blue {{ border-top-color: #3498db; }}
.kpi-card.green {{ border-top-color: #27ae60; }}
.kpi-card.purple {{ border-top-color: #8e44ad; }}
.kpi-card.red {{ border-top-color: #e74c3c; }}
.kpi-card.gold {{ border-top-color: #f1c40f; }}
.kpi-label {{ font-size: 11px; text-transform: uppercase; color: #7f8c8d; font-weight: 600; letter-spacing: 0.5px; }}
.kpi-value {{ font-size: 32px; font-weight: 700; margin-top: 4px; color: #0f1b2d; }}

.section {{
    background: white;
    border-radius: 12px;
    padding: 24px;
    margin-bottom: 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
.section h2 {{
    font-size: 16px;
    font-weight: 700;
    color: #0f1b2d;
    margin-bottom: 16px;
    padding-bottom: 8px;
    border-bottom: 2px solid #f0f2f5;
}}
.section h2 .count {{ color: #7f8c8d; font-weight: 400; }}

.chart-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 24px;
    margin-bottom: 24px;
}}
.chart-card {{
    background: white;
    border-radius: 12px;
    padding: 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}}
.chart-card h3 {{ font-size: 14px; font-weight: 600; color: #0f1b2d; margin-bottom: 12px; }}

table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ text-align: left; padding: 10px 12px; background: #f8f9fa; color: #7f8c8d; font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 2px solid #e9ecef; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #f0f2f5; }}
tr:hover {{ background: #f8f9fa; }}

.badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
    color: white;
}}
.name-cell {{ font-weight: 600; color: #0f1b2d; }}
.money {{ font-family: 'SF Mono', 'Consolas', monospace; text-align: right; }}
.notes {{ color: #7f8c8d; font-size: 12px; max-width: 200px; }}
.overdue {{ color: #e74c3c; font-weight: 600; }}
.due-today {{ background-color: #fffbec !important; }}

.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}

.empty-state {{ text-align: center; padding: 40px; color: #7f8c8d; }}
.empty-state p {{ margin-top: 8px; font-size: 14px; }}

.refresh-note {{ text-align: center; color: #7f8c8d; font-size: 12px; margin-top: 16px; padding: 12px; }}

.editable-row:hover {{ background: #edf7f6 !important; }}

/* Tabs */
.tab-nav {{
    display: flex;
    gap: 0;
    background: white;
    border-radius: 12px 12px 0 0;
    margin-bottom: 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    overflow: hidden;
}}
.tab-btn {{
    flex: 1;
    padding: 14px 20px;
    border: none;
    background: white;
    font-size: 13px;
    font-weight: 600;
    color: #7f8c8d;
    cursor: pointer;
    border-bottom: 3px solid transparent;
    transition: all 0.2s;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.tab-btn:hover {{ background: #f8f9fa; color: #2c3e50; }}
.tab-btn.active {{ color: #1abc9c; border-bottom-color: #1abc9c; background: #f0faf8; }}
.tab-content {{ display: none; }}
.tab-content.active {{ display: block; }}

/* Progress bars */
.progress-bar-container {{
    background: #f0f2f5;
    border-radius: 8px;
    height: 24px;
    overflow: hidden;
    position: relative;
    margin: 8px 0;
}}
.progress-bar-fill {{
    height: 100%;
    border-radius: 8px;
    transition: width 0.5s ease;
    display: flex;
    align-items: center;
    padding-left: 8px;
    font-size: 11px;
    font-weight: 600;
    color: white;
    min-width: 40px;
}}
.progress-bar-fill.green {{ background: linear-gradient(90deg, #27ae60, #2ecc71); }}
.progress-bar-fill.red {{ background: linear-gradient(90deg, #e74c3c, #e67e22); }}
.progress-bar-fill.blue {{ background: linear-gradient(90deg, #2980b9, #3498db); }}
.progress-bar-fill.teal {{ background: linear-gradient(90deg, #16a085, #1abc9c); }}

.pace-indicator {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 600;
}}
.pace-ahead {{ background: #d5f5e3; color: #27ae60; }}
.pace-behind {{ background: #fadbd8; color: #e74c3c; }}

.target-card {{
    background: white;
    border-radius: 12px;
    padding: 24px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    margin-bottom: 16px;
}}
.target-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
}}
.target-header h3 {{
    font-size: 15px;
    font-weight: 700;
    color: #0f1b2d;
}}
.target-meta {{
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: #7f8c8d;
    margin-top: 4px;
}}

/* Funnel */
.funnel-stage {{
    display: flex;
    align-items: center;
    margin-bottom: 8px;
    gap: 12px;
}}
.funnel-label {{
    width: 140px;
    font-size: 12px;
    font-weight: 600;
    color: #2c3e50;
    text-align: right;
}}
.funnel-bar-wrap {{
    flex: 1;
    display: flex;
    align-items: center;
    gap: 8px;
}}
.funnel-bar {{
    height: 28px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    padding-left: 10px;
    font-size: 12px;
    font-weight: 600;
    color: white;
    min-width: 30px;
    transition: width 0.5s ease;
}}
.funnel-rate {{
    font-size: 11px;
    color: #7f8c8d;
    white-space: nowrap;
}}
.funnel-velocity {{
    font-size: 11px;
    color: #95a5a6;
    min-width: 60px;
}}

/* Scoreboard */
.score-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}}
.score-card {{
    background: white;
    border-radius: 12px;
    padding: 20px;
    text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    border-top: 4px solid #1abc9c;
}}
.score-card.fire {{ border-top-color: #e74c3c; }}
.score-card h4 {{
    font-size: 11px;
    text-transform: uppercase;
    color: #7f8c8d;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
}}
.score-big {{
    font-size: 36px;
    font-weight: 700;
    color: #0f1b2d;
}}
.score-target {{
    font-size: 12px;
    color: #7f8c8d;
    margin-top: 4px;
}}
.streak-badge {{
    display: inline-block;
    background: linear-gradient(135deg, #e74c3c, #f39c12);
    color: white;
    padding: 6px 16px;
    border-radius: 20px;
    font-size: 14px;
    font-weight: 700;
}}

.btn {{ display: inline-block; padding: 8px 20px; border-radius: 8px; font-size: 13px; font-weight: 600; border: none; cursor: pointer; }}
.btn-primary {{ background: #1abc9c; color: white; }}
.btn-primary:hover {{ background: #16a085; }}
.btn-danger {{ background: #e74c3c; color: white; }}
.btn-danger:hover {{ background: #c0392b; }}
.btn-secondary {{ background: #bdc3c7; color: #2c3e50; }}

.modal-overlay {{ display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 1000; justify-content: center; align-items: center; }}
.modal-overlay.active {{ display: flex; }}
.modal {{ background: white; border-radius: 16px; padding: 32px; width: 500px; max-width: 90vw; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }}
.modal h2 {{ font-size: 18px; margin-bottom: 20px; color: #0f1b2d; }}
.modal label {{ display: block; font-size: 11px; font-weight: 600; text-transform: uppercase; color: #7f8c8d; margin-bottom: 4px; margin-top: 12px; letter-spacing: 0.5px; }}
.modal input, .modal select, .modal textarea {{ width: 100%; padding: 8px 12px; border: 1px solid #dde1e6; border-radius: 8px; font-size: 14px; font-family: inherit; }}
.modal textarea {{ resize: vertical; min-height: 60px; }}
.modal select {{ background: white; }}
.modal .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
.modal .actions {{ display: flex; gap: 8px; margin-top: 24px; justify-content: flex-end; }}
.modal .actions .left {{ margin-right: auto; }}

.add-btn {{ margin-bottom: 16px; float: right; }}

@media (max-width: 900px) {{
    .chart-grid {{ grid-template-columns: 1fr; }}
    .two-col {{ grid-template-columns: 1fr; }}
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr) !important; }}
}}

/* ── Mobile-first optimizations ── */
@media (max-width: 600px) {{
    /* Header */
    .header {{
        padding: 16px;
        flex-direction: column;
        gap: 8px;
        text-align: center;
    }}
    .header h1 {{ font-size: 18px; }}
    .header .updated {{ font-size: 11px; }}

    /* Container */
    .container {{ padding: 12px; }}

    /* KPI cards — 2 per row, tighter */
    .kpi-grid {{
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 8px !important;
        margin-bottom: 12px !important;
    }}
    .kpi-card {{
        padding: 12px 14px;
        border-radius: 8px;
    }}
    .kpi-value {{ font-size: 22px; }}
    .kpi-label {{ font-size: 10px; }}

    /* Tabs — scrollable horizontal */
    .tab-nav {{
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        border-radius: 8px 8px 0 0;
        scrollbar-width: none;
    }}
    .tab-nav::-webkit-scrollbar {{ display: none; }}
    .tab-btn {{
        padding: 10px 12px;
        font-size: 11px;
        white-space: nowrap;
        min-width: fit-content;
        letter-spacing: 0;
    }}

    /* Charts */
    .chart-grid {{
        grid-template-columns: 1fr !important;
        gap: 12px;
        margin-top: 12px !important;
    }}
    .chart-card {{
        padding: 14px;
        border-radius: 8px;
    }}

    /* Sections */
    .section {{
        padding: 14px;
        margin-bottom: 12px;
        border-radius: 8px;
    }}
    .section h2 {{ font-size: 14px; margin-bottom: 10px; }}

    /* Two columns → stack */
    .two-col {{
        grid-template-columns: 1fr !important;
        gap: 12px;
    }}

    /* Tables — horizontal scroll wrapper */
    .section table,
    .target-card table {{
        display: block;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        white-space: nowrap;
    }}
    th, td {{ padding: 8px 10px; font-size: 12px; }}
    .notes {{ max-width: 120px; white-space: normal; }}
    .name-cell {{ font-size: 12px; }}

    /* Target cards */
    .target-card {{
        padding: 16px;
        border-radius: 8px;
        margin-bottom: 12px;
    }}
    .target-header {{
        flex-direction: column;
        gap: 6px;
        align-items: flex-start;
    }}
    .target-header h3 {{ font-size: 14px; }}
    .target-meta {{
        flex-direction: column;
        gap: 2px;
        font-size: 11px;
    }}

    /* Funnel */
    .funnel-stage {{
        flex-wrap: wrap;
        gap: 4px;
        margin-bottom: 10px;
    }}
    .funnel-label {{
        width: 100%;
        text-align: left;
        font-size: 11px;
    }}
    .funnel-bar {{ height: 24px; font-size: 11px; }}
    .funnel-rate {{ font-size: 10px; }}
    .funnel-velocity {{ font-size: 10px; min-width: 50px; }}

    /* Scoreboard */
    .score-grid {{
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 8px;
    }}
    .score-card {{ padding: 14px; border-radius: 8px; }}
    .score-big {{ font-size: 28px; }}
    .score-target {{ font-size: 11px; }}
    .streak-badge {{ font-size: 12px; padding: 5px 14px; }}

    /* Progress bars */
    .progress-bar-container {{ height: 20px; }}
    .progress-bar-fill {{ font-size: 10px; min-width: 30px; }}

    /* Pace indicator */
    .pace-indicator {{ font-size: 10px; padding: 2px 8px; }}

    /* Modal */
    .modal {{
        padding: 20px;
        border-radius: 12px;
        width: 95vw;
        max-height: 85vh;
    }}
    .modal h2 {{ font-size: 16px; margin-bottom: 14px; }}
    .modal .form-row {{
        grid-template-columns: 1fr;
        gap: 0;
    }}
    .modal input, .modal select, .modal textarea {{
        font-size: 16px; /* prevents iOS zoom on focus */
        padding: 10px 12px;
    }}
    .modal label {{ margin-top: 8px; }}
    .modal .actions {{
        flex-direction: column-reverse;
        gap: 8px;
    }}
    .modal .actions .left {{ margin-right: 0; }}
    .modal .actions .btn {{ width: 100%; text-align: center; padding: 12px; }}

    /* Add button */
    .add-btn {{ float: none; display: block; width: 100%; margin-bottom: 10px; text-align: center; }}

    /* Pipeline header with button */
    .section h2 {{
        flex-direction: column !important;
        gap: 8px;
        align-items: flex-start !important;
    }}
    .section h2 .btn {{ width: 100%; text-align: center; }}

    /* Refresh note */
    .refresh-note {{ font-size: 11px; padding: 8px; }}
}}
</style>
</head>
<body>

<div class="header">
    <div>
        <h1>CALM <span>MONEY</span> — Pipeline</h1>
    </div>
    <div class="updated">Updated: {now.strftime('%B %d, %Y at %I:%M %p')}<br>Refresh page for latest data</div>
</div>

<div class="container">

    <div class="kpi-grid" style="grid-template-columns: repeat(4, 1fr)">
        <div class="kpi-card blue">
            <div class="kpi-label">Total AUM</div>
            <div class="kpi-value">{fmt_money(forecast_aum)}</div>
        </div>
        <div class="kpi-card green">
            <div class="kpi-label">Premium YTD</div>
            <div class="kpi-value">{fmt_money(forecast_revenue)}</div>
        </div>
        <div class="kpi-card gold">
            <div class="kpi-label">FYC YTD</div>
            <div class="kpi-value">{fmt_money(won_fyc)}</div>
        </div>
        <div class="kpi-card purple">
            <div class="kpi-label">Pipeline AUM</div>
            <div class="kpi-value">{fmt_money(total_pipeline)}</div>
        </div>
    </div>
    <div class="kpi-grid" style="grid-template-columns: repeat(4, 1fr); margin-top: 12px">
        <div class="kpi-card">
            <div class="kpi-label">Active Deals</div>
            <div class="kpi-value">{len(active)}</div>
        </div>
        <div class="kpi-card red">
            <div class="kpi-label">Hot Leads</div>
            <div class="kpi-value">{hot_count}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Win Rate</div>
            <div class="kpi-value">{win_rate:.0f}%</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Overdue</div>
            <div class="kpi-value">{len(overdue)}</div>
        </div>
    </div>

    <div class="tab-nav">
        <button class="tab-btn active" onclick="showTab('pipeline')">Pipeline</button>
        <button class="tab-btn" onclick="showTab('forecast')">Revenue Forecast</button>
        <button class="tab-btn" onclick="showTab('funnel')">Conversion Funnel</button>
        <button class="tab-btn" onclick="showTab('scoreboard')">Activity Score</button>
        <button class="tab-btn" onclick="showTab('tasks')">Tasks</button>
    </div>

    <!-- ═══ TAB 1: PIPELINE (existing) ═══ -->
    <div class="tab-content active" id="tab-pipeline">

    <div class="chart-grid" style="margin-top:24px">
        <div class="chart-card">
            <h3>By Stage</h3>
            <canvas id="stageChart"></canvas>
        </div>
        <div class="chart-card">
            <h3>By Source</h3>
            <canvas id="sourceChart"></canvas>
        </div>
        <div class="chart-card">
            <h3>By Product</h3>
            <canvas id="productChart"></canvas>
        </div>
    </div>

    {'<div class="section"><h2>Overdue Follow-Ups <span class="count">(' + str(len(overdue)) + ')</span></h2><table><tr><th>Prospect</th><th>Was Due</th><th>Status</th><th>Phone</th></tr>' + overdue_rows + '</table></div>' if overdue else ''}

    <div class="section">
        <h2 style="display:flex;justify-content:space-between;align-items:center">Active Pipeline <span class="count">({len(active)} deals)</span> <button class="btn btn-primary" onclick="openAdd()">+ Add Prospect</button></h2>
        {'<table><tr><th>Prospect</th><th>Priority</th><th>Stage</th><th>Product</th><th>AUM</th><th>Premium</th><th>Follow-Up</th><th>Notes</th></tr>' + prospect_rows + '</table>' if active else '<div class="empty-state"><p>No active deals yet. Text your Telegram bot to add prospects.</p></div>'}
    </div>

    <div class="two-col">
        <div class="section">
            <h2>Closed-Won <span class="count">({len(won)})</span></h2>
            {'<table><tr><th>Client</th><th>Product</th><th>AUM</th><th>Revenue</th><th>Source</th></tr>' + won_rows + '</table>' if won else '<div class="empty-state"><p>No wins yet. Keep grinding!</p></div>'}
        </div>
        <div class="section">
            <h2>Recent Activity <span class="count">(last 10)</span></h2>
            {'<table><tr><th>Date</th><th>Prospect</th><th>Action</th><th>Outcome</th><th>Next</th></tr>' + activity_rows + '</table>' if activities else '<div class="empty-state"><p>No activity logged yet.</p></div>'}
        </div>
    </div>

    <div class="two-col">
        <div class="section">
            <h2>Upcoming Meetings <span class="count">({len([m for m in meetings if m['status'] != 'Cancelled'])})</span></h2>
            {'<table><tr><th>Date</th><th>Time</th><th>Prospect</th><th>Type</th><th>Status</th><th>Prep</th></tr>' + ''.join(f'<tr><td>{_esc(m["date"])}</td><td>{_esc(m["time"])}</td><td class="name-cell">{_esc(m["prospect"])}</td><td>{_esc(m["type"])}</td><td><span class="badge" style="background:{"#27ae60" if m["status"]=="Completed" else "#e74c3c" if m["status"]=="Cancelled" else "#3498db"}">{_esc(m["status"])}</span></td><td class="notes">{_esc(m["prep_notes"][:50])}{"..." if len(m["prep_notes"])>50 else ""}</td></tr>' for m in meetings if m['status'] != 'Cancelled') + '</table>' if meetings else '<div class="empty-state"><p>No meetings scheduled. Text the bot to add one.</p></div>'}
        </div>
        <div class="section">
            <h2>Insurance Book <span class="count">({len(book_entries)} contacts)</span></h2>
            {'<div style="display:flex;gap:24px;margin-bottom:16px"><div class="kpi-card" style="flex:1;padding:12px 16px"><div class="kpi-label">Called</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if b["status"].lower() not in ("not called","")])) + '</div></div><div class="kpi-card green" style="flex:1;padding:12px 16px"><div class="kpi-label">Booked</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if b["status"].lower()=="booked meeting"])) + '</div></div><div class="kpi-card blue" style="flex:1;padding:12px 16px"><div class="kpi-label">Remaining</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if b["status"].lower() in ("not called","")])) + '</div></div></div><table><tr><th>Name</th><th>Phone</th><th>Status</th><th>Last Called</th><th>Notes</th></tr>' + ''.join(f'<tr><td class="name-cell">{_esc(b["name"])}</td><td>{_esc(b["phone"])}</td><td><span class="badge" style="background:{"#27ae60" if b["status"].lower()=="booked meeting" else "#e74c3c" if b["status"].lower()=="not interested" else "#f39c12" if b["status"].lower() in ("callback","no answer") else "#3498db"}">{_esc(b["status"])}</span></td><td>{_esc(b["last_called"].split(" ")[0] if b["last_called"] and b["last_called"]!="None" else "")}</td><td class="notes">{_esc(b["notes"][:40])}{"..." if len(b["notes"])>40 else ""}</td></tr>' for b in book_entries[:20]) + '</table>' if book_entries else '<div class="empty-state"><p>No insurance book uploaded. Send a CSV via Telegram.</p></div>'}
        </div>
    </div>

    <div class="refresh-note">Click any prospect row to edit. Changes save to your pipeline instantly.</div>

    </div><!-- end tab-pipeline -->

    <!-- ═══ TAB 2: REVENUE FORECAST ═══ -->
    <div class="tab-content" id="tab-forecast" style="margin-top:24px">

        <div class="kpi-grid" style="grid-template-columns: repeat(3, 1fr)">
            <div class="kpi-card green">
                <div class="kpi-label">Total Premium YTD</div>
                <div class="kpi-value">{fmt_money(forecast_revenue)}</div>
            </div>
            <div class="kpi-card blue">
                <div class="kpi-label">Total AUM</div>
                <div class="kpi-value">{fmt_money(forecast_aum)}</div>
            </div>
            <div class="kpi-card gold">
                <div class="kpi-label">FYC (Won)</div>
                <div class="kpi-value">{fmt_money(won_fyc)}</div>
            </div>
        </div>
        <div class="kpi-grid" style="grid-template-columns: repeat(3, 1fr); margin-top:12px">
            <div class="kpi-card purple">
                <div class="kpi-label">Projected Premium</div>
                <div class="kpi-value">{fmt_money(projected_revenue)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Projected AUM</div>
                <div class="kpi-value">{fmt_money(projected_aum)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Projected FYC</div>
                <div class="kpi-value">{fmt_money(projected_fyc)}</div>
            </div>
        </div>

        <div class="two-col">
            <div class="target-card">
                <div class="target-header">
                    <h3>Premium Target: {fmt_money(PREMIUM_TARGET)}</h3>
                    <span class="pace-indicator {'pace-ahead' if premium_on_pace else 'pace-behind'}">{'Ahead of pace' if premium_on_pace else 'Behind pace'}</span>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill {'green' if premium_on_pace else 'red'}" style="width:{min(premium_pct, 100):.0f}%">{premium_pct:.0f}%</div>
                </div>
                <div class="target-meta">
                    <span>Actual: {fmt_money(forecast_revenue)} (baseline {fmt_money(BASELINE_PREMIUM)} + pipeline {fmt_money(won_revenue)})</span>
                    <span>Gap: {fmt_money(max(0, PREMIUM_TARGET - forecast_revenue))}</span>
                </div>
                <div class="target-meta" style="margin-top:8px">
                    <span>Pipeline weighted: +{fmt_money(weighted_revenue)}</span>
                    <span>Need {fmt_money(max(0, (PREMIUM_TARGET - forecast_revenue - weighted_revenue) / max(1, days_remaining) * 30))}/mo to close gap</span>
                </div>
            </div>
            <div class="target-card">
                <div class="target-header">
                    <h3>AUM Target: {fmt_money(AUM_TARGET)}</h3>
                    <span class="pace-indicator {'pace-ahead' if aum_on_pace else 'pace-behind'}">{'Ahead of pace' if aum_on_pace else 'Behind pace'}</span>
                </div>
                <div class="progress-bar-container">
                    <div class="progress-bar-fill {'green' if aum_on_pace else 'red'}" style="width:{min(aum_pct, 100):.0f}%">{aum_pct:.0f}%</div>
                </div>
                <div class="target-meta">
                    <span>Actual: {fmt_money(forecast_aum)} (baseline {fmt_money(BASELINE_AUM)} + pipeline {fmt_money(won_aum_pipeline)})</span>
                    <span>Gap: {fmt_money(max(0, AUM_TARGET - forecast_aum))}</span>
                </div>
                <div class="target-meta" style="margin-top:8px">
                    <span>Pipeline weighted: +{fmt_money(weighted_aum)}</span>
                    <span>Need {fmt_money(max(0, (AUM_TARGET - forecast_aum - weighted_aum) / max(1, days_remaining) * 30))}/mo to close gap</span>
                </div>
            </div>
        </div>

        <div class="target-card">
            <h3 style="margin-bottom:16px">Monthly Revenue vs Target</h3>
            <canvas id="monthlyChart" height="80"></canvas>
        </div>

        <div class="target-card">
            <div class="target-header">
                <h3>Year Progress</h3>
                <span style="font-size:12px;color:#7f8c8d">{days_elapsed} of {days_total} days ({pct_year:.0f}%)</span>
            </div>
            <div class="progress-bar-container">
                <div class="progress-bar-fill teal" style="width:{pct_year:.0f}%">{pct_year:.0f}%</div>
            </div>
        </div>

        <div class="section">
            <h2>Pipeline Weighted Forecast</h2>
            <table>
                <tr><th>Stage</th><th>Deals</th><th>Prob</th><th>Premium</th><th>Wtd Premium</th><th>AUM</th><th>Wtd AUM</th><th>FYC</th><th>Wtd FYC</th></tr>
                {''.join(f'<tr><td><span class="badge" style="background:{STAGE_COLORS.get(s, "#BDC3C7")}">{_esc(s)}</span></td><td>{stage_counts.get(s, 0)}</td><td>{int(stage_probability.get(s, 0.1)*100)}%</td><td class="money">{fmt_money(stage_revenue.get(s, 0))}</td><td class="money">{fmt_money(stage_revenue.get(s, 0) * stage_probability.get(s, 0.1))}</td><td class="money">{fmt_money(sum(parse_money(p["aum"]) for p in active if p["stage"]==s))}</td><td class="money">{fmt_money(sum(parse_money(p["aum"]) for p in active if p["stage"]==s) * stage_probability.get(s, 0.1))}</td><td class="money">{fmt_money(stage_fyc.get(s, 0))}</td><td class="money">{fmt_money(stage_fyc.get(s, 0) * stage_probability.get(s, 0.1))}</td></tr>' for s in stage_order[:-1] if stage_counts.get(s, 0) > 0)}
                <tr style="font-weight:700;border-top:2px solid #2c3e50"><td>Total Weighted</td><td></td><td></td><td></td><td class="money">{fmt_money(weighted_revenue)}</td><td></td><td class="money">{fmt_money(weighted_aum)}</td><td></td><td class="money">{fmt_money(weighted_fyc)}</td></tr>
            </table>
        </div>

    </div><!-- end tab-forecast -->

    <!-- ═══ TAB 3: CONVERSION FUNNEL ═══ -->
    <div class="tab-content" id="tab-funnel" style="margin-top:24px">

        <div class="section">
            <h2>Sales Funnel</h2>
            <div style="max-width:700px;margin:0 auto;padding:20px 0">
                {''.join(f'<div class="funnel-stage"><div class="funnel-label">{_esc(stage_order[i])}</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:{max(8, funnel_counts[stage_order[i]] / max(1, funnel_counts[stage_order[0]]) * 100):.0f}%;background:{STAGE_COLORS.get(stage_order[i], "#BDC3C7")}">{funnel_counts[stage_order[i]]}</div><div class="funnel-rate">{f"{funnel_rates[i]:.0f}% pass" if i < len(funnel_rates) else ""}</div><div class="funnel-velocity">{f"~{avg_stage_days.get(stage_order[i], 0):.0f}d avg" if stage_order[i] in avg_stage_days else ""}</div></div></div>' for i in range(len(stage_order)))}
            </div>
        </div>

        <div class="two-col">
            <div class="section">
                <h2>Source Effectiveness</h2>
                {'<table><tr><th>Source</th><th>Total Leads</th><th>Wins</th><th>Conversion</th></tr>' + source_eff_rows + '</table>' if source_conversion else '<div class="empty-state"><p>No source data yet.</p></div>'}
            </div>
            <div class="section">
                <h2>Deals Aging (Active Pipeline)</h2>
                {'<table><tr><th>Prospect</th><th>Stage</th><th>Days Open</th><th>Status</th></tr>' + aging_rows + '</table>' if active else '<div class="empty-state"><p>No active deals.</p></div>'}
            </div>
        </div>

        <div class="section">
            <h2>Stage Velocity</h2>
            <canvas id="velocityChart" height="60"></canvas>
        </div>

    </div><!-- end tab-funnel -->

    <!-- ═══ TAB 4: ACTIVITY SCOREBOARD ═══ -->
    <div class="tab-content" id="tab-scoreboard" style="margin-top:24px">

        <div style="text-align:center;margin-bottom:24px">
            {f'<div class="streak-badge">🔥 {streak} Day Streak</div>' if streak > 0 else '<div style="color:#7f8c8d;font-size:14px">No streak yet — make a call to start one!</div>'}
        </div>

        <div class="score-grid">
            <div class="score-card {'fire' if calls_today >= DAILY_CALLS_TARGET else ''}">
                <h4>Calls Today</h4>
                <div class="score-big">{calls_today}</div>
                <div class="score-target">Target: {DAILY_CALLS_TARGET}</div>
                <div class="progress-bar-container" style="margin-top:8px">
                    <div class="progress-bar-fill {'green' if calls_today >= DAILY_CALLS_TARGET else 'blue'}" style="width:{min(calls_today / DAILY_CALLS_TARGET * 100, 100):.0f}%"></div>
                </div>
            </div>
            <div class="score-card {'fire' if emails_today >= DAILY_EMAILS_TARGET else ''}">
                <h4>Emails Today</h4>
                <div class="score-big">{emails_today}</div>
                <div class="score-target">Target: {DAILY_EMAILS_TARGET}</div>
                <div class="progress-bar-container" style="margin-top:8px">
                    <div class="progress-bar-fill {'green' if emails_today >= DAILY_EMAILS_TARGET else 'blue'}" style="width:{min(emails_today / DAILY_EMAILS_TARGET * 100, 100):.0f}%"></div>
                </div>
            </div>
            <div class="score-card {'fire' if meetings_week >= WEEKLY_MEETINGS_TARGET else ''}">
                <h4>Meetings This Week</h4>
                <div class="score-big">{meetings_week}</div>
                <div class="score-target">Target: {WEEKLY_MEETINGS_TARGET}</div>
                <div class="progress-bar-container" style="margin-top:8px">
                    <div class="progress-bar-fill {'green' if meetings_week >= WEEKLY_MEETINGS_TARGET else 'blue'}" style="width:{min(meetings_week / WEEKLY_MEETINGS_TARGET * 100, 100):.0f}%"></div>
                </div>
            </div>
            <div class="score-card">
                <h4>Total Activities Today</h4>
                <div class="score-big">{activities_today}</div>
                <div class="score-target">Week total: {activities_week}</div>
            </div>
        </div>

        <div class="two-col">
            <div class="section">
                <h2>This Week's Numbers</h2>
                <table>
                    <tr><th>Metric</th><th>Today</th><th>This Week</th><th>Target</th></tr>
                    <tr><td>Calls</td><td>{calls_today}</td><td>{calls_week}</td><td>{DAILY_CALLS_TARGET}/day</td></tr>
                    <tr><td>Emails</td><td>{emails_today}</td><td>{emails_week}</td><td>{DAILY_EMAILS_TARGET}/day</td></tr>
                    <tr><td>Meetings</td><td>{meetings_today}</td><td>{meetings_week}</td><td>{WEEKLY_MEETINGS_TARGET}/week</td></tr>
                    <tr style="font-weight:700;border-top:2px solid #2c3e50"><td>Total Activities</td><td>{activities_today}</td><td>{activities_week}</td><td></td></tr>
                </table>
            </div>
            <div class="section">
                <h2>Insurance Book Progress</h2>
                {'<div style="text-align:center;padding:20px"><div class="score-big" style="font-size:48px">' + str(len([b for b in book_entries if b["status"].lower() not in ("not called","")])) + '<span style="font-size:20px;color:#7f8c8d">/' + str(len(book_entries)) + '</span></div><div style="color:#7f8c8d;margin-top:4px">Contacts Called</div><div class="progress-bar-container" style="margin-top:12px"><div class="progress-bar-fill teal" style="width:' + str(min(len([b for b in book_entries if b["status"].lower() not in ("not called","")]) / max(1, len(book_entries)) * 100, 100)) + '%">' + str(int(len([b for b in book_entries if b["status"].lower() not in ("not called","")]) / max(1, len(book_entries)) * 100)) + '%</div></div><div class="target-meta" style="margin-top:8px"><span>Booked: ' + str(len([b for b in book_entries if b["status"].lower()=="booked meeting"])) + '</span><span>Not Interested: ' + str(len([b for b in book_entries if b["status"].lower()=="not interested"])) + '</span><span>Callbacks: ' + str(len([b for b in book_entries if b["status"].lower()=="callback"])) + '</span></div></div>' if book_entries else '<div class="empty-state"><p>Upload an insurance book CSV to track progress.</p></div>'}
            </div>
        </div>

    </div><!-- end tab-scoreboard -->

    <!-- ═══ TAB 5: TASKS ═══ -->
    <div class="tab-content" id="tab-tasks">

        <div class="kpi-grid" style="grid-template-columns: repeat(4, 1fr); margin-top:24px">
            <div class="kpi-card {'red' if len(overdue_tasks) > 0 else ''}">
                <div class="kpi-label">Overdue</div>
                <div class="kpi-value">{len(overdue_tasks)}</div>
            </div>
            <div class="kpi-card {'gold' if len(due_today_tasks) > 0 else ''}">
                <div class="kpi-label">Due Today</div>
                <div class="kpi-value">{len(due_today_tasks)}</div>
            </div>
            <div class="kpi-card blue">
                <div class="kpi-label">Pending</div>
                <div class="kpi-value">{len(all_tasks)}</div>
            </div>
            <div class="kpi-card green">
                <div class="kpi-label">Done This Week</div>
                <div class="kpi-value">{len(completed_this_week)}</div>
            </div>
        </div>

        {'<div class="section"><h2>Overdue <span class="count" style="color:#E74C3C">(' + str(len(overdue_tasks)) + ')</span></h2><table><tr><th style="width:40px"></th><th>Task</th><th>Prospect</th><th>Due Date</th><th style="width:40px"></th></tr>' + overdue_task_rows + '</table></div>' if overdue_tasks else ''}

        {'<div class="section"><h2>Due Today <span class="count" style="color:#F39C12">(' + str(len(due_today_tasks)) + ')</span></h2><table><tr><th style="width:40px"></th><th>Task</th><th>Prospect</th><th style="width:40px"></th></tr>' + due_today_task_rows + '</table></div>' if due_today_tasks else ''}

        <div class="section">
            <h2 style="display:flex;justify-content:space-between;align-items:center">Upcoming & No Date <span class="count">({len(upcoming_tasks)})</span> <button onclick="openAddTask()" style="background:#27AE60;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:14px">+ Add Task</button></h2>
            {'<table><tr><th style="width:40px"></th><th>Task</th><th>Prospect</th><th>Due Date</th><th style="width:40px"></th></tr>' + upcoming_task_rows + '</table>' if upcoming_tasks else '<div class="empty-state"><p>No upcoming tasks. Add one above or use /todo in Telegram!</p></div>'}
        </div>

        {'<div class="section"><h2>Recently Completed <span class="count">(' + str(len(completed_tasks_recent)) + ')</span></h2><table><tr><th style="width:40px"></th><th>Task</th><th>Prospect</th><th>Completed</th></tr>' + completed_rows + '</table></div>' if completed_rows else ''}

    </div><!-- end tab-tasks -->

</div>

<!-- Edit Modal -->
<div class="modal-overlay" id="editModal">
<div class="modal">
    <h2 id="modalTitle">Edit Prospect</h2>
    <input type="hidden" id="origName">
    <div class="form-row">
        <div><label>Name</label><input id="fName" type="text"></div>
        <div><label>Phone</label><input id="fPhone" type="text"></div>
    </div>
    <div class="form-row">
        <div><label>Email</label><input id="fEmail" type="text"></div>
        <div><label>Source</label>
            <select id="fSource">
                <option value="">—</option>
                <option>Referral</option><option>Website</option><option>Social Media</option>
                <option>Seminar</option><option>Cold Outreach</option><option>LinkedIn</option>
                <option>Podcast</option><option>Networking</option><option>Centre of Influence</option><option>Other</option>
            </select>
        </div>
    </div>
    <div class="form-row">
        <div><label>Priority</label>
            <select id="fPriority">
                <option value="">—</option>
                <option>Hot</option><option>Warm</option><option>Cold</option>
            </select>
        </div>
        <div><label>Stage</label>
            <select id="fStage">
                <option value="">—</option>
                <option>New Lead</option><option>Contacted</option><option>Discovery Call</option>
                <option>Needs Analysis</option><option>Plan Presentation</option><option>Proposal Sent</option>
                <option>Negotiation</option><option>Closed-Won</option><option>Closed-Lost</option><option>Nurture</option>
            </select>
        </div>
    </div>
    <div class="form-row">
        <div><label>Product</label>
            <select id="fProduct">
                <option value="">—</option>
                <option>Life Insurance</option><option>Wealth Management</option><option>Life Insurance + Wealth</option>
                <option>Disability Insurance</option><option>Critical Illness</option><option>Group Benefits</option>
                <option>Estate Planning</option><option>Other</option>
            </select>
        </div>
        <div><label>Next Follow-Up</label><input id="fFollowup" type="date"></div>
    </div>
    <div class="form-row">
        <div><label>AUM</label><input id="fAum" type="text" placeholder="e.g. 500000"></div>
        <div><label>Premium</label><input id="fRevenue" type="text" placeholder="e.g. 5000"></div>
    </div>
    <label>Notes</label>
    <textarea id="fNotes"></textarea>
    <div class="actions">
        <button class="btn btn-danger left" id="deleteBtn" onclick="deleteProspect()">Delete</button>
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="saveProspect()">Save</button>
    </div>
</div>
</div>

<!-- Task Modal -->
<div class="modal-overlay" id="taskModal">
<div class="modal" style="max-width:500px">
    <h2>Add Task</h2>
    <div style="margin-bottom:12px">
        <label>Task</label><input id="tTitle" type="text" placeholder="What needs to be done?" style="width:100%">
    </div>
    <div class="form-row">
        <div><label>Prospect (optional)</label><input id="tProspect" type="text" placeholder="Prospect name"></div>
        <div><label>Due Date</label><input id="tDue" type="date"></div>
    </div>
    <div class="form-row">
        <div><label>Reminder</label><input id="tRemind" type="datetime-local"></div>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
        <button onclick="saveTask()" style="background:#27AE60;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer">Save</button>
        <button onclick="closeTaskModal()" style="background:#95A5A6;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer">Cancel</button>
    </div>
</div>
</div>

<script>
// Tab switching
function showTab(name) {{
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    event.target.classList.add('active');
    // Initialize charts when their tab is shown
    if (name === 'forecast' && !window._forecastInit) initForecastCharts();
    if (name === 'funnel' && !window._funnelInit) initFunnelCharts();
}}

const chartColors = ['#1abc9c','#3498db','#8e44ad','#e67e22','#f39c12','#2980b9','#e74c3c','#27ae60','#95a5a6','#2c3e50'];

new Chart(document.getElementById('stageChart'), {{
    type: 'doughnut',
    data: {{
        labels: {stage_labels},
        datasets: [{{ data: {stage_values}, backgroundColor: {stage_chart_colors} }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }} }}
}});

new Chart(document.getElementById('sourceChart'), {{
    type: 'doughnut',
    data: {{
        labels: {source_labels},
        datasets: [{{ data: {source_values}, backgroundColor: chartColors }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }} }}
}});

new Chart(document.getElementById('productChart'), {{
    type: 'doughnut',
    data: {{
        labels: {product_labels},
        datasets: [{{ data: {product_values}, backgroundColor: chartColors }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }} }}
}});

// Modal logic
let isAdding = false;

function openEdit(p) {{
    isAdding = false;
    document.getElementById('modalTitle').textContent = 'Edit: ' + p.name;
    document.getElementById('origName').value = p.name;
    document.getElementById('fName').value = p.name;
    document.getElementById('fPhone').value = p.phone || '';
    document.getElementById('fEmail').value = p.email || '';
    document.getElementById('fSource').value = p.source || '';
    document.getElementById('fPriority').value = p.priority || '';
    document.getElementById('fStage').value = p.stage || '';
    document.getElementById('fProduct').value = p.product || '';
    document.getElementById('fAum').value = p.aum || '';
    document.getElementById('fRevenue').value = p.revenue || '';
    document.getElementById('fNotes').value = p.notes || '';
    let fu = p.next_followup || '';
    if (fu && fu !== 'None') {{
        fu = fu.split(' ')[0];
        if (/^\\d{{4}}-\\d{{2}}-\\d{{2}}$/.test(fu)) document.getElementById('fFollowup').value = fu;
        else document.getElementById('fFollowup').value = '';
    }} else document.getElementById('fFollowup').value = '';
    document.getElementById('deleteBtn').style.display = 'inline-block';
    document.getElementById('editModal').classList.add('active');
}}

function openAdd() {{
    isAdding = true;
    document.getElementById('modalTitle').textContent = 'Add Prospect';
    document.getElementById('origName').value = '';
    ['fName','fPhone','fEmail','fNotes','fAum','fRevenue','fFollowup'].forEach(id => document.getElementById(id).value = '');
    ['fSource','fPriority','fProduct'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('fStage').value = 'New Lead';
    document.getElementById('deleteBtn').style.display = 'none';
    document.getElementById('editModal').classList.add('active');
}}

function closeModal() {{
    document.getElementById('editModal').classList.remove('active');
}}

function getFormData() {{
    return {{
        name: document.getElementById('fName').value.trim(),
        phone: document.getElementById('fPhone').value.trim(),
        email: document.getElementById('fEmail').value.trim(),
        source: document.getElementById('fSource').value,
        priority: document.getElementById('fPriority').value,
        stage: document.getElementById('fStage').value,
        product: document.getElementById('fProduct').value,
        aum: document.getElementById('fAum').value.trim(),
        revenue: document.getElementById('fRevenue').value.trim(),
        next_followup: document.getElementById('fFollowup').value,
        notes: document.getElementById('fNotes').value.trim(),
    }};
}}

const _csrfToken = document.querySelector('meta[name="csrf-token"]').content;

async function saveProspect() {{
    const data = getFormData();
    if (!data.name) {{ alert('Name is required'); return; }}
    const hdrs = {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}};
    try {{
        let res;
        if (isAdding) {{
            res = await fetch('/api/prospect', {{ method: 'POST', headers: hdrs, body: JSON.stringify(data) }});
        }} else {{
            const origName = document.getElementById('origName').value;
            res = await fetch('/api/prospect/' + encodeURIComponent(origName), {{ method: 'PUT', headers: hdrs, body: JSON.stringify(data) }});
        }}
        const result = await res.json();
        if (result.ok) {{ closeModal(); location.reload(); }}
        else alert(result.error || 'Error saving');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function deleteProspect() {{
    const name = document.getElementById('origName').value;
    if (!confirm('Delete ' + name + '?')) return;
    try {{
        const res = await fetch('/api/prospect/' + encodeURIComponent(name), {{ method: 'DELETE', headers: {{'X-CSRF-Token': _csrfToken}} }});
        const result = await res.json();
        if (result.ok) {{ closeModal(); location.reload(); }}
        else alert(result.error || 'Error deleting');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

document.getElementById('editModal').addEventListener('click', function(e) {{
    if (e.target === this) closeModal();
}});

// Task management
function openAddTask() {{
    document.getElementById('tTitle').value = '';
    document.getElementById('tProspect').value = '';
    document.getElementById('tDue').value = '';
    document.getElementById('tRemind').value = '';
    document.getElementById('taskModal').classList.add('active');
    document.getElementById('tTitle').focus();
}}

function closeTaskModal() {{
    document.getElementById('taskModal').classList.remove('active');
}}

document.getElementById('taskModal').addEventListener('click', function(e) {{
    if (e.target === this) closeTaskModal();
}});

async function saveTask() {{
    const title = document.getElementById('tTitle').value.trim();
    if (!title) {{ alert('Task title is required'); return; }}
    const data = {{
        title: title,
        prospect: document.getElementById('tProspect').value.trim(),
        due_date: document.getElementById('tDue').value || null,
        remind_at: document.getElementById('tRemind').value ? document.getElementById('tRemind').value.replace('T', ' ') : null,
        assigned_to: '',
        created_by: '',
    }};
    try {{
        const res = await fetch('/api/task', {{ method: 'POST', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}}, body: JSON.stringify(data) }});
        const result = await res.json();
        if (result.ok) {{ closeTaskModal(); location.reload(); }}
        else alert(result.error || 'Error creating task');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function completeTask(id, checkbox) {{
    try {{
        const res = await fetch('/api/task/' + id + '/complete', {{ method: 'PUT', headers: {{'X-CSRF-Token': _csrfToken}} }});
        const result = await res.json();
        if (result.ok) {{ location.reload(); }}
        else {{ checkbox.checked = false; alert(result.error || 'Error'); }}
    }} catch(e) {{ checkbox.checked = false; alert('Error: ' + e.message); }}
}}

async function deleteTask(id) {{
    if (!confirm('Delete this task?')) return;
    try {{
        const res = await fetch('/api/task/' + id, {{ method: 'DELETE', headers: {{'X-CSRF-Token': _csrfToken}} }});
        const result = await res.json();
        if (result.ok) {{ location.reload(); }}
        else alert(result.error || 'Error');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Forecast charts (lazy init)
function initForecastCharts() {{
    window._forecastInit = true;
    const ctx = document.getElementById('monthlyChart');
    if (!ctx) return;
    new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: {all_months},
            datasets: [
                {{
                    label: 'Won Premium',
                    data: {monthly_rev_values},
                    backgroundColor: '#1abc9c',
                    borderRadius: 6,
                }},
                {{
                    label: 'Monthly Target',
                    data: {monthly_target_line},
                    type: 'line',
                    borderColor: '#e74c3c',
                    borderDash: [5, 5],
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: false,
                }}
            ]
        }},
        options: {{
            responsive: true,
            scales: {{
                y: {{
                    beginAtZero: true,
                    ticks: {{ callback: v => '$' + (v/1000).toFixed(0) + 'K' }}
                }}
            }},
            plugins: {{
                legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }}
            }}
        }}
    }});
}}

// Velocity chart (lazy init)
function initFunnelCharts() {{
    window._funnelInit = true;
    const ctx = document.getElementById('velocityChart');
    if (!ctx) return;
    const velocityLabels = {list(avg_stage_days.keys())};
    const velocityData = {[round(v, 1) for v in avg_stage_days.values()]};
    new Chart(ctx, {{
        type: 'bar',
        data: {{
            labels: velocityLabels,
            datasets: [{{
                label: 'Avg Days in Stage',
                data: velocityData,
                backgroundColor: velocityData.map(d => d > 14 ? '#e74c3c' : d > 7 ? '#f39c12' : '#27ae60'),
                borderRadius: 6,
            }}]
        }},
        options: {{
            indexAxis: 'y',
            responsive: true,
            scales: {{ x: {{ beginAtZero: true, title: {{ display: true, text: 'Days' }} }} }},
            plugins: {{ legend: {{ display: false }} }}
        }}
    }});
}}
</script>

</body>
</html>"""

    return Response(html, mimetype="text/html")


def register_webhook(flask_app, process_update_fn=None):
    """Register the Telegram webhook route and intake webhook on the Flask app."""
    from webhook_intake import intake_bp
    flask_app.register_blueprint(intake_bp)

    telegram_webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        if process_update_fn is None:
            return "Bot not initialized", 503
        # Validate Telegram's secret_token header if configured
        if telegram_webhook_secret:
            token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not hmac.compare_digest(token, telegram_webhook_secret):
                return "Unauthorized", 401
        update_data = request.get_json(force=True, silent=True)
        if not update_data:
            return "ok"
        # Fire and forget — don't block Flask thread waiting for LLM responses
        import threading
        threading.Thread(target=process_update_fn, args=(update_data,), daemon=True).start()
        return "ok"


def run_dashboard():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def start_dashboard_thread():
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
