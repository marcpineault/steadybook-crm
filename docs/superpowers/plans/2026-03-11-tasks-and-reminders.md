# Tasks & Reminders Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a to-do list and reminder system with Telegram commands (`/todo`, `/tasks`, `/done`), a dashboard tab, and scheduler integration.

**Architecture:** New `tasks` table in SQLite, CRUD functions in `db.py`, three bot commands using GPT for natural language parsing, a new Tasks tab in the dashboard, and scheduler hooks for morning briefing + auto-nag + per-minute custom reminders.

**Tech Stack:** SQLite, python-telegram-bot, OpenAI GPT-5 (tool calling), Flask, APScheduler, vanilla JS

---

## Chunk 1: Database Layer

### Task 1: Add tasks table and CRUD functions to db.py

**Files:**
- Modify: `db.py` (add table to `init_db()`, add CRUD functions)
- Create: `tests/test_tasks_db.py`

- [ ] **Step 1: Write failing tests for task CRUD**

Create `tests/test_tasks_db.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DATA_DIR", "/tmp/test_calm_bot")
os.makedirs("/tmp/test_calm_bot", exist_ok=True)

import db


def setup_function():
    if os.path.exists(db.DB_PATH):
        os.remove(db.DB_PATH)
    db.init_db()


def test_add_task_basic():
    result = db.add_task({
        "title": "Send John the brochure",
        "assigned_to": "123",
        "created_by": "123",
    })
    assert result["id"] is not None
    assert result["title"] == "Send John the brochure"
    assert result["status"] == "pending"
    assert result["assigned_to"] == "123"


def test_add_task_with_prospect_and_due_date():
    result = db.add_task({
        "title": "Call about term quote",
        "prospect": "John Smith",
        "due_date": "2026-03-15",
        "remind_at": "2026-03-14 09:00",
        "assigned_to": "123",
        "created_by": "123",
    })
    assert result["prospect"] == "John Smith"
    assert result["due_date"] == "2026-03-15"
    assert result["remind_at"] == "2026-03-14 09:00"


def test_add_task_requires_title():
    result = db.add_task({"title": "", "assigned_to": "123", "created_by": "123"})
    assert result is None


def test_get_tasks_filters_by_assignee():
    db.add_task({"title": "Task A", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Task B", "assigned_to": "222", "created_by": "222"})
    tasks = db.get_tasks(assigned_to="111")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Task A"


def test_get_tasks_filters_by_status():
    t = db.add_task({"title": "Task C", "assigned_to": "111", "created_by": "111"})
    db.complete_task(t["id"], "111")
    pending = db.get_tasks(assigned_to="111", status="pending")
    assert len(pending) == 0
    completed = db.get_tasks(assigned_to="111", status="completed")
    assert len(completed) == 1


def test_get_tasks_filters_by_prospect():
    db.add_task({"title": "Task D", "prospect": "John", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Task E", "prospect": "Sarah", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_tasks(assigned_to="111", prospect="John")
    assert len(tasks) == 1
    assert tasks[0]["prospect"] == "John"


def test_get_tasks_orders_by_due_date():
    db.add_task({"title": "Later", "due_date": "2026-03-20", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Sooner", "due_date": "2026-03-10", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "No date", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_tasks(assigned_to="111")
    assert tasks[0]["title"] == "Sooner"
    assert tasks[1]["title"] == "Later"
    assert tasks[2]["title"] == "No date"


def test_complete_task():
    t = db.add_task({"title": "Finish this", "assigned_to": "111", "created_by": "111"})
    result = db.complete_task(t["id"], "111")
    assert "Completed" in result
    tasks = db.get_tasks(assigned_to="111", status="completed")
    assert len(tasks) == 1
    assert tasks[0]["completed_at"] is not None


def test_complete_task_wrong_user():
    t = db.add_task({"title": "Not yours", "assigned_to": "111", "created_by": "111"})
    result = db.complete_task(t["id"], "999")
    assert "not authorized" in result.lower() or "not found" in result.lower()


def test_complete_task_admin_override():
    """Admin (empty string means check is skipped when admin_chat_id passed)."""
    t = db.add_task({"title": "Admin completes", "assigned_to": "222", "created_by": "222"})
    result = db.complete_task(t["id"], "222", is_admin=True)
    assert "Completed" in result


def test_delete_task():
    t = db.add_task({"title": "Delete me", "assigned_to": "111", "created_by": "111"})
    result = db.delete_task(t["id"], "111")
    assert "Deleted" in result
    tasks = db.get_tasks(assigned_to="111")
    assert len(tasks) == 0


def test_get_due_tasks():
    db.add_task({"title": "Due today", "due_date": "2026-03-11", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Due tomorrow", "due_date": "2026-03-12", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_due_tasks("2026-03-11")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Due today"


def test_get_overdue_tasks():
    db.add_task({"title": "Overdue", "due_date": "2026-03-01", "assigned_to": "111", "created_by": "111"})
    db.add_task({"title": "Future", "due_date": "2099-12-31", "assigned_to": "111", "created_by": "111"})
    tasks = db.get_overdue_tasks()
    titles = [t["title"] for t in tasks]
    assert "Overdue" in titles
    assert "Future" not in titles


def test_get_reminder_tasks():
    db.add_task({
        "title": "Remind me",
        "remind_at": "2026-03-11 09:00",
        "assigned_to": "111",
        "created_by": "111",
    })
    db.add_task({
        "title": "Later reminder",
        "remind_at": "2026-03-11 15:00",
        "assigned_to": "111",
        "created_by": "111",
    })
    tasks = db.get_reminder_tasks("2026-03-11 10:00")
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Remind me"


def test_clear_reminder():
    t = db.add_task({
        "title": "Clear me",
        "remind_at": "2026-03-11 09:00",
        "assigned_to": "111",
        "created_by": "111",
    })
    db.clear_reminder(t["id"])
    tasks = db.get_reminder_tasks("2026-03-11 10:00")
    assert len(tasks) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_tasks_db.py -v`
