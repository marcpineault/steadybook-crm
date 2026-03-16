# System Audit Fixes — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix security vulnerabilities, reduce notification noise, remove dead weight, and add missing intelligence features across the calm-money-bot system.

**Architecture:** Surgical fixes across existing modules — no new modules created. Changes touch dashboard.py (security + cleanup), scheduler.py (noise reduction), bot.py (dead weight removal), db.py (indexes + intelligence), and briefing.py (referral integration).

**Tech Stack:** Python 3.13, Flask, python-telegram-bot, SQLite, GPT-4.1

---

## Chunk 1: Security Fixes

### Task 1: Auth-gate the dashboard route

The `GET /` route at `dashboard.py:558` renders all client PII (names, phone numbers, AUM, notes) with zero authentication. Every `/api/*` route has `@_require_auth` but the page itself is wide open.

**Files:**
- Modify: `dashboard.py:558-560`
- Test: `tests/test_dashboard_security.py` (create)

- [ ] **Step 1: Write failing test — unauthenticated dashboard returns 401**

```python
# tests/test_dashboard_security.py
import os
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("DASHBOARD_API_KEY", "test-secret-key")

import pytest
from dashboard import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_dashboard_requires_auth(client):
    """GET / without credentials returns 401."""
    resp = client.get("/")
    assert resp.status_code == 401


def test_dashboard_accessible_with_api_key(client):
    """GET / with valid API key returns 200."""
    resp = client.get("/", headers={"X-API-Key": "test-secret-key"})
    assert resp.status_code == 200


def test_dashboard_accessible_with_query_key(client):
    """GET / with valid query param key returns 200."""
    resp = client.get("/?key=test-secret-key")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard_security.py -v`
Expected: FAIL — `test_dashboard_requires_auth` fails because GET / returns 200

- [ ] **Step 3: Add auth to dashboard route**

In `dashboard.py`, add `@_require_auth` decorator to the dashboard function. But since this is a page (not API), we need to return an HTML login prompt instead of JSON 401. The simplest approach: add a query param `?key=` check or reuse the existing `_require_auth` which checks `X-API-Key` header. For browser access, add cookie-based session auth.

Replace the route at line 558:

```python
@app.route("/")
def dashboard():
    # Check API key from header (programmatic) or query param (browser bookmark)
    api_key = request.headers.get("X-API-Key", "") or request.args.get("key", "")
    if DASHBOARD_API_KEY and not (api_key and hmac.compare_digest(api_key, DASHBOARD_API_KEY)):
        return Response(
            "<html><body><h2>Unauthorized</h2><p>Append ?key=YOUR_KEY to the URL.</p></body></html>",
            status=401,
            mimetype="text/html",
        )
    csrf_token = _generate_csrf_token()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dashboard_security.py -v`
Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_dashboard_security.py
git commit -m "fix: auth-gate dashboard route — require API key for PII access"
```

---

### Task 2: Fix XSS via chart label injection

Python lists are interpolated directly into `<script>` blocks via f-strings at `dashboard.py:2113-2132` and `2499-2500`. A prospect with a malicious name/source/stage could inject JavaScript.

**Files:**
- Modify: `dashboard.py:2113-2114, 2122-2123, 2131-2132, 2499-2500`
- Test: `tests/test_dashboard_security.py` (extend)

- [ ] **Step 1: Write failing test — XSS via chart labels**

```python
# Add to tests/test_dashboard_security.py

