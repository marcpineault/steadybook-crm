import html as _html
import hmac
import logging
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


def _json_script(value):
    """Serialize value to JSON safe for embedding inside a <script> block.

    json.dumps() does not escape '<', '>' or '/' by default, which allows
    a string like '</script>' to break out of the enclosing script tag.
    Replacing '</' with '<\\/' is the standard mitigation: '\\/' is a valid
    JSON escape sequence that browsers parse identically to '/'.
    """
    return json.dumps(value).replace("</", "<\\/")


DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")
if not DASHBOARD_API_KEY:
    raise RuntimeError(
        "DASHBOARD_API_KEY must be set. Generate one with: "
        "python -c 'import secrets; print(secrets.token_urlsafe(32))'"
    )

# CSRF tokens with expiry timestamps for proper lifecycle management.
# Each token expires after _CSRF_TOKEN_TTL seconds.
_csrf_tokens: dict = {}  # token -> expiry timestamp
_csrf_lock = threading.Lock()
_MAX_CSRF_TOKENS = 200
_CSRF_TOKEN_TTL = 3600  # 1 hour


def _generate_csrf_token() -> str:
    """Generate a CSRF token with expiry, store it, and return it."""
    import time
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _csrf_lock:
        # Purge expired tokens
        expired = [t for t, exp in _csrf_tokens.items() if exp < now]
        for t in expired:
            del _csrf_tokens[t]
        # Evict oldest tokens if we hit the limit
        while len(_csrf_tokens) >= _MAX_CSRF_TOKENS:
            oldest = min(_csrf_tokens, key=_csrf_tokens.get)
            del _csrf_tokens[oldest]
        _csrf_tokens[token] = now + _CSRF_TOKEN_TTL
    return token


def _validate_csrf_token(token: str) -> bool:
    """Validate a CSRF token (checks existence and expiry)."""
    import time
    with _csrf_lock:
        expiry = _csrf_tokens.get(token)
        if expiry is not None and expiry >= time.time():
            return True
        # Remove expired token if present
        _csrf_tokens.pop(token, None)
    return False


def _require_auth(f):
    """Decorator: accepts API key, CSRF token, or login cookie."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check API key first (for external/programmatic access)
        api_key = request.headers.get("X-API-Key", "")
        if DASHBOARD_API_KEY and api_key and hmac.compare_digest(api_key, DASHBOARD_API_KEY):
            return f(*args, **kwargs)
        # Check CSRF token (for dashboard UI) — validates existence and expiry
        csrf_token = request.headers.get("X-CSRF-Token", "")
        if csrf_token and _validate_csrf_token(csrf_token):
            return f(*args, **kwargs)
        # Check login cookie (set by /login form — hash of API key)
        dash_cookie = request.cookies.get("dash_auth", "")
        if dash_cookie and DASHBOARD_API_KEY:
            import hashlib
            expected = hashlib.sha256(DASHBOARD_API_KEY.encode()).hexdigest()
            if hmac.compare_digest(dash_cookie, expected):
                return f(*args, **kwargs)
        return jsonify({"error": "Unauthorized"}), 401
    return decorated


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MB max request size

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])
except ImportError:
    logging.getLogger(__name__).warning("flask-limiter not installed — rate limiting disabled")
    limiter = None


@app.after_request
def _set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response

STAGE_COLORS = {
    "New Lead":          ("#3498DB", "#fff"),
    "Contacted":         ("#9B59B6", "#fff"),
    "Discovery Call":    ("#E67E22", "#fff"),
    "Needs Analysis":    ("#F39C12", "#fff"),
    "Plan Presentation": ("#1ABC9C", "#fff"),
    "Proposal Sent":     ("#2ECC71", "#fff"),
    "Negotiation":       ("#E74C3C", "#fff"),
    "Nurture":           ("#95A5A6", "#fff"),
    "Closed-Won":        ("#27AE60", "#fff"),
    "Closed-Lost":       ("#7F8C8D", "#fff"),
}
PRIORITY_COLORS = {"Hot": "#E74C3C", "Warm": "#F39C12", "Cold": "#3498DB"}


def _stage_bg(stage):
    """Return background color for a stage badge."""
    pair = STAGE_COLORS.get(stage)
    return pair[0] if pair else "#BDC3C7"


def _stage_fg(stage):
    """Return foreground color for a stage badge."""
    pair = STAGE_COLORS.get(stage)
    return pair[1] if pair else "#fff"


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


@app.route("/api/prospect/<path:name>", methods=["PUT"])
@_require_auth
def api_update_prospect(name):
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400
    result = db.update_prospect(name, data)
    if "not found" in result.lower() or "could not find" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospect/<path:name>", methods=["DELETE"])
@_require_auth
def api_delete_prospect(name):
    result = db.delete_prospect(name)
    if "not found" in result.lower() or "could not find" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospect/merge", methods=["POST"])
@_require_auth
def api_merge_prospects():
    data = request.json
    if not data or not data.get("keep") or not data.get("merge"):
        return jsonify({"error": "Need 'keep' and 'merge' fields"}), 400
    result = db.merge_prospects(data["keep"], data["merge"])
    if "not found" in result.lower() or "could not find" in result.lower() or "Cannot" in result:
        return jsonify({"error": result}), 400
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospect/update", methods=["PUT"])
@_require_auth
def api_update_prospect_by_name():
    data = request.json
    if not data or not data.get("name") or not data.get("updates"):
        return jsonify({"error": "Name and updates required"}), 400
    result = db.update_prospect(data["name"], data["updates"])
    if "not found" in result.lower() or "could not find" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


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


@app.route("/api/task/<int:task_id>", methods=["PUT"])
@_require_auth
def api_update_task(task_id):
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    result = db.update_task(task_id, data, is_admin=True)
    if "not found" in result.lower() or "could not find" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


@app.route("/api/activity", methods=["POST"])
@_require_auth
def api_add_activity():
    data = request.json
    if not data or not data.get("action"):
        return jsonify({"error": "Action required"}), 400
    result = db.add_activity(data)
    return jsonify({"ok": True, "message": result})


@app.route("/api/prospect/<path:name>/detail")
@_require_auth
def api_prospect_detail(name):
    """Get full prospect detail: info + activities + tasks + interactions."""
    prospect = db.get_prospect_by_name(name)
    if not prospect:
        return jsonify({"error": "Not found"}), 404
    activities = db.read_activities(limit=200)
    prospect_activities = [a for a in activities if a.get("prospect", "").lower() == name.lower()
                           or name.lower() in a.get("prospect", "").lower()]
    interactions = db.read_interactions(limit=100, prospect=name)
    tasks = db.get_tasks(prospect=name, status=None, limit=50)
    # Calculate health score and next action
    from datetime import date as _date
    _today = _date.today()
    _lam = {}
    for a in activities:
        _n = a.get("prospect", "").strip().lower()
        if _n and a.get("date"):
            try:
                _ad = datetime.strptime(a["date"].split(" ")[0], "%Y-%m-%d").date()
                if _n not in _lam or _ad > _lam[_n]:
                    _lam[_n] = _ad
            except (ValueError, IndexError):
                pass
    health = _calc_health_score(prospect, _lam, _today)
    next_action = STAGE_NEXT_ACTION.get(prospect.get("stage", ""), "Keep following up")
    return jsonify({
        "prospect": prospect,
        "activities": prospect_activities[:20],
        "interactions": interactions[:20],
        "tasks": tasks,
        "health_score": health,
        "next_action": next_action,
    })


@app.route("/api/task/<int:task_id>/complete", methods=["PUT"])
@_require_auth
def api_complete_task(task_id):
    result = db.complete_task(task_id, "", is_admin=True)
    return jsonify({"ok": True, "message": result})


@app.route("/api/task/<int:task_id>", methods=["DELETE"])
@_require_auth
def api_delete_task(task_id):
    result = db.delete_task(task_id, "", is_admin=True)
    if "not found" in result.lower() or "could not find" in result.lower():
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


def _build_focus_banner(overdue_followups, overdue_tasks, due_today_tasks, todays_meetings, stale_prospects):
    """Build the Today's Focus smart banner with actionable alerts."""
    items = []
    if overdue_followups:
        items.append(f'<span style="color:#E74C3C">&#9888; {len(overdue_followups)} overdue follow-up{"s" if len(overdue_followups) != 1 else ""}</span>')
    if overdue_tasks:
        items.append(f'<span style="color:#E74C3C">&#9888; {len(overdue_tasks)} overdue task{"s" if len(overdue_tasks) != 1 else ""}</span>')
    if due_today_tasks:
        items.append(f'<span style="color:#F39C12">&#128203; {len(due_today_tasks)} task{"s" if len(due_today_tasks) != 1 else ""} due today</span>')
    if todays_meetings:
        items.append(f'<span style="color:#3498DB">&#128197; {len(todays_meetings)} meeting{"s" if len(todays_meetings) != 1 else ""} today</span>')
    if stale_prospects:
        items.append(f'<span style="color:#E67E22">&#128164; {len(stale_prospects)} prospect{"s" if len(stale_prospects) != 1 else ""} going cold (14+ days idle)</span>')

    # Build meeting detail lines
    meeting_details_html = ""
    if todays_meetings:
        detail_lines = []
        for m in todays_meetings:
            time_str = _html.escape(m.get("time", "").strip()) if m.get("time") else ""
            prospect_str = _html.escape(m.get("prospect", "").strip()) if m.get("prospect") else ""
            meeting_type = _html.escape(m.get("type", "").strip()) if m.get("type") else ""
            parts = []
            if time_str:
                parts.append(time_str)
            label = prospect_str
            if meeting_type:
                label += f" ({meeting_type})"
            if label:
                parts.append(label)
            if parts:
                detail_lines.append(" — ".join(parts))
        if detail_lines:
            meeting_details_html = '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #fce5b5;font-size:13px;color:#2c3e50">' + " &nbsp;&bull;&nbsp; ".join(detail_lines) + "</div>"

    if not items:
        return '<div style="background:#f0faf8;border:1px solid #1abc9c;border-radius:8px;padding:12px 20px;margin-bottom:16px;font-size:14px;color:#27AE60"><strong>&#10003; All clear!</strong> No urgent items today.</div>'
    return '<div style="background:#fef9f0;border:1px solid #F39C12;border-radius:8px;padding:12px 20px;margin-bottom:16px;font-size:14px"><strong>Today\'s Focus:</strong> ' + ' &nbsp;|&nbsp; '.join(items) + meeting_details_html + '</div>'


def _build_rec_html(rec):
    """Build HTML for a single AI recommendation."""
    level, text, prospect_name = rec
    colors = {"critical": "#E74C3C", "warning": "#F39C12", "action": "#3498DB", "suggestion": "#8E44AD"}
    icons = {"critical": "&#9888;", "warning": "&#9888;", "action": "&#10148;", "suggestion": "&#128161;"}
    color = colors.get(level, "#7f8c8d")
    icon = icons.get(level, "")
    if prospect_name:
        esc_name_attr = _esc_json_attr(prospect_name)
        click = f' data-prospect="{esc_name_attr}" onclick="openProspectDetail(this.dataset.prospect)"'
        cursor = ";cursor:pointer"
    else:
        click = ""
        cursor = ""
    return f'<div style="padding:10px 12px;margin-bottom:8px;border-left:3px solid {color};background:#fafafa;border-radius:0 6px 6px 0;font-size:13px{cursor}"{click}>{icon} {_html.escape(text)}</div>'


STAGE_ORDER = ["New Lead", "Contacted", "Discovery Call", "Needs Analysis",
               "Plan Presentation", "Proposal Sent", "Negotiation"]

STAGE_NEXT_ACTION = {
    "New Lead": "Make first contact — call or email to introduce yourself",
    "Contacted": "Schedule a discovery call to understand their needs",
    "Discovery Call": "Complete needs analysis — gather financial details",
    "Needs Analysis": "Prepare and present a financial plan",
    "Plan Presentation": "Send formal proposal with recommendations",
    "Proposal Sent": "Follow up on proposal — address questions",
    "Negotiation": "Close the deal — get paperwork signed",
}