Expected: FAIL — `AttributeError: module 'db' has no attribute 'add_task'`

- [ ] **Step 3: Add tasks table to init_db()**

In `db.py`, inside the `init_db()` function's `conn.executescript("""...""")`, add after the `interactions` table:

```python
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                prospect TEXT DEFAULT '',
                due_date TEXT,
                remind_at TEXT,
                assigned_to TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now')),
                completed_at TEXT
            );
```

- [ ] **Step 4: Add CRUD functions**

Add these functions at the end of `db.py` (before the migration section):

```python
# ── Tasks CRUD ──

def add_task(data: dict):
    """Add a task. Returns the created task as dict, or None if no title."""
    title = data.get("title", "").strip()
    if not title:
        return None

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO tasks
               (title, prospect, due_date, remind_at, assigned_to, created_by, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                data.get("prospect", ""),
                data.get("due_date"),
                data.get("remind_at"),
                data.get("assigned_to", ""),
                data.get("created_by", ""),
                data.get("notes", ""),
            ),
        )
        task_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_dict(row)


def get_tasks(assigned_to=None, status="pending", prospect=None, limit=50):
    """Get tasks with filters. Orders by due_date ASC (nulls last), then created_at DESC."""
    conditions = []
    params = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if assigned_to:
        conditions.append("assigned_to = ?")
        params.append(assigned_to)
    if prospect:
        conditions.append("LOWER(prospect) LIKE ?")
        params.append(f"%{prospect.lower()}%")

    where = " AND ".join(conditions) if conditions else "1=1"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(
            f"""SELECT * FROM tasks WHERE {where}
                ORDER BY
                    CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
                    due_date ASC,
                    created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()
    return _rows_to_dicts(rows)


def complete_task(task_id: int, completed_by: str, is_admin: bool = False) -> str:
    """Mark a task as completed. Only assignee or admin can complete."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != completed_by:
            return f"Not authorized to complete task {task_id}."
        conn.execute(
            "UPDATE tasks SET status = 'completed', completed_at = datetime('now') WHERE id = ?",
            (task_id,),
        )
    return f"Completed: {row['title']}"


def delete_task(task_id: int, deleted_by: str, is_admin: bool = False) -> str:
    """Delete a task. Only assignee or admin can delete."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return f"Task {task_id} not found."
        if not is_admin and row["assigned_to"] != deleted_by:
            return f"Not authorized to delete task {task_id}."
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return f"Deleted: {row['title']}"


def get_due_tasks(date_str: str):
    """Get pending tasks due on a specific date."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE due_date = ? AND status = 'pending' ORDER BY created_at",
            (date_str,),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_overdue_tasks():
    """Get pending tasks with due_date before today."""
    today = date.today().strftime("%Y-%m-%d")
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE due_date < ? AND status = 'pending' ORDER BY due_date ASC",
            (today,),
        ).fetchall()
    return _rows_to_dicts(rows)


def get_reminder_tasks(now_str: str):
    """Get pending tasks with remind_at <= now that haven't been cleared."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE remind_at IS NOT NULL AND remind_at <= ? AND status = 'pending' ORDER BY remind_at",
            (now_str,),
        ).fetchall()
    return _rows_to_dicts(rows)


def clear_reminder(task_id: int):
    """Clear remind_at after firing so it doesn't repeat."""
    with get_db() as conn:
        conn.execute("UPDATE tasks SET remind_at = NULL WHERE id = ?", (task_id,))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_tasks_db.py -v`