def test_chart_labels_are_json_escaped(client):
    """Chart labels must use json.dumps, not str(list), to prevent XSS."""
    import db
    # Add a prospect with XSS payload in source
    db.add_prospect({
        "name": "Test XSS",
        "source": "'];alert(1)//",
        "stage": "New Lead",
        "priority": "Hot",
    })
    resp = client.get("/", headers={"X-API-Key": "test-secret-key"})
    html = resp.data.decode()
    # The XSS payload should be JSON-escaped, not raw
    assert "'];alert(1)//" not in html
    # Clean up
    db.delete_prospect("Test XSS")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dashboard_security.py::test_chart_labels_are_json_escaped -v`
Expected: FAIL — raw payload appears in HTML

- [ ] **Step 3: Replace all chart label interpolation with json.dumps**

In `dashboard.py`, find the chart data preparation section (before the f-string) where `stage_labels`, `source_labels`, `product_labels` are built. Add `import json` at the top if not already present (it is — line 6).

Replace at lines 2113-2114:
```python
        labels: {json.dumps(stage_labels)},
        datasets: [{{ data: {json.dumps(stage_values)}, backgroundColor: {json.dumps(stage_chart_colors)} }}]
```

Replace at lines 2122-2123:
```python
        labels: {json.dumps(source_labels)},
        datasets: [{{ data: {json.dumps(source_values)}, backgroundColor: chartColors }}]
```

Replace at lines 2131-2132:
```python
        labels: {json.dumps(product_labels)},
        datasets: [{{ data: {json.dumps(product_values)}, backgroundColor: chartColors }}]
```

Replace at lines 2499-2500:
```python
    const velocityLabels = {json.dumps(list(avg_stage_days.keys()))};
    const velocityData = {json.dumps([round(v, 1) for v in avg_stage_days.values()])};
```

- [ ] **Step 4: Fix onclick JS string escaping pattern**

Replace the `_esc().replace("'", "\\'")` pattern at lines 371, 1001, 1045, 1087 with `json.dumps()` for JS string context.

At line 371 (use `json.dumps` directly on the raw name — `_html.escape` is not needed here since we're in JS string context, and the HTML attribute is already protected by surrounding quotes):
```python
    esc_name = json.dumps(prospect_name)[1:-1] if prospect_name else ""
```

At line 1001:
```python
        esc_detail_name = json.dumps(_esc(p["name"]))[1:-1]
```

At line 1045:
```python
        esc_name = json.dumps(_esc(p["name"]))[1:-1]
```

At line 1087:
```python
        task_prospect_esc = json.dumps(_esc(t.get("prospect", "")))[1:-1]
```

Note: `json.dumps("string")[1:-1]` strips the outer quotes while retaining internal JSON escaping (backslashes, special chars). This is safe for embedding in a JS single-quoted string.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_dashboard_security.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard.py tests/test_dashboard_security.py
git commit -m "fix: XSS — use json.dumps for chart labels and JS string escaping"
```

---

### Task 3: Add database indexes

Zero indexes exist across 14 tables. Add indexes for the most frequently queried columns.

**Files:**
- Modify: `db.py` — inside `init_db()` after all CREATE TABLE statements
- Test: `tests/test_db_indexes.py` (create)

- [ ] **Step 1: Write test verifying indexes exist**

```python
# tests/test_db_indexes.py
import db


def test_critical_indexes_exist():
    """Verify performance-critical indexes are created."""
    db.init_db()
    with db.get_db() as conn:
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
        ).fetchall()
        index_names = {r["name"] for r in indexes}

    expected = {
        "idx_prospects_email",
        "idx_outcomes_resend_id",
        "idx_tasks_status_due",
        "idx_approval_queue_status",
        "idx_nurture_status",
    }
    assert expected.issubset(index_names), f"Missing indexes: {expected - index_names}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_indexes.py -v`
Expected: FAIL — no idx_ indexes exist

- [ ] **Step 3: Add indexes after CREATE TABLE block in init_db()**

Add after the last CREATE TABLE (around line 294 in `db.py`):

```python
        # Performance indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prospects_email ON prospects(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_resend_id ON outcomes(resend_email_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_outcomes_target ON outcomes(target, sent_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_remind ON tasks(remind_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_queue_status ON approval_queue(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_nurture_status ON nurture_sequences(status, next_touch_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activities_prospect ON activities(prospect)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_prospect ON interactions(prospect)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_indexes.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db_indexes.py
git commit -m "perf: add database indexes for frequently queried columns"
```

