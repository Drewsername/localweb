#!/bin/bash
# Auto-deploy script for localweb
# Pulls latest from GitHub and restarts services if changes detected
# Frontend is pre-built and committed — no Node needed on the Pi

REPO_DIR="/home/pi/localweb"
LOG_FILE="/home/pi/localweb/deploy/update.log"

cd "$REPO_DIR" || exit 1

# Fetch latest
git fetch origin main --quiet

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

echo "[$(date)] Updating from $LOCAL to $REMOTE" >> "$LOG_FILE"

git pull origin main --quiet

# Reinstall backend deps if requirements.txt changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "backend/requirements.txt"; then
    echo "[$(date)] Updating backend dependencies..." >> "$LOG_FILE"
    cd "$REPO_DIR/backend" && source venv/bin/activate && pip install -r requirements.txt
fi

# Restart backend
echo "[$(date)] Restarting localweb..." >> "$LOG_FILE"
sudo systemctl restart localweb

echo "[$(date)] Update complete" >> "$LOG_FILE"
