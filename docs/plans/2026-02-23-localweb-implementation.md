# localweb Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Set up localweb as a standalone repo with Flask backend, Vite+React+TS+Tailwind frontend, e-ink hello world driver, and cron-based auto-deploy to a Raspberry Pi.

**Architecture:** Flask API serves endpoints that control hardware (starting with Inky wHAT e-ink display). Vite frontend provides the control dashboard UI. Deploy script on the Pi polls GitHub every 5 minutes via cron, with a force-update script for immediate pulls.

**Tech Stack:** Python 3 / Flask, TypeScript / React / Vite / Tailwind CSS, Inky wHAT (PIL), bash/cron for deployment

---

### Task 1: Initialize standalone git repo and create GitHub remote

**Files:**
- Create: `.gitignore`
- Create: `README.md`
- Delete: `eink.py` (will be recreated in proper location)
- Remove: existing `.git` (this is currently a subfolder of personalsite)

**Step 1: Remove localweb from the parent personalsite repo's tracking**

The `localweb/` folder currently lives inside the `personalsite` git repo. We need to:
1. Remove `localweb/` from the parent repo's tracking (but keep the files)
2. Initialize a fresh git repo inside `localweb/`

```bash
# From the parent repo root (drewbermudezdotcom/)
cd /c/Users/Drew/Desktop/drewbermudezdotcom
git rm -r --cached localweb/
echo "localweb/" >> .gitignore
git add .gitignore
git commit -m "chore: remove localweb subfolder (moved to own repo)"
```

