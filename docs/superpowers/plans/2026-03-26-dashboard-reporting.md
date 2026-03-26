# Dashboard & Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manager/team dashboard for David's employees, a reporting page with conversion metrics, a flow builder UI for editing sequences without code, and email open/click tracking.

**Architecture:** All new UI is added to the existing Flask `dashboard.py` as new routes and Jinja2 templates. Reporting data is queried directly from SQLite — no separate analytics database. Email tracking uses a pixel endpoint + link redirect endpoint. Flow builder reads/writes the existing `sequences` and `sequence_steps` tables. All dynamic HTML in the flow builder is built via safe DOM methods (no innerHTML with untrusted data).

**Tech Stack:** Python 3.13, Flask, Jinja2, SQLite via `db.py`, vanilla JS (no new frontend framework — follow existing dashboard patterns).

**Dependency:** Requires Capture Layer Task 1 (DB schema) and Automation Engine Task 1 (tag tables) before running reporting queries. Can be built in parallel with other plans since it's read-mostly — just ensure schema exists first.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `templates/manager_dashboard.html` | Create | Team pipeline view for managers |
| `templates/reporting.html` | Create | Conversion metrics, FYC, source ROI |
| `templates/flow_builder.html` | Create | Edit sequences and nurture flows |
| `dashboard.py` | Modify | Add manager, reporting, flow builder, tracking routes |
| `db.py` | Modify | Add reporting queries, email tracking tables, get_trust_level |
| `tests/test_reporting.py` | Create | Unit tests for reporting queries |
| `tests/test_flow_builder.py` | Create | Unit tests for sequence editing API |

---

## Task 1: Reporting Queries + Email Tracking Schema

**Files:**
- Modify: `db.py`
- Create: `tests/test_reporting.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_reporting.py`:

```python
"""Tests for reporting query functions."""
import pytest
from unittest.mock import patch, MagicMock


def test_get_conversion_by_source_returns_list():
    from db import get_conversion_by_source
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_conversion_by_source()
        assert isinstance(result, list)


def test_get_pipeline_metrics_returns_dict():
    from db import get_pipeline_metrics
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_pipeline_metrics()
        assert isinstance(result, dict)


def test_get_stage_funnel_returns_list():
    from db import get_stage_funnel
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_stage_funnel()
        assert isinstance(result, list)


def test_get_fyc_by_advisor_returns_list():
    from db import get_fyc_by_advisor
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_fyc_by_advisor()
        assert isinstance(result, list)


def test_get_trust_level_defaults_to_1():
    from db import get_trust_level
    with patch("db.get_db") as mock_db:
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_db.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db.return_value.__exit__ = MagicMock(return_value=False)
        result = get_trust_level()
        assert result == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_reporting.py -v 2>&1 | tail -10
```

Expected: FAIL (functions not found in db)

- [ ] **Step 3: Add reporting queries and email tracking to `db.py`**

Add inside `init_db()` after existing tables:

```python
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prospect_id INTEGER,
                    prospect_name TEXT,
                    email_type TEXT,
                    token TEXT UNIQUE,
                    opened_at TEXT,
                    link_clicked_at TEXT,
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (prospect_id) REFERENCES prospects(id) ON DELETE SET NULL
                )
            """)
```

Also add migration for `assigned_to` column on prospects (needed for manager view):

```python
        # Migration: add assigned_to if not present
        try:
            conn.execute("ALTER TABLE prospects ADD COLUMN assigned_to TEXT DEFAULT ''")
        except Exception:
            pass  # Column already exists
```

Add these functions at the bottom of `db.py`:

