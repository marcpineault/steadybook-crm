#!/bin/bash
# Autonomous Code Improvement — Nightly Run
# Called by cron. Launches Claude Code to audit and improve the codebase.

REPO_DIR="/Users/map98/Desktop/calm-money-bot"
LOG_DIR="${REPO_DIR}/logs/autonomous"
DATE=$(date +%Y-%m-%d)
DAY_OF_WEEK=$(date +%A)
ERROR_LOG="${LOG_DIR}/errors.log"
LOCKFILE="/tmp/calm-money-bot-autonomous.lock"
PROMPT_FILE="${REPO_DIR}/prompts/autonomous-system.txt"

mkdir -p "$LOG_DIR"

# Prevent concurrent runs
exec 200>"$LOCKFILE"
flock -n 200 || { echo "[$DATE] Another run is already in progress" >> "$ERROR_LOG"; exit 1; }

cd "$REPO_DIR"

echo "[$DATE] Starting autonomous run (${DAY_OF_WEEK})" >> "$ERROR_LOG"

# Pull latest (abort rebase on conflict)
git pull --rebase origin master 2>>"$ERROR_LOG" || {
    git rebase --abort 2>/dev/null
    echo "[$DATE] git pull --rebase failed" >> "$ERROR_LOG"
    exit 1
}

# Run Claude Code with autonomous prompt (1 hour timeout, $5 budget cap)
timeout 3600 claude --print \
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
    echo "[$DATE] Autonomous run completed — changes pushed" >> "$ERROR_LOG"
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
