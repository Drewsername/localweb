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

# Install librespot if not present
if ! command -v librespot &> /dev/null; then
    echo "Installing librespot..."
    curl -sL https://github.com/librespot-org/librespot/releases/latest/download/librespot-linux-armhf.tar.gz | tar xz -C /usr/local/bin/ || echo "librespot install failed — install manually"
fi

# Create audio pipe if needed
test -p /tmp/librespot-pipe || mkfifo /tmp/librespot-pipe

# Enable and restart librespot
sudo cp "$REPO_DIR/deploy/localweb-librespot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable localweb-librespot
sudo systemctl restart localweb-librespot

echo "Done! localweb and librespot are up to date."
