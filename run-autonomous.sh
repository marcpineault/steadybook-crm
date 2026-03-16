#!/bin/bash
# Autonomous Code Improvement — Nightly Run
# Called by cron. Launches Claude Code to audit and improve the codebase.

REPO_DIR="/Users/map98/Desktop/calm-money-bot"
WEBSITE_DIR="/Users/map98/Desktop/Pineault-wealth"
LOG_DIR="${REPO_DIR}/logs/autonomous"
DATE=$(date +%Y-%m-%d)
DAY_OF_WEEK=$(date +%A)
ERROR_LOG="${LOG_DIR}/errors.log"
LOCKFILE="/tmp/calm-money-bot-autonomous.lock"
PROMPT_FILE="${REPO_DIR}/prompts/autonomous-system.txt"

mkdir -p "$LOG_DIR"

# Prevent concurrent runs (macOS-compatible — no flock)
if [ -f "$LOCKFILE" ]; then
    # Check if the PID in the lockfile is still running
    old_pid=$(cat "$LOCKFILE" 2>/dev/null)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "[$DATE] Another run is already in progress (PID $old_pid)" >> "$ERROR_LOG"
        exit 1
    fi
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

cd "$REPO_DIR"

echo "[$DATE] Starting autonomous run (${DAY_OF_WEEK})" >> "$ERROR_LOG"

# Pull latest for bot repo (abort rebase on conflict)
git pull --rebase origin master 2>>"$ERROR_LOG" || {
    git rebase --abort 2>/dev/null
    echo "[$DATE] git pull --rebase failed (calm-money-bot)" >> "$ERROR_LOG"
    exit 1
}

# Pull latest for website repo if it exists
if [ -d "$WEBSITE_DIR" ]; then
    cd "$WEBSITE_DIR"
    git pull --rebase origin main 2>>"$ERROR_LOG" || {
        git rebase --abort 2>/dev/null
        echo "[$DATE] git pull --rebase failed (Pineault-wealth) — continuing" >> "$ERROR_LOG"
    }
    cd "$REPO_DIR"
fi

# Run Claude Code with autonomous prompt (1 hour timeout, $5 budget cap)
gtimeout 3600 claude --print \
    --dangerously-skip-permissions \
    --max-budget-usd 5 \
    --prompt "Today is ${DAY_OF_WEEK}, ${DATE}. $(cat "$PROMPT_FILE")" \
    2>>"$ERROR_LOG"

# Verify tests pass before pushing
cd "$REPO_DIR"
pytest_exit=0
python3 -m pytest tests/ --tb=short -q 2>>"$ERROR_LOG" || pytest_exit=$?

if [ $pytest_exit -eq 0 ]; then
    git push origin master 2>>"$ERROR_LOG"
    echo "[$DATE] Bot repo — changes pushed" >> "$ERROR_LOG"
else
    echo "[$DATE] Tests failed after autonomous run — not pushing" >> "$ERROR_LOG"
    # Notify Marc via Telegram if env vars are available
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="Autonomous run failed — tests didn't pass. Check logs/autonomous/errors.log" \
            >/dev/null 2>&1
    fi
fi

# Push website repo changes if any were made
if [ -d "$WEBSITE_DIR" ]; then
    cd "$WEBSITE_DIR"
    if [ -n "$(git status --porcelain)" ] || [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main 2>/dev/null)" ]; then
        git push origin main 2>>"$ERROR_LOG" && \
            echo "[$DATE] Website repo — changes pushed" >> "$ERROR_LOG" || \
            echo "[$DATE] Website repo — push failed" >> "$ERROR_LOG"
    fi
fi

echo "[$DATE] Autonomous run complete" >> "$ERROR_LOG"
