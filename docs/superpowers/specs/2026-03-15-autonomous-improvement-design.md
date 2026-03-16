# Autonomous Code Improvement — Design Spec

## Overview

Set up a nightly cron job on Marc's spare desktop that launches Claude Code autonomously. Each run reads a standing instruction file (`AUTONOMOUS.md`), works through the checklist — auditing code, making improvements, researching SEO, running tests — commits changes, pushes to origin, and writes a daily log.

**Goal:** The system improves itself every night without Marc's involvement. He walks in each morning and sees what was done via git log and daily summaries.

## Architecture

```
┌──────────────────────────────┐
│     Spare Desktop (macOS)    │
│                              │
│  Cron (midnight daily)       │
│     │                        │
│     ▼                        │
│  run-autonomous.sh           │
│     │                        │
│     ├─ git pull origin       │
│     ├─ claude --prompt ...   │
│     │    ├─ Read AUTONOMOUS.md│
│     │    ├─ Audit & improve  │
│     │    ├─ Run tests        │
│     │    ├─ Commit changes   │
│     │    └─ Write daily log  │
│     ├─ git push origin       │
│     └─ (exit)                │
│                              │
└──────────────────────────────┘
```

## Components

### 1. AUTONOMOUS.md (repo root)

A natural language checklist that defines what Claude Code should do each run. Marc can edit this anytime — add one-off tasks (checkboxes), modify standing instructions, or adjust priorities.

Structure:
- **Every Run** — tasks that execute every night (test suite, code quality, security checks)
- **Weekly** — tasks that run once per week (SEO research, prompt optimization, deeper audits)
- **As Needed** — one-off checkbox tasks that get checked off when complete

Example content:

```markdown
# Autonomous Work — Standing Instructions

## Every Run
- Pull latest, run full test suite, fix any failures
- Check for Python dependency security vulnerabilities (pip audit)
- Review recently changed files for code quality improvements
- Keep bot.py under 3000 lines — extract if growing

## Weekly (run on Sunday night)
- Research SEO opportunities for calmmoney.ca — save to content/seo-research.md
- Audit GPT prompts across all modules for cost/quality optimization
- Check dashboard.py for UX improvements
- Review scheduler timing — is notification volume still appropriate?

## As Needed
- [ ] One-off tasks Marc adds here get completed and checked off
```

### 2. run-autonomous.sh (repo root)

Shell script that cron calls. Handles:
- `cd` to repo directory
- `git pull --rebase origin master`
- Launch Claude Code with the autonomous prompt
- After Claude exits, verify tests pass
- `git push origin master` (only if tests pass)
- Log any errors to `logs/autonomous/errors.log`

```bash
#!/bin/bash
set -euo pipefail

REPO_DIR="/Users/map98/Desktop/calm-money-bot"
LOG_DIR="${REPO_DIR}/logs/autonomous"
DATE=$(date +%Y-%m-%d)
ERROR_LOG="${LOG_DIR}/errors.log"

mkdir -p "$LOG_DIR"

cd "$REPO_DIR"

# Pull latest
git pull --rebase origin master 2>>"$ERROR_LOG" || {
    echo "[$DATE] git pull failed" >> "$ERROR_LOG"
    exit 1
}

# Run Claude Code with autonomous prompt
claude --print \
    --max-turns 50 \
    --prompt "Read AUTONOMOUS.md in this repo. Work through every applicable task. For each change: write tests first, make the change, verify tests pass, commit with a clear message. When done, write a summary to logs/autonomous/${DATE}.md. Do NOT push — the wrapper script handles that. Do NOT ask questions — make your best judgment. If something is unclear or risky, skip it and note why in the log." \
    2>>"$ERROR_LOG"

# Verify tests pass before pushing
cd "$REPO_DIR"
python3 -m pytest tests/ --tb=short -q 2>>"$ERROR_LOG"
if [ $? -eq 0 ]; then
    git push origin master 2>>"$ERROR_LOG"
else
    echo "[$DATE] Tests failed after autonomous run — not pushing" >> "$ERROR_LOG"
fi
```

### 3. Daily Log (logs/autonomous/YYYY-MM-DD.md)

Written by Claude Code at the end of each run. Format:

```markdown
# Autonomous Run — YYYY-MM-DD

## Changes Made
- [commit message]: [1-line description of what and why]
- [commit message]: [1-line description]

## Tests
- N passed, N failed (if any failures: what and why)

## Research Notes
- [Any SEO findings, content ideas, or insights saved to content/]

## Skipped
- [Tasks from AUTONOMOUS.md that were skipped and why]

## AUTONOMOUS.md Updates
- [Any checkbox tasks completed]
- [Any standing tasks that may need revision]
```

### 4. Cron Configuration

```cron
# Run autonomous improvement at midnight daily
0 0 * * * /Users/map98/Desktop/calm-money-bot/run-autonomous.sh >> /Users/map98/Desktop/calm-money-bot/logs/autonomous/cron.log 2>&1
```

## Safety Guardrails

1. **Tests must pass before push** — if tests break, changes stay local and the error is logged. Marc can review and decide what to do.
2. **No destructive operations** — Claude is instructed to never delete files, drop tables, force-push, or make breaking schema changes.
3. **No production deploys** — script pushes to origin but Railway deploy is controlled separately.
4. **Max turns cap (50)** — prevents runaway sessions that burn API credits.
5. **Clear commit messages** — every change is traceable in git history.
6. **Daily log** — full transparency. Marc reads the log, sees exactly what happened.
7. **Skip-and-log for uncertainty** — if a task is unclear or risky, Claude skips it and explains why in the log rather than guessing.

## What It Can Do

### Day 1 Capabilities
- Fix test failures and regressions
- Code quality improvements (extract functions, simplify logic, improve naming)
- Add missing test coverage
- Check and update Python dependencies for security vulnerabilities
- Performance optimizations (query optimization, unnecessary API calls)
- Documentation updates

### After SEO Engine Is Built (Phase 2)
- Research long-tail keywords for London, Ontario financial planning
- Generate blog post drafts and save to content/
- Audit calmmoney.ca for technical SEO issues
- Monitor competitor content and surface opportunities

### After Bot Self-Learning Is Built (Phase 3)
- Analyze outcome tracking data and adjust GPT prompts
- Optimize follow-up draft quality based on Marc's edit patterns
- Tune scheduling and notification frequency based on engagement data

## File Structure

```
calm-money-bot/
├── AUTONOMOUS.md              # Standing instructions (Marc edits)
├── run-autonomous.sh          # Cron entry point
├── logs/
│   └── autonomous/
│       ├── 2026-03-16.md      # Daily run summaries
│       ├── 2026-03-17.md
│       ├── errors.log         # Script-level errors
│       └── cron.log           # Cron output
└── content/
    ├── PLAYBOOK.md            # Content playbook (existing)
    └── seo-research.md        # SEO findings (generated)
```

## Constraints

- **Spare desktop must be running** — if it's off or asleep, cron doesn't fire
- **Claude Code CLI must be authenticated** — API key or login must be active
- **Network access required** — for git pull/push and any web research
- **API costs** — each nightly run uses Claude API tokens. Max-turns cap of 50 limits spend. Estimated ~$1-5 per run depending on complexity.

## Non-Goals

- Not building a custom agent framework (that's OpenClaw's territory)
- Not auto-deploying to production (Railway deploy stays manual)
- Not modifying the website repo yet (that's Phase 2: SEO Engine)
- Not replacing Marc's judgment on client-facing content (approval queue stays)
