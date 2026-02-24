#!/bin/bash
# Force immediate update - run this on the Pi or via SSH:
#   ssh pi@<pi-address> '/home/pi/localweb/deploy/force-update.sh'

REPO_DIR="/home/pi/localweb"

cd "$REPO_DIR" || exit 1

echo "Force pulling latest from GitHub..."
git pull origin main

echo "Rebuilding frontend..."
cd frontend && npm install && npm run build && cd ..

echo "Updating backend dependencies..."
cd backend && source venv/bin/activate && pip install -r requirements.txt && cd ..

echo "Restarting services..."
sudo systemctl restart localweb-backend
sudo systemctl restart localweb-frontend

echo "Done! localweb is up to date."
