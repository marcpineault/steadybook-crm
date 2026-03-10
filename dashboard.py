import os
import threading
from datetime import date, datetime
from pathlib import Path

import json

import openpyxl
from flask import Flask, Response, request, jsonify

DATA_DIR = os.environ.get("DATA_DIR", "")
if DATA_DIR:
    PIPELINE_PATH = os.path.join(DATA_DIR, "pipeline.xlsx")
else:
    PIPELINE_PATH = os.environ.get("PIPELINE_PATH", "pipeline.xlsx")

app = Flask(__name__)

DATA_START = 5
MAX_ROWS = 80

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
    if not Path(PIPELINE_PATH).exists():
        return [], [], [], []

    wb = openpyxl.load_workbook(PIPELINE_PATH, data_only=True)

    # Pipeline
    ws = wb["Pipeline"]
    prospects = []
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        name = ws.cell(row=r, column=1).value
        if not name:
            continue
        prospects.append({
            "name": str(name),
            "phone": str(ws.cell(row=r, column=2).value or ""),
            "email": str(ws.cell(row=r, column=3).value or ""),
            "source": str(ws.cell(row=r, column=4).value or ""),
            "priority": str(ws.cell(row=r, column=5).value or ""),
            "stage": str(ws.cell(row=r, column=6).value or ""),
            "product": str(ws.cell(row=r, column=7).value or ""),
            "aum": ws.cell(row=r, column=8).value or 0,
            "revenue": ws.cell(row=r, column=9).value or 0,
            "first_contact": str(ws.cell(row=r, column=10).value or ""),
            "next_followup": str(ws.cell(row=r, column=11).value or ""),
            "notes": str(ws.cell(row=r, column=13).value or ""),
        })

    # Activity log
    log_ws = wb["Activity Log"]
    activities = []
    for r in range(3, 103):
        d = log_ws.cell(row=r, column=1).value
        if not d:
            continue
        activities.append({
            "date": str(d),
            "prospect": str(log_ws.cell(row=r, column=2).value or ""),
            "action": str(log_ws.cell(row=r, column=3).value or ""),
            "outcome": str(log_ws.cell(row=r, column=4).value or ""),
            "next_step": str(log_ws.cell(row=r, column=5).value or ""),
        })

    # Meetings
    meetings = []
    if "Meetings" in wb.sheetnames:
        ms = wb["Meetings"]
        for r in range(3, 103):
            d = ms.cell(row=r, column=1).value
            if not d:
                continue
            meetings.append({
                "date": str(d).split(" ")[0] if d else "",
                "time": str(ms.cell(row=r, column=2).value or ""),
                "prospect": str(ms.cell(row=r, column=3).value or ""),
                "type": str(ms.cell(row=r, column=4).value or ""),
                "prep_notes": str(ms.cell(row=r, column=5).value or ""),
                "status": str(ms.cell(row=r, column=6).value or "Scheduled"),
            })

    # Insurance Book
    book_entries = []
    if "Insurance Book" in wb.sheetnames:
        bs = wb["Insurance Book"]
        for r in range(3, 203):
            name = bs.cell(row=r, column=1).value
            if not name:
                continue
            book_entries.append({
                "name": str(name),
                "phone": str(bs.cell(row=r, column=2).value or ""),
                "address": str(bs.cell(row=r, column=3).value or ""),
                "policy_start": str(bs.cell(row=r, column=4).value or ""),
                "status": str(bs.cell(row=r, column=5).value or "Not Called"),
                "last_called": str(bs.cell(row=r, column=6).value or ""),
                "notes": str(bs.cell(row=r, column=7).value or ""),
                "retry_date": str(bs.cell(row=r, column=8).value or ""),
            })

    wb.close()
    return prospects, activities, meetings, book_entries