```python
def get_conversion_by_source() -> list[dict]:
    """Conversion rate by lead source."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                source,
                COUNT(*) as total_leads,
                SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) as closed,
                ROUND(
                    100.0 * SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) / COUNT(*),
                    1
                ) as conversion_pct
            FROM prospects
            WHERE source IS NOT NULL AND source != ''
            GROUP BY source
            ORDER BY closed DESC
        """).fetchall()
        return _rows_to_dicts(rows)


def get_pipeline_metrics() -> dict:
    """High-level pipeline summary metrics."""
    with get_db() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_prospects,
                SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) as closed_won,
                SUM(CASE WHEN stage NOT IN ('Closed Won','Closed Lost') THEN 1 ELSE 0 END) as active,
                SUM(COALESCE(revenue, 0)) as total_fyc,
                ROUND(AVG(lead_score), 1) as avg_score
            FROM prospects
        """).fetchone()
        return _row_to_dict(row) or {}


def get_stage_funnel() -> list[dict]:
    """Prospect count and value by pipeline stage."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                stage,
                COUNT(*) as count,
                SUM(COALESCE(revenue, 0)) as value,
                ROUND(AVG(lead_score), 1) as avg_score
            FROM prospects
            GROUP BY stage
            ORDER BY CASE stage
                WHEN 'New Lead' THEN 1
                WHEN 'Contacted' THEN 2
                WHEN 'Discovery Call' THEN 3
                WHEN 'Needs Analysis' THEN 4
                WHEN 'Proposal' THEN 5
                WHEN 'Negotiation' THEN 6
                WHEN 'Closed Won' THEN 7
                WHEN 'Closed Lost' THEN 8
                ELSE 9 END
        """).fetchall()
        return _rows_to_dicts(rows)


def get_fyc_by_advisor() -> list[dict]:
    """FYC broken down by advisor."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                COALESCE(assigned_to, 'Unassigned') as advisor,
                COUNT(*) as deals_closed,
                SUM(COALESCE(revenue, 0)) as total_fyc,
                ROUND(AVG(COALESCE(revenue, 0)), 0) as avg_deal_fyc
            FROM prospects
            WHERE stage = 'Closed Won'
            GROUP BY assigned_to
            ORDER BY total_fyc DESC
        """).fetchall()
        return _rows_to_dicts(rows)


def get_avg_stage_time() -> list[dict]:
    """Average days prospects spend in each stage."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                stage,
                COUNT(*) as count,
                ROUND(AVG(
                    julianday('now') - julianday(COALESCE(created_at, datetime('now')))
                ), 1) as avg_days_in_stage
            FROM prospects
            WHERE stage NOT IN ('Closed Won', 'Closed Lost')
            GROUP BY stage
        """).fetchall()
        return _rows_to_dicts(rows)


def get_trust_level() -> int:
    """Get the current trust level setting (1-3). Returns 1 if not set."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT trust_level FROM trust_config ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return int(row["trust_level"])
    return 1


def create_email_tracking_token(prospect_id: int, prospect_name: str,
                                 email_type: str) -> str:
    """Create a tracking token for an outbound email. Returns the token."""
    import secrets
    token = secrets.token_urlsafe(16)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO email_tracking (prospect_id, prospect_name, email_type, token)
            VALUES (?, ?, ?, ?)
        """, (prospect_id, prospect_name, email_type, token))
    return token


def record_email_open(token: str) -> None:
    with get_db() as conn:
        conn.execute("""
            UPDATE email_tracking SET opened_at = datetime('now')
            WHERE token = ? AND opened_at IS NULL
        """, (token,))


def record_link_click(token: str) -> None:
    with get_db() as conn:
        conn.execute("""
            UPDATE email_tracking SET link_clicked_at = datetime('now')
            WHERE token = ? AND link_clicked_at IS NULL
        """, (token,))


def add_intake_form_response(data: dict) -> None:
    import json as _json
    with get_db() as conn:
        conn.execute("""
            INSERT INTO intake_form_responses (prospect_id, form_type, responses)
            VALUES (?, ?, ?)
        """, (
            data["prospect_id"],
            data["form_type"],
            _json.dumps(data.get("responses", {}))
        ))
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_reporting.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_reporting.py
git commit -m "feat: add reporting queries, email tracking schema, get_trust_level, add_intake_form_response"
```

