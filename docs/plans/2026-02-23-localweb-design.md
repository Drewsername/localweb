# localweb Design

## Overview

A home control system running on a Raspberry Pi with a Flask API backend and Vite + React + TypeScript + Tailwind frontend. Deployed via cron-based git pull from its own GitHub repo.

## Architecture

```
localweb/
├── backend/              # Flask API
│   ├── app.py            # Flask app entry point
│   ├── drivers/
│   │   └── eink.py       # Inky wHAT driver (hello world)
│   └── requirements.txt
├── frontend/             # Vite + React + TS + Tailwind
│   ├── src/
│   ├── package.json
│   ├── vite.config.ts
│   └── tailwind.config.js
├── deploy/
│   ├── update.sh          # Git pull + restart services script
│   ├── force-update.sh    # Manual trigger: SSH and run this to update immediately
│   └── localweb.crontab   # Cron entry (runs update.sh every 5 min)
├── .gitignore
└── README.md
```

## Components

### Backend (Flask)
- Flask serves the API
- `/api/eink` endpoint triggers the hello world display on the Inky wHAT
- `InkyHandler` class cleaned up from existing eink.py (no Dexcom, no credentials)

### Frontend (Vite + React + TS + Tailwind)
- Simple page with a button to trigger the e-ink hello world
- Will grow into full control dashboard

### Deployment
- Cron job on the Pi runs `update.sh` every 5 minutes
- `update.sh` does `git pull` and restarts services if changes detected
- `force-update.sh` for manual immediate updates (run on Pi or via SSH)
- GitHub repo: `localweb` (separate from drewbermudezdotcom)

## Not included (future)
- Dexcom glucose integration
- Guest detection / drewbermudez.com integration
- Authentication