PIPELINE_COLS = {
    "name": 1, "phone": 2, "email": 3, "source": 4,
    "priority": 5, "stage": 6, "product": 7,
    "aum": 8, "revenue": 9, "first_contact": 10,
    "next_followup": 11, "notes": 13,
}


@app.route("/api/prospect", methods=["POST"])
def api_add_prospect():
    data = request.json
    if not data or not data.get("name"):
        return jsonify({"error": "Name required"}), 400

    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    target_row = None
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        if not ws.cell(row=r, column=1).value:
            target_row = r
            break

    if not target_row:
        wb.close()
        return jsonify({"error": "Pipeline full"}), 400

    for field, col in PIPELINE_COLS.items():
        val = data.get(field, "")
        if val:
            ws.cell(row=target_row, column=col, value=val)

    if not data.get("first_contact"):
        ws.cell(row=target_row, column=10, value=date.today().strftime("%Y-%m-%d"))
    if not data.get("stage"):
        ws.cell(row=target_row, column=6, value="New Lead")

    wb.save(PIPELINE_PATH)
    wb.close()
    return jsonify({"ok": True, "row": target_row})


@app.route("/api/prospect/<name>", methods=["PUT"])
def api_update_prospect(name):
    data = request.json
    if not data:
        return jsonify({"error": "No data"}), 400

    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    found_row = None
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        cell_val = ws.cell(row=r, column=1).value
        if cell_val and str(cell_val).strip().lower() == name.strip().lower():
            found_row = r
            break

    if not found_row:
        wb.close()
        return jsonify({"error": f"Prospect '{name}' not found"}), 404

    for field, col in PIPELINE_COLS.items():
        if field in data:
            ws.cell(row=found_row, column=col, value=data[field])

    wb.save(PIPELINE_PATH)
    wb.close()
    return jsonify({"ok": True})


@app.route("/api/prospect/<name>", methods=["DELETE"])
def api_delete_prospect(name):
    wb = openpyxl.load_workbook(PIPELINE_PATH)
    ws = wb["Pipeline"]

    found_row = None
    for r in range(DATA_START, DATA_START + MAX_ROWS):
        cell_val = ws.cell(row=r, column=1).value
        if cell_val and str(cell_val).strip().lower() == name.strip().lower():
            found_row = r
            break

    if not found_row:
        wb.close()
        return jsonify({"error": f"Prospect '{name}' not found"}), 404

    for col in range(1, 14):
        ws.cell(row=found_row, column=col, value=None)

    wb.save(PIPELINE_PATH)
    wb.close()
    return jsonify({"ok": True})