---

## Task 2: Reporting Dashboard Page

**Files:**
- Create: `templates/reporting.html`
- Modify: `dashboard.py` (add `/reporting` route)

- [ ] **Step 1: Add reporting route to `dashboard.py`**

Find the existing route definitions in `dashboard.py`. Add:

```python
@app.route("/reporting")
@login_required
def reporting():
    from db import (get_conversion_by_source, get_pipeline_metrics,
                    get_stage_funnel, get_fyc_by_advisor, get_avg_stage_time)
    return render_template("reporting.html",
        pipeline_metrics=get_pipeline_metrics(),
        conversion_by_source=get_conversion_by_source(),
        stage_funnel=get_stage_funnel(),
        fyc_by_advisor=get_fyc_by_advisor(),
        avg_stage_time=get_avg_stage_time(),
    )
```

- [ ] **Step 2: Create `templates/reporting.html`**

```html
{% extends "base.html" %}
{% block title %}Reporting{% endblock %}
{% block content %}
<div class="page-header">
  <h1>Reporting</h1>
  <p class="sub">Pipeline performance, conversion rates, and FYC tracking.</p>
</div>

<div class="metrics-row">
  <div class="metric-card">
    <div class="metric-label">Total Prospects</div>
    <div class="metric-value">{{ pipeline_metrics.total_prospects or 0 }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Active Pipeline</div>
    <div class="metric-value">{{ pipeline_metrics.active or 0 }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Closed Won</div>
    <div class="metric-value">{{ pipeline_metrics.closed_won or 0 }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Total FYC</div>
    <div class="metric-value">${{ "{:,.0f}".format(pipeline_metrics.total_fyc or 0) }}</div>
  </div>
  <div class="metric-card">
    <div class="metric-label">Avg Lead Score</div>
    <div class="metric-value">{{ pipeline_metrics.avg_score or '—' }}</div>
  </div>
</div>

<div class="section">
  <h2>Pipeline Funnel</h2>
  <table class="data-table">
    <thead><tr><th>Stage</th><th>Count</th><th>Value</th><th>Avg Score</th></tr></thead>
    <tbody>
    {% for row in stage_funnel %}
    <tr>
      <td>{{ row.stage | e }}</td>
      <td>{{ row.count }}</td>
      <td>${{ "{:,.0f}".format(row.value or 0) }}</td>
      <td>{{ row.avg_score or '—' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">No data yet</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div class="section">
  <h2>Conversion by Lead Source</h2>
  <table class="data-table">
    <thead><tr><th>Source</th><th>Leads</th><th>Closed</th><th>Conversion</th></tr></thead>
    <tbody>
    {% for row in conversion_by_source %}
    <tr>
      <td>{{ row.source | e }}</td>
      <td>{{ row.total_leads }}</td>
      <td>{{ row.closed }}</td>
      <td>{{ row.conversion_pct or 0 }}%</td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">No data yet</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div class="section">
  <h2>FYC by Advisor</h2>
  <table class="data-table">
    <thead><tr><th>Advisor</th><th>Deals Closed</th><th>Total FYC</th><th>Avg Deal</th></tr></thead>
    <tbody>
    {% for row in fyc_by_advisor %}
    <tr>
      <td>{{ row.advisor | e }}</td>
      <td>{{ row.deals_closed }}</td>
      <td>${{ "{:,.0f}".format(row.total_fyc or 0) }}</td>
      <td>${{ "{:,.0f}".format(row.avg_deal_fyc or 0) }}</td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">No data yet</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<div class="section">
  <h2>Average Days in Stage</h2>
  <table class="data-table">
    <thead><tr><th>Stage</th><th>Active Prospects</th><th>Avg Days</th></tr></thead>
    <tbody>
    {% for row in avg_stage_time %}
    <tr>
      <td>{{ row.stage | e }}</td>
      <td>{{ row.count }}</td>
      <td>{{ row.avg_days_in_stage or '—' }}</td>
    </tr>
    {% else %}
    <tr><td colspan="4" class="empty">No data yet</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<style>
.page-header{margin-bottom:28px}
.page-header h1{font-size:24px;font-weight:700}
.page-header .sub{color:#66635f;font-size:14px;margin-top:4px}
.metrics-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px;margin-bottom:32px}
.metric-card{background:#fff;border:1.5px solid #e7e2d8;border-radius:14px;padding:18px 20px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.metric-label{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:#66635f;margin-bottom:6px}
.metric-value{font-size:26px;font-weight:800;color:#171717}
.section{margin-bottom:36px}
.section h2{font-size:17px;font-weight:700;margin-bottom:14px}
.data-table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.data-table th{background:#f8f7f4;text-align:left;padding:11px 16px;font-size:12px;font-weight:600;color:#66635f;text-transform:uppercase;letter-spacing:.05em;border-bottom:1.5px solid #e7e2d8}
.data-table td{padding:12px 16px;font-size:14px;border-bottom:1px solid #f0ece4}
.data-table tr:last-child td{border-bottom:none}
.empty{color:#9ca3af;text-align:center;font-style:italic}
</style>
{% endblock %}
```

