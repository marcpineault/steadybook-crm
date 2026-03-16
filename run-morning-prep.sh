#!/bin/bash
# Morning Prep — Quick daily intelligence run
# Called by cron at 6 AM. Lighter than the nightly run.

REPO_DIR="/Users/map98/Desktop/calm-money-bot"
LOG_DIR="${REPO_DIR}/logs/autonomous"
DATE=$(date +%Y-%m-%d)
DAY_OF_WEEK=$(date +%A)
ERROR_LOG="${LOG_DIR}/errors.log"
LOCKFILE="/tmp/calm-money-bot-autonomous.lock"
PROMPT_FILE="${REPO_DIR}/prompts/morning-prep.txt"

mkdir -p "$LOG_DIR"
mkdir -p "${REPO_DIR}/content/morning-prep"

# Prevent concurrent runs (shared lock with nightly)
exec 200>"$LOCKFILE"
flock -n 200 || { echo "[$DATE] Morning prep skipped — autonomous run in progress" >> "$ERROR_LOG"; exit 1; }

cd "$REPO_DIR"

echo "[$DATE] Starting morning prep (${DAY_OF_WEEK})" >> "$ERROR_LOG"

# Pull latest
git pull --rebase origin master 2>>"$ERROR_LOG" || {
    git rebase --abort 2>/dev/null
    echo "[$DATE] Morning prep: git pull failed" >> "$ERROR_LOG"
    exit 1
}

# Run Claude Code — shorter timeout and lower budget for morning prep
timeout 900 claude --print \
    --dangerously-skip-permissions \
    --max-budget-usd 2 \
    --prompt "Today is ${DAY_OF_WEEK}, ${DATE}. $(cat "$PROMPT_FILE")" \
    2>>"$ERROR_LOG"

# Push any commits
cd "$REPO_DIR"
git push origin master 2>>"$ERROR_LOG" && \
    echo "[$DATE] Morning prep complete — pushed" >> "$ERROR_LOG" || \
    echo "[$DATE] Morning prep — nothing to push" >> "$ERROR_LOG"