@app.route("/api/prospects")
def api_list_prospects():
    prospects, _, _, _ = read_data()
    return jsonify(prospects)


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
    prospects, activities, meetings, book_entries = read_data()
    today = date.today()

    active = [p for p in prospects if p["stage"] not in ("Closed-Won", "Closed-Lost", "")]
    won = [p for p in prospects if p["stage"] == "Closed-Won"]
    lost = [p for p in prospects if p["stage"] == "Closed-Lost"]

    total_pipeline = sum(float(str(p["aum"]).replace("$","").replace(",","") or 0) for p in active)
    total_revenue = sum(float(str(p["revenue"]).replace("$","").replace(",","") or 0) for p in active)
    won_revenue = sum(float(str(p["revenue"]).replace("$","").replace(",","") or 0) for p in won)
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

    # Stage counts for chart
    stage_counts = {}
    stage_revenue = {}
    for p in prospects:
        s = p["stage"]
        if s:
            stage_counts[s] = stage_counts.get(s, 0) + 1
            try:
                stage_revenue[s] = stage_revenue.get(s, 0) + float(str(p["revenue"]).replace("$","").replace(",","") or 0)
            except ValueError:
                pass

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

        p_json = json.dumps(p).replace("'", "&#39;").replace('"', "&quot;")
        prospect_rows += f"""<tr class="editable-row" onclick='openEdit({p_json})' style="cursor:pointer">
            <td class="name-cell">{p["name"]}</td>
            <td><span class="badge" style="background:{pri_bg}">{p["priority"]}</span></td>
            <td><span class="badge" style="background:{stage_bg};color:{stage_fg}">{p["stage"]}</span></td>
            <td>{p["product"]}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td class="{fu_class}">{fu_display}</td>
            <td class="notes">{p["notes"][:60]}{'...' if len(p["notes"]) > 60 else ''}</td>
        </tr>"""

    # Won deals rows
    won_rows = ""
    for p in won:
        won_rows += f"""<tr>
            <td class="name-cell">{p["name"]}</td>
            <td>{p["product"]}</td>
            <td class="money">{fmt_money_full(p["aum"])}</td>
            <td class="money">{fmt_money_full(p["revenue"])}</td>
            <td>{p["source"]}</td>
        </tr>"""

    # Activity rows (last 10)
    activity_rows = ""
    for a in activities[:10]:
        activity_rows += f"""<tr>
            <td>{a["date"].split(" ")[0]}</td>
            <td>{a["prospect"]}</td>
            <td>{a["action"]}</td>
            <td>{a["outcome"]}</td>
            <td>{a["next_step"]}</td>
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
            <td class="name-cell">{p["name"]}</td>
            <td>{fu}</td>
            <td class="overdue">{days_late} days late</td>
            <td>{p["phone"]}</td>
        </tr>"""

    # Chart data as JSON-like strings for inline JS
    stage_labels = list(stage_counts.keys())
    stage_values = list(stage_counts.values())
    stage_chart_colors = [STAGE_COLORS.get(s, "#BDC3C7") for s in stage_labels]

    source_labels = list(source_counts.keys())
    source_values = list(source_counts.values())

    product_labels = list(product_counts.keys())
    product_values = list(product_counts.values())

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
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

.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}