Expected: All 16 tests PASS

- [ ] **Step 6: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS (existing 21 + new 16)

- [ ] **Step 7: Commit**

```bash
git add db.py tests/test_tasks_db.py
git commit -m "feat: add tasks table and CRUD functions to db layer"
```

---

## Chunk 2: Telegram Bot Commands

### Task 2: Add /todo command with GPT parsing

**Files:**
- Modify: `bot.py` (add prompt, tool defs, command handler, register handler)

- [ ] **Step 1: Add task tool definitions**

In `bot.py`, add after the existing `TOOLS` list (around line 1101):

```python
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
```

- [ ] **Step 2: Add TASK_TOOLS to TOOL_FUNCTIONS**

Add to the `TOOL_FUNCTIONS` dict:

```python
    "create_task": lambda args: _create_task_from_tool(args),
```

And add the helper function before `TOOL_FUNCTIONS`:

```python
def _create_task_from_tool(args):
    """Called by LLM tool to create a task. assigned_to/created_by set by caller."""
    # Placeholder — will be filled with chat_id by cmd_todo
    return json.dumps(args)
```

- [ ] **Step 3: Add the /todo prompt**

Add before `cmd_todo`:

```python
# ── /todo command — task creation ──

PROMPT_TODO = """You help create tasks and to-do items. Today is {today}.

{formatting}

The user wants to create a task. Parse their message to extract:
1. title — the core task (required)
2. prospect — a prospect/client name if mentioned (use lookup_prospect to verify). Empty string if not prospect-related.
3. due_date — in YYYY-MM-DD format if a date is mentioned ("by Friday", "March 15", "tomorrow", "next week" = next Monday)
4. remind_at — in YYYY-MM-DD HH:MM format if they want a reminder ("remind me Thursday 9am"). Default to 09:00 if time not specified.

If the user says "@marc" or "for marc", note that in your response — the caller will handle assignment.

Call create_task with the parsed fields. Reply with a SHORT confirmation showing what was created. One or two lines max."""
```

- [ ] **Step 4: Add cmd_todo handler**

```python
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

        # Use _llm_respond but intercept create_task to inject chat_id
        response = client.chat.completions.create(
            model="gpt-5",
            max_completion_tokens=512,
            tools=TASK_TOOLS,
            tool_choice="auto",
            messages=messages,
        )

        msg = response.choices[0].message
        task_data = None

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
                    # Determine assignee
                    assigned_to = chat_id
                    if "@marc" in user_msg.lower() or "for marc" in user_msg.lower():
                        assigned_to = ADMIN_CHAT_ID
                    elif not is_admin:
                        # Coworkers can only assign to self or admin
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
```