---

## Chunk 2: Noise Reduction

### Task 4: Remove redundant scheduler jobs

Three jobs are fully redundant with the morning briefing: `daily_market_check` (7:30 AM — market events are in the 8 AM briefing), `followup_reminder` (9:30 AM — call list is in the briefing), and `morning_briefing` runs on weekends when nothing is actionable.

**Files:**
- Modify: `scheduler.py` — remove 2 job functions + registrations, add `day_of_week` to briefing
- Test: `tests/test_scheduler.py` (extend or modify)

- [ ] **Step 1: Check existing scheduler tests for removed jobs**

Run: `python -m pytest tests/ -k "scheduler" -v --collect-only`

Review which tests reference `daily_market_check`, `followup_reminder`. These tests must be updated or removed.

- [ ] **Step 2: Remove `daily_market_check` function and registration**

Delete the function at lines 820-841 and remove its `add_job` registration (find it in `start_scheduler`).

- [ ] **Step 3: Remove `followup_reminder` function and registration**

Delete the function at lines 599-632 and remove its `add_job` registration at lines 922-930.

- [ ] **Step 4: Add `day_of_week="mon-fri"` to morning briefing**

At lines 912-919, change:
```python
scheduler.add_job(
    morning_briefing,
    "cron",
    day_of_week="mon-fri",
    hour=8,
    minute=0,
    id="morning_briefing",
    name="Daily Morning Briefing",
)
```

- [ ] **Step 5: Update or remove affected tests**

Remove/update tests that assert on `daily_market_check` or `followup_reminder`.

- [ ] **Step 6: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add scheduler.py tests/
git commit -m "fix: remove 2 redundant scheduler jobs, restrict briefing to weekdays"
```

---

### Task 5: Reduce auto-nag and draft nudge frequency

`auto_nag` fires 5x/day (9,11,13,15,17). Reduce to 2x (9 AM, 14 PM). `nudge_stale_drafts` fires 4x/day. Reduce to 1x (14 PM).

**Files:**
- Modify: `scheduler.py` — change cron expressions in `start_scheduler()`

- [ ] **Step 1: Change auto_nag frequency**

At lines 933-940, change `hour="9,11,13,15,17"` to `hour="9,14"`:
```python
scheduler.add_job(
    auto_nag,
    "cron",
    hour="9,14",
    minute=0,
    id="auto_nag",
    name="Auto-Nag Check",
)
```

- [ ] **Step 2: Change nudge_stale_drafts frequency**

At lines 985-993, change `hour="10,12,14,16"` to `hour="14"`:
```python
scheduler.add_job(
    nudge_stale_drafts,
    "cron",
    day_of_week="mon-fri",
    hour="14",
    minute=30,
    id="nudge_stale_drafts",
    name="Nudge Stale Drafts",
)
```

- [ ] **Step 3: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add scheduler.py
git commit -m "fix: reduce auto-nag to 2x/day, draft nudges to 1x/day"
```

---

### Task 6: Fix task reminder log verbosity

`check_task_reminders` at lines 637-678 logs every 60 seconds when no matches are found, listing all pending reminders. This produces thousands of log lines per day.

**Files:**
- Modify: `scheduler.py:649-652`

- [ ] **Step 1: Change logger.info to logger.debug for no-match case**

At line 652, change:
```python
                logger.info(f"Task reminders: now={now_str}, no matches. Pending reminders: {reminders}")
```
to:
```python
                logger.debug(f"Task reminders: now={now_str}, no matches. Pending reminders: {reminders}")
```

- [ ] **Step 2: Commit**

```bash
git add scheduler.py
git commit -m "fix: reduce task reminder polling log noise to debug level"
```

---

### Task 7: Fix meeting prep dedup surviving restarts

