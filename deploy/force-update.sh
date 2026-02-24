#!/bin/bash
# Force immediate update - run this on the Pi or via SSH:
#   ssh pi@10.0.0.74 '/home/pi/localweb/deploy/force-update.sh'
# Frontend is pre-built and committed — no Node needed on the Pi

REPO_DIR="/home/pi/localweb"

cd "$REPO_DIR" || exit 1

echo "Force pulling latest from GitHub..."
git pull origin main

echo "Updating backend dependencies..."
cd "$REPO_DIR/backend" && source venv/bin/activate && pip install -r requirements.txt

echo "Restarting localweb..."
sudo systemctl restart localweb

echo "Done! localweb is up to date."