- [ ] **Step 5: Add cmd_tasks handler**

```python
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /tasks command — list pending tasks."""
    chat_id = str(update.effective_chat.id)
    is_admin = _is_admin(update)

    if is_admin:
        # Admin sees all tasks
        tasks = db.get_tasks()
    else:
        tasks = db.get_tasks(assigned_to=chat_id)

    if not tasks:
        await update.message.reply_text("No pending tasks. Use /todo to add one!")
        return

    today = date.today()
    lines = ["Your tasks:\n"] if not is_admin else ["All tasks:\n"]

    for t in tasks:
        # Determine emoji based on due date
        due = t.get("due_date")
        if due:
            try:
                due_dt = datetime.strptime(due, "%Y-%m-%d").date()
                days_diff = (due_dt - today).days
                if days_diff < 0:
                    emoji = "\U0001f534"  # red circle — overdue
                    due_str = f"due {due} — {abs(days_diff)}d overdue"
                elif days_diff == 0:
                    emoji = "\U0001f7e1"  # yellow circle — due today
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
```

- [ ] **Step 6: Add cmd_done handler**

```python
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
```

- [ ] **Step 7: Register the new command handlers**

In the command handler registration section of `bot.py` (around line 1960), add:

```python
    app.add_handler(CommandHandler("todo", cmd_todo))
    app.add_handler(CommandHandler("td", cmd_todo))    # alias
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("done", cmd_done))
```

- [ ] **Step 8: Update /help and coworker access message**

In the help text, add the task commands. In `_require_admin`, update the coworker access message to include `/todo`, `/tasks`, `/done`.

Find the coworker denial message (around line 46) and update:
```python
    await update.message.reply_text(
        "You have access to /quote, /add, /status, /msg, /todo, /tasks, and /done.\n"
        "Try: /quote disability office worker 50k income 3k benefit\n"
        "Or: /add John Smith, interested in life insurance\n"
        "Or: /msg Hey Marc, can we chat about the Johnson file?\n"
        "Or: /todo send brochure to John by Friday"
    )
```

Also update `cmd_todo`, `cmd_tasks`, and `cmd_done` to allow coworker access (do NOT gate behind `_require_admin`).

- [ ] **Step 9: Verify compilation**

Run: `python3 -c "import py_compile; py_compile.compile('bot.py', doraise=True); print('OK')"`
Expected: OK

- [ ] **Step 10: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 11: Commit**

```bash
git add bot.py
git commit -m "feat: add /todo, /tasks, /done Telegram commands with GPT parsing"
```

---

## Chunk 3: Scheduler Integration

### Task 3: Add task reminders to scheduler

**Files:**
- Modify: `scheduler.py` (add tasks to morning briefing, auto-nag, new reminder job)

- [ ] **Step 1: Add tasks section to morning briefing**

In `scheduler.py`, in `_morning_briefing_inner()`, add after the existing sections (before the final `msg = "\n".join(lines)`):

```python
    # Tasks due today and overdue
    try:
        overdue_tasks = db.get_overdue_tasks()
        due_today_tasks = db.get_due_tasks(today.strftime("%Y-%m-%d"))

        if overdue_tasks or due_today_tasks:
            lines.append("TASKS:")
            for t in overdue_tasks:
                days_late = (today - datetime.strptime(t["due_date"], "%Y-%m-%d").date()).days
                prospect_str = f" [{t['prospect']}]" if t.get("prospect") else ""
                lines.append(f"  \u26a0\ufe0f OVERDUE ({days_late}d): {t['title']}{prospect_str}")
            for t in due_today_tasks:
                prospect_str = f" [{t['prospect']}]" if t.get("prospect") else ""
                lines.append(f"  \U0001f4cb Due today: {t['title']}{prospect_str}")
            lines.append("")
    except Exception as e:
        logger.warning(f"Could not load tasks for briefing: {e}")
```

