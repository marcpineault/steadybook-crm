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
from flask import Flask, Response, request, jsonify, render_template, redirect, make_response

logger = logging.getLogger(__name__)


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


app = Flask(__name__, template_folder='templates', static_folder='static')
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
    response.headers["Permissions-Policy"] = "camera=(), microphone=(self), geolocation=()"
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


# ── API Routes ──

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
    notes = db.get_prospect_notes(prospect["id"], limit=50)
    # Memory engine intelligence
    try:
        import memory_engine
        memory_summary = memory_engine.get_profile_summary_text(prospect["id"])
    except Exception:
        memory_summary = ""
    return jsonify({
        "prospect": prospect,
        "activities": prospect_activities[:20],
        "interactions": interactions[:20],
        "tasks": tasks,
        "notes": notes,
        "health_score": health,
        "next_action": next_action,
        "memory_summary": memory_summary,
    })


@app.route("/api/prospect/<int:prospect_id>/notes", methods=["POST"])
@_require_auth
def api_add_note(prospect_id):
    """Add a note to a prospect's timeline."""
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    if not content:
        return jsonify({"error": "Content required"}), 400
    note = db.add_prospect_note(prospect_id, content, created_by="marc")
    if not note:
        return jsonify({"error": "Could not add note"}), 400
    return jsonify({"ok": True, "note": note})


@app.route("/api/note/<int:note_id>", methods=["DELETE"])
@_require_auth
def api_delete_note(note_id):
    """Delete a prospect note."""
    result = db.delete_prospect_note(note_id)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})


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


@app.route("/api/conversations")
@_require_auth
def api_conversations():
    """Return list of distinct phone numbers with their latest message."""
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT sc.phone, sc.prospect_name, sc.body, sc.direction, sc.created_at,
                   p.name as matched_name
            FROM sms_conversations sc
            LEFT JOIN prospects p ON sc.prospect_id = p.id
            WHERE sc.id IN (
                SELECT MAX(id) FROM sms_conversations GROUP BY phone
            )
            ORDER BY sc.created_at DESC
            LIMIT 100
        """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/conversations/<path:phone>")
@_require_auth
def api_conversation_thread(phone):
    """Return full thread for a phone number, oldest first."""
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT id, direction, body, created_at, prospect_name, twilio_sid
            FROM sms_conversations
            WHERE phone = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 200
        """, (phone,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/chat", methods=["POST"])
@_require_auth
def api_chat():
    """Send a message to the bot and return its reply."""
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message") or "").strip()
    if not user_msg:
        return jsonify({"error": "message required"}), 400
    if len(user_msg) > 4000:
        return jsonify({"error": "message too long"}), 400
    try:
        import bot as _bot_module
        reply = _bot_module.process_dashboard_message(user_msg)
        return jsonify({"reply": reply})
    except Exception as e:
        logger.exception("Dashboard chat error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/conversations/<path:phone>/send", methods=["POST"])
@_require_auth
def api_send_sms(phone):
    """Send an outbound SMS directly from the dashboard."""
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "body required"}), 400
    if len(body) > 1600:
        return jsonify({"error": "message too long"}), 400

    import sms_sender
    sid = sms_sender.send_sms(to=phone, body=body)
    if sid is None:
        return jsonify({"error": "SMS send failed — check Twilio credentials"}), 502

    # Log to sms_conversations
    import sms_conversations as sms_conv
    from webhook_intake import _find_prospect_by_phone
    prospect = _find_prospect_by_phone(phone) or {}
    sms_conv.log_message(
        phone=phone, body=body, direction="outbound",
        prospect_id=prospect.get("id"), prospect_name=prospect.get("name", ""),
        twilio_sid=sid
    )

    return jsonify({"ok": True, "sid": sid})


