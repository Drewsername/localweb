# localweb

Home control system running on a Raspberry Pi.

## Stack

- **Backend:** Python / Flask
- **Frontend:** TypeScript / React / Vite / Tailwind CSS
- **Hardware:** Inky wHAT e-ink display

## Development

### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Deployment

This project auto-deploys to the Raspberry Pi via cron. See `deploy/README.md`.

To force an immediate update on the Pi:

```bash
ssh pi@<pi-address> '/home/pi/localweb/deploy/force-update.sh'
```
