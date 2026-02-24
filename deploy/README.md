# Deployment

## First-time Pi setup

```bash
# Clone the repo
cd /home/pi
git clone https://github.com/Drewsername/localweb.git
cd localweb

# Backend setup
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..

# Frontend setup
cd frontend
npm install
npm run build
cd ..

# Make scripts executable
chmod +x deploy/update.sh deploy/force-update.sh

# Install cron job
crontab deploy/localweb.crontab
```

## Force update

From your dev machine:

```bash
ssh pi@<pi-address> '/home/pi/localweb/deploy/force-update.sh'
```

## Logs

Check the auto-update log:

```bash
cat /home/pi/localweb/deploy/update.log
```