.empty-state {{ text-align: center; padding: 40px; color: #7f8c8d; }}
.empty-state p {{ margin-top: 8px; font-size: 14px; }}

.refresh-note {{ text-align: center; color: #7f8c8d; font-size: 12px; margin-top: 16px; padding: 12px; }}

.editable-row:hover {{ background: #edf7f6 !important; }}

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
    .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
}}
</style>
</head>
<body>

<div class="header">
    <div>
        <h1>CALM <span>MONEY</span> — Pipeline</h1>
    </div>
    <div class="updated">Updated: {today.strftime('%B %d, %Y at %I:%M %p')}<br>Refresh page for latest data</div>
</div>

<div class="container">

    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">Active Deals</div>
            <div class="kpi-value">{len(active)}</div>
        </div>
        <div class="kpi-card blue">
            <div class="kpi-label">Pipeline Value</div>
            <div class="kpi-value">{fmt_money(total_pipeline)}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">Est. Revenue</div>
            <div class="kpi-value">{fmt_money(total_revenue)}</div>
        </div>
        <div class="kpi-card green">
            <div class="kpi-label">Won Revenue</div>
            <div class="kpi-value">{fmt_money(won_revenue)}</div>
        </div>
        <div class="kpi-card red">
            <div class="kpi-label">Hot Leads</div>
            <div class="kpi-value">{hot_count}</div>
        </div>
        <div class="kpi-card gold">
            <div class="kpi-label">Win Rate</div>
            <div class="kpi-value">{win_rate:.0f}%</div>
        </div>
    </div>

    <div class="chart-grid">
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
        {'<table><tr><th>Prospect</th><th>Priority</th><th>Stage</th><th>Product</th><th>AUM/Premium</th><th>Revenue</th><th>Follow-Up</th><th>Notes</th></tr>' + prospect_rows + '</table>' if active else '<div class="empty-state"><p>No active deals yet. Text your Telegram bot to add prospects.</p></div>'}
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
            {'<table><tr><th>Date</th><th>Time</th><th>Prospect</th><th>Type</th><th>Status</th><th>Prep</th></tr>' + ''.join(f'<tr><td>{m["date"]}</td><td>{m["time"]}</td><td class="name-cell">{m["prospect"]}</td><td>{m["type"]}</td><td><span class="badge" style="background:{"#27ae60" if m["status"]=="Completed" else "#e74c3c" if m["status"]=="Cancelled" else "#3498db"}">{m["status"]}</span></td><td class="notes">{m["prep_notes"][:50]}{"..." if len(m["prep_notes"])>50 else ""}</td></tr>' for m in meetings if m['status'] != 'Cancelled') + '</table>' if meetings else '<div class="empty-state"><p>No meetings scheduled. Text the bot to add one.</p></div>'}
        </div>
        <div class="section">
            <h2>Insurance Book <span class="count">({len(book_entries)} contacts)</span></h2>
            {'<div style="display:flex;gap:24px;margin-bottom:16px"><div class="kpi-card" style="flex:1;padding:12px 16px"><div class="kpi-label">Called</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if b["status"].lower() not in ("not called","")])) + '</div></div><div class="kpi-card green" style="flex:1;padding:12px 16px"><div class="kpi-label">Booked</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if b["status"].lower()=="booked meeting"])) + '</div></div><div class="kpi-card blue" style="flex:1;padding:12px 16px"><div class="kpi-label">Remaining</div><div class="kpi-value" style="font-size:24px">' + str(len([b for b in book_entries if b["status"].lower() in ("not called","")])) + '</div></div></div><table><tr><th>Name</th><th>Phone</th><th>Status</th><th>Last Called</th><th>Notes</th></tr>' + ''.join(f'<tr><td class="name-cell">{b["name"]}</td><td>{b["phone"]}</td><td><span class="badge" style="background:{"#27ae60" if b["status"].lower()=="booked meeting" else "#e74c3c" if b["status"].lower()=="not interested" else "#f39c12" if b["status"].lower() in ("callback","no answer") else "#3498db"}">{b["status"]}</span></td><td>{b["last_called"].split(" ")[0] if b["last_called"] and b["last_called"]!="None" else ""}</td><td class="notes">{b["notes"][:40]}{"..." if len(b["notes"])>40 else ""}</td></tr>' for b in book_entries[:20]) + '</table>' if book_entries else '<div class="empty-state"><p>No insurance book uploaded. Send a CSV via Telegram.</p></div>'}
        </div>
    </div>

    <div class="refresh-note">Click any prospect row to edit. Changes save to your pipeline instantly.</div>

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
        <div><label>AUM / Premium</label><input id="fAum" type="text" placeholder="e.g. 500000"></div>
        <div><label>Revenue</label><input id="fRevenue" type="text" placeholder="e.g. 5000"></div>
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

<script>
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

async function saveProspect() {{
    const data = getFormData();
    if (!data.name) {{ alert('Name is required'); return; }}
    try {{
        let res;
        if (isAdding) {{
            res = await fetch('/api/prospect', {{ method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data) }});
        }} else {{
            const origName = document.getElementById('origName').value;
            res = await fetch('/api/prospect/' + encodeURIComponent(origName), {{ method: 'PUT', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(data) }});
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
        const res = await fetch('/api/prospect/' + encodeURIComponent(name), {{ method: 'DELETE' }});
        const result = await res.json();
        if (result.ok) {{ closeModal(); location.reload(); }}
        else alert(result.error || 'Error deleting');
    }} catch(e) {{ alert('Error: ' + e.message); }}
}}

document.getElementById('editModal').addEventListener('click', function(e) {{
    if (e.target === this) closeModal();
}});
</script>

</body>
</html>"""

    return Response(html, mimetype="text/html")


def run_dashboard():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)


def start_dashboard_thread():
    t = threading.Thread(target=run_dashboard, daemon=True)
    t.start()