`send_meeting_prep_docs` uses a function attribute `_sent_preps` (line 753) that resets when the bot restarts, causing duplicate prep docs.

**Files:**
- Modify: `scheduler.py:753-759`

- [ ] **Step 1: Replace function attribute with file-based tracking**

Use a **separate file** `meeting_prep_state.json` (not `nag_state.json`) to avoid race conditions with `auto_nag` which also writes to `nag_state.json` concurrently via APScheduler.

```python
# Replace lines 753-759 with:
import json as _json
state_file = os.path.join(os.environ.get("DATA_DIR", "."), "meeting_prep_state.json")
try:
    with open(state_file) as f:
        state = _json.load(f)
except (FileNotFoundError, ValueError):
    state = {}
sent_preps = set(state.get("sent_preps", []))

meeting_id = str(m.get("id", ""))
if meeting_id in sent_preps:
    continue
```

After sending, save back:
```python
sent_preps.add(meeting_id)
# Keep only last 50 to prevent unbounded growth
state["sent_preps"] = list(sent_preps)[-50:]
with open(state_file, "w") as f:
    _json.dump(state, f)
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add scheduler.py
git commit -m "fix: persist meeting prep dedup to disk — survives restarts"
```

---

## Chunk 3: Dead Weight Removal

### Task 8: Remove redundant Telegram commands

5 commands (`/pipeline`, `/overdue`, `/meetings`, `/calls`, `/stats`) are one-liner wrappers around functions GPT already calls via natural language chat. Remove them.

**Files:**
- Modify: `bot.py` — remove 5 command functions + 5 handler registrations + update `/start` help text

- [ ] **Step 1: Remove the 5 command functions**

Delete these functions from `bot.py`:
- `cmd_pipeline` (lines 1959-1967)
- `cmd_overdue` (lines 1969-1977)
- `cmd_meetings` (lines 1979-1987)
- `cmd_calls` (lines 1989-1997)
- `cmd_stats` (lines 1999-2007)

- [ ] **Step 2: Remove handler registrations**

In `build_application()` (lines 3164-3204), remove:
```python
app.add_handler(CommandHandler("pipeline", cmd_pipeline))
app.add_handler(CommandHandler("overdue", cmd_overdue))
app.add_handler(CommandHandler("meetings", cmd_meetings))
app.add_handler(CommandHandler("calls", cmd_calls))
app.add_handler(CommandHandler("stats", cmd_stats))
```

- [ ] **Step 3: Update /start help text**

Find the `/start` command handler and remove the 5 commands from the help text list shown to the admin.

- [ ] **Step 4: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS (update any tests that reference these commands)

- [ ] **Step 5: Commit**

```bash
git add bot.py
git commit -m "refactor: remove 5 redundant commands — use natural language chat instead"
```

---

### Task 9: Remove GPT-based content generation

The `/content` command and `weekly_content_plan` scheduler job use GPT-4.1 for content generation. Content is now generated in Claude Code conversations (saved to `content/` folder). Remove the command, scheduler job, and the `content_engine.py` module.

**Files:**
- Modify: `bot.py` — remove `cmd_content` function + `handle_content_callback` + handler registrations
- Modify: `scheduler.py` — remove `weekly_content_plan` function + job registration
- Modify: `content_engine.py` — strip down to only `get_brand_voice_examples()` and `add_brand_voice_example()` (needed by `/voice` command)
- Modify or delete: `tests/test_content_engine.py`, `tests/test_scheduler_content.py`

**IMPORTANT:** The `/voice` command at `bot.py:2710` imports `content_engine.get_brand_voice_examples()`. Do NOT delete `content_engine.py` entirely — keep the brand voice functions.

- [ ] **Step 1: Remove `cmd_content` from bot.py**

Delete the function at lines 2719-2815.

- [ ] **Step 2: Remove `handle_content_callback` from bot.py**