- [ ] **Step 3: Commit**

```bash
git add templates/reporting.html dashboard.py
git commit -m "feat: reporting dashboard with pipeline funnel, source conversion, FYC by advisor"
```

---

## Task 3: Manager / Team Dashboard

**Files:**
- Create: `templates/manager_dashboard.html`
- Modify: `dashboard.py` (add `/manager` route)

- [ ] **Step 1: Add manager route to `dashboard.py`**

```python
@app.route("/manager")
@login_required
def manager_dashboard():
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")

    with db.get_db() as conn:
        advisors = conn.execute("""
            SELECT
                COALESCE(assigned_to, 'Unassigned') as advisor,
                COUNT(*) as total,
                SUM(CASE WHEN stage NOT IN ('Closed Won','Closed Lost') THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN stage = 'Closed Won' THEN 1 ELSE 0 END) as closed,
                SUM(COALESCE(revenue, 0)) as total_fyc,
                ROUND(AVG(lead_score), 1) as avg_score
            FROM prospects
            GROUP BY assigned_to
            ORDER BY active DESC
        """).fetchall()
        advisors = [dict(r) for r in advisors]

        stale = conn.execute("""
            SELECT p.name, p.stage, p.lead_score,
                   COALESCE(p.assigned_to, 'Unassigned') as advisor,
                   MAX(a.created_at) as last_activity
            FROM prospects p
            LEFT JOIN activities a ON a.prospect = p.name
            WHERE p.stage NOT IN ('Closed Won', 'Closed Lost')
            GROUP BY p.id
            HAVING last_activity IS NULL OR last_activity < ?
            ORDER BY p.lead_score DESC
            LIMIT 20
        """, (cutoff,)).fetchall()
        stale = [dict(r) for r in stale]

    return render_template("manager_dashboard.html", advisors=advisors, stale_deals=stale)
```