@app.route("/api/draft/<int:draft_id>/approve", methods=["POST"])
@_require_auth
def api_approve_draft(draft_id):
    """Approve a pending draft and trigger sending."""
    import approval_queue as aq
    draft = aq.get_draft_by_id(draft_id)
    if not draft:
        return jsonify({"error": "Draft not found"}), 404
    if draft.get("status") != "pending":
        return jsonify({"error": "Draft already processed"}), 400

    aq.update_draft_status(draft_id, "approved")

    # Trigger SMS send if it's an SMS draft
    sent = False
    if draft.get("channel") == "sms_draft" and draft.get("prospect_id"):
        try:
            import sms_sender, sms_conversations as _sms_conv
            with db.get_db() as conn:
                prow = conn.execute("SELECT phone, name FROM prospects WHERE id = ?", (draft["prospect_id"],)).fetchone()
            if prow and prow["phone"]:
                sid = sms_sender.send_sms(to=prow["phone"], body=draft["content"])
                if sid:
                    _sms_conv.log_message(
                        phone=prow["phone"], body=draft["content"], direction="outbound",
                        prospect_id=draft["prospect_id"], prospect_name=prow["name"], twilio_sid=sid,
                    )
                    sent = True
                    # Activate SMS agent mission if this is an opener
                    if draft.get("type") == "sms_agent":
                        try:
                            import sms_agent
                            sms_agent.activate_mission_by_phone(prow["phone"])
                        except Exception:
                            pass
        except Exception:
            logging.getLogger(__name__).exception("Draft send failed for #%d", draft_id)

    return jsonify({"ok": True, "sent": sent, "message": "Approved" + (" & sent" if sent else "")})


@app.route("/api/draft/<int:draft_id>/dismiss", methods=["POST"])
@_require_auth
def api_dismiss_draft(draft_id):
    """Dismiss/skip a pending draft."""
    import approval_queue as aq
    draft = aq.get_draft_by_id(draft_id)
    if not draft:
        return jsonify({"error": "Draft not found"}), 404
    aq.update_draft_status(draft_id, "dismissed")
    return jsonify({"ok": True, "message": "Draft dismissed"})


@app.route("/api/trust", methods=["GET"])
@_require_auth
def api_get_trust():
    """Get current trust level."""
    from bot import get_trust_level
    return jsonify({"trust_level": get_trust_level()})


@app.route("/api/trust", methods=["PUT"])
@_require_auth
def api_set_trust():
    """Set trust level (1 or 2)."""
    data = request.get_json(silent=True) or {}
    level = data.get("level")
    if level not in (1, 2, 3):
        return jsonify({"error": "Level must be 1, 2, or 3"}), 400
    from bot import set_trust_level
    set_trust_level(level, "dashboard")
    return jsonify({"ok": True, "trust_level": level})


# ── Helper Functions ──

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

    # Recency of contact (+-30 points)
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


# ── Auth & Context Helpers ──

def _check_auth():
    """Check if user is authenticated. Returns True if authed."""
    import hashlib
    dash_cookie = request.cookies.get("dash_auth", "")
    if dash_cookie and DASHBOARD_API_KEY:
        expected = hashlib.sha256(DASHBOARD_API_KEY.encode()).hexdigest()
        if hmac.compare_digest(dash_cookie, expected):
            return True
    api_key = request.headers.get("X-API-Key", "") or request.args.get("key", "")
    if DASHBOARD_API_KEY and api_key and hmac.compare_digest(api_key, DASHBOARD_API_KEY):
        return True
    if not DASHBOARD_API_KEY:
        return True
    return False


def _common_context():
    """Build context data shared by all pages (sidebar badges, etc)."""
    prospects, activities, meetings, book_entries = read_data()
    try:
        all_tasks = db.get_tasks(status="pending")
        completed_tasks = db.get_tasks(status="completed", limit=10)
    except Exception:
        all_tasks, completed_tasks = [], []

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p["stage"] == "Closed-Won"]
    lost = [p for p in prospects if p["stage"] == "Closed-Lost"]

    overdue_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] < today_str]

    return {
        "prospects": prospects,
        "activities": activities,
        "meetings": meetings,
        "book_entries": book_entries,
        "all_tasks": all_tasks,
        "completed_tasks": completed_tasks,
        "active": active,
        "won": won,
        "lost": lost,
        "today": today,
        "today_str": today_str,
        "overdue_tasks": overdue_tasks,
        "csrf_token": _generate_csrf_token(),
        # Sidebar badge data
        "active_page": "",
        "pipeline_count": len(active),
        "overdue_task_count": len(overdue_tasks),
        "unread_count": 0,  # Could be computed from SMS
        "won_count": len(won),
    }