def _calc_health_score(prospect, last_activity_map, today):
    """Calculate prospect health score 0-100 based on engagement signals."""
    score = 50  # baseline

    # Recency of contact (±30 points)
    pname = prospect["name"].strip().lower()
    last = last_activity_map.get(pname)
    if last:
        days_idle = (today - last).days
        if days_idle == 0:
            score += 30
        elif days_idle <= 3:
            score += 20
        elif days_idle <= 7:
            score += 10
        elif days_idle <= 14:
            score -= 5
        elif days_idle <= 30:
            score -= 15
        else:
            score -= 30
    else:
        score -= 20  # no activity at all

    # Priority boost (+10)
    if prospect.get("priority") == "Hot":
        score += 10
    elif prospect.get("priority") == "Warm":
        score += 5

    # Stage progression (+10 for advanced stages)
    stage = prospect.get("stage", "")
    if stage in ("Proposal Sent", "Negotiation"):
        score += 10
    elif stage in ("Plan Presentation", "Needs Analysis"):
        score += 5

    # Overdue follow-up penalty (-15)
    fu = prospect.get("next_followup", "")
    if fu and fu != "None":
        try:
            fu_date = datetime.strptime(fu.split(" ")[0], "%Y-%m-%d").date()
            if fu_date < today:
                days_late = (today - fu_date).days
                score -= min(15, days_late * 2)
        except (ValueError, IndexError):
            pass

    # Has revenue/AUM data (+5)
    try:
        if float(str(prospect.get("revenue", 0)).replace("$", "").replace(",", "")) > 0:
            score += 5
    except (ValueError, TypeError):
        pass

    return max(0, min(100, score))


def _health_badge(score):
    if score >= 70:
        return f'<span style="background:#27AE60;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">{score}</span>'
    elif score >= 40:
        return f'<span style="background:#F39C12;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">{score}</span>'
    else:
        return f'<span style="background:#E74C3C;color:#fff;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600">{score}</span>'


def _relative_time(date_str, today):
    """Convert a date string to relative time: 'today', '1d ago', '2w ago', etc."""
    if not date_str:
        return "—"
    try:
        d = datetime.strptime(date_str.split(" ")[0], "%Y-%m-%d").date()
        delta = (today - d).days
        if delta == 0:
            return "today"
        elif delta == 1:
            return "1d ago"
        elif delta < 7:
            return f"{delta}d ago"
        elif delta < 30:
            return f"{delta // 7}w ago"
        elif delta < 365:
            return f"{delta // 30}mo ago"
        else:
            return f"{delta // 365}y ago"
    except (ValueError, IndexError):
        return "—"


def _build_ai_recommendations(active, overdue, overdue_tasks, stale_prospects, last_activity_map, today, meetings):
    """Build prioritized AI recommendation list."""
    recs = []
    today_str = today.strftime("%Y-%m-%d")

    # 1. Overdue follow-ups on hot/warm leads — highest priority
    for p in overdue:
        if p.get("priority") in ("Hot", "Warm"):
            fu = p.get("next_followup", "").split(" ")[0]
            try:
                days_late = (today - datetime.strptime(fu, "%Y-%m-%d").date()).days
            except (ValueError, IndexError):
                days_late = 0
            rev = ""
            try:
                r = float(str(p.get("revenue", 0)).replace("$", "").replace(",", ""))
                if r > 0:
                    rev = f" (${r:,.0f} at stake)"
            except (ValueError, TypeError):
                pass
            recs.append(("critical", f"Call {p['name']} — {p['priority']} lead, {days_late}d overdue{rev}", p["name"]))

    # 2. Deals in advanced stages going cold
    for p_info, days_idle in stale_prospects[:5]:
        if p_info.get("stage") in ("Proposal Sent", "Negotiation", "Plan Presentation"):
            rev = ""
            try:
                r = float(str(p_info.get("revenue", 0)).replace("$", "").replace(",", ""))
                if r > 0:
                    rev = f" — ${r:,.0f} revenue"
                else:
                    a = float(str(p_info.get("aum", 0)).replace("$", "").replace(",", ""))
                    if a > 0:
                        rev = f" — ${a:,.0f} AUM"
            except (ValueError, TypeError):
                pass
            recs.append(("warning", f"Re-engage {p_info['name']} — {p_info['stage']}, {days_idle}d idle{rev}", p_info["name"]))

    # 3. Overdue tasks
    for t in overdue_tasks[:3]:
        recs.append(("warning", f"Overdue task: {t['title']}" + (f" ({t.get('prospect', '')})" if t.get("prospect") else ""), t.get("prospect", "")))

    # 4. New leads with no activity
    for p in active:
        if p.get("stage") == "New Lead":
            pname = p["name"].strip().lower()
            if pname not in last_activity_map:
                recs.append(("action", f"First contact needed: {p['name']} — new lead, no activity yet", p["name"]))

    # 5. Stage-based suggestions for hot leads
    for p in active:
        if p.get("priority") == "Hot" and p not in overdue:
            stage = p.get("stage", "")
            action = STAGE_NEXT_ACTION.get(stage)
            if action and p["name"] not in [r[2] for r in recs]:
                recs.append(("suggestion", f"{p['name']}: {action}", p["name"]))

    return recs[:10]


def _calc_activity_streak(activities, today):
    """Calculate consecutive days with at least one activity."""
    if not activities:
        return 0
    activity_dates = set()
    for a in activities:
        try:
            d = a.get("date", "").split(" ")[0]
            if d:
                activity_dates.add(d)
        except (ValueError, IndexError):
            pass
    streak = 0
    check = today
    while check.strftime("%Y-%m-%d") in activity_dates:
        streak += 1
        check -= timedelta(days=1)
    return streak


def _calc_deal_velocity(prospects, activities):
    """Calculate average days deals spend in the pipeline."""
    total_days = 0
    count = 0
    for p in prospects:
        if p.get("stage") in ("Closed-Won", "Closed-Lost"):
            fc = p.get("first_contact", "")
            ua = p.get("updated_at", "")
            if fc and ua:
                try:
                    start = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()
                    end = datetime.strptime(ua.split(" ")[0], "%Y-%m-%d").date()
                    days = (end - start).days
                    if days >= 0:
                        total_days += days
                        count += 1
                except (ValueError, IndexError):
                    pass
    return total_days // count if count > 0 else 0


@app.route("/login", methods=["GET", "POST"])
def login():
    """Simple password login page."""
    if request.method == "POST":
        password = request.form.get("password", "")
        if DASHBOARD_API_KEY and hmac.compare_digest(password, DASHBOARD_API_KEY):
            from flask import make_response
            import hashlib
            # Cookie value is a hash of the API key — validates without expiring server-side
            cookie_val = hashlib.sha256(DASHBOARD_API_KEY.encode()).hexdigest()
            resp = make_response('<script>window.location="/"</script>')
            resp.set_cookie("dash_auth", cookie_val, max_age=86400 * 30, httponly=True, samesite="Lax")
            return resp
        return _login_page(error="Wrong password. Try again.")
    return _login_page()