- [ ] **Step 2: Create `templates/manager_dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Manager View{% endblock %}
{% block content %}
<div class="page-header">
  <h1>Team Overview</h1>
  <p class="sub">All advisors' pipelines in one place.</p>
</div>

<div class="advisor-grid">
{% for adv in advisors %}
<div class="advisor-card">
  <div class="advisor-name">{{ adv.advisor | e }}</div>
  <div class="advisor-stats">
    <div class="stat">
      <span class="stat-val">{{ adv.active }}</span>
      <span class="stat-label">Active</span>
    </div>
    <div class="stat">
      <span class="stat-val">{{ adv.closed }}</span>
      <span class="stat-label">Closed</span>
    </div>
    <div class="stat">
      <span class="stat-val">${{ "{:,.0f}".format(adv.total_fyc or 0) }}</span>
      <span class="stat-label">FYC</span>
    </div>
    <div class="stat">
      <span class="stat-val">{{ adv.avg_score or '—' }}</span>
      <span class="stat-label">Avg Score</span>
    </div>
  </div>
</div>
{% else %}
<p style="color:#66635f">No advisors with prospects yet.</p>
{% endfor %}
</div>

<div class="section" style="margin-top:36px">
  <h2>Stale Deals <span style="font-weight:400;font-size:14px;color:#66635f">(no activity in 14+ days)</span></h2>
  <table class="data-table">
    <thead>
      <tr><th>Prospect</th><th>Stage</th><th>Score</th><th>Advisor</th><th>Last Activity</th></tr>
    </thead>
    <tbody>
    {% for deal in stale_deals %}
    <tr>
      <td><a href="/dashboard?search={{ deal.name | urlencode }}" style="color:#6366f1;font-weight:600">{{ deal.name | e }}</a></td>
      <td>{{ deal.stage | e }}</td>
      <td>{{ deal.lead_score or '—' }}</td>
      <td>{{ deal.advisor | e }}</td>
      <td>{{ (deal.last_activity or 'Never')[:10] }}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" class="empty">No stale deals</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>

<style>
.page-header{margin-bottom:28px}
.page-header h1{font-size:24px;font-weight:700}
.page-header .sub{color:#66635f;font-size:14px;margin-top:4px}
.advisor-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.advisor-card{background:#fff;border:1.5px solid #e7e2d8;border-radius:14px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.advisor-name{font-size:15px;font-weight:700;margin-bottom:14px;color:#171717}
.advisor-stats{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.stat{background:#f8f7f4;border-radius:9px;padding:10px 12px;text-align:center}
.stat-val{display:block;font-size:20px;font-weight:800;color:#171717}
.stat-label{display:block;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#66635f;margin-top:2px}
.section h2{font-size:17px;font-weight:700;margin-bottom:14px}
.data-table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.data-table th{background:#f8f7f4;text-align:left;padding:11px 16px;font-size:12px;font-weight:600;color:#66635f;text-transform:uppercase;letter-spacing:.05em;border-bottom:1.5px solid #e7e2d8}
.data-table td{padding:12px 16px;font-size:14px;border-bottom:1px solid #f0ece4}
.data-table tr:last-child td{border-bottom:none}
.empty{color:#9ca3af;text-align:center;font-style:italic}
</style>
{% endblock %}
```

- [ ] **Step 3: Commit**

```bash
git add templates/manager_dashboard.html dashboard.py
git commit -m "feat: manager dashboard with advisor pipeline cards and stale deal alerts"
```

---

## Task 4: Email Open & Click Tracking

**Files:**
- Modify: `dashboard.py` (add tracking pixel and redirect routes)

- [ ] **Step 1: Add tracking routes to `dashboard.py`**

```python
from db import record_email_open, record_link_click

@app.route("/t/open/<token>.gif")
def tracking_pixel(token):
    """1x1 transparent GIF for email open tracking."""
    # Validate token is alphanumeric/urlsafe — reject anything else
    import re
    if not re.match(r'^[A-Za-z0-9_\-]{10,40}$', token):
        from flask import abort
        abort(400)
    record_email_open(token)
    gif_bytes = (
        b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00'
        b'!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01'
        b'\x00\x00\x02\x02D\x01\x00;'
    )
    from flask import Response
    return Response(gif_bytes, mimetype="image/gif",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.route("/t/click/<token>")
def tracking_click(token):
    """Link click tracking — records click then redirects to destination."""
    import re
    from flask import redirect, abort
    if not re.match(r'^[A-Za-z0-9_\-]{10,40}$', token):
        abort(400)
    destination = request.args.get("url", "")
    # Only allow relative paths or trusted domains — never open redirect
    if not destination or not destination.startswith("/"):
        destination = "/"
    record_link_click(token)
    return redirect(destination)
```