Find and delete the `handle_content_callback` function (handles `content_approve_` and `content_dismiss_` callbacks).

- [ ] **Step 3: Remove handler registrations in build_application()**

Remove from lines 3164-3204:
```python
app.add_handler(CommandHandler("content", cmd_content))
app.add_handler(CallbackQueryHandler(handle_content_callback, pattern=r"^content_"))
```

- [ ] **Step 4: Remove `weekly_content_plan` from scheduler.py**

Delete the function at lines 778-816 and its `add_job` registration.

- [ ] **Step 5: Strip content_engine.py down to brand voice functions only**

Keep ONLY these functions in `content_engine.py`:
- `get_brand_voice_examples(platform=None, limit=10)`
- `add_brand_voice_example(platform, content, post_type="general")`

Remove everything else: `generate_post()`, `generate_weekly_plan()`, `format_plan_for_telegram()`, the GPT prompts, the OpenAI client import, and `market_intel` import.

- [ ] **Step 6: Update /start help text**

Remove `/content` from the admin command list.

- [ ] **Step 7: Remove or update content-related tests**

Delete `tests/test_scheduler_content.py`. Update `tests/test_content_engine.py` to only test the brand voice functions that remain.

- [ ] **Step 8: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add bot.py scheduler.py tests/
git rm content_engine.py
git commit -m "refactor: remove GPT content generation — content now generated via Claude"
```

---

### Task 10: Remove deprecated pipeline_lock

`pipeline_lock` at `bot.py:41` is marked deprecated but still used at lines 1394 and 1844. SQLite handles its own concurrency via `db.get_db()` context manager. The lock protects nothing.

**Files:**
- Modify: `bot.py` — remove declaration + 2 usages

- [ ] **Step 1: Remove the lock declaration**

Delete line 41:
```python
pipeline_lock = threading.RLock()
```

- [ ] **Step 2: Remove `with pipeline_lock:` at line 1394**

Dedent the code inside the `with` block. The lock wraps the `_llm_respond` function — just remove the context manager while keeping the code inside it.

- [ ] **Step 3: Remove `with pipeline_lock:` at line 1844**

Same — dedent the code inside the `with` block in `cmd_todo`.

- [ ] **Step 4: Keep `import threading`**

`threading.Thread` is used at `bot.py:3241` for the event loop. Only remove the `pipeline_lock` declaration — do NOT remove `import threading`.

- [ ] **Step 5: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add bot.py
git commit -m "refactor: remove deprecated pipeline_lock — SQLite handles concurrency"
```

---

### Task 11: Remove unused dashboard endpoints and debug route

Three API endpoints are never called: `GET /api/prospects` (line 162), `GET /api/tasks` (line 207), `GET /api/tasks/debug` (line 198).

**Files:**
- Modify: `dashboard.py` — remove 3 route functions

- [ ] **Step 1: Remove the 3 unused endpoints**

Delete:
- `api_list_prospects` at line 162-166
- `api_debug_tasks` at line 198-204
- `api_list_tasks` at line 207-214

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add dashboard.py
git commit -m "refactor: remove 3 unused dashboard API endpoints"
```

---

### Task 12: Fix broken draft edit flow

The "Edit" button in the approval queue stores `editing_draft_id` in `context.user_data` at line 2591 but nothing reads it. Either fix or remove.

**Files:**
- Modify: `bot.py` — remove the edit option from draft keyboards and the dead code path

- [ ] **Step 1: Remove "Edit" from draft keyboards**

Find `_draft_keyboard()` function and remove the Edit button. Change the keyboard to only have: Approve, Skip, Snooze.

- [ ] **Step 2: Remove the `elif action == "edit":` block in handle_draft_callback**

Delete the dead code at lines 2585-2591.

- [ ] **Step 3: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add bot.py
git commit -m "fix: remove broken Edit button from draft approval — was dead code"
```

---

