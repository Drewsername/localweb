# Deployment

The frontend is built on the dev machine and committed to the repo.
The Pi only runs the Flask backend which serves both the API and the built frontend.

## First-time Pi setup

```bash
# Clone the repo
cd /home/pi
git clone https://github.com/Drewsername/localweb.git
cd localweb

# Backend setup (using pyenv Python 3.11)
cd backend
~/.pyenv/versions/3.11.*/bin/python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..

# Make scripts executable
chmod +x deploy/update.sh deploy/force-update.sh

# Install cron job
crontab deploy/localweb.crontab

# Install and start the systemd service
sudo cp deploy/localweb.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable localweb
sudo systemctl start localweb
```

## Force update

From your dev machine:

```bash
ssh pi@10.0.0.74 '/home/pi/localweb/deploy/force-update.sh'
```

## Logs

Check the auto-update log:

```bash
cat /home/pi/localweb/deploy/update.log
```

Check the service log:

```bash
sudo journalctl -u localweb -f
```