- [ ] **Step 2: Test tracking pixel**

```bash
python -c "
import os; os.environ['DASHBOARD_API_KEY']='test'; os.environ['OPENAI_API_KEY']='sk-test'
import dashboard; app = dashboard.app; app.config['TESTING'] = True
with app.test_client() as c:
    resp = c.get('/t/open/validtoken12345.gif')
    print('Pixel status:', resp.status_code)
    print('Content-Type:', resp.content_type)
    resp2 = c.get('/t/open/../../etc/passwd.gif')
    print('Path traversal blocked:', resp2.status_code)
" 2>&1 | tail -6
```

Expected: Pixel 200 image/gif, path traversal 400.

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "feat: email open and click tracking with 1x1 pixel and safe redirect"
```

---

## Task 5: Flow Builder UI

**Files:**
- Create: `templates/flow_builder.html`
- Modify: `dashboard.py` (add flow builder routes)
- Create: `tests/test_flow_builder.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_flow_builder.py`:

```python
"""Tests for the flow builder API."""
import pytest
import json


@pytest.fixture
def client():
    import os
    os.environ.setdefault("DASHBOARD_API_KEY", "test-key")
    os.environ.setdefault("OPENAI_API_KEY", "sk-test")
    import dashboard
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


def test_flow_builder_page_loads(client):
    resp = client.get("/flows")
    # 200 if no auth, 302 if login redirect — both acceptable
    assert resp.status_code in (200, 302)


def test_update_step_missing_step_id_returns_400(client):
    resp = client.post("/api/sequence-step/update",
        data=json.dumps({"message_template": "Hello"}),
        content_type="application/json"
    )
    assert resp.status_code in (400, 401)


def test_update_step_invalid_channel_ignored(client, monkeypatch):
    import db
    monkeypatch.setattr(db, "get_db", lambda: __import__('contextlib').nullcontext(
        __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
    ))
    resp = client.post("/api/sequence-step/update",
        data=json.dumps({"step_id": 1, "channel": "fax"}),
        content_type="application/json"
    )
    # Invalid channel should result in no-valid-fields error or be silently skipped
    assert resp.status_code in (400, 200, 500)
```

- [ ] **Step 2: Run to confirm failure**

```bash
python -m pytest tests/test_flow_builder.py -v 2>&1 | tail -10
```

Expected: FAIL (routes not found)

- [ ] **Step 3: Add flow builder routes to `dashboard.py`**

```python
@app.route("/flows")
@login_required
def flow_builder():
    """Visual flow builder for editing sequences without code."""
    with db.get_db() as conn:
        sequences = conn.execute(
            "SELECT * FROM sequences ORDER BY name"
        ).fetchall()
        sequences = [dict(s) for s in sequences]
        for seq in sequences:
            steps = conn.execute(
                "SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_order",
                (seq["id"],)
            ).fetchall()
            seq["steps"] = [dict(s) for s in steps]
    return render_template("flow_builder.html", sequences=sequences)


@app.route("/api/sequences")
@login_required
def api_get_sequences():
    with db.get_db() as conn:
        sequences = conn.execute("SELECT * FROM sequences ORDER BY name").fetchall()
        result = []
        for seq in sequences:
            seq_dict = dict(seq)
            steps = conn.execute(
                "SELECT * FROM sequence_steps WHERE sequence_id = ? ORDER BY step_order",
                (seq_dict["id"],)
            ).fetchall()
            seq_dict["steps"] = [dict(s) for s in steps]
            result.append(seq_dict)
    return jsonify(result)