## Chunk 4: Intelligence Improvements

### Task 13: Wire referral candidates into morning briefing

`scoring.get_referral_candidates()` identifies Closed-Won clients in the 14-30 day or 90-120 day window for referral nudges. It's built but never called outside tests.

**Files:**
- Modify: `briefing.py` — add referral candidates to briefing data and prompt
- Test: `tests/test_briefing.py` (extend)

- [ ] **Step 1: Write failing test**

```python
# Add to existing briefing tests
def test_briefing_includes_referral_candidates(monkeypatch):
    """Morning briefing data should include referral candidates."""
    import briefing
    import scoring

    monkeypatch.setattr(scoring, "get_referral_candidates", lambda: [
        {"name": "Jane Won", "product": "RRSP", "days_since_close": 20, "nudge_type": "first"}
    ])

    data = briefing.assemble_briefing_data()
    assert "referral_candidates" in data
    assert len(data["referral_candidates"]) == 1
    assert data["referral_candidates"][0]["name"] == "Jane Won"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_briefing.py -k "referral" -v`
Expected: FAIL — `referral_candidates` not in data

- [ ] **Step 3: Add referral candidates to assemble_briefing_data()**

In `briefing.py`, after the `call_list` line (around line 55), add:

```python
    # Referral nudge candidates
    try:
        referral_candidates = scoring.get_referral_candidates()
    except Exception:
        referral_candidates = []
```

Add to the `data` dict:
```python
    data["referral_candidates"] = referral_candidates
```

- [ ] **Step 4: Add referral section to _build_briefing_prompt()**

In `_build_briefing_prompt()`, after the call list section, add:

```python
    # Referral candidates
    ref_lines = []
    for r in data.get("referral_candidates", []):
        nudge = "first follow-up (2-4 weeks post-close)" if r["nudge_type"] == "first" else "second check-in (3-4 months)"
        ref_lines.append(f"- {r['name']} ({r['product']}) — {r['days_since_close']} days since close, {nudge}")
    referral_summary = "\n".join(ref_lines) if ref_lines else "None right now"
```

Add to `BRIEFING_PROMPT` template (after the "WHAT'S WORKING" or last existing section):
```
REFERRAL OPPORTUNITIES:
{referral_summary}
```

And add to the `.format()` call at the end of `_build_briefing_prompt()`:
```python
        referral_summary=_escape_braces(referral_summary),
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_briefing.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add briefing.py tests/test_briefing.py
git commit -m "feat: surface referral candidates in morning briefing"
```

---

### Task 14: Auto-offer nurture enrollment for new cold-stage prospects

When a prospect is added in "New Lead" or "Contacted" stage, the system should offer to start a nurture sequence via inline Telegram button. Currently nurture requires manual `/nurture start` which is never used.

**Files:**
- Modify: `bot.py` — after `add_prospect` tool call, send nurture offer
- Test: `tests/test_nurture_auto.py` (create)

- [ ] **Step 1: Write test**

```python
# tests/test_nurture_auto.py
import nurture
import db


def test_create_sequence_for_new_lead():
    """Nurture sequence can be created for a new prospect."""
    db.init_db()
    result = db.add_prospect({"name": "Test Nurture", "stage": "New Lead", "priority": "Warm"})
    assert "added" in result.lower() or "Test Nurture" in result

    # Look up prospect ID
    prospect = db.read_pipeline()
    test_p = [p for p in prospect if p["name"] == "Test Nurture"]
    assert len(test_p) == 1

    seq = nurture.create_sequence("Test Nurture", prospect_id=test_p[0]["id"])
    assert seq is not None
    assert seq["status"] == "active"
    assert seq["current_touch"] == 0

    # Clean up
    db.delete_prospect("Test Nurture")
```

- [ ] **Step 2: Run test**

Run: `python -m pytest tests/test_nurture_auto.py -v`
Expected: PASS (nurture.create_sequence already works)