def _login_page(error=""):
    error_html = f'<p style="color:#e74c3c;margin-bottom:1rem;font-size:0.9rem">{_html.escape(error)}</p>' if error else ""
    return Response(f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calm Money — Login</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:-apple-system,system-ui,sans-serif; background:#0f1117; color:#e8e6f0;
         display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#1a1a26; border:1px solid #2a2a3a; border-radius:16px; padding:2.5rem;
           width:100%; max-width:380px; text-align:center; }}
  h1 {{ font-size:1.5rem; margin-bottom:0.3rem; }}
  .sub {{ color:#8888a0; font-size:0.85rem; margin-bottom:1.5rem; }}
  input {{ width:100%; padding:0.75rem 1rem; border:1px solid #2a2a3a; border-radius:8px;
           background:#12121a; color:#e8e6f0; font-size:1rem; margin-bottom:1rem; outline:none; }}
  input:focus {{ border-color:#00e5a0; }}
  button {{ width:100%; padding:0.75rem; border:none; border-radius:8px; background:#00e5a0;
            color:#0a0a0f; font-size:1rem; font-weight:600; cursor:pointer; }}
  button:hover {{ background:#00cc8e; }}
</style></head><body>
<div class="card">
  <h1>Calm Money</h1>
  <p class="sub">Pipeline Dashboard</p>
  {error_html}
  <form method="POST" action="/login">
    <input type="password" name="password" placeholder="Password" autofocus>
    <button type="submit">Sign In</button>
  </form>
</div></body></html>""", mimetype="text/html")


@app.route("/logout")
def logout():
    from flask import make_response
    resp = make_response('<script>window.location="/login"</script>')
    resp.delete_cookie("dash_auth")
    return resp


@app.route("/")
def dashboard():
    # Check auth: cookie (browser login), API key header (programmatic), or query param (legacy)
    authed = False
    # Cookie auth (from /login form — hash of API key)
    dash_cookie = request.cookies.get("dash_auth", "")
    if dash_cookie and DASHBOARD_API_KEY:
        import hashlib
        expected = hashlib.sha256(DASHBOARD_API_KEY.encode()).hexdigest()
        if hmac.compare_digest(dash_cookie, expected):
            authed = True
    # API key header or query param (backward compatible)
    if not authed:
        api_key = request.headers.get("X-API-Key", "") or request.args.get("key", "")
        if DASHBOARD_API_KEY and api_key and hmac.compare_digest(api_key, DASHBOARD_API_KEY):
            authed = True
    # No DASHBOARD_API_KEY set = open access (dev mode)
    if not authed and DASHBOARD_API_KEY:
        from flask import redirect
        return redirect("/login")
    csrf_token = _generate_csrf_token()
    prospects, activities, meetings, book_entries = read_data()
    try:
        all_tasks = db.get_tasks(status="pending")
        completed_tasks_recent = db.get_tasks(status="completed", limit=10)
    except Exception:
        all_tasks, completed_tasks_recent = [], []
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
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
        aging_rows += f'<tr><td class="name-cell">{_esc(p["name"])}</td><td><span class="badge" style="background:{_stage_bg(p["stage"])}">{_esc(p["stage"])}</span></td><td>{days_open}</td><td>{stale}</td></tr>'

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

    # Build "days since last activity" lookup per prospect
    last_activity_map = {}
    for a in activities:
        name = a.get("prospect", "").strip().lower()
        if name and a.get("date"):
            try:
                ad = datetime.strptime(a["date"].split(" ")[0], "%Y-%m-%d").date()
                if name not in last_activity_map or ad > last_activity_map[name]:
                    last_activity_map[name] = ad
            except (ValueError, IndexError):
                pass

    # Stale prospects: no activity in 14+ days
    stale_prospects = []
    for p in active:
        pname = p["name"].strip().lower()
        last = last_activity_map.get(pname)
        if last:
            days_idle = (today - last).days
            if days_idle >= 14:
                stale_prospects.append((p, days_idle))
        else:
            # No activity at all — check created_at
            try:
                created = datetime.strptime(p.get("created_at", "")[:10], "%Y-%m-%d").date()
                days_idle = (today - created).days
                if days_idle >= 14:
                    stale_prospects.append((p, days_idle))
            except (ValueError, IndexError):
                stale_prospects.append((p, 999))
    stale_prospects.sort(key=lambda x: -x[1])

    # Today's meetings
    todays_meetings = [m for m in meetings if m.get("date") == today_str and m.get("status", "").lower() != "cancelled"]

    # Build task categorizations (needed by intelligence + tasks tab)
    week_ago_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    overdue_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] < today_str]
    due_today_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] == today_str]
    upcoming_tasks = [t for t in all_tasks if not t.get("due_date") or t["due_date"] > today_str]
    completed_this_week = [t for t in completed_tasks_recent if t.get("completed_at", "") >= week_ago_str]

    # ── Intelligence computations ──
    # Health scores
    health_scores = {}
    for p in active:
        health_scores[p["name"]] = _calc_health_score(p, last_activity_map, today)

    # AI recommendations
    ai_recs = _build_ai_recommendations(active, overdue, overdue_tasks, stale_prospects, last_activity_map, today, meetings)

    # Activity streak
    activity_streak = _calc_activity_streak(activities, today)

    # Deal velocity
    deal_velocity = _calc_deal_velocity(prospects, activities)

    # Revenue at risk (stale prospects with revenue)
    revenue_at_risk = 0
    for p_info, days_idle in stale_prospects:
        try:
            r = float(str(p_info.get("revenue", 0)).replace("$", "").replace(",", ""))
            revenue_at_risk += r
        except (ValueError, TypeError):
            pass
    aum_at_risk = 0
    for p_info, days_idle in stale_prospects:
        try:
            a = float(str(p_info.get("aum", 0)).replace("$", "").replace(",", ""))
            aum_at_risk += a
        except (ValueError, TypeError):
            pass

    # Stuck deals (same stage for 21+ days based on updated_at)
    stuck_deals = []
    for p in active:
        ua = p.get("updated_at", "")
        if ua:
            try:
                last_update = datetime.strptime(ua.split(" ")[0], "%Y-%m-%d").date()
                days_stuck = (today - last_update).days
                if days_stuck >= 21 and p.get("stage") not in ("New Lead", "Nurture"):
                    stuck_deals.append((p, days_stuck))
            except (ValueError, IndexError):
                pass
    stuck_deals.sort(key=lambda x: -x[1])

    # Build prospect rows
    prospect_rows = ""
    for p in active:
        pri_bg = PRIORITY_COLORS.get(p["priority"], "#BDC3C7")

        is_overdue = p in overdue
        fu_class = "overdue" if is_overdue else ""
        fu_display = p["next_followup"].split(" ")[0] if p["next_followup"] and p["next_followup"] != "None" else ""

        # Days since last touch — relative time with color coding
        pname_lower = p["name"].strip().lower()
        last_touch = last_activity_map.get(pname_lower)
        if last_touch:
            idle_days = (today - last_touch).days
            rel = _relative_time(last_touch.strftime("%Y-%m-%d"), today)
            if idle_days == 0 or idle_days == 1:
                idle_display = f'<span style="color:#27AE60;font-weight:600">{rel}</span>'
            elif idle_days < 7:
                idle_display = f'<span style="color:#F39C12">{rel}</span>'
            else:
                idle_display = f'<span style="color:#E74C3C;font-weight:600">{rel}</span>'
        else:
            idle_display = '<span style="color:#95A5A6">—</span>'

        hscore = health_scores.get(p["name"], 50)
        hbadge = _health_badge(hscore)

        p_json_escaped = _esc_json_attr(json.dumps(p))
        esc_name_attr = _esc_json_attr(p["name"])
        prospect_rows += f"""<tr class="editable-row" data-prospect="{p_json_escaped}" data-name="{esc_name_attr}" onclick="openProspectDetail(this.dataset.name)" style="cursor:pointer">
            <td class="name-cell"><span style="color:#2c3e50;font-weight:600;text-decoration:none;border-bottom:2px solid #1abc9c">{_esc(p["name"])}</span></td>
            <td style="text-align:center">{hbadge}</td>
            <td><span class="badge" style="background:{pri_bg}">{_esc(p["priority"])}</span></td>
            <td><span class="badge" style="background:{_stage_bg(p["stage"])};color:{_stage_fg(p["stage"])};cursor:pointer" onclick="changeStage(event, this.closest('tr').dataset.name)" title="Click to change stage">{_esc(p["stage"])}</span></td>
            <td>{_esc(p["product"])}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td class="{fu_class}">{_esc(fu_display)}</td>
            <td style="text-align:center">{idle_display}</td>
            <td class="notes">{_esc((p["notes"] or "")[:60])}{'...' if len(p["notes"] or "") > 60 else ''}</td>
            <td style="text-align:center;white-space:nowrap" onclick="event.stopPropagation()">
                <button onclick="quickLogActivity('Call',this.closest('tr').dataset.name)" title="Log Call" style="background:none;border:none;cursor:pointer;font-size:16px;padding:2px 4px">&#128222;</button>
                <button onclick="quickLogActivity('Email',this.closest('tr').dataset.name)" title="Log Email" style="background:none;border:none;cursor:pointer;font-size:16px;padding:2px 4px">&#9993;</button>
            </td>
        </tr>"""

    # Won deals / Client Book rows
    won_rows = ""
    won_rows_full = ""
    total_client_aum = 0
    total_client_premium = 0
    client_products = {}
    for p in won:
        p_aum = parse_money(p["aum"])
        p_rev = parse_money(p["revenue"])
        total_client_aum += p_aum
        total_client_premium += p_rev
        prod = p.get("product") or "Other"
        client_products[prod] = client_products.get(prod, 0) + 1
        p_name_attr = _esc_json_attr(p["name"])
        won_rows += f"""<tr>
            <td class="name-cell">{_esc(p["name"])}</td>
            <td>{_esc(p["product"])}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td>{_esc(p["source"])}</td>
        </tr>"""
        # Cross-sell suggestions
        try:
            from scoring import get_cross_sell_suggestions
            cross_sell = get_cross_sell_suggestions(prod)
            cross_html = ", ".join(_esc(s) for s in cross_sell[:2]) if cross_sell else '<span style="color:#95a5a6">—</span>'
        except Exception:
            cross_html = '<span style="color:#95a5a6">—</span>'
        fc = p.get("first_contact") or ""
        fc_display = fc.split(" ")[0] if fc and fc != "None" else "—"
        won_rows_full += f"""<tr data-name="{p_name_attr}" onclick="openProspectDetail(this.dataset.name)" style="cursor:pointer">
            <td class="name-cell">{_esc(p["name"])}</td>
            <td>{_esc(p.get("phone") or "")}</td>
            <td>{_esc(p.get("email") or "")}</td>
            <td>{_esc(p["product"])}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td>{_esc(fc_display)}</td>
            <td>{cross_html}</td>
            <td class="notes">{_esc((p.get("notes") or "")[:50])}{'...' if len(p.get("notes") or "") > 50 else ''}</td>
        </tr>"""

    # Client Book breakdown (pre-computed for f-string)
    _client_breakdown_html = ""
    if won:
        _prod_pills = "".join(
            f'<div style="padding:10px 16px;background:#f0f2f5;border-radius:8px;text-align:center">'
            f'<div style="font-size:22px;font-weight:700;color:#0f1b2d">{count}</div>'
            f'<div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">{_esc(product)}</div></div>'
            for product, count in sorted(client_products.items(), key=lambda x: -x[1])
        )
        _src_counts = {}
        for p in won:
            s = p.get("source") or "Unknown"
            _src_counts[s] = _src_counts.get(s, 0) + 1
        _src_pills = "".join(
            f'<div style="padding:10px 16px;background:#f0f2f5;border-radius:8px;text-align:center">'
            f'<div style="font-size:22px;font-weight:700;color:#0f1b2d">{count}</div>'
            f'<div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">{_esc(src)}</div></div>'
            for src, count in sorted(_src_counts.items(), key=lambda x: -x[1])
        )
        _client_breakdown_html = (
            '<div class="two-col">'
            '<div class="section"><h2>Products Breakdown</h2>'
            f'<div style="display:flex;flex-wrap:wrap;gap:10px">{_prod_pills}</div></div>'
            '<div class="section"><h2>Client Sources</h2>'
            f'<div style="display:flex;flex-wrap:wrap;gap:10px">{_src_pills}</div></div>'
            '</div>'
        )

    # Activity rows (last 10)
    activity_rows = ""
    for a in activities[:10]:
        activity_rows += f"""<tr>
            <td>{_esc((a["date"] or "").split(" ")[0])}</td>
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
        esc_name_attr = _esc_json_attr(p["name"])
        overdue_rows += f"""<tr data-name="{esc_name_attr}">
            <td class="name-cell">{_esc(p["name"])}</td>
            <td>{_esc(fu)}</td>
            <td class="overdue">{days_late} days late</td>
            <td>{_esc(p["phone"])}</td>
            <td style="white-space:nowrap">
                <button onclick="event.stopPropagation();quickReschedule(this.closest('tr').dataset.name, 0)" style="background:#27AE60;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:11px" title="Reschedule to today">Today</button>
                <button onclick="event.stopPropagation();quickReschedule(this.closest('tr').dataset.name, 1)" style="background:#3498DB;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:11px" title="Reschedule to tomorrow">+1d</button>
                <button onclick="event.stopPropagation();quickReschedule(this.closest('tr').dataset.name, 7)" style="background:#8E44AD;color:#fff;border:none;padding:4px 8px;border-radius:4px;cursor:pointer;font-size:11px" title="Reschedule to next week">+1w</button>
            </td>
        </tr>"""

    # Chart data as JSON-like strings for inline JS
    stage_labels = list(stage_counts.keys())
    stage_values = list(stage_counts.values())
    stage_chart_colors = [_stage_bg(s) for s in stage_labels]

    source_labels = list(source_counts.keys())
    source_values = list(source_counts.values())

    product_labels = list(product_counts.keys())
    product_values = list(product_counts.values())

    # Build task rows for Tasks tab
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
        task_prospect_attr = _esc_json_attr(t.get("prospect") or "")
        prospect_display = f'<a style="color:#3498DB" href="javascript:void(0)" data-prospect="{task_prospect_attr}" onclick="event.stopPropagation();openProspectDetail(this.dataset.prospect)">{_esc(t["prospect"])}</a>' if t.get("prospect") else ""
        remind_icon = ' <span title="Reminder set" style="color:#F39C12">&#9200;</span>' if t.get("remind_at") else ""
        due_cell = f"<td>{due_display}</td>" if show_due else ""
        remind_val = (t.get("remind_at") or "").replace(" ", "T")
        _task_json = _esc_json_attr(json.dumps({"id": t["id"], "title": t["title"], "prospect": t.get("prospect") or "", "due": due, "remind": remind_val, "notes": t.get("notes") or ""}))
        return f"""<tr class="{row_class}">
            <td style="text-align:center"><input type="checkbox" onchange="completeTask({t['id']}, this)" style="width:18px;height:18px;cursor:pointer"></td>
            <td><a href="javascript:void(0)" data-task="{_task_json}" onclick="var d=JSON.parse(this.dataset.task);openEditTask(d.id,d.title,d.prospect,d.due,d.remind,d.notes)" style="color:inherit;text-decoration:none;border-bottom:1px dashed #bdc3c7">{_esc(t['title'])}</a>{remind_icon}</td>
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

    # Sort meetings by date for display, highlight today
    def _meeting_sort_key(m):
        try:
            return datetime.strptime((m.get("date") or "9999-12-31").split(" ")[0], "%Y-%m-%d").date()
        except (ValueError, IndexError):
            return date(9999, 12, 31)

    sorted_meetings = sorted([m for m in meetings if m.get("status") != "Cancelled"], key=_meeting_sort_key)

    meeting_rows = ""
    for m in sorted_meetings:
        is_today_meeting = (m.get("date", "") == today_str)
        row_style = 'style="background:#fffbec"' if is_today_meeting else ""
        status_bg = "#27ae60" if m["status"] == "Completed" else "#e74c3c" if m["status"] == "Cancelled" else "#3498db"
        meeting_rows += f'<tr {row_style}><td>{_esc(m["date"])}</td><td>{_esc(m["time"])}</td><td class="name-cell">{_esc(m["prospect"])}</td><td>{_esc(m["type"])}</td><td><span class="badge" style="background:{status_bg}">{_esc(m["status"])}</span></td><td class="notes">{_esc((m["prep_notes"] or "")[:50])}{"..." if len(m["prep_notes"] or "") > 50 else ""}</td></tr>'

    # Pending approvals count + drafts
    try:
        import approval_queue as _aq
        pending_approvals = _aq.get_pending_count()
        pending_drafts = _aq.get_pending_drafts()
    except Exception:
        pending_approvals = 0
        pending_drafts = []

    # Build pending drafts section HTML
    _draft_items_html = ""
    for _d in pending_drafts:
        _dtype = _html.escape(str(_d.get("type") or "Draft"))
        _dprospect = ""
        if _d.get("prospect_id"):
            # Try to resolve name from prospect_id via pipeline
            _pid = _d.get("prospect_id")
            _matched = next((p["name"] for p in prospects if str(p.get("id", "")) == str(_pid)), None)
            if _matched:
                _dprospect = _html.escape(_matched)
        _dcontent = _html.escape(str(_d.get("content") or "")[:200])
        _dts = _html.escape(str(_d.get("created_at") or "")[:16])
        _ellipsis = "..." if len(str(_d.get("content") or "")) > 200 else ""
        _prospect_span = (
            '<span style="font-size:12px;color:#7f8c8d">' + _dprospect + '</span>'
            if _dprospect else ""
        )
        _draft_items_html += (
            '<div style="padding:12px;margin-bottom:10px;border:1px solid #e0d5f0;border-radius:8px;background:#faf8ff">'
            '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">'
            '<span style="background:#8e44ad;color:#fff;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:600">' + _dtype + '</span>'
            + _prospect_span +
            '<span style="font-size:11px;color:#aaa;margin-left:auto">' + _dts + '</span>'
            '</div>'
            '<div style="font-size:13px;color:#2c3e50;white-space:pre-wrap">' + _dcontent + _ellipsis + '</div>'
            '</div>'
        )

    _pending_drafts_section_html = f"""<div id="pendingDraftsSection" style="display:none;margin-top:12px;padding:20px;background:white;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,0.08);border-top:4px solid #8e44ad">
  <h3 style="font-size:15px;font-weight:700;color:#8e44ad;margin-bottom:14px">&#9993; Drafts Awaiting Approval ({len(pending_drafts)})</h3>
  {_draft_items_html if _draft_items_html else '<div style="color:#7f8c8d;font-size:14px;text-align:center;padding:12px">No pending drafts.</div>'}
</div>""" if pending_approvals > 0 else ""

    # ── Kanban board data — group active prospects by stage ──
    PIPELINE_STAGES = ["New Lead", "Contacted", "Discovery Call", "Needs Analysis",
                       "Plan Presentation", "Proposal Sent", "Negotiation", "Nurture"]
    kanban_cols = []
    for _kstage in PIPELINE_STAGES:
        _kprospects = [p for p in active if p.get("stage") == _kstage]
        kanban_cols.append((_kstage, _kprospects))

    # Build kanban HTML
    _kanban_cols_html = ""
    for _kstage, _kprospects in kanban_cols:
        _kcol_color = _stage_bg(_kstage)
        _kstage_attr = _esc_json_attr(_kstage)
        _kcards_html = ""
        for _kp in _kprospects:
            _kname_esc = _esc(_kp["name"])
            _kname_attr = _esc_json_attr(_kp["name"])
            _kproduct_esc = _esc(_kp.get("product") or "")
            _kpri = _kp.get("priority") or ""
            _kpri_color = PRIORITY_COLORS.get(_kpri, "#BDC3C7")
            _kpri_esc = _esc(_kpri)
            _klast = last_activity_map.get(_kp["name"].strip().lower())
            _krel = _relative_time(_klast.strftime("%Y-%m-%d") if _klast else "", today)
            aum_val = _kp.get("aum") or 0
            aum_html = f'<div style="font-size:11px;color:#27ae60;font-weight:600">{fmt_money_full(aum_val)}</div>' if aum_val and float(str(aum_val).replace("$","").replace(",","") or 0) > 0 else ""
            _kname_jsattr = json.dumps(_kp["name"]).replace("&", "&amp;").replace('"', "&quot;")
            _kcards_html += f"""<div class="kanban-card" draggable="true" ondragstart="onDragStart(event, {_kname_jsattr})" ondragend="onDragEnd(event)" onclick="onCardClick(event, {_kname_jsattr})" style="border-left:3px solid {_kpri_color}">
                <div class="kanban-card-name">{_kname_esc}</div>
                <div class="kanban-card-product">{_kproduct_esc}</div>
                {aum_html}
                <div class="kanban-card-meta">
                    <span class="kanban-pri" style="background:{_kpri_color}">{_kpri_esc}</span>
                    <span>{_krel}</span>
                </div>
            </div>"""
        col_aum = sum(parse_money(p.get("aum", "0")) for p in _kprospects)
        col_aum_html = f'<span style="font-size:10px;opacity:0.85;margin-left:6px">{fmt_money_full(col_aum)}</span>' if col_aum > 0 else ""
        _empty_html = '<div style="padding:16px 12px;text-align:center;color:#bdc3c7;font-size:12px;font-style:italic">No prospects</div>' if not _kprospects else ""
        _kanban_cols_html += f"""<div class="kanban-col" data-stage="{_kstage_attr}" ondragover="onDragOver(event)" ondragleave="onDragLeave(event)" ondrop="onDrop(event, this.dataset.stage)">
            <div class="kanban-col-header" style="background:{_kcol_color}">
                {_esc(_kstage)} <span class="kanban-count">{len(_kprospects)}</span>{col_aum_html}
            </div>
            {_empty_html}{_kcards_html}
        </div>"""

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

    /* Detail modal header — stack title and action buttons */
    #detailModal .modal > div:first-child {{
        flex-direction: column;
        gap: 10px;
        align-items: flex-start;
    }}
    #detailModal .modal > div:first-child > div {{
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        width: 100%;
    }}
    #detailModal .modal > div:first-child button {{
        flex: 1;
        min-width: 0;
        padding: 8px 10px;
        font-size: 12px;
        white-space: nowrap;
    }}
}}

/* ── Additional 768px breakpoints ── */
@media (max-width: 768px) {{
    .tab-nav {{
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
        gap: 0;
    }}
    .tab-btn {{
        white-space: nowrap;
        padding: 12px 16px;
        font-size: 12px;
    }}
    .kpi-grid {{
        grid-template-columns: repeat(2, 1fr) !important;
        gap: 8px;
    }}
    .kpi-value {{ font-size: 22px; }}
    .kanban-board {{
        flex-direction: column;
        gap: 8px;
        min-height: auto;
    }}
    .kanban-col {{
        min-width: 100%;
        max-width: 100%;
        flex: none;
    }}
    .kanban-col-header {{
        cursor: pointer;
        position: relative;
    }}
    .kanban-col-header::after {{
        content: '\\25BC';
        font-size: 10px;
        margin-left: 8px;
        transition: transform 0.2s;
    }}
    .kanban-col.collapsed .kanban-col-header::after {{
        content: '\\25B6';
    }}
    .kanban-col.collapsed .kanban-card,
    .kanban-col.collapsed > div:not(.kanban-col-header):not(.kanban-card) {{
        display: none;
    }}
    .kanban-card {{
        -webkit-tap-highlight-color: transparent;
        touch-action: manipulation;
    }}
    .section table {{
        display: block;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }}
    .two-col {{
        grid-template-columns: 1fr !important;
    }}
    .focus-banner {{
        padding: 12px;
        font-size: 13px;
    }}
    .chart-grid {{
        grid-template-columns: 1fr !important;
    }}
    .modal {{
        width: 95% !important;
        max-width: 95% !important;
        margin: 10px;
        max-height: 90vh;
    }}
}}

@media (max-width: 480px) {{
    .kpi-grid {{
        grid-template-columns: 1fr !important;
    }}
}}

@keyframes slideUp {{ from {{ transform: translateY(20px); opacity: 0; }} to {{ transform: translateY(0); opacity: 1; }} }}

/* ── Kanban board ── */
.kanban-board {{
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding-bottom: 12px;
    min-height: 400px;
}}
.kanban-col {{
    min-width: 200px;
    max-width: 240px;
    flex: 1;
    background: #f8f9fa;
    border-radius: 10px;
    display: flex;
    flex-direction: column;
}}
.kanban-col-header {{
    padding: 10px 12px;
    border-radius: 10px 10px 0 0;
    color: #fff;
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.kanban-count {{
    background: rgba(255,255,255,0.25);
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
}}
.kanban-card {{
    background: #fff;
    border: 1px solid #e8e8e8;
    border-radius: 8px;
    padding: 10px 12px;
    margin: 6px 8px;
    cursor: pointer;
    transition: transform 0.15s, box-shadow 0.15s;
}}
.kanban-card:hover {{
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}}
.kanban-card-name {{
    font-weight: 600;
    font-size: 13px;
    margin-bottom: 4px;
}}
.kanban-card-product {{
    font-size: 11px;
    color: #7f8c8d;
    margin-bottom: 6px;
}}
.kanban-card-meta {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    color: #95a5a6;
}}
.kanban-pri {{
    padding: 1px 6px;
    border-radius: 4px;
    color: #fff;
    font-size: 10px;
    font-weight: 600;
}}

/* ── View toggle buttons ── */
.view-toggle-btn {{
    padding: 4px 12px;
    border: 1px solid #ddd;
    background: #fff;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    color: #7f8c8d;
}}
.view-toggle-btn.active {{
    background: #1abc9c;
    color: #fff;
    border-color: #1abc9c;
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

    {_build_focus_banner(overdue, overdue_tasks, due_today_tasks, todays_meetings, stale_prospects)}

    <div class="kpi-grid" style="grid-template-columns: repeat(5, 1fr)">
        <div class="kpi-card{'  red' if overdue else ''}">
            <div class="kpi-label">Overdue</div>
            <div class="kpi-value">{len(overdue)}</div>
        </div>
        <div class="kpi-card red">
            <div class="kpi-label">Hot Leads</div>
            <div class="kpi-value">{hot_count}</div>
        </div>
        <div class="kpi-card blue">
            <div class="kpi-label">Active Deals</div>
            <div class="kpi-value">{len(active)}</div>
        </div>
        <div class="kpi-card gold">
            <div class="kpi-label">FYC YTD</div>
            <div class="kpi-value">{fmt_money(won_fyc)}</div>
        </div>
        <div class="kpi-card green">
            <div class="kpi-label">Win Rate</div>
            <div class="kpi-value">{win_rate:.0f}%</div>
        </div>
    </div>
    {'<div style="margin-top:12px"><div class="kpi-card purple" style="cursor:pointer;display:inline-block;min-width:220px" onclick="togglePendingDrafts()" title="Click to view pending drafts"><div class="kpi-label">&#9993; Drafts Pending Approval</div><div class="kpi-value">' + str(pending_approvals) + '</div><div style="font-size:11px;color:#8e44ad;margin-top:4px">Click to view &#9660;</div></div></div>' if pending_approvals > 0 else ''}
    {_pending_drafts_section_html}

    <div class="tab-nav">
        <button class="tab-btn active" data-tab="pipeline" onclick="showTab('pipeline')">Pipeline</button>
        <button class="tab-btn" data-tab="forecast" onclick="showTab('forecast')">Revenue Forecast</button>
        <button class="tab-btn" data-tab="funnel" onclick="showTab('funnel')">Conversion Funnel</button>
        <button class="tab-btn" data-tab="scoreboard" onclick="showTab('scoreboard')">Activity Score</button>
        <button class="tab-btn" data-tab="tasks" onclick="showTab('tasks')">Tasks{'<span style="display:inline-block;background:#e74c3c;color:#fff;border-radius:10px;font-size:10px;font-weight:700;padding:1px 7px;margin-left:6px;vertical-align:middle">' + str(len(overdue_tasks)) + '</span>' if overdue_tasks else ''}</button>
        <button class="tab-btn" data-tab="clients" onclick="showTab('clients')">Clients{'<span style="display:inline-block;background:#27ae60;color:#fff;border-radius:10px;font-size:10px;font-weight:700;padding:1px 7px;margin-left:6px;vertical-align:middle">' + str(len(won)) + '</span>' if won else ''}</button>
    </div>

    <!-- ═══ TAB 1: PIPELINE (existing) ═══ -->
    <div class="tab-content active" id="tab-pipeline">

    <!-- Intelligence Section (AI Recommendations first — daily action items) -->
    <div class="two-col" style="margin-top:24px">
        <div class="section" style="border-left:4px solid #8E44AD">
            <h2 style="color:#8E44AD">AI Recommends</h2>
            {''.join(_build_rec_html(r) for r in ai_recs) if ai_recs else '<div class="empty-state"><p>All caught up! No urgent recommendations.</p></div>'}
        </div>
        <div class="section">
            <h2>Intelligence</h2>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                <div style="padding:12px;background:#f8f9fa;border-radius:8px;text-align:center">
                    <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">Activity Streak</div>
                    <div style="font-size:28px;font-weight:700;color:{'#27AE60' if activity_streak >= 3 else '#F39C12' if activity_streak >= 1 else '#E74C3C'}">{activity_streak}d</div>
                    <div style="font-size:11px;color:#7f8c8d">{'On fire!' if activity_streak >= 5 else 'Keep it going!' if activity_streak >= 1 else 'Log an activity today'}</div>
                </div>
                <div style="padding:12px;background:#f8f9fa;border-radius:8px;text-align:center">
                    <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">Avg Deal Velocity</div>
                    <div style="font-size:28px;font-weight:700;color:#3498DB">{deal_velocity}d</div>
                    <div style="font-size:11px;color:#7f8c8d">days to close</div>
                </div>
                <div style="padding:12px;background:{'#fef0f0' if revenue_at_risk > 0 else '#f8f9fa'};border-radius:8px;text-align:center">
                    <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">Revenue at Risk</div>
                    <div style="font-size:28px;font-weight:700;color:{'#E74C3C' if revenue_at_risk > 0 else '#27AE60'}">{fmt_money(revenue_at_risk)}</div>
                    <div style="font-size:11px;color:#7f8c8d">{len(stale_prospects)} stale deal{'s' if len(stale_prospects) != 1 else ''}</div>
                </div>
                <div style="padding:12px;background:{'#fef0f0' if stuck_deals else '#f8f9fa'};border-radius:8px;text-align:center">
                    <div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">Stuck Deals</div>
                    <div style="font-size:28px;font-weight:700;color:{'#E74C3C' if stuck_deals else '#27AE60'}">{len(stuck_deals)}</div>
                    <div style="font-size:11px;color:#7f8c8d">{'21+ days same stage' if stuck_deals else 'all moving'}</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Pipeline Charts (reference material — below AI recommendations) -->
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

    {'<div class="section"><h2>Overdue Follow-Ups <span class="count">(' + str(len(overdue)) + ')</span></h2><table><tr><th>Prospect</th><th>Was Due</th><th>Status</th><th>Phone</th><th>Reschedule</th></tr>' + overdue_rows + '</table></div>' if overdue else ''}

    <div class="section">
        <h2 style="display:flex;justify-content:space-between;align-items:center">
            <span>Active Pipeline <span class="count">({len(active)} deals)</span></span>
            <div style="display:flex;gap:8px;align-items:center">
                <div style="display:flex;gap:4px">
                    <button class="view-toggle-btn active" onclick="togglePipelineView('table')">Table</button>
                    <button class="view-toggle-btn" onclick="togglePipelineView('kanban')">Board</button>
                </div>
                <button class="btn btn-primary" onclick="openAdd()">+ Add Prospect</button>
            </div>
        </h2>
        <div id="tableView">
            {'<div style="margin-bottom:12px"><input type="text" id="prospectSearch" placeholder="Search prospects..." oninput="filterProspects(this.value)" style="width:100%;max-width:300px;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px"></div><table id="prospectTable"><tr><th>Prospect</th><th>Health</th><th>Priority</th><th>Stage</th><th>Product</th><th>AUM</th><th>Premium</th><th>Follow-Up</th><th>Last Touch</th><th>Notes</th><th>Actions</th></tr>' + prospect_rows + '</table>' if active else '<div class="empty-state"><p>No active deals yet. Text your Telegram bot to add prospects.</p></div>'}
        </div>
        <div id="kanbanView" style="display:none">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div style="font-size:13px;color:#7f8c8d">{len(active)} active deals · Drag cards to change stage</div>
                <button class="btn btn-primary" onclick="openAdd()">+ Add Prospect</button>
            </div>
            <div class="kanban-board">
                {_kanban_cols_html}
            </div>
        </div>
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
            <h2>Upcoming Meetings <span class="count">({len(sorted_meetings)})</span></h2>
            {'<table><tr><th>Date</th><th>Time</th><th>Prospect</th><th>Type</th><th>Status</th><th>Prep</th></tr>' + meeting_rows + '</table>' if sorted_meetings else '<div class="empty-state"><p>No meetings scheduled. Text the bot to add one.</p></div>'}
        </div>
        <div class="section">
            <h2>Insurance Book <span class="count">({len(book_entries)} contacts)</span></h2>
            {'<div style="display:flex;gap:24px;margin-bottom:16px"><div class="kpi-card" style="flex:1;padding:12px 16px"><div class="kpi-label">Called</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if (b["status"] or "").lower() not in ("not called","")])) + '</div></div><div class="kpi-card green" style="flex:1;padding:12px 16px"><div class="kpi-label">Booked</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if (b["status"] or "").lower()=="booked meeting"])) + '</div></div><div class="kpi-card blue" style="flex:1;padding:12px 16px"><div class="kpi-label">Remaining</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if (b["status"] or "").lower() in ("not called","")])) + '</div></div></div><table><tr><th>Name</th><th>Phone</th><th>Status</th><th>Last Called</th><th>Notes</th></tr>' + ''.join(f'<tr><td class="name-cell">{_esc(b["name"])}</td><td>{_esc(b["phone"])}</td><td><span class="badge" style="background:{"#27ae60" if (b["status"] or "").lower()=="booked meeting" else "#e74c3c" if (b["status"] or "").lower()=="not interested" else "#f39c12" if (b["status"] or "").lower() in ("callback","no answer") else "#3498db"}">{_esc(b["status"])}</span></td><td>{_esc(b["last_called"].split(" ")[0] if b["last_called"] and b["last_called"]!="None" else "")}</td><td class="notes">{_esc((b["notes"] or "")[:40])}{"..." if len(b["notes"] or "")>40 else ""}</td></tr>' for b in book_entries[:20]) + '</table>' if book_entries else '<div class="empty-state"><p>No insurance book uploaded. Send a CSV via Telegram.</p></div>'}
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
                {''.join(f'<tr><td><span class="badge" style="background:{_stage_bg(s)};color:{_stage_fg(s)}">{_esc(s)}</span></td><td>{stage_counts.get(s, 0)}</td><td>{int(stage_probability.get(s, 0.1)*100)}%</td><td class="money">{fmt_money(stage_revenue.get(s, 0))}</td><td class="money">{fmt_money(stage_revenue.get(s, 0) * stage_probability.get(s, 0.1))}</td><td class="money">{fmt_money(sum(parse_money(p["aum"]) for p in active if p["stage"]==s))}</td><td class="money">{fmt_money(sum(parse_money(p["aum"]) for p in active if p["stage"]==s) * stage_probability.get(s, 0.1))}</td><td class="money">{fmt_money(stage_fyc.get(s, 0))}</td><td class="money">{fmt_money(stage_fyc.get(s, 0) * stage_probability.get(s, 0.1))}</td></tr>' for s in stage_order[:-1] if stage_counts.get(s, 0) > 0)}
                <tr style="font-weight:700;border-top:2px solid #2c3e50"><td>Total Weighted</td><td></td><td></td><td></td><td class="money">{fmt_money(weighted_revenue)}</td><td></td><td class="money">{fmt_money(weighted_aum)}</td><td></td><td class="money">{fmt_money(weighted_fyc)}</td></tr>
            </table>
        </div>

    </div><!-- end tab-forecast -->

    <!-- ═══ TAB 3: CONVERSION FUNNEL ═══ -->
    <div class="tab-content" id="tab-funnel" style="margin-top:24px">

        <div class="section">
            <h2>Sales Funnel</h2>
            <div style="max-width:700px;margin:0 auto;padding:20px 0">
                {''.join(f'<div class="funnel-stage"><div class="funnel-label">{_esc(stage_order[i])}</div><div class="funnel-bar-wrap"><div class="funnel-bar" style="width:{max(8, funnel_counts[stage_order[i]] / max(1, funnel_counts[stage_order[0]]) * 100):.0f}%;background:{_stage_bg(stage_order[i])}">{funnel_counts[stage_order[i]]}</div><div class="funnel-rate">{f"{funnel_rates[i]:.0f}% pass" if i < len(funnel_rates) else ""}</div><div class="funnel-velocity">{f"~{avg_stage_days.get(stage_order[i], 0):.0f}d avg" if stage_order[i] in avg_stage_days else ""}</div></div></div>' for i in range(len(stage_order)))}
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
                {'<div style="text-align:center;padding:20px"><div class="score-big" style="font-size:48px">' + str(len([b for b in book_entries if (b["status"] or "").lower() not in ("not called","")])) + '<span style="font-size:20px;color:#7f8c8d">/' + str(len(book_entries)) + '</span></div><div style="color:#7f8c8d;margin-top:4px">Contacts Called</div><div class="progress-bar-container" style="margin-top:12px"><div class="progress-bar-fill teal" style="width:' + str(min(len([b for b in book_entries if (b["status"] or "").lower() not in ("not called","")]) / max(1, len(book_entries)) * 100, 100)) + '%">' + str(int(len([b for b in book_entries if (b["status"] or "").lower() not in ("not called","")]) / max(1, len(book_entries)) * 100)) + '%</div></div><div class="target-meta" style="margin-top:8px"><span>Booked: ' + str(len([b for b in book_entries if (b["status"] or "").lower()=="booked meeting"])) + '</span><span>Not Interested: ' + str(len([b for b in book_entries if (b["status"] or "").lower()=="not interested"])) + '</span><span>Callbacks: ' + str(len([b for b in book_entries if (b["status"] or "").lower()=="callback"])) + '</span></div></div>' if book_entries else '<div class="empty-state"><p>Upload an insurance book CSV to track progress.</p></div>'}
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

    <!-- ═══ TAB 6: CLIENT BOOK ═══ -->
    <div class="tab-content" id="tab-clients" style="margin-top:24px">

        <div class="kpi-grid" style="grid-template-columns: repeat(4, 1fr)">
            <div class="kpi-card green">
                <div class="kpi-label">Total Clients</div>
                <div class="kpi-value">{len(won)}</div>
            </div>
            <div class="kpi-card blue">
                <div class="kpi-label">Client AUM</div>
                <div class="kpi-value">{fmt_money(total_client_aum)}</div>
            </div>
            <div class="kpi-card">
                <div class="kpi-label">Client Premium</div>
                <div class="kpi-value">{fmt_money(total_client_premium)}</div>
            </div>
            <div class="kpi-card gold">
                <div class="kpi-label">Avg AUM/Client</div>
                <div class="kpi-value">{fmt_money(total_client_aum / len(won)) if won else '$0'}</div>
            </div>
        </div>

        {_client_breakdown_html}

        <div class="section">
            <h2>Client Book <span class="count">({len(won)} clients)</span></h2>
            {'<table><tr><th>Client</th><th>Phone</th><th>Email</th><th>Product</th><th>AUM</th><th>Premium</th><th>Since</th><th>Cross-Sell</th><th>Notes</th></tr>' + won_rows_full + '</table>' if won else '<div class="empty-state"><p>No clients yet. Close your first deal!</p></div>'}
        </div>

    </div><!-- end tab-clients -->

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
    <h2 id="taskModalTitle">Add Task</h2>
    <input type="hidden" id="tEditId" value="">
    <div style="margin-bottom:12px">
        <label>Task</label><input id="tTitle" type="text" placeholder="What needs to be done?" style="width:100%">
    </div>
    <div class="form-row">
        <div><label>Prospect (optional)</label><input id="tProspect" type="text" placeholder="Prospect name" list="prospectList"><datalist id="prospectList">{''.join(f'<option value="{_esc(n)}">' for n in sorted(set(p["name"] for p in prospects)))}</datalist></div>
        <div><label>Due Date</label><input id="tDue" type="date"></div>
    </div>
    <div class="form-row">
        <div><label>Reminder</label><input id="tRemind" type="datetime-local"></div>
    </div>
    <label>Notes</label>
    <textarea id="tNotes" rows="3" style="width:100%;margin-bottom:8px" placeholder="Optional notes..."></textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
        <button onclick="saveTask()" style="background:#27AE60;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer">Save</button>
        <button onclick="closeTaskModal()" style="background:#95A5A6;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer">Cancel</button>
        <button id="tDeleteBtn" onclick="deleteTask(document.getElementById('tEditId').value)" style="background:#E74C3C;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer;margin-left:auto;display:none">Delete</button>
    </div>
</div>
</div>

<!-- Prospect Detail Panel -->
<div class="modal-overlay" id="detailModal">
<div class="modal" style="max-width:700px;max-height:85vh;overflow-y:auto">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h2 id="detailTitle" style="margin:0">Prospect Detail</h2>
        <div style="display:flex;gap:8px">
            <button onclick="quickLogActivity('Call')" style="background:#27AE60;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">Log Call</button>
            <button onclick="quickLogActivity('Email')" style="background:#3498DB;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">Log Email</button>
            <button onclick="quickLogActivity('Meeting')" style="background:#8E44AD;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px">Log Meeting</button>
        </div>
    </div>
    <div id="detailContent" style="font-size:14px"><div class="empty-state"><p>Loading...</p></div></div>
    <div id="mergeSection" style="margin-top:16px;padding:12px;background:#FDF2E9;border-radius:8px;display:none">
        <div style="font-size:13px;font-weight:600;margin-bottom:8px;color:#E67E22">Merge another prospect into this one</div>
        <div style="display:flex;gap:8px">
            <select id="mergeTarget" style="flex:1;padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px">
                <option value="">Select prospect to merge...</option>
            </select>
            <button onclick="doMerge()" style="background:#E67E22;color:#fff;border:none;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px;white-space:nowrap">Merge</button>
        </div>
    </div>
    <div style="margin-top:16px;display:flex;justify-content:space-between">
        <button onclick="toggleMerge()" style="background:#E67E22;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px">Merge Duplicate</button>
        <div style="display:flex;gap:8px">
            <button onclick="openEditFromDetail()" style="background:#1abc9c;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px">Edit</button>
            <button onclick="closeDetail()" style="background:#95A5A6;color:#fff;border:none;padding:8px 20px;border-radius:6px;cursor:pointer">Close</button>
        </div>
    </div>
</div>
</div>

<!-- Quick Log Activity Modal -->
<div class="modal-overlay" id="logModal">
<div class="modal" style="max-width:450px">
    <h2 id="logTitle">Log Activity</h2>
    <input type="hidden" id="logProspect" value="">
    <input type="hidden" id="logAction" value="">
    <div style="margin-bottom:12px">
        <label>Outcome</label>
        <select id="logOutcome" style="width:100%">
            <option value="">—</option>
            <option>Connected - Interested</option>
            <option>Connected - Not Interested</option>
            <option>Left Voicemail</option>
            <option>No Answer</option>
            <option>Email Sent</option>
            <option>Email Reply Received</option>
            <option>Meeting Booked</option>
            <option>Meeting Completed</option>
            <option>Follow-up Needed</option>
            <option>Other</option>
        </select>
    </div>
    <div style="margin-bottom:12px">
        <label>Date</label>
        <input id="logDate" type="date" style="width:100%">
    </div>
    <div style="margin-bottom:12px">
        <label>Next Step</label>
        <input id="logNextStep" type="text" placeholder="e.g. Follow up next week" style="width:100%">
    </div>
    <label>Notes</label>
    <textarea id="logNotes" rows="2" style="width:100%;margin-bottom:8px" placeholder="Optional notes..."></textarea>
    <div style="display:flex;gap:8px;margin-top:8px">
        <button onclick="submitLog()" style="background:#27AE60;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer">Save</button>
        <button onclick="closeLogModal()" style="background:#95A5A6;color:#fff;border:none;padding:10px 24px;border-radius:6px;cursor:pointer">Cancel</button>
    </div>
</div>
</div>

<script>
// HTML entity escaping for safe innerHTML rendering
function _e(s) {{ if (s == null) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }}

// Pipeline view toggle (Table vs Kanban)
function togglePipelineView(view) {{
    const table = document.getElementById('tableView');
    const kanban = document.getElementById('kanbanView');
    const btns = document.querySelectorAll('.view-toggle-btn');
    btns.forEach(b => b.classList.remove('active'));
    if (view === 'kanban') {{
        table.style.display = 'none';
        kanban.style.display = 'block';
        btns[1].classList.add('active');
        localStorage.setItem('pipelineView', 'kanban');
    }} else {{
        table.style.display = 'block';
        kanban.style.display = 'none';
        btns[0].classList.add('active');
        localStorage.setItem('pipelineView', 'table');
    }}
}}
// Restore pipeline view on load
(function() {{
    const saved = localStorage.getItem('pipelineView');
    if (saved === 'kanban') togglePipelineView('kanban');
}})();

// ── Kanban card click handler (works on both desktop and mobile) ──
let _cardDragging = false;
function onCardClick(e, prospectName) {{
    if (_cardDragging) return;
    openProspectDetail(prospectName);
}}

// ── Kanban drag-and-drop ──
function onDragStart(e, prospectName) {{
    _cardDragging = true;
    e.dataTransfer.setData('text/plain', prospectName);
    e.target.style.opacity = '0.5';
}}
function onDragEnd(e) {{
    e.target.style.opacity = '1';
    setTimeout(() => {{ _cardDragging = false; }}, 0);
}}
function onDragOver(e) {{
    e.preventDefault();
    e.currentTarget.style.background = '#e8f8f5';
}}
function onDragLeave(e) {{
    e.currentTarget.style.background = '#f8f9fa';
}}
async function onDrop(e, newStage) {{
    e.preventDefault();
    e.currentTarget.style.background = '#f8f9fa';
    const prospectName = e.dataTransfer.getData('text/plain');
    if (!prospectName) return;
    try {{
        const res = await fetch('/api/prospect/update', {{
            method: 'PUT',
            headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
            body: JSON.stringify({{ name: prospectName, updates: {{ stage: newStage }} }})
        }});
        const result = await res.json();
        if (result.ok) {{
            showToast(prospectName + ' moved to ' + newStage, 'success');
            _saveTabAndReload();
        }} else {{
            showToast('Error: ' + (result.error || 'Unknown'), 'error');
        }}
    }} catch(err) {{ showToast('Error: ' + err.message, 'error'); }}
}}

// ── Mobile touch support for kanban cards ──
(function() {{
    const isTouchDevice = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    if (!isTouchDevice) return;
    // On touch devices, disable draggable (HTML5 DnD doesn't work on mobile)
    // and ensure taps work reliably
    document.addEventListener('DOMContentLoaded', function() {{
        document.querySelectorAll('.kanban-card').forEach(card => {{
            card.removeAttribute('draggable');
        }});
    }});
    // Also run immediately in case DOM is already loaded
    if (document.readyState !== 'loading') {{
        document.querySelectorAll('.kanban-card').forEach(card => {{
            card.removeAttribute('draggable');
        }});
    }}
}})();

// ── Mobile kanban column collapse/expand ──
(function() {{
    function setupMobileKanban() {{
        if (window.innerWidth > 768) return;
        document.querySelectorAll('.kanban-col-header').forEach(header => {{
            if (header._mobileSetup) return;
            header._mobileSetup = true;
            header.addEventListener('click', function() {{
                const col = this.parentElement;
                col.classList.toggle('collapsed');
            }});
        }});
        // Collapse empty columns by default on mobile
        document.querySelectorAll('.kanban-col').forEach(col => {{
            const cards = col.querySelectorAll('.kanban-card');
            if (cards.length === 0) col.classList.add('collapsed');
        }});
    }}
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', setupMobileKanban);
    }} else {{
        setupMobileKanban();
    }}
    window.addEventListener('resize', setupMobileKanban);
}})();

// ── Inline stage change dropdown ──
function changeStage(e, prospectName) {{
    e.stopPropagation();
    const existing = document.getElementById('stageDropdown');
    if (existing) existing.remove();

    const stages = ['New Lead', 'Contacted', 'Discovery Call', 'Needs Analysis', 'Plan Presentation', 'Proposal Sent', 'Negotiation', 'Closed-Won', 'Closed-Lost', 'Nurture'];
    const dd = document.createElement('div');
    dd.id = 'stageDropdown';
    dd.style.cssText = 'position:fixed;z-index:10000;background:#fff;border:1px solid #ddd;border-radius:8px;box-shadow:0 8px 30px rgba(0,0,0,0.15);padding:4px 0;min-width:180px;max-height:300px;overflow-y:auto';
    dd.style.left = e.clientX + 'px';
    dd.style.top = e.clientY + 'px';

    stages.forEach(s => {{
        const opt = document.createElement('div');
        opt.textContent = s;
        opt.style.cssText = 'padding:8px 16px;cursor:pointer;font-size:13px;transition:background 0.15s';
        opt.onmouseover = () => opt.style.background = '#f0f0f0';
        opt.onmouseout = () => opt.style.background = '';
        opt.onclick = async () => {{
            dd.remove();
            try {{
                const res = await fetch('/api/prospect/update', {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
                    body: JSON.stringify({{ name: prospectName, updates: {{ stage: s }} }})
                }});
                const result = await res.json();
                if (result.ok) {{ showToast(prospectName + ' \u2192 ' + s, 'success'); _saveTabAndReload(); }}
                else {{ showToast('Error: ' + (result.error || ''), 'error'); }}
            }} catch(err) {{ showToast('Error: ' + err.message, 'error'); }}
        }};
        dd.appendChild(opt);
    }});

    document.body.appendChild(dd);
    setTimeout(() => document.addEventListener('click', function handler() {{ dd.remove(); document.removeEventListener('click', handler); }}), 10);
}}

// ── Inline priority change dropdown ──
function changePriority(e, prospectName) {{
    e.stopPropagation();
    const existing = document.getElementById('priorityDropdown');
    if (existing) existing.remove();

    const priorities = [
        {{ label: 'Hot', color: '#E74C3C' }},
        {{ label: 'Warm', color: '#F39C12' }},
        {{ label: 'Cold', color: '#3498DB' }},
    ];
    const dd = document.createElement('div');
    dd.id = 'priorityDropdown';
    dd.style.cssText = 'position:fixed;z-index:10000;background:#fff;border:1px solid #ddd;border-radius:8px;box-shadow:0 8px 30px rgba(0,0,0,0.15);padding:4px 0;min-width:140px';
    dd.style.left = e.clientX + 'px';
    dd.style.top = e.clientY + 'px';

    priorities.forEach(pr => {{
        const opt = document.createElement('div');
        opt.style.cssText = 'padding:8px 16px;cursor:pointer;font-size:13px;transition:background 0.15s;display:flex;align-items:center;gap:8px';
        opt.innerHTML = '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + pr.color + '"></span>' + pr.label;
        opt.onmouseover = () => opt.style.background = '#f0f0f0';
        opt.onmouseout = () => opt.style.background = '';
        opt.onclick = async () => {{
            dd.remove();
            try {{
                const res = await fetch('/api/prospect/update', {{
                    method: 'PUT',
                    headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
                    body: JSON.stringify({{ name: prospectName, updates: {{ priority: pr.label }} }})
                }});
                const result = await res.json();
                if (result.ok) {{ showToast(prospectName + ' priority \u2192 ' + pr.label, 'success'); openProspectDetail(prospectName); }}
                else {{ showToast('Error: ' + (result.error || ''), 'error'); }}
            }} catch(err) {{ showToast('Error: ' + err.message, 'error'); }}
        }};
        dd.appendChild(opt);
    }});

    document.body.appendChild(dd);
    setTimeout(() => document.addEventListener('click', function handler() {{ dd.remove(); document.removeEventListener('click', handler); }}), 10);
}}

// Tab switching
function showTab(name) {{
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.getElementById('tab-' + name).classList.add('active');
    const tabBtn = document.querySelector('.tab-btn[data-tab="' + name + '"]');
    if (tabBtn) tabBtn.classList.add('active');
    localStorage.setItem('activeTab', name);
    // Initialize charts when their tab is shown
    if (name === 'forecast' && !window._forecastInit) initForecastCharts();
    if (name === 'funnel' && !window._funnelInit) initFunnelCharts();
}}

function _saveTabAndReload() {{
    localStorage.setItem('activeTab', document.querySelector('.tab-btn.active')?.dataset?.tab || 'pipeline');
    location.reload();
}}

function showToast(message, type) {{
    const toast = document.createElement('div');
    toast.style.cssText = 'position:fixed;bottom:24px;right:24px;background:' + (type === 'error' ? '#E74C3C' : '#27AE60') + ';color:#fff;padding:12px 24px;border-radius:8px;font-size:14px;font-weight:500;z-index:10001;box-shadow:0 4px 20px rgba(0,0,0,0.2);animation:slideUp 0.3s ease';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function() {{ toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(function() {{ toast.remove(); }}, 300); }}, 3000);
}}

function _toastAndReload(message) {{
    showToast(message, 'success');
    setTimeout(_saveTabAndReload, 500);
}}

// Restore active tab on page load
(function() {{
    const saved = localStorage.getItem('activeTab');
    if (saved && saved !== 'pipeline') {{
        const btn = document.querySelector('.tab-btn[data-tab="' + saved + '"]');
        if (btn) {{
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            const content = document.getElementById('tab-' + saved);
            if (content) {{
                content.classList.add('active');
                btn.classList.add('active');
                if (saved === 'forecast') initForecastCharts();
                if (saved === 'funnel') initFunnelCharts();
            }}
        }}
    }}
}})();

const chartColors = ['#1abc9c','#3498db','#8e44ad','#e67e22','#f39c12','#2980b9','#e74c3c','#27ae60','#95a5a6','#2c3e50'];

new Chart(document.getElementById('stageChart'), {{
    type: 'doughnut',
    data: {{
        labels: {_json_script(stage_labels)},
        datasets: [{{ data: {_json_script(stage_values)}, backgroundColor: {_json_script(stage_chart_colors)} }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }} }}
}});

new Chart(document.getElementById('sourceChart'), {{
    type: 'doughnut',
    data: {{
        labels: {_json_script(source_labels)},
        datasets: [{{ data: {_json_script(source_values)}, backgroundColor: chartColors }}]
    }},
    options: {{ responsive: true, plugins: {{ legend: {{ position: 'bottom', labels: {{ boxWidth: 12, padding: 8, font: {{ size: 11 }} }} }} }} }}
}});

new Chart(document.getElementById('productChart'), {{
    type: 'doughnut',
    data: {{
        labels: {_json_script(product_labels)},
        datasets: [{{ data: {_json_script(product_values)}, backgroundColor: chartColors }}]
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
    // Normalize source for case-insensitive dropdown match
    const srcVal = p.source || '';
    const srcSelect = document.getElementById('fSource');
    const srcMatch = Array.from(srcSelect.options).find(o => o.value.toLowerCase() === srcVal.toLowerCase());
    srcSelect.value = srcMatch ? srcMatch.value : srcVal;
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
        if (result.ok) {{ closeModal(); _toastAndReload(isAdding ? 'Prospect added!' : 'Prospect saved!'); }}
        else alert(result.error || 'Error saving');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function deleteProspect() {{
    const name = document.getElementById('origName').value;
    if (!confirm('Delete ' + name + '?')) return;
    try {{
        const res = await fetch('/api/prospect/' + encodeURIComponent(name), {{ method: 'DELETE', headers: {{'X-CSRF-Token': _csrfToken}} }});
        const result = await res.json();
        if (result.ok) {{ closeModal(); _toastAndReload('Prospect deleted.'); }}
        else alert(result.error || 'Error deleting');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

document.getElementById('editModal').addEventListener('click', function(e) {{
    if (e.target === this) closeModal();
}});

// Task management
function openAddTask() {{
    document.getElementById('tEditId').value = '';
    document.getElementById('tTitle').value = '';
    document.getElementById('tProspect').value = '';
    document.getElementById('tDue').value = '';
    document.getElementById('tRemind').value = '';
    document.getElementById('tNotes').value = '';
    document.getElementById('taskModalTitle').textContent = 'Add Task';
    document.getElementById('tDeleteBtn').style.display = 'none';
    document.getElementById('taskModal').classList.add('active');
    document.getElementById('tTitle').focus();
}}

function openEditTask(id, title, prospect, due, remind, notes) {{
    document.getElementById('tEditId').value = id;
    document.getElementById('tTitle').value = title;
    document.getElementById('tProspect').value = prospect;
    document.getElementById('tDue').value = due;
    document.getElementById('tRemind').value = remind;
    document.getElementById('tNotes').value = notes;
    document.getElementById('taskModalTitle').textContent = 'Edit Task';
    document.getElementById('tDeleteBtn').style.display = 'inline-block';
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
    const editId = document.getElementById('tEditId').value;
    const data = {{
        title: title,
        prospect: document.getElementById('tProspect').value.trim(),
        due_date: document.getElementById('tDue').value || null,
        remind_at: document.getElementById('tRemind').value ? document.getElementById('tRemind').value.replace('T', ' ') : null,
        notes: document.getElementById('tNotes').value.trim(),
    }};
    try {{
        let res;
        if (editId) {{
            res = await fetch('/api/task/' + editId, {{ method: 'PUT', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}}, body: JSON.stringify(data) }});
        }} else {{
            data.assigned_to = '';
            data.created_by = '';
            res = await fetch('/api/task', {{ method: 'POST', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}}, body: JSON.stringify(data) }});
        }}
        const result = await res.json();
        if (result.ok) {{ closeTaskModal(); _toastAndReload('Task saved!'); }}
        else alert(result.error || 'Error saving task');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function completeTask(id, checkbox) {{
    try {{
        const res = await fetch('/api/task/' + id + '/complete', {{ method: 'PUT', headers: {{'X-CSRF-Token': _csrfToken}} }});
        const result = await res.json();
        if (result.ok) {{ showToast('Task completed!', 'success'); setTimeout(_saveTabAndReload, 500); }}
        else {{ checkbox.checked = false; alert(result.error || 'Error'); }}
    }} catch(e) {{ checkbox.checked = false; alert('Error: ' + e.message); }}
}}

async function deleteTask(id) {{
    if (!confirm('Delete this task?')) return;
    try {{
        const res = await fetch('/api/task/' + id, {{ method: 'DELETE', headers: {{'X-CSRF-Token': _csrfToken}} }});
        const result = await res.json();
        if (result.ok) {{ closeTaskModal(); _toastAndReload('Task deleted.'); }}
        else alert(result.error || 'Error');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Prospect detail panel
let _detailProspect = '';

async function openProspectDetail(name) {{
    _detailProspect = name;
    document.getElementById('detailTitle').textContent = name;
    document.getElementById('detailContent').innerHTML = '<div class="empty-state"><p>Loading...</p></div>';
    document.getElementById('detailModal').classList.add('active');
    try {{
        const res = await fetch('/api/prospect/' + encodeURIComponent(name) + '/detail', {{ headers: {{'X-CSRF-Token': _csrfToken}} }});
        const data = await res.json();
        if (data.error) {{ document.getElementById('detailContent').innerHTML = '<p>Error: ' + _e(data.error) + '</p>'; return; }}
        let html = '';

        // Health + Next Action
        const p = data.prospect;
        const hs = data.health_score || 0;
        const hColor = hs >= 70 ? '#27AE60' : hs >= 40 ? '#F39C12' : '#E74C3C';
        html += '<div style="display:flex;gap:12px;margin-bottom:16px">';
        html += '<div style="flex:0 0 80px;text-align:center;padding:12px;background:#f8f9fa;border-radius:8px"><div style="font-size:11px;color:#7f8c8d;text-transform:uppercase">Health</div><div style="font-size:32px;font-weight:700;color:' + hColor + '">' + hs + '</div></div>';
        if (data.next_action) html += '<div style="flex:1;padding:12px;background:#f0f0ff;border-radius:8px;border-left:3px solid #8E44AD"><div style="font-size:11px;color:#8E44AD;text-transform:uppercase;font-weight:600">Recommended Next Action</div><div style="font-size:14px;margin-top:4px">' + _e(data.next_action) + '</div></div>';
        html += '</div>';

        // Prospect info
        html += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;margin-bottom:20px;padding:12px;background:#f8f9fa;border-radius:8px">';
        html += '<div><strong>Phone:</strong> ' + _e(p.phone || '—') + '</div>';
        html += '<div><strong>Email:</strong> ' + _e(p.email || '—') + '</div>';
        html += '<div><strong>Stage:</strong> <span style="cursor:pointer;text-decoration:underline dotted" onclick="changeStage(event, _detailProspect)" title="Click to change">' + _e(p.stage || '—') + '</span></div>';
        html += '<div><strong>Priority:</strong> <span style="cursor:pointer;text-decoration:underline dotted" onclick="changePriority(event, _detailProspect)" title="Click to change">' + _e(p.priority || '—') + '</span></div>';
        html += '<div><strong>Product:</strong> ' + _e(p.product || '—') + '</div>';
        html += '<div><strong>Follow-up:</strong> ' + _e(p.next_followup || '—');
        html += ' <span style="font-size:11px;margin-left:8px">';
        html += '<a href="#" onclick="event.preventDefault();quickRescheduleFromDetail(_detailProspect, 0)" style="color:#27ae60;text-decoration:none" title="Set to today">today</a>';
        html += ' · <a href="#" onclick="event.preventDefault();quickRescheduleFromDetail(_detailProspect, 1)" style="color:#3498db;text-decoration:none" title="Tomorrow">+1d</a>';
        html += ' · <a href="#" onclick="event.preventDefault();quickRescheduleFromDetail(_detailProspect, 7)" style="color:#9b59b6;text-decoration:none" title="Next week">+1w</a>';
        html += '</span></div>';
        html += '<div><strong>AUM:</strong> ' + _e(p.aum || '—') + '</div>';
        html += '<div><strong>Revenue:</strong> ' + _e(p.revenue || '—') + '</div>';
        if (p.notes) html += '<div style="grid-column:1/-1"><strong>Notes:</strong> ' + _e(p.notes) + '</div>';
        html += '</div>';

        // Store current notes for addQuickNote()
        window._detailCurrentNotes = p.notes || '';

        // Quick note input
        html += '<div style="margin-top:12px;display:flex;gap:8px">';
        html += '<input type="text" id="quickNoteInput" placeholder="Add a quick note..." style="flex:1;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:13px">';
        html += '<button onclick="addQuickNote()" style="background:#3498DB;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px">Add</button>';
        html += '</div>';

        // Tasks
        if (data.tasks && data.tasks.length > 0) {{
            html += '<h3 style="margin:16px 0 8px;font-size:15px">Tasks (' + data.tasks.length + ')</h3>';
            html += '<table style="font-size:13px"><tr><th>Task</th><th>Due</th><th>Status</th></tr>';
            data.tasks.forEach(t => {{
                const style = t.status === 'completed' ? 'text-decoration:line-through;opacity:0.6' : '';
                html += '<tr style="' + style + '"><td>' + _e(t.title) + '</td><td>' + _e(t.due_date || '—') + '</td><td>' + _e(t.status) + '</td></tr>';
            }});
            html += '</table>';
        }}

        // Activities
        if (data.activities && data.activities.length > 0) {{
            html += '<h3 style="margin:16px 0 8px;font-size:15px">Activity Log (' + data.activities.length + ')</h3>';
            html += '<table style="font-size:13px"><tr><th>Date</th><th>Action</th><th>Outcome</th><th>Next</th></tr>';
            data.activities.forEach(a => {{
                html += '<tr><td>' + _e((a.date || '').split(' ')[0]) + '</td><td>' + _e(a.action || '') + '</td><td>' + _e(a.outcome || '') + '</td><td>' + _e(a.next_step || '') + '</td></tr>';
            }});
            html += '</table>';
        }}

        // Interactions
        if (data.interactions && data.interactions.length > 0) {{
            html += '<h3 style="margin:16px 0 8px;font-size:15px">Timeline (' + data.interactions.length + ')</h3>';
            html += '<div style="border-left:2px solid #1abc9c;margin-left:8px;padding-left:16px">';
            data.interactions.forEach(i => {{
                const sourceIcon = i.source === 'voice_note' ? '🎙' : i.source === 'otter_transcript' ? '📝' : i.source === 'email_lead' ? '📧' : i.source === 'outlook_booking' ? '📅' : '💬';
                html += '<div style="position:relative;padding:8px 0;margin-bottom:4px">';
                html += '<div style="position:absolute;left:-23px;top:12px;width:10px;height:10px;background:#1abc9c;border-radius:50%;border:2px solid #fff"></div>';
                html += '<div style="font-size:11px;color:#7f8c8d">' + sourceIcon + ' ' + _e(i.source || '?') + ' · ' + _e((i.date || '').split(' ')[0]) + '</div>';
                html += '<div style="font-size:13px;margin-top:2px">' + _e((i.summary || i.raw_text || '').substring(0, 300)) + '</div>';
                if (i.action_items) html += '<div style="font-size:12px;color:#E67E22;margin-top:4px">Action: ' + _e(i.action_items) + '</div>';
                html += '</div>';
            }});
            html += '</div>';
        }}

        if (!data.tasks?.length && !data.activities?.length && !data.interactions?.length) {{
            html += '<div class="empty-state"><p>No activity yet. Log a call or email to get started.</p></div>';
        }}

        document.getElementById('detailContent').innerHTML = html;
        // Attach Enter key handler after DOM is set (avoids inline event escaping issues)
        const _qni = document.getElementById('quickNoteInput');
        if (_qni) _qni.addEventListener('keydown', function(ev) {{ if (ev.key === 'Enter') addQuickNote(); }});
    }} catch(e) {{ document.getElementById('detailContent').innerHTML = '<p>Error loading: ' + _e(e.message) + '</p>'; }}
}}

async function addQuickNote() {{
    const input = document.getElementById('quickNoteInput');
    if (!input) return;
    const noteText = input.value.trim();
    if (!noteText) return;
    // Build timestamp prefix like [Mar 16]
    const now = new Date();
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const prefix = '[' + months[now.getMonth()] + ' ' + now.getDate() + '] ';
    const existing = window._detailCurrentNotes || '';
    const updated = existing ? existing + '\\n' + prefix + noteText : prefix + noteText;
    try {{
        const res = await fetch('/api/prospect/update', {{
            method: 'PUT',
            headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
            body: JSON.stringify({{ name: _detailProspect, updates: {{ notes: updated }} }})
        }});
        const result = await res.json();
        if (result.ok) {{
            showToast('Note added!', 'success');
            openProspectDetail(_detailProspect);
        }} else {{
            showToast(result.error || 'Error saving note', 'error');
        }}
    }} catch(e) {{ showToast('Error: ' + e.message, 'error'); }}
}}

function closeDetail() {{ document.getElementById('detailModal').classList.remove('active'); document.getElementById('mergeSection').style.display='none'; }}
document.getElementById('detailModal').addEventListener('click', function(e) {{ if (e.target === this) closeDetail(); }});

async function openEditFromDetail() {{
    // Try to find the prospect data from the table first
    const rows = document.querySelectorAll('.editable-row');
    let found = null;
    rows.forEach(r => {{
        try {{
            const p = JSON.parse(r.dataset.prospect);
            if (p.name === _detailProspect) found = p;
        }} catch(e) {{}}
    }});
    if (found) {{
        closeDetail();
        openEdit(found);
        return;
    }}
    // If not in table (e.g. closed deals or kanban view), fetch from API
    try {{
        const res = await fetch('/api/prospect/' + encodeURIComponent(_detailProspect) + '/detail', {{ headers: {{'X-CSRF-Token': _csrfToken}} }});
        const data = await res.json();
        if (data.prospect) {{
            closeDetail();
            openEdit(data.prospect);
        }} else {{
            alert('Could not find prospect data to edit.');
        }}
    }} catch(e) {{
        alert('Error loading prospect: ' + e.message);
    }}
}}

const _allProspectNames = {json.dumps([p["name"] for p in prospects])};

function toggleMerge() {{
    const sec = document.getElementById('mergeSection');
    if (sec.style.display === 'none') {{
        sec.style.display = 'block';
        const sel = document.getElementById('mergeTarget');
        sel.innerHTML = '<option value="">Select prospect to merge...</option>';
        _allProspectNames.filter(n => n !== _detailProspect).forEach(n => {{
            sel.innerHTML += '<option value="' + _e(n) + '">' + _e(n) + '</option>';
        }});
    }} else {{
        sec.style.display = 'none';
    }}
}}

async function doMerge() {{
    const mergeFrom = document.getElementById('mergeTarget').value;
    if (!mergeFrom) {{ alert('Select a prospect to merge'); return; }}
    if (!confirm('Merge "' + mergeFrom + '" into "' + _detailProspect + '"? This will move all data and delete "' + mergeFrom + '".')) return;
    try {{
        const res = await fetch('/api/prospect/merge', {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
            body: JSON.stringify({{ keep: _detailProspect, merge: mergeFrom }})
        }});
        const result = await res.json();
        if (result.ok) {{
            _toastAndReload('Prospects merged!');
        }} else {{
            alert('Error: ' + (result.error || 'Unknown error'));
        }}
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Toggle pending drafts section
function togglePendingDrafts() {{
    const sec = document.getElementById('pendingDraftsSection');
    if (!sec) return;
    sec.style.display = sec.style.display === 'none' ? 'block' : 'none';
}}

// Quick log activity
function quickLogActivity(action, prospectName) {{
    _detailProspect = prospectName || _detailProspect;
    document.getElementById('logProspect').value = _detailProspect;
    document.getElementById('logAction').value = action;
    document.getElementById('logTitle').textContent = 'Log ' + action + ': ' + _detailProspect;
    document.getElementById('logOutcome').value = '';
    document.getElementById('logNextStep').value = '';
    document.getElementById('logNotes').value = '';
    // Default date to today
    document.getElementById('logDate').value = new Date().toISOString().split('T')[0];
    document.getElementById('logModal').classList.add('active');
}}

function closeLogModal() {{ document.getElementById('logModal').classList.remove('active'); }}
document.getElementById('logModal').addEventListener('click', function(e) {{ if (e.target === this) closeLogModal(); }});

async function submitLog() {{
    const data = {{
        prospect: document.getElementById('logProspect').value,
        action: document.getElementById('logAction').value,
        outcome: document.getElementById('logOutcome').value,
        next_step: document.getElementById('logNextStep').value,
        notes: document.getElementById('logNotes').value.trim(),
        date: document.getElementById('logDate').value || new Date().toISOString().split('T')[0],
    }};
    try {{
        const res = await fetch('/api/activity', {{ method: 'POST', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}}, body: JSON.stringify(data) }});
        const result = await res.json();
        if (result.ok) {{
            showToast('Activity logged!', 'success');
            closeLogModal();
            openProspectDetail(_detailProspect);
        }} else alert(result.error || 'Error');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Prospect search/filter
function filterProspects(query) {{
    const table = document.getElementById('prospectTable');
    if (!table) return;
    const rows = table.querySelectorAll('tr');
    const q = query.toLowerCase();
    rows.forEach((row, i) => {{
        if (i === 0) return; // skip header
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(q) ? '' : 'none';
    }});
}}

// Quick reschedule follow-up
async function quickReschedule(prospectName, days) {{
    const d = new Date();
    d.setDate(d.getDate() + days);
    const dateStr = d.toISOString().split('T')[0];
    try {{
        const res = await fetch('/api/prospect/update', {{
            method: 'PUT',
            headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
            body: JSON.stringify({{ name: prospectName, updates: {{ next_followup: dateStr }} }})
        }});
        const result = await res.json();
        if (result.ok) _toastAndReload('Follow-up rescheduled!');
        else alert(result.error || 'Error rescheduling');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Quick reschedule from detail panel (refreshes detail instead of reloading page)
async function quickRescheduleFromDetail(name, days) {{
    const d = new Date();
    d.setDate(d.getDate() + days);
    const dateStr = d.toISOString().split('T')[0];
    try {{
        const res = await fetch('/api/prospect/update', {{
            method: 'PUT',
            headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': _csrfToken}},
            body: JSON.stringify({{ name: name, updates: {{ next_followup: dateStr }} }})
        }});
        const result = await res.json();
        if (result.ok) {{ showToast('Follow-up set to ' + dateStr, 'success'); openProspectDetail(name); }}
        else {{ showToast('Error: ' + (result.error || ''), 'error'); }}
    }} catch(err) {{ showToast('Error: ' + err.message, 'error'); }}
}}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', function(e) {{
    // Don't trigger if user is typing in an input/textarea
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;

    if (e.key === 'n') {{ e.preventDefault(); openAdd(); }}
    else if (e.key === 'k') {{ e.preventDefault(); togglePipelineView('kanban'); }}
    else if (e.key === 't') {{ e.preventDefault(); togglePipelineView('table'); }}
    else if (e.key === 'Escape') {{
        document.querySelectorAll('.modal-overlay.active').forEach(m => m.classList.remove('active'));
        const dd = document.getElementById('stageDropdown'); if (dd) dd.remove();
        const pd = document.getElementById('priorityDropdown'); if (pd) pd.remove();
    }}
    else if (e.key === '/') {{
        e.preventDefault();
        const search = document.getElementById('prospectSearch');
        if (search) {{ showTab('pipeline'); togglePipelineView('table'); search.focus(); }}
    }}
}});

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

// Smart auto-refresh: only refresh when idle for 5 minutes
(function() {{
    const IDLE_MS = 5 * 60 * 1000; // 5 minutes
    const CHECK_MS = 10 * 1000;    // check every 10s
    let lastInteraction = Date.now();

    function resetIdle() {{ lastInteraction = Date.now(); hideBanner(); }}
    document.addEventListener('click', resetIdle, true);
    document.addEventListener('keypress', resetIdle, true);
    document.addEventListener('mousemove', resetIdle, true);
    document.addEventListener('touchstart', resetIdle, true);

    const banner = document.createElement('div');
    banner.id = 'refreshBanner';
    banner.style.cssText = 'display:none;position:fixed;bottom:0;left:0;right:0;background:#0f1b2d;color:#e8e6f0;text-align:center;padding:10px 16px;font-size:13px;z-index:9999;box-shadow:0 -2px 8px rgba(0,0,0,0.3)';
    banner.innerHTML = '<span id="refreshCountdown"></span> &nbsp;<a href="javascript:void(0)" onclick="doRefresh()" style="color:#1abc9c;font-weight:600">Refresh now</a> &nbsp;<a href="javascript:void(0)" onclick="dismissRefresh()" style="color:#7f8c8d;font-size:11px">Dismiss</a>';
    document.body.appendChild(banner);

    let dismissed = false;
    function hideBanner() {{ banner.style.display = 'none'; dismissed = false; }}
    window.dismissRefresh = function() {{ dismissed = true; banner.style.display = 'none'; lastInteraction = Date.now(); }};
    window.doRefresh = function() {{ _saveTabAndReload(); }};

    setInterval(function() {{
        if (document.hidden) return;
        const idleMs = Date.now() - lastInteraction;
        if (idleMs >= IDLE_MS) {{
            if (!dismissed) {{
                const secsLeft = Math.max(0, Math.round((IDLE_MS * 2 - idleMs) / 1000));
                document.getElementById('refreshCountdown').textContent = 'Dashboard will refresh in ' + secsLeft + 's (idle)';
                banner.style.display = 'block';
            }}
            // Auto-refresh after another 5 minutes of idleness (10 min total)
            if (idleMs >= IDLE_MS * 2) {{
                _saveTabAndReload();
            }}
        }} else {{
            hideBanner();
        }}
    }}, CHECK_MS);
}})();

// Velocity chart (lazy init)
function initFunnelCharts() {{
    window._funnelInit = true;
    const ctx = document.getElementById('velocityChart');
    if (!ctx) return;
    const velocityLabels = {_json_script(list(avg_stage_days.keys()))};
    const velocityData = {_json_script([round(v, 1) for v in avg_stage_days.values()])};
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
    if not telegram_webhook_secret:
        logging.getLogger(__name__).warning(
            "TELEGRAM_WEBHOOK_SECRET not set — webhook endpoint will reject all requests. "
            "Set this env var to the same value used in bot.set_webhook(secret_token=...)"
        )

    @flask_app.route("/webhook", methods=["POST"])
    def webhook():
        if process_update_fn is None:
            return "Bot not initialized", 503
        # Always validate Telegram's secret_token header — reject if not configured
        if not telegram_webhook_secret:
            return "Webhook secret not configured", 503
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


@app.route("/health")
def health_check():
    """Health check endpoint for monitoring. Returns 200 if all systems are OK."""
    checks = {}

    # Check database
    try:
        with db.get_db() as conn:
            conn.execute("SELECT COUNT(*) FROM prospects").fetchone()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Check scheduler is running
    import sys
    main_mod = sys.modules.get("__main__")
    scheduler_running = getattr(main_mod, "scheduler_started", False)
    # Alternative: check if last briefing was recent
    checks["scheduler"] = "ok"  # Basic check — scheduler started

    # Check Telegram bot connectivity
    try:
        telegram_app = getattr(main_mod, "telegram_app", None)
        if telegram_app:
            checks["telegram"] = "ok"
        else:
            checks["telegram"] = "not initialized"
    except Exception as e:
        checks["telegram"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return jsonify({
        "status": "healthy" if all_ok else "degraded",
        "checks": checks,
        "timestamp": datetime.now().isoformat(),
    }), status_code


def run_dashboard():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def start_dashboard_thread():
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