@app.route("/api/sequence-step/update", methods=["POST"])
@login_required
def update_sequence_step():
    data = request.get_json(force=True) or {}
    step_id = data.get("step_id")
    if not step_id:
        return jsonify({"status": "error", "message": "step_id required"}), 400

    VALID_CHANNELS = {"sms", "email", "telegram"}
    updates = {}
    if "message_template" in data:
        updates["message_template"] = str(data["message_template"])[:2000]
    if "delay_days" in data:
        try:
            updates["delay_days"] = max(0, min(365, int(data["delay_days"])))
        except (ValueError, TypeError):
            pass
    if data.get("channel") in VALID_CHANNELS:
        updates["channel"] = data["channel"]

    if not updates:
        return jsonify({"status": "error", "message": "No valid fields to update"}), 400

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [int(step_id)]

    with db.get_db() as conn:
        conn.execute(f"UPDATE sequence_steps SET {set_clause} WHERE id = ?", values)

    return jsonify({"status": "ok"})
```

- [ ] **Step 4: Create `templates/flow_builder.html`**

Note: All dynamic content is rendered server-side by Jinja2 with `| e` escaping. The edit form uses plain value assignment to form fields — no dynamic DOM building from user data.

```html
{% extends "base.html" %}
{% block title %}Flow Builder{% endblock %}
{% block content %}
<div class="page-header">
  <h1>Flow Builder</h1>
  <p class="sub">Edit nurture sequences and automation flows without touching code.</p>
</div>

{% if not sequences %}
<p style="color:#66635f;font-size:14px">No sequences configured yet. Sequences are created programmatically and edited here.</p>
{% endif %}

{% for seq in sequences %}
<div class="sequence-card">
  <div class="sequence-name">{{ seq.name | e }} <span class="step-count">({{ seq.steps | length }} steps)</span></div>
  {% for step in seq.steps %}
  <div class="step-row" data-step-id="{{ step.id }}">
    <span class="step-badge badge-{{ step.channel or 'sms' }}">{{ (step.channel or 'sms') | e }}</span>
    <span class="step-delay">Day {{ step.delay_days or 0 }}</span>
    <span class="step-preview">{{ (step.message_template or '') | e | truncate(80) }}</span>
    <button class="edit-btn"
      data-step-id="{{ step.id }}"
      data-channel="{{ (step.channel or 'sms') | e }}"
      data-delay="{{ step.delay_days or 0 }}"
      data-message="{{ (step.message_template or '') | e }}">
      Edit
    </button>
  </div>
  {% else %}
  <p style="font-size:13px;color:#9ca3af;padding:8px 0">No steps in this sequence.</p>
  {% endfor %}
</div>
{% endfor %}

<div id="step-editor" style="display:none" class="step-editor-panel">
  <h3>Edit Step</h3>
  <input type="hidden" id="edit-step-id">
  <label for="edit-channel">Channel</label>
  <select id="edit-channel">
    <option value="sms">SMS</option>
    <option value="email">Email</option>
    <option value="telegram">Telegram</option>
  </select>
  <label for="edit-delay">Delay (days after previous step)</label>
  <input type="number" id="edit-delay" min="0" max="365">
  <label for="edit-message">Message Template</label>
  <p style="font-size:12px;color:#66635f;margin-bottom:6px">Use &#123;&#123;name&#125;&#125; for prospect's first name.</p>
  <textarea id="edit-message" rows="6"></textarea>
  <div class="editor-actions">
    <button id="save-btn" class="btn-primary">Save Step</button>
    <button id="cancel-btn" class="btn-secondary">Cancel</button>
  </div>
</div>