**Step 2: Initialize fresh git repo in localweb/**

```bash
cd /c/Users/Drew/Desktop/drewbermudezdotcom/localweb
git init
```

**Step 3: Create .gitignore**

```gitignore
# Python
__pycache__/
*.pyc
venv/
.env

# Node
node_modules/
dist/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
```

**Step 4: Create README.md**

```markdown
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
source venv/bin/activate  # On Pi: source venv/bin/activate
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
```

**Step 5: Delete old eink.py from root**

```bash
rm eink.py
```

**Step 6: Create GitHub repo and push**

```bash
gh repo create localweb --public --source=. --remote=origin
git add .
git commit -m "Initial commit: project structure and docs"
git push -u origin main
```

---

### Task 2: Set up Flask backend with e-ink hello world

**Files:**
- Create: `backend/app.py`
- Create: `backend/drivers/__init__.py`
- Create: `backend/drivers/eink.py`
- Create: `backend/requirements.txt`

**Step 1: Create requirements.txt**

```txt
flask>=3.0
flask-cors>=4.0
inky[rpi]>=2.0
Pillow>=10.0
```

**Step 2: Create the e-ink driver**

Create `backend/drivers/__init__.py` (empty file).

Create `backend/drivers/eink.py`:

```python
from inky import InkyWHAT
from PIL import Image, ImageFont, ImageDraw


class InkyHandler:
    def __init__(self):
        self.inky_display = InkyWHAT("red")
        self.inky_display.set_border(self.inky_display.WHITE)

    def clear(self):
        self.img = Image.new("P", (self.inky_display.WIDTH, self.inky_display.HEIGHT))
        self.draw = ImageDraw.Draw(self.img)

    def draw_text(self, message, size=48, position="c", color=None):
        if color is None:
            color = self.inky_display.RED
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        bbox = font.getbbox(message)
        m_w, m_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d_w, d_h = self.inky_display.WIDTH, self.inky_display.HEIGHT

        if position == "c":
            x = (d_w - m_w) // 2
            y = (d_h - m_h) // 2
        elif position == "br":
            x = d_w - m_w
            y = d_h - m_h
        elif position == "tc":
            x = (d_w - m_w) // 2
            y = 0
        else:
            x, y = 0, 0

        self.draw.text((x, y), message, color, font)

    def show(self):
        img = self.img.rotate(180)
        self.inky_display.set_image(img)
        self.inky_display.show()

    def hello_world(self):
        self.clear()
        self.draw_text("Hello World!", size=64, position="c")
        self.show()
```

**Step 3: Create Flask app**

Create `backend/app.py`:

```python
import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Only import e-ink driver on the Pi (it requires hardware)
eink = None
if os.environ.get("LOCALWEB_ENV") != "dev":
    try:
        from drivers.eink import InkyHandler
        eink = InkyHandler()
    except Exception as e:
        print(f"E-ink not available: {e}")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "eink_available": eink is not None})


@app.post("/api/eink/hello")
def eink_hello():
    if eink is None:
        return jsonify({"error": "E-ink display not available"}), 503
    eink.hello_world()
    return jsonify({"message": "Hello World displayed on e-ink"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
```

**Step 4: Commit**

```bash
git add backend/
git commit -m "feat: add Flask backend with e-ink hello world driver"
```

---

### Task 3: Set up Vite + React + TypeScript + Tailwind frontend

**Step 1: Scaffold Vite project**

```bash
cd /c/Users/Drew/Desktop/drewbermudezdotcom/localweb
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss @tailwindcss/vite
```

**Step 2: Configure Tailwind via Vite plugin**

Edit `frontend/vite.config.ts`:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:5000",
    },
  },
});
```

**Step 3: Add Tailwind import to CSS**

Replace contents of `frontend/src/index.css`:

```css
@import "tailwindcss";
```

**Step 4: Create the main App component**

Replace contents of `frontend/src/App.tsx`:

```tsx
import { useState } from "react";

function App() {
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function sendHello() {
    setLoading(true);
    setStatus(null);
    try {
      const res = await fetch("/api/eink/hello", { method: "POST" });
      const data = await res.json();
      setStatus(res.ok ? data.message : data.error);
    } catch {
      setStatus("Failed to connect to backend");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
      <div className="text-center space-y-6">
        <h1 className="text-4xl font-bold">localweb</h1>
        <p className="text-gray-400">Home control system</p>
        <button
          onClick={sendHello}
          disabled={loading}
          className="px-6 py-3 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded-lg font-medium transition-colors"
        >
          {loading ? "Sending..." : "Say Hello (E-Ink)"}
        </button>
        {status && (
          <p className="text-sm text-gray-300">{status}</p>
        )}
      </div>
    </div>
  );
}

export default App;
```

**Step 5: Clean up Vite boilerplate**

Remove files that won't be used:
- `frontend/src/App.css`
- `frontend/src/assets/react.svg`
- `frontend/public/vite.svg`

**Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: add Vite + React + TS + Tailwind frontend"
```

---

### Task 4: Create deployment scripts

**Files:**
- Create: `deploy/update.sh`
- Create: `deploy/force-update.sh`
- Create: `deploy/localweb.crontab`
- Create: `deploy/README.md`

**Step 1: Create update.sh**

```bash
#!/bin/bash
# Auto-deploy script for localweb
# Pulls latest from GitHub and restarts services if changes detected

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

# Rebuild frontend if package.json changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "frontend/package.json"; then
    echo "[$(date)] Rebuilding frontend..." >> "$LOG_FILE"
    cd frontend && npm install && npm run build && cd ..
fi

# Reinstall backend deps if requirements.txt changed
if git diff "$LOCAL" "$REMOTE" --name-only | grep -q "backend/requirements.txt"; then
    echo "[$(date)] Updating backend dependencies..." >> "$LOG_FILE"
    cd backend && source venv/bin/activate && pip install -r requirements.txt && cd ..
fi

# Restart services
echo "[$(date)] Restarting services..." >> "$LOG_FILE"
sudo systemctl restart localweb-backend
sudo systemctl restart localweb-frontend

echo "[$(date)] Update complete" >> "$LOG_FILE"
```

**Step 2: Create force-update.sh**

```bash
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
```

**Step 3: Create crontab file**

```crontab
# localweb auto-deploy: check for updates every 5 minutes
*/5 * * * * /home/pi/localweb/deploy/update.sh
```

**Step 4: Create deploy/README.md**

```markdown
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
```

**Step 5: Commit**

```bash
git add deploy/
git commit -m "feat: add deployment scripts with cron auto-update and force-update"
```

---

### Task 5: Push everything to GitHub

**Step 1: Push all commits**

```bash
git push origin main
```

---