- [ ] **Step 2: Add overdue tasks to auto-nag**

In `scheduler.py`, in `auto_nag()`, add after the meeting-tomorrow section (before `_save_nag_state(nag_state)`):

```python
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
```

- [ ] **Step 3: Add custom reminder job**

Add a new async function:

```python
async def check_task_reminders():
    """Check for tasks with remind_at <= now and send reminders."""
    if not _bot:
        return

    try:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        tasks = db.get_reminder_tasks(now_str)

        for t in tasks:
            chat_id = t.get("assigned_to")
            if not chat_id:
                chat_id = CHAT_ID  # fallback to admin

            due_str = f" (due {t['due_date']})" if t.get("due_date") else ""
            prospect_str = f" [{t['prospect']}]" if t.get("prospect") else ""
            msg = f"\u23f0 Reminder: {t['title']}{prospect_str}{due_str}"

            try:
                await _bot.send_message(chat_id=chat_id, text=msg)
                logger.info(f"Task reminder sent: #{t['id']} to {chat_id}")
            except Exception as e:
                logger.warning(f"Could not send task reminder #{t['id']}: {e}")

            db.clear_reminder(t["id"])

    except Exception as e:
        logger.error(f"Task reminder check failed: {e}")
```

- [ ] **Step 4: Register the reminder job in start_scheduler()**

Add to `start_scheduler()` after the existing jobs:

```python
    # Task reminders — check every 60 seconds
    scheduler.add_job(
        check_task_reminders,
        "interval",
        seconds=60,
        id="task_reminders",
        name="Task Reminder Check",
    )
```

Update the logger.info line to include the new job.

- [ ] **Step 5: Add db import if not present**

Ensure `scheduler.py` imports db: `import db` (check if already imported).

- [ ] **Step 6: Verify compilation**

Run: `python3 -c "import py_compile; py_compile.compile('scheduler.py', doraise=True); print('OK')"`
Expected: OK

- [ ] **Step 7: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 8: Commit**

```bash
git add scheduler.py
git commit -m "feat: add task reminders to morning briefing, auto-nag, and per-minute check"
```

---

## Chunk 4: Dashboard Tasks Tab

### Task 4: Add Tasks tab and API endpoints to dashboard

**Files:**
- Modify: `dashboard.py` (add API endpoints, add tab HTML + JS, query tasks in dashboard())

- [ ] **Step 1: Add task API endpoints**

Add after the existing `/api/prospects` endpoint:

```python
@app.route("/api/task", methods=["POST"])
@_require_auth
def api_add_task():
    data = request.json
    if not data or not data.get("title"):
        return jsonify({"error": "Title required"}), 400
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
    result = db.complete_task(task_id, "", is_admin=True)  # dashboard = admin access
    return jsonify({"ok": True, "message": result})


@app.route("/api/task/<int:task_id>", methods=["DELETE"])
@_require_auth
def api_delete_task(task_id):
    result = db.delete_task(task_id, "", is_admin=True)
    if "not found" in result.lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "message": result})
```

- [ ] **Step 2: Query tasks in the dashboard() function**

In the `dashboard()` function, after `read_data()`, add:

```python
    all_tasks = db.get_tasks(status="pending")
    completed_tasks_recent = db.get_tasks(status="completed", limit=10)
```

- [ ] **Step 3: Add Tasks tab button**

In the tab-nav div, add a 5th button:

```python
        <button class="tab-btn" onclick="showTab('tasks')">Tasks</button>
```

- [ ] **Step 4: Build task rows data in Python**

After the `all_tasks` query, build the HTML rows:

```python
    today_str = today.strftime("%Y-%m-%d")
    task_rows = ""
    for t in all_tasks:
        due = t.get("due_date") or ""
        row_class = ""
        due_display = ""
        if due:
            if due < today_str:
                row_class = "overdue"
                days_late = (today - datetime.strptime(due, "%Y-%m-%d").date()).days
                due_display = f'{_esc(due)} <span style="color:#E74C3C">({days_late}d overdue)</span>'
            elif due == today_str:
                row_class = "due-today"
                due_display = f'<span style="color:#F39C12;font-weight:600">Today</span>'
            else:
                due_display = _esc(due)

        prospect_display = _esc(t.get("prospect", ""))
        task_rows += f"""<tr class="{row_class}">
            <td style="text-align:center"><input type="checkbox" onchange="completeTask({t['id']}, this)" style="width:18px;height:18px;cursor:pointer"></td>
            <td>{_esc(t['title'])}</td>
            <td>{prospect_display}</td>
            <td>{due_display}</td>
            <td style="text-align:center"><button onclick="deleteTask({t['id']})" style="background:none;border:none;color:#E74C3C;cursor:pointer;font-size:16px">\u2715</button></td>
        </tr>"""

    completed_rows = ""
    for t in completed_tasks_recent:
        completed_rows += f"""<tr style="opacity:0.5;text-decoration:line-through">
            <td style="text-align:center">\u2705</td>
            <td>{_esc(t['title'])}</td>
            <td>{_esc(t.get('prospect', ''))}</td>
            <td>{_esc(t.get('completed_at', '')[:10])}</td>
            <td></td>
        </tr>"""
```

- [ ] **Step 5: Add Tasks tab HTML**

After `</div><!-- end tab-scoreboard -->`, add:

```python
    <!-- ═══ TAB 5: TASKS ═══ -->
    <div class="tab-content" id="tab-tasks">
        <div class="section" style="margin-top:24px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
                <h2 style="margin:0">Tasks</h2>
                <button onclick="openAddTask()" style="background:#27AE60;color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:14px">+ Add Task</button>
            </div>
            <table>
                <thead><tr>
                    <th style="width:40px"></th>
                    <th>Task</th>
                    <th>Prospect</th>
                    <th>Due Date</th>
                    <th style="width:40px"></th>
                </tr></thead>
                <tbody>{task_rows}</tbody>
            </table>
            {'<div class="empty-state"><p>No pending tasks. Add one above or use /todo in Telegram!</p></div>' if not task_rows else ''}
        </div>

        {'<div class="section"><h2>Recently Completed</h2><table><thead><tr><th style="width:40px"></th><th>Task</th><th>Prospect</th><th>Completed</th><th style="width:40px"></th></tr></thead><tbody>' + completed_rows + '</tbody></table></div>' if completed_rows else ''}
    </div><!-- end tab-tasks -->
```

- [ ] **Step 6: Add task modal HTML**

After the existing edit modal closing `</div>`, add:

```python
<!-- Task Modal -->
<div class="modal-overlay" id="taskModal">
<div class="modal" style="max-width:500px">
    <h2>Add Task</h2>
    <div class="form-row">
        <div style="flex:1"><label>Task</label><input id="tTitle" type="text" placeholder="What needs to be done?"></div>
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
```

- [ ] **Step 7: Add task JavaScript**

Add in the `<script>` section, after the existing JS:

```javascript
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
```

- [ ] **Step 8: Verify compilation**

Run: `python3 -c "import py_compile; py_compile.compile('dashboard.py', doraise=True); print('OK')"`
Expected: OK

- [ ] **Step 9: Run full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add dashboard.py
git commit -m "feat: add Tasks tab with CRUD API, modal, and inline actions"
```

---

## Chunk 5: Final Integration & Push

### Task 5: Integration test and deploy

- [ ] **Step 1: Run full test suite one final time**

Run: `python3 -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify all files compile**

Run: `python3 -c "import py_compile; [py_compile.compile(f, doraise=True) for f in ['bot.py','dashboard.py','db.py','scheduler.py']]; print('All OK')"`
Expected: All OK

- [ ] **Step 3: Push to origin**

```bash
git push origin master
```

Railway auto-deploys on push.