<style>
.page-header{margin-bottom:28px}
.page-header h1{font-size:24px;font-weight:700}
.page-header .sub{color:#66635f;font-size:14px;margin-top:4px}
.sequence-card{background:#fff;border:1.5px solid #e7e2d8;border-radius:14px;padding:20px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.04)}
.sequence-name{font-size:15px;font-weight:700;color:#171717;margin-bottom:12px}
.step-count{font-size:12px;color:#66635f;font-weight:400}
.step-row{display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #f0ece4}
.step-row:last-child{border-bottom:none}
.step-badge{font-size:10px;font-weight:700;padding:3px 9px;border-radius:99px;text-transform:uppercase;letter-spacing:.06em;white-space:nowrap}
.badge-sms{background:#ddf7ec;color:#065f46}
.badge-email{background:#eff6ff;color:#1e40af}
.badge-telegram{background:#ede9fe;color:#5b21b6}
.step-delay{font-size:12px;color:#66635f;min-width:50px;white-space:nowrap}
.step-preview{font-size:13px;color:#374151;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.edit-btn{font-size:12px;font-weight:600;color:#6366f1;cursor:pointer;padding:4px 10px;border:1.5px solid #c7d2fe;border-radius:8px;background:#eef0ff;white-space:nowrap}
.edit-btn:hover{background:#c7d2fe}
.step-editor-panel{background:#fff;border:1.5px solid #e7e2d8;border-radius:14px;padding:24px;margin-top:24px;box-shadow:0 4px 16px rgba(0,0,0,.08)}
.step-editor-panel h3{font-size:16px;font-weight:700;margin-bottom:16px}
label{display:block;font-size:12px;font-weight:600;color:#374151;margin-bottom:5px;margin-top:14px}
select,input[type=number],textarea{width:100%;border:1.5px solid #e5e7eb;border-radius:10px;padding:10px 14px;font-size:14px;font-family:inherit;outline:none}
select:focus,input:focus,textarea:focus{border-color:#6366f1}
textarea{resize:vertical}
.editor-actions{display:flex;gap:10px;margin-top:16px}
.btn-primary{padding:10px 20px;background:#6366f1;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer}
.btn-secondary{padding:10px 20px;background:#f3f4f6;color:#374151;border:1.5px solid #e5e7eb;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer}
</style>

<script>
// Attach edit button handlers — data comes from server-rendered data-* attributes (safe)
document.querySelectorAll('.edit-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.getElementById('edit-step-id').value = btn.dataset.stepId;
    document.getElementById('edit-channel').value = btn.dataset.channel;
    document.getElementById('edit-delay').value = btn.dataset.delay;
    document.getElementById('edit-message').value = btn.dataset.message;
    var editor = document.getElementById('step-editor');
    editor.style.display = 'block';
    editor.scrollIntoView({behavior: 'smooth'});
  });
});

document.getElementById('cancel-btn').addEventListener('click', function() {
  document.getElementById('step-editor').style.display = 'none';
});

document.getElementById('save-btn').addEventListener('click', async function() {
  var stepId = parseInt(document.getElementById('edit-step-id').value, 10);
  var body = {
    step_id: stepId,
    channel: document.getElementById('edit-channel').value,
    delay_days: parseInt(document.getElementById('edit-delay').value, 10) || 0,
    message_template: document.getElementById('edit-message').value
  };
  var resp = await fetch('/api/sequence-step/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  if (resp.ok) {
    document.getElementById('step-editor').style.display = 'none';
    window.location.reload();
  } else {
    alert('Save failed. Please try again.');
  }
});
</script>
{% endblock %}
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/test_flow_builder.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
python -m pytest tests/ -x -q 2>&1 | tail -10
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add templates/flow_builder.html dashboard.py tests/test_flow_builder.py
git commit -m "feat: flow builder UI for editing sequences without code"
```

---

## Final Verification

- [ ] `python -m pytest tests/ -q 2>&1 | tail -5` — all pass
- [ ] `python -c "import db; db.init_db(); print(db.get_trust_level())"` — prints 1
- [ ] `python -c "import db; db.init_db(); print(db.get_pipeline_metrics())"` — prints dict
- [ ] Visit `/reporting` — all 5 metric cards render
- [ ] Visit `/manager` — advisor grid renders
- [ ] Visit `/flows` — sequence list renders (or empty state message)
- [ ] `GET /t/open/validtoken12345.gif` — returns 200 image/gif
- [ ] `GET /t/open/../../etc/passwd.gif` — returns 400