- [ ] **Step 3: Add nurture offer after prospect creation in bot.py**

In `bot.py`, find the `add_prospect` tool function handler (the code path where `TOOL_FUNCTIONS["add_prospect"]` is called in `_llm_respond`). After a successful add, check the stage and offer nurture:

```python
# After successful add_prospect in _llm_respond tool dispatch
if func_name == "add_prospect" and "added" in result.lower():
    stage = func_args.get("stage", "")
    if stage in ("New Lead", "Contacted", "Nurture"):
        prospect_name = func_args.get("name", "")
        if prospect_name:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "Start Nurture Sequence",
                    callback_data=f"nurture_offer_start_{prospect_name[:50]}"
                ),
                InlineKeyboardButton("Skip", callback_data="nurture_offer_skip"),
            ]])
            await _bot.send_message(
                chat_id=CHAT_ID,
                text=f"New prospect {prospect_name} added as {stage}. Start a nurture sequence?",
                reply_markup=keyboard,
            )
```

- [ ] **Step 4: Add callback handler for nurture_start**

Add a new callback handler in `build_application()`:

```python
async def handle_nurture_offer(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "nurture_offer_skip":
        await query.edit_message_text(query.message.text + "\n\nSkipped nurture sequence.")
        return
    if data.startswith("nurture_offer_start_"):
        name = data[len("nurture_offer_start_"):]
        import nurture
        seq = nurture.create_sequence(name)
        if seq:
            await query.edit_message_text(
                f"Nurture sequence started for {name} — "
                f"{seq['total_touches']} touches over ~25 days. I'll queue drafts for your approval."
            )
        else:
            await query.edit_message_text(f"Could not start nurture for {name}.")

# In build_application():
app.add_handler(CallbackQueryHandler(handle_nurture_offer, pattern=r"^nurture_offer_"))
```

Note: Use `nurture_offer_` prefix (not just `nurture_`) to avoid intercepting other nurture-related callbacks.

- [ ] **Step 5: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add bot.py tests/test_nurture_auto.py
git commit -m "feat: auto-offer nurture sequence when adding cold-stage prospects"
```

---

### Task 15: Batch nurture notifications into single message

Currently each nurture touch sends a separate Telegram message. If 5 sequences are due, that's 5 messages at 9:15 AM. Batch them into one.

**Files:**
- Modify: `scheduler.py` — rewrite `check_nurture_sequences` to batch

- [ ] **Step 1: Rewrite check_nurture_sequences to batch**

Replace the function at lines 846-886:

```python
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
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add scheduler.py
git commit -m "feat: batch nurture notifications into single Telegram message"
```

---

## Ordering & Dependencies

**Critical ordering rules:**
1. **Task 1 before Task 2** — Task 2's XSS test uses the auth header from Task 1
2. **Task 9 must keep brand voice functions** — `/voice` command depends on `content_engine.get_brand_voice_examples()`
3. **Tasks 4, 5, 6, 7, 15 all modify `scheduler.py`** — after Task 4 removes ~50 lines, all subsequent line numbers shift. Search by function name, not line number.
4. **Chunks are independent** — Chunk 1 (security), Chunk 2 (noise), Chunk 3 (dead weight), Chunk 4 (intelligence) can be executed in parallel by separate agents, except tasks within a chunk should run sequentially.

---

## Summary

| Chunk | Tasks | Impact |
|-------|-------|--------|
| 1: Security | Tasks 1-3 | Auth-gate dashboard, fix XSS, add indexes |
| 2: Noise | Tasks 4-7 | Kill 2 jobs, reduce frequencies, fix bugs |
| 3: Dead Weight | Tasks 8-12 | Remove ~200 lines of unused code |
| 4: Intelligence | Tasks 13-15 | Referral nudges, auto-nurture, batched notifications |

Total estimated Telegram messages per weekday after changes: **4-6** (down from 15-20).