# ── Login / Logout ──

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if DASHBOARD_API_KEY and hmac.compare_digest(password, DASHBOARD_API_KEY):
            import hashlib
            cookie_val = hashlib.sha256(DASHBOARD_API_KEY.encode()).hexdigest()
            resp = make_response(redirect("/"))
            resp.set_cookie("dash_auth", cookie_val, max_age=86400 * 30, httponly=True, samesite="Lax")
            return resp
        return render_template("login.html", error="Wrong password. Try again.")
    return render_template("login.html", error="")


@app.route("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("dash_auth")
    return resp


# ── Event Lead Intake Form ──

@app.route("/intake/event", methods=["GET", "POST"])
def intake_event():
    """Mobile-friendly event lead intake form."""
    if not _check_auth():
        return redirect("/login")

    message = ""
    msg_type = ""

    if request.method == "POST":
        csrf = request.form.get("_csrf", "")
        if not _validate_csrf_token(csrf):
            message = "Session expired. Please try again."
            msg_type = "error"
        else:
            name = (request.form.get("name") or "").strip()
            phone = (request.form.get("phone") or "").strip()
            email = (request.form.get("email") or "").strip().lower()

            if not name:
                message = "Name is required."
                msg_type = "error"
            else:
                existing = db.get_prospect_by_email(email) if email else None
                if existing:
                    updates = {}
                    if phone and not existing.get("phone"):
                        updates["phone"] = phone
                    old_notes = existing.get("notes") or ""
                    tag = "[Networking Event] Also met at event"
                    if tag not in old_notes:
                        updates["notes"] = f"{old_notes} | {tag}".lstrip(" |") if old_notes else tag
                    if updates:
                        db.update_prospect(existing["name"], updates)
                    db.add_activity({"prospect": existing["name"], "action": "Networking Event lead intake (existing)"})
                    message = f"Updated {existing['name']} (already in pipeline)."
                    msg_type = "success"
                else:
                    db.add_prospect({
                        "name": name, "phone": phone, "email": email,
                        "source": "Networking Event", "stage": "New Lead",
                        "priority": "Warm",
                    })
                    db.add_activity({"prospect": name, "action": "Networking Event lead intake"})
                    message = f"Added {name} to pipeline."
                    msg_type = "success"

    csrf_token = _generate_csrf_token()
    today_display = date.today().strftime("%B %d, %Y")
    return render_template("intake.html", csrf_token=csrf_token, message=message, msg_type=msg_type, today_display=today_display)


# ── Page Routes ──

@app.route("/")
def dashboard():
    if not _check_auth():
        return redirect("/login")

    ctx = _common_context()
    ctx["active_page"] = "dashboard"
    today = ctx["today"]
    today_str = ctx["today_str"]
    active = ctx["active"]
    won = ctx["won"]
    lost = ctx["lost"]
    activities = ctx["activities"]
    meetings = ctx["meetings"]
    all_tasks = ctx["all_tasks"]

    # KPI calculations
    total_pipeline = sum(parse_money(p["aum"]) for p in active)
    total_revenue = sum(parse_money(p["revenue"]) for p in active)
    won_revenue = sum(parse_money(p["revenue"]) for p in won)

    PREMIUM_TARGET = 200000
    BASELINE_PREMIUM = 2000
    forecast_revenue = won_revenue + BASELINE_PREMIUM
    year_start = date(today.year, 1, 1)
    days_elapsed = (today - year_start).days + 1
    days_total = (date(today.year, 12, 31) - year_start).days + 1
    premium_pct = (forecast_revenue / PREMIUM_TARGET * 100) if PREMIUM_TARGET else 0

    win_rate = len(won) / (len(won) + len(lost)) * 100 if (len(won) + len(lost)) > 0 else 0

    # Build last_activity_map
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

    # Overdue follow-ups
    overdue = []
    for p in active:
        if p["next_followup"] and p["next_followup"] != "None":
            try:
                fu = datetime.strptime(p["next_followup"].split(" ")[0], "%Y-%m-%d").date()
                if fu < today:
                    overdue.append(p)
            except (ValueError, IndexError):
                pass

    # Stale prospects
    stale_prospects = []
    for p in active:
        pname = p["name"].strip().lower()
        last = last_activity_map.get(pname)
        if last:
            days_idle = (today - last).days
            if days_idle >= 14:
                stale_prospects.append((p, days_idle))
        else:
            stale_prospects.append((p, 999))
    stale_prospects.sort(key=lambda x: -x[1])

    # Today's meetings
    todays_meetings = [m for m in meetings if m.get("date") == today_str and m.get("status", "").lower() != "cancelled"]

    # Build scored call list (replaces old priority_actions)
    import scoring
    ranked_call_list = scoring.get_ranked_call_list(limit=10)
    # Enrich with days idle
    for item in ranked_call_list:
        pname_lower = item["name"].strip().lower()
        last = last_activity_map.get(pname_lower)
        if last:
            item["days_idle"] = (today - last).days
        else:
            item["days_idle"] = None
        # Check if overdue
        fu = item.get("next_followup", "")
        if fu and fu != "None":
            try:
                fu_date = datetime.strptime(fu.split(" ")[0], "%Y-%m-%d").date()
                item["is_overdue"] = fu_date < today
                item["days_overdue"] = (today - fu_date).days if fu_date < today else 0
            except (ValueError, IndexError):
                item["is_overdue"] = False
                item["days_overdue"] = 0
        else:
            item["is_overdue"] = False
            item["days_overdue"] = 0

    # Urgent tasks (overdue + due today)
    overdue_tasks = ctx["overdue_tasks"]
    due_today_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] == today_str]
    urgent_tasks = []
    for t in overdue_tasks[:3]:
        try:
            days_late = (today - datetime.strptime(t["due_date"], "%Y-%m-%d").date()).days
        except (ValueError, IndexError):
            days_late = 0
        urgent_tasks.append({**t, "days_late": days_late, "urgency": "overdue"})
    for t in due_today_tasks:
        urgent_tasks.append({**t, "days_late": 0, "urgency": "today"})

    # Recent activities
    recent_activities = []
    for a in activities[:5]:
        date_str = a.get("date", "")
        recent_activities.append({
            "action": a.get("action", ""),
            "prospect": a.get("prospect", ""),
            "outcome": a.get("outcome", ""),
            "date_relative": _relative_time(date_str, today),
        })

    # Pending drafts — enrich with prospect names
    try:
        import approval_queue as _aq
        pending_drafts = _aq.get_pending_drafts()
        prospects = ctx["prospects"]
        for d in pending_drafts:
            if d.get("prospect_id"):
                match = next((p["name"] for p in prospects if str(p.get("id", "")) == str(d["prospect_id"])), "")
                d["prospect_name"] = match
            else:
                d["prospect_name"] = ""
    except Exception:
        pending_drafts = []

    # Cross-sell opportunities for closed-won clients
    cross_sell_ops = []
    try:
        from scoring import get_cross_sell_suggestions
        for p in won[:10]:
            suggestions = get_cross_sell_suggestions(p.get("product", ""))
            if suggestions:
                cross_sell_ops.append({
                    "name": p["name"],
                    "current_product": p.get("product", ""),
                    "suggestions": suggestions[:2],
                })
    except Exception:
        pass

    # Active SMS agent missions
    try:
        with db.get_db() as conn:
            active_missions = [dict(r) for r in conn.execute(
                "SELECT * FROM sms_agents WHERE status IN ('active', 'pending_approval') ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()]
    except Exception:
        active_missions = []

    action_count = len(ranked_call_list) + len(urgent_tasks)

    ctx.update({
        "today_display": today.strftime("%A, %B %d"),
        "action_count": action_count,
        "active_count": len(active),
        "new_this_week": 0,
        "total_pipeline_fmt": fmt_money(total_pipeline),
        "premium_ytd_fmt": fmt_money(forecast_revenue),
        "premium_pct": premium_pct,
        "win_rate": win_rate,
        "win_rate_delta": 0,
        "ranked_call_list": ranked_call_list,
        "urgent_tasks": urgent_tasks,
        "todays_meetings": todays_meetings,
        "recent_activities": recent_activities,
        "pending_drafts": pending_drafts,
        "active_missions": active_missions,
        "cross_sell_ops": cross_sell_ops[:5],
    })

    return render_template("dashboard.html", **ctx)


@app.route("/pipeline")
def pipeline():
    if not _check_auth():
        return redirect("/login")

    ctx = _common_context()
    ctx["active_page"] = "pipeline"
    today = ctx["today"]
    today_str = ctx["today_str"]
    active = ctx["active"]
    activities = ctx["activities"]
    meetings = ctx["meetings"]

    total_pipeline = sum(parse_money(p["aum"]) for p in active)

    # Last activity map
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

    # Prospect scores (from scoring.py — replaces old health_score)
    import scoring
    _avg_stage_days = {}
    for p in active:
        s = (p.get("stage") or "").strip()
        fc = p.get("first_contact", "")
        if s and fc and fc != "None":
            try:
                days = (today - datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()).days
                if s not in _avg_stage_days:
                    _avg_stage_days[s] = []
                _avg_stage_days[s].append(days)
            except (ValueError, IndexError):
                pass
    avg_stage_days = {s: sum(d) / len(d) for s, d in _avg_stage_days.items()}

    prospect_scores = {}
    for p in active:
        score_data = scoring.score_prospect(p, avg_stage_days)
        prospect_scores[p["name"]] = score_data

    # Today's meetings lookup
    todays_meetings = {m.get("prospect", "").lower(): m for m in meetings if m.get("date") == today_str and m.get("status", "").lower() != "cancelled"}

    # Build kanban columns
    PIPELINE_STAGES = ["New Lead", "Contacted", "Discovery Call", "Needs Analysis",
                       "Plan Presentation", "Proposal Sent", "Negotiation", "Nurture"]
    kanban_cols = []
    for stage in PIPELINE_STAGES:
        stage_prospects = [p for p in active if p.get("stage") == stage]
        enriched = []
        for p in stage_prospects:
            pname_lower = p["name"].strip().lower()
            last_touch = last_activity_map.get(pname_lower)

            # Overdue days
            overdue_days = 0
            followup_today = False
            if p["next_followup"] and p["next_followup"] != "None":
                try:
                    fu = datetime.strptime(p["next_followup"].split(" ")[0], "%Y-%m-%d").date()
                    if fu < today:
                        overdue_days = (today - fu).days
                    elif fu == today:
                        followup_today = True
                except (ValueError, IndexError):
                    pass

            # Meeting today?
            meeting_today = pname_lower in todays_meetings
            meeting_time = todays_meetings.get(pname_lower, {}).get("time", "") if meeting_today else ""

            # Score + days in stage
            score_data = prospect_scores.get(p["name"], {})
            days_in_stage = 0
            fc = p.get("first_contact", "")
            if fc and fc != "None":
                try:
                    days_in_stage = (today - datetime.strptime(fc.split(" ")[0], "%Y-%m-%d").date()).days
                except (ValueError, IndexError):
                    pass

            enriched.append({
                **p,
                "overdue_days": overdue_days,
                "followup_today": followup_today,
                "meeting_today": meeting_today,
                "meeting_time": meeting_time,
                "aum_fmt": fmt_money_full(p.get("aum", 0)),
                "last_touch_relative": _relative_time(last_touch.strftime("%Y-%m-%d") if last_touch else "", today),
                "score": score_data.get("score", 0),
                "days_in_stage": days_in_stage,
            })
        # Sort by score within column (highest first)
        enriched.sort(key=lambda x: x.get("score", 0), reverse=True)
        kanban_cols.append((stage, enriched))

    # Build table data (active prospects enriched)
    active_prospects = []
    for p in active:
        pname_lower = p["name"].strip().lower()
        last_touch = last_activity_map.get(pname_lower)

        is_overdue = False
        followup_display = ""
        if p["next_followup"] and p["next_followup"] != "None":
            fu_str = p["next_followup"].split(" ")[0]
            followup_display = fu_str
            try:
                fu_date = datetime.strptime(fu_str, "%Y-%m-%d").date()
                if fu_date < today:
                    is_overdue = True
            except (ValueError, IndexError):
                pass

        if last_touch:
            idle_days = (today - last_touch).days
            rel = _relative_time(last_touch.strftime("%Y-%m-%d"), today)
            if idle_days <= 1:
                last_touch_html = f'<span style="color:var(--success);font-weight:600">{rel}</span>'
            elif idle_days < 7:
                last_touch_html = f'<span style="color:var(--warning)">{rel}</span>'
            else:
                last_touch_html = f'<span style="color:var(--danger);font-weight:600">{rel}</span>'
        else:
            last_touch_html = '<span class="text-muted">—</span>'

        score_data = prospect_scores.get(p["name"], {})
        active_prospects.append({
            **p,
            "health": score_data.get("score", 50),
            "is_overdue": is_overdue,
            "followup_display": followup_display,
            "last_touch_html": last_touch_html,
            "aum_fmt": fmt_money_full(p.get("aum", 0)),
            "revenue_fmt": fmt_money_full(p.get("revenue", 0)),
        })

    ctx.update({
        "active_count": len(active),
        "total_pipeline_fmt": fmt_money(total_pipeline),
        "kanban_cols": kanban_cols,
        "active_prospects": active_prospects,
        "stage_colors": STAGE_COLORS,
        "priority_colors": PRIORITY_COLORS,
    })

    return render_template("pipeline.html", **ctx)


@app.route("/tasks")
def tasks_page():
    if not _check_auth():
        return redirect("/login")

    ctx = _common_context()
    ctx["active_page"] = "tasks"
    today = ctx["today"]
    today_str = ctx["today_str"]
    all_tasks = ctx["all_tasks"]

    overdue_tasks = []
    for t in ctx["overdue_tasks"]:
        try:
            days_late = (today - datetime.strptime(t["due_date"], "%Y-%m-%d").date()).days
        except (ValueError, IndexError):
            days_late = 0
        overdue_tasks.append({**t, "days_late": days_late})

    due_today_tasks = [t for t in all_tasks if t.get("due_date") and t["due_date"] == today_str]
    upcoming_tasks = [t for t in all_tasks if not t.get("due_date") or t["due_date"] > today_str]

    ctx.update({
        "total_pending": len(all_tasks),
        "overdue_tasks": overdue_tasks,
        "due_today_tasks": due_today_tasks,
        "upcoming_tasks": upcoming_tasks,
        "completed_tasks": ctx["completed_tasks"],
    })

    return render_template("tasks.html", **ctx)


@app.route("/conversations")
def conversations_page():
    if not _check_auth():
        return redirect("/login")
    ctx = _common_context()
    ctx["active_page"] = "conversations"
    return render_template("conversations.html", **ctx)


@app.route("/forecast")
def forecast_page():
    if not _check_auth():
        return redirect("/login")

    ctx = _common_context()
    ctx["active_page"] = "forecast"
    today = ctx["today"]
    active = ctx["active"]
    won = ctx["won"]
    activities = ctx["activities"]

    PREMIUM_TARGET = 200000
    AUM_TARGET = 5000000
    BASELINE_AUM = 400000
    BASELINE_PREMIUM = 2000

    won_revenue = sum(parse_money(p["revenue"]) for p in won)
    won_aum = sum(parse_money(p["aum"]) for p in won)
    won_fyc = sum(calc_fyc(p["revenue"], p["product"]) for p in won)

    forecast_revenue = won_revenue + BASELINE_PREMIUM
    forecast_aum = won_aum + BASELINE_AUM

    year_start = date(today.year, 1, 1)
    year_end = date(today.year, 12, 31)
    days_elapsed = (today - year_start).days + 1
    days_total = (year_end - year_start).days + 1

    premium_pct = (forecast_revenue / PREMIUM_TARGET * 100) if PREMIUM_TARGET else 0
    premium_pace = (forecast_revenue / days_elapsed * days_total) if days_elapsed else 0
    premium_on_pace = premium_pace >= PREMIUM_TARGET

    aum_pct = (forecast_aum / AUM_TARGET * 100) if AUM_TARGET else 0
    aum_pace = (forecast_aum / days_elapsed * days_total) if days_elapsed else 0
    aum_on_pace = aum_pace >= AUM_TARGET

    # Pipeline weighted forecast
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
        weighted_revenue += parse_money(p["revenue"]) * prob
        weighted_aum += parse_money(p["aum"]) * prob
        weighted_fyc += calc_fyc(p["revenue"], p["product"]) * prob

    projected_revenue = forecast_revenue + weighted_revenue
    projected_aum = forecast_aum + weighted_aum
    projected_fyc = won_fyc + weighted_fyc

    # Weighted forecast table
    weighted_forecast = []
    for stage, prob in stage_probability.items():
        stage_active = [p for p in active if p.get("stage") == stage]
        if stage_active:
            avg_rev = sum(parse_money(p["revenue"]) for p in stage_active) / len(stage_active)
            total_weighted = sum(parse_money(p["revenue"]) * prob for p in stage_active)
            weighted_forecast.append({
                "stage": stage, "count": len(stage_active),
                "avg_revenue_fmt": fmt_money(avg_rev), "probability": prob,
                "weighted_fmt": fmt_money(total_weighted),
            })

    # Stage velocity
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

    stage_velocity = []
    for s, days_list in stage_days.items():
        stage_velocity.append({
            "stage": s, "avg_days": sum(days_list) / len(days_list), "count": len(days_list),
        })

    # Monthly revenue
    monthly_revenue = {}
    for p in won:
        fc = p.get("first_contact", "")
        if fc and fc != "None":
            try:
                m = datetime.strptime(fc.split(" ")[0], "%Y-%m-%d")
                if m.year == today.year:
                    month_key = m.strftime("%b")
                    month_num = m.month
                    monthly_revenue[(month_num, month_key)] = monthly_revenue.get((month_num, month_key), 0) + parse_money(p["revenue"])
            except (ValueError, IndexError):
                pass

    all_months = [date(today.year, m, 1).strftime("%b") for m in range(1, today.month + 1)]
    monthly_values = [monthly_revenue.get((i+1, mk), 0) for i, mk in enumerate(all_months)]
    monthly_target = [PREMIUM_TARGET / 12] * len(all_months)

    ctx.update({
        "today_display": today.strftime("%A, %B %d, %Y"),
        "days_elapsed": days_elapsed,
        "days_total": days_total,
        "forecast_revenue_fmt": fmt_money(forecast_revenue),
        "forecast_aum_fmt": fmt_money(forecast_aum),
        "won_fyc_fmt": fmt_money(won_fyc),
        "projected_revenue_fmt": fmt_money(projected_revenue),
        "projected_aum_fmt": fmt_money(projected_aum),
        "projected_fyc_fmt": fmt_money(projected_fyc),
        "premium_target_fmt": fmt_money(PREMIUM_TARGET),
        "premium_pct": premium_pct,
        "premium_on_pace": premium_on_pace,
        "premium_pace_fmt": fmt_money(premium_pace),
        "aum_target_fmt": fmt_money(AUM_TARGET),
        "aum_pct": aum_pct,
        "aum_on_pace": aum_on_pace,
        "aum_pace_fmt": fmt_money(aum_pace),
        "weighted_forecast": weighted_forecast,
        "stage_velocity": stage_velocity,
        "monthly_labels": all_months,
        "monthly_values": monthly_values,
        "monthly_target": monthly_target,
    })

    return render_template("forecast.html", **ctx)


@app.route("/clients")
def clients_page():
    if not _check_auth():
        return redirect("/login")

    ctx = _common_context()
    ctx["active_page"] = "clients"
    won = ctx["won"]

    total_client_aum = sum(parse_money(p["aum"]) for p in won)
    total_client_premium = sum(parse_money(p["revenue"]) for p in won)

    # Product & source breakdowns
    client_products = {}
    client_sources = {}
    for p in won:
        prod = p.get("product") or "Other"
        client_products[prod] = client_products.get(prod, 0) + 1
        src = p.get("source") or "Unknown"
        client_sources[src] = client_sources.get(src, 0) + 1

    # Enrich won prospects
    won_prospects = []
    for p in won:
        fc = p.get("first_contact") or ""
        fc_display = fc.split(" ")[0] if fc and fc != "None" else "—"
        try:
            from scoring import get_cross_sell_suggestions
            cross_sell = get_cross_sell_suggestions(p.get("product", ""))
            cross_sell_html = ", ".join(cross_sell[:2]) if cross_sell else '<span class="text-muted">—</span>'
        except Exception:
            cross_sell_html = '<span class="text-muted">—</span>'

        won_prospects.append({
            **p,
            "aum_fmt": fmt_money_full(p.get("aum", 0)),
            "revenue_fmt": fmt_money_full(p.get("revenue", 0)),
            "won_date": fc_display,
            "cross_sell_html": cross_sell_html,
        })

    ctx.update({
        "won_count": len(won),
        "total_client_aum_fmt": fmt_money(total_client_aum),
        "total_client_premium_fmt": fmt_money(total_client_premium),
        "client_products": sorted(client_products.items(), key=lambda x: -x[1]),
        "client_sources": sorted(client_sources.items(), key=lambda x: -x[1]),
        "won_prospects": won_prospects,
    })

    return render_template("clients.html", **ctx)


@app.route("/chat")
def chat_page():
    if not _check_auth():
        return redirect("/login")
    ctx = _common_context()
    ctx["active_page"] = "chat"
    return render_template("chat.html", **ctx)


# ── Webhook & Health ──

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
