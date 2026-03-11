# Tasks & Reminders System — Design Spec

## Overview

Add a to-do list and reminder system to the Calm Money bot. Tasks can be general business items or linked to a specific prospect. Managed via Telegram commands and the web dashboard. Reminders surface in the morning briefing, auto-nag cycle, and at custom per-task times.

## Database

New `tasks` table in SQLite (`db.py`):

| Column | Type | Default | Purpose |
|--------|------|---------|---------|
| id | INTEGER PK | AUTO | Auto-increment |
| title | TEXT NOT NULL | — | Task description |
| prospect | TEXT | '' | Optional prospect name link |
| due_date | TEXT | NULL | YYYY-MM-DD |
| remind_at | TEXT | NULL | YYYY-MM-DD HH:MM (one-time custom reminder) |
| assigned_to | TEXT | '' | Telegram chat ID of assignee |
| created_by | TEXT | '' | Telegram chat ID of creator |
| status | TEXT | 'pending' | 'pending' or 'completed' |
| notes | TEXT | '' | Optional details |
| created_at | TEXT | datetime('now') | Auto timestamp |
| completed_at | TEXT | NULL | Set when marked done |

### DB functions (in `db.py`)

- `add_task(data: dict) -> dict` — Insert task, return the created row as dict (with id)
- `get_tasks(assigned_to=None, status='pending', prospect=None, limit=50) -> list[dict]` — Filtered query, ordered by due_date ASC (nulls last), then created_at DESC
- `complete_task(task_id: int, completed_by: str) -> str` — Set status='completed', completed_at=now. Only the assignee or admin can complete.
- `get_due_tasks(date_str: str) -> list[dict]` — Tasks with due_date = date_str and status='pending'
- `get_overdue_tasks() -> list[dict]` — Tasks with due_date < today and status='pending'
- `get_reminder_tasks(now_str: str) -> list[dict]` — Tasks with remind_at <= now_str and status='pending' and remind_at IS NOT NULL
- `clear_reminder(task_id: int)` — Set remind_at=NULL after firing (so it doesn't repeat)
- `delete_task(task_id: int, deleted_by: str) -> str` — Hard delete. Only assignee or admin.

## Telegram Commands

### `/todo <text>` — Create a task

Natural language input parsed by GPT to extract:
- **title** (required)
- **prospect** name (optional — matched against existing prospects)
- **due_date** (optional — "by Friday", "March 15", "tomorrow")
- **remind_at** (optional — "remind me Thursday 9am", "remind 3/14 2pm")
- **assigned_to** (optional — defaults to sender; "@marc" assigns to admin)

Examples:
```
/todo send John the brochure by Friday
/todo renew E&O insurance by March 20 remind me March 19 9am
/todo @marc review Sarah's application
```

Response: confirmation message with parsed fields shown.

Coworkers can create tasks for themselves or assign to Marc. Marc can assign to anyone.

### `/tasks` — List pending tasks

Shows the sender's pending tasks, ordered by:
1. Overdue (highlighted)
2. Due today
3. Due soon
4. No due date

Format:
```
Your tasks:
🔴 #12 Call John re: term quote (due Mar 8 — 3 days overdue)
🟡 #15 Send Sarah the brochure (due today)
📋 #18 Renew E&O insurance (due Mar 20)
📋 #22 Update CRM contacts (no due date)

Reply /done <id> to complete
```

Marc sees all tasks grouped by assignee. Coworkers see only their own.

### `/done <id>` — Complete a task

Marks task as completed. Accepts task ID number.

Response: "Completed: <title>"

## Dashboard

### New "Tasks" tab (5th tab)

**Layout:**
- Filter bar: All / Mine / Overdue / By Prospect dropdown
- Task list with columns: Status checkbox, Title, Prospect (linked), Due Date, Assigned To
- Overdue rows highlighted red
- Due-today rows highlighted yellow
- "Add Task" button opens a modal (title, prospect dropdown, due date, reminder, assign to)
- Clicking checkbox marks task complete via API

### API endpoints (in `dashboard.py`)

All require `@_require_auth` decorator:

- `POST /api/task` — Create task (JSON body: title, prospect, due_date, remind_at, assigned_to)
- `GET /api/tasks` — List tasks (query params: status, assigned_to, prospect)
- `PUT /api/task/<id>/complete` — Mark complete
- `DELETE /api/task/<id>` — Delete task

## Reminders Integration

### Morning briefing (`scheduler.py`)

Add a "Tasks" section to the 8AM daily briefing:
```
📋 Tasks due today:
  • Send John the brochure
  • Call Sarah re: renewal

⚠️ Overdue tasks:
  • Review pricing sheet (2 days overdue)
```

Only shown if there are due/overdue tasks. Sent to each user with their own tasks.

### Auto-nag cycle (existing 2-hour job)

Add overdue tasks to the nag messages. Uses the same `nag_state.json` cooldown to avoid repeating the same task alert within 24 hours. Key format: `task_{id}`.

### Custom reminders (new scheduler job)

New job: `check_task_reminders` — runs every 60 seconds.
- Queries `get_reminder_tasks(now)` for tasks with `remind_at <= now`
- Sends Telegram message to `assigned_to`: "Reminder: <title> (due <due_date>)"
- Calls `clear_reminder(task_id)` after sending so it fires only once

## Coworker Access

| Action | Marc (admin) | Coworkers |
|--------|-------------|-----------|
| Create task | Yes, assign to anyone | Yes, assign to self or Marc |
| View tasks | All tasks | Own tasks only |
| Complete task | Any task | Own tasks only |
| Delete task | Any task | Own tasks only |
| Dashboard tasks tab | All tasks, filter by assignee | Own tasks only (if dashboard access exists) |

## Files to modify

| File | Changes |
|------|---------|
| `db.py` | Add `tasks` table to `init_db()`, add CRUD functions |
| `bot.py` | Add `/todo`, `/tasks`, `/done` command handlers + GPT parsing |
| `dashboard.py` | Add Tasks tab, API endpoints, JS for task management |
| `scheduler.py` | Add tasks to morning briefing, auto-nag, new reminder job |

## Out of scope

- Recurring tasks (e.g., "every Monday")
- Task categories/tags
- Task comments or history
- File attachments on tasks
- Email/SMS reminders (Telegram only)
