# Accounts, Presence Detection & Govee Lighting Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add multi-user accounts with WiFi presence detection, e-ink welcome greetings, and Govee smart lighting control to Drewtopia.

**Architecture:** Flask backend with SQLite for user/settings storage, a Govee API service layer proxying all device commands, a background ARP scanner thread for presence detection, and a React frontend with React Router for onboarding/dashboard/lighting pages. Every app screen shows live device state; stored preferences are only applied on arrival.

**Tech Stack:** Flask, SQLite, python-dotenv, requests, React 19, React Router, Tailwind CSS 4, TypeScript

---

### Task 1: SQLite Database Layer

**Files:**
- Create: `backend/db.py`
- Create: `backend/data/` (directory, with `.gitkeep`)

**Step 1: Create the data directory**

```bash
mkdir -p backend/data
touch backend/data/.gitkeep
```

**Step 2: Add `.gitignore` for database files**

Add to `backend/.gitignore`:
```
data/*.db
.env
```

**Step 3: Write the database module**

Create `backend/db.py`:

```python
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "localweb.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            mac_address TEXT UNIQUE NOT NULL,
            ip_address TEXT,
            is_home INTEGER DEFAULT 0,
            last_seen DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            namespace TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, namespace, key)
        );
    """)
    conn.commit()
    conn.close()
```

**Step 4: Wire init_db into app startup**

In `backend/app.py`, add near the top after app creation:

```python
from db import init_db
init_db()
```

**Step 5: Add python-dotenv and requests to requirements.txt**

```
flask>=3.0
flask-cors>=4.0
inky[rpi]>=2.0
Pillow>=10.0
python-dotenv>=1.0
requests>=2.31
```

**Step 6: Create `backend/.env`**

```
GOVEE_API_KEY=9b585a28-4bd6-4f8b-bef3-bb858dc3de31
```

**Step 7: Load .env in app.py**

At the very top of `backend/app.py`:

```python
from dotenv import load_dotenv
load_dotenv()
```

**Step 8: Verify database initializes**

```bash
cd backend
LOCALWEB_ENV=dev python -c "from db import init_db; init_db(); print('OK')"
```

Expected: prints "OK", creates `backend/data/localweb.db`

**Step 9: Commit**

```bash
git add backend/db.py backend/data/.gitkeep backend/.gitignore backend/requirements.txt backend/.env
git commit -m "feat: add SQLite database layer with users and settings tables"
```

Note: Normally `.env` would not be committed, but since this is a private home network project with a non-sensitive Govee hobby API key, committing is acceptable. If the user prefers, skip committing `.env`.

---

### Task 2: User Registration & MAC Detection Routes

**Files:**
- Create: `backend/routes/users.py`
- Create: `backend/routes/__init__.py`
- Modify: `backend/app.py`

**Step 1: Create routes package**

Create `backend/routes/__init__.py` (empty file).

**Step 2: Write the users blueprint**

Create `backend/routes/users.py`:

```python
import subprocess
import re
import platform
from flask import Blueprint, jsonify, request
from db import get_db

users_bp = Blueprint("users", __name__)


def get_mac_for_ip(ip):
    """Look up MAC address for an IP via the system ARP table."""
    try:
        if platform.system() == "Windows":
            output = subprocess.check_output(["arp", "-a"], text=True)
        else:
            output = subprocess.check_output(["ip", "neigh"], text=True)

        for line in output.splitlines():
            if ip in line:
                match = re.search(
                    r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line
                )
                if match:
                    return match.group(0).lower().replace("-", ":")
    except Exception:
        pass
    return None


def get_client_ip():
    """Get the real client IP, respecting X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


@users_bp.post("/api/users/register")
def register():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    name = data["name"].strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    ip = get_client_ip()
    mac = get_mac_for_ip(ip)

    if not mac:
        return jsonify({"error": "Could not detect your device. Make sure you're on the same WiFi network."}), 400

    db = get_db()
    try:
        existing = db.execute(
            "SELECT id, name FROM users WHERE mac_address = ?", (mac,)
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE users SET name = ?, ip_address = ? WHERE id = ?",
                (name, ip, existing["id"]),
            )
            db.commit()
            user_id = existing["id"]
        else:
            cursor = db.execute(
                "INSERT INTO users (name, mac_address, ip_address) VALUES (?, ?, ?)",
                (name, mac, ip),
            )
            db.commit()
            user_id = cursor.lastrowid

        return jsonify({"id": user_id, "name": name})
    finally:
        db.close()


@users_bp.get("/api/users/me")
def me():
    ip = get_client_ip()
    mac = get_mac_for_ip(ip)

    if not mac:
        return jsonify({"error": "Device not recognized"}), 404

    db = get_db()
    try:
        user = db.execute(
            "SELECT id, name, is_home FROM users WHERE mac_address = ?", (mac,)
        ).fetchone()

        if not user:
            return jsonify({"error": "User not registered"}), 404

        return jsonify({"id": user["id"], "name": user["name"], "is_home": bool(user["is_home"])})
    finally:
        db.close()


@users_bp.get("/api/users/home")
def users_home():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, name, last_seen FROM users WHERE is_home = 1 ORDER BY last_seen DESC"
        ).fetchall()
        return jsonify([{"id": r["id"], "name": r["name"], "last_seen": r["last_seen"]} for r in rows])
    finally:
        db.close()
```

**Step 3: Register blueprint in app.py**

Add to `backend/app.py` after `CORS(app)`:

```python
from routes.users import users_bp
app.register_blueprint(users_bp)
```

**Step 4: Verify routes load**

```bash
cd backend
LOCALWEB_ENV=dev python -c "from app import app; print([r.rule for r in app.url_map.iter_rules()])"
```

Expected: should list `/api/users/register`, `/api/users/me`, `/api/users/home` among others.

**Step 5: Commit**

```bash
git add backend/routes/
git commit -m "feat: add user registration and MAC detection routes"
```

---

### Task 3: Settings Routes

**Files:**
- Create: `backend/routes/settings.py`
- Modify: `backend/app.py`

**Step 1: Write the settings blueprint**

Create `backend/routes/settings.py`:

```python
import json
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from db import get_db
from routes.users import get_client_ip, get_mac_for_ip

settings_bp = Blueprint("settings", __name__)


def get_current_user_id():
    """Resolve the current request to a user ID via IP -> MAC -> user lookup."""
    ip = get_client_ip()
    mac = get_mac_for_ip(ip)
    if not mac:
        return None
    db = get_db()
    user = db.execute("SELECT id FROM users WHERE mac_address = ?", (mac,)).fetchone()
    db.close()
    return user["id"] if user else None


@settings_bp.get("/api/settings")
def get_settings():
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"error": "User not recognized"}), 404

    db = get_db()
    try:
        rows = db.execute(
            "SELECT namespace, key, value FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchall()

        settings = {}
        for r in rows:
            ns = r["namespace"]
            if ns not in settings:
                settings[ns] = {}
            settings[ns][r["key"]] = json.loads(r["value"])

        return jsonify(settings)
    finally:
        db.close()


@settings_bp.put("/api/settings/<path:namespace>")
def update_settings(namespace):
    user_id = get_current_user_id()
    if not user_id:
        return jsonify({"error": "User not recognized"}), 404

    data = request.get_json()
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object of key-value pairs"}), 400

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        for key, value in data.items():
            db.execute(
                """INSERT INTO user_settings (user_id, namespace, key, value, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, namespace, key)
                   DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                (user_id, namespace, key, json.dumps(value), now),
            )
        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()
```

**Step 2: Register blueprint in app.py**

```python
from routes.settings import settings_bp
app.register_blueprint(settings_bp)
```

**Step 3: Commit**

```bash
git add backend/routes/settings.py backend/app.py
git commit -m "feat: add generic user settings routes"
```

---

### Task 4: Govee Service Layer

**Files:**
- Create: `backend/services/__init__.py`
- Create: `backend/services/govee.py`

**Step 1: Create services package**

Create `backend/services/__init__.py` (empty file).

**Step 2: Write the Govee service**

Create `backend/services/govee.py`:

```python
import os
import uuid
import requests

GOVEE_BASE = "https://openapi.api.govee.com"


class GoveeService:
    def __init__(self):
        self.api_key = os.environ.get("GOVEE_API_KEY", "")
        self._devices_cache = None

    @property
    def headers(self):
        return {
            "Content-Type": "application/json",
            "Govee-API-Key": self.api_key,
        }

    def get_devices(self):
        """GET /router/api/v1/user/devices — list all devices and their capabilities."""
        resp = requests.get(
            f"{GOVEE_BASE}/router/api/v1/user/devices",
            headers=self.headers,
        )
        resp.raise_for_status()
        data = resp.json()
        self._devices_cache = data.get("data", [])
        return self._devices_cache

    def _find_device(self, device_id):
        """Look up a device's SKU from cached device list."""
        if self._devices_cache is None:
            self.get_devices()
        for d in self._devices_cache:
            if d.get("device") == device_id:
                return d
        return None

    def get_device_state(self, device_id):
        """POST /router/api/v1/device/state — get current device state."""
        device = self._find_device(device_id)
        if not device:
            return None

        resp = requests.post(
            f"{GOVEE_BASE}/router/api/v1/device/state",
            headers=self.headers,
            json={
                "requestId": str(uuid.uuid4()),
                "payload": {
                    "sku": device["sku"],
                    "device": device_id,
                },
            },
        )
        resp.raise_for_status()
        return resp.json().get("payload", {})

    def control_device(self, device_id, capability):
        """POST /router/api/v1/device/control — send a control command.

        capability should be a dict with: type, instance, value
        e.g. {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "value": 1}
        """
        device = self._find_device(device_id)
        if not device:
            return None

        resp = requests.post(
            f"{GOVEE_BASE}/router/api/v1/device/control",
            headers=self.headers,
            json={
                "requestId": str(uuid.uuid4()),
                "payload": {
                    "sku": device["sku"],
                    "device": device_id,
                    "capability": capability,
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    def get_scenes(self, device_id):
        """POST /router/api/v1/device/scenes — get available dynamic scenes."""
        device = self._find_device(device_id)
        if not device:
            return None

        resp = requests.post(
            f"{GOVEE_BASE}/router/api/v1/device/scenes",
            headers=self.headers,
            json={
                "requestId": str(uuid.uuid4()),
                "payload": {
                    "sku": device["sku"],
                    "device": device_id,
                },
            },
        )
        resp.raise_for_status()
        return resp.json().get("payload", {})

    def apply_user_settings(self, settings_dict):
        """Apply a dict of {device_namespace: {key: value}} settings.

        settings_dict looks like:
        {
            "govee.<device_id>": {"power": 1, "brightness": 70, "colorRgb": 16711680},
            ...
        }

        Maps each key to the correct Govee capability type and sends the command.
        """
        capability_map = {
            "power": {"type": "devices.capabilities.on_off", "instance": "powerSwitch"},
            "brightness": {"type": "devices.capabilities.range", "instance": "brightness"},
            "colorRgb": {"type": "devices.capabilities.color_setting", "instance": "colorRgb"},
            "colorTemperatureK": {"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK"},
        }

        for ns, keys in settings_dict.items():
            if not ns.startswith("govee."):
                continue
            device_id = ns[len("govee."):]
            for key, value in keys.items():
                cap_template = capability_map.get(key)
                if not cap_template:
                    continue
                capability = {**cap_template, "value": value}
                try:
                    self.control_device(device_id, capability)
                except Exception as e:
                    print(f"Failed to apply {key}={value} to {device_id}: {e}")
```

**Step 3: Commit**

```bash
git add backend/services/
git commit -m "feat: add Govee API service layer with full device control"
```

---

### Task 5: Govee API Routes

**Files:**
- Create: `backend/routes/govee.py`
- Modify: `backend/app.py`

**Step 1: Write the Govee blueprint**

Create `backend/routes/govee.py`:

```python
import json
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from services.govee import GoveeService
from routes.settings import get_current_user_id
from db import get_db

govee_bp = Blueprint("govee", __name__)
govee = GoveeService()


@govee_bp.get("/api/govee/devices")
def list_devices():
    try:
        devices = govee.get_devices()
        return jsonify(devices)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@govee_bp.get("/api/govee/devices/<path:device_id>/state")
def device_state(device_id):
    try:
        state = govee.get_device_state(device_id)
        if state is None:
            return jsonify({"error": "Device not found"}), 404
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@govee_bp.post("/api/govee/devices/<path:device_id>/control")
def control_device(device_id):
    data = request.get_json()
    if not data or "capability" not in data:
        return jsonify({"error": "capability object is required"}), 400

    capability = data["capability"]

    try:
        result = govee.control_device(device_id, capability)
        if result is None:
            return jsonify({"error": "Device not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 502

    # Save as user's preferred setting if we can identify the user
    user_id = get_current_user_id()
    if user_id:
        _save_govee_setting(user_id, device_id, capability)

    return jsonify(result)


@govee_bp.get("/api/govee/devices/<path:device_id>/scenes")
def device_scenes(device_id):
    try:
        scenes = govee.get_scenes(device_id)
        if scenes is None:
            return jsonify({"error": "Device not found"}), 404
        return jsonify(scenes)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


def _save_govee_setting(user_id, device_id, capability):
    """Persist a Govee control action as the user's preferred setting."""
    instance = capability.get("instance", "")
    value = capability.get("value")

    # Map Govee instance names to our settings keys
    key_map = {
        "powerSwitch": "power",
        "brightness": "brightness",
        "colorRgb": "colorRgb",
        "colorTemperatureK": "colorTemperatureK",
    }

    key = key_map.get(instance, instance)
    namespace = f"govee.{device_id}"
    now = datetime.now(timezone.utc).isoformat()

    db = get_db()
    try:
        db.execute(
            """INSERT INTO user_settings (user_id, namespace, key, value, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, namespace, key)
               DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (user_id, namespace, key, json.dumps(value), now),
        )
        db.commit()
    finally:
        db.close()
```

**Step 2: Register blueprint in app.py**

```python
from routes.govee import govee_bp
app.register_blueprint(govee_bp)
```

**Step 3: Commit**

```bash
git add backend/routes/govee.py backend/app.py
git commit -m "feat: add Govee device control routes with auto-save settings"
```

---

### Task 6: Presence Scanner & E-ink Updates

**Files:**
- Create: `backend/services/presence.py`
- Modify: `backend/drivers/eink.py`
- Modify: `backend/app.py`

**Step 1: Add welcome and idle methods to InkyHandler**

Add to `backend/drivers/eink.py`:

```python
def welcome(self, name):
    self.clear()
    self.draw_text("Welcome home,", size=36, position="tc")
    self.draw_text(name + "!", size=52, position="c")
    self.show()

def idle(self):
    self.clear()
    self.draw_text("Drewtopia", size=56, position="c")
    self.show()
```

**Step 2: Write the presence scanner**

Create `backend/services/presence.py`:

```python
import subprocess
import re
import platform
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from db import get_db

SCAN_INTERVAL = 30  # seconds
DEPARTURE_THRESHOLD = 300  # 5 minutes


class PresenceScanner:
    def __init__(self, eink=None, govee=None):
        self.eink = eink
        self.govee = govee
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            try:
                self._scan()
            except Exception as e:
                print(f"Presence scan error: {e}")
            time.sleep(SCAN_INTERVAL)

    def _get_network_macs(self):
        """Parse ARP table for all MAC addresses on the local network."""
        macs = set()
        try:
            if platform.system() == "Windows":
                output = subprocess.check_output(["arp", "-a"], text=True)
            else:
                output = subprocess.check_output(["ip", "neigh"], text=True)

            for line in output.splitlines():
                match = re.search(
                    r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line
                )
                if match:
                    macs.add(match.group(0).lower().replace("-", ":"))
        except Exception as e:
            print(f"ARP scan failed: {e}")
        return macs

    def _scan(self):
        network_macs = self._get_network_macs()
        now = datetime.now(timezone.utc)
        departure_cutoff = now - timedelta(seconds=DEPARTURE_THRESHOLD)

        db = get_db()
        try:
            users = db.execute("SELECT id, name, mac_address, is_home FROM users").fetchall()

            newly_arrived = []
            anyone_home = False

            for user in users:
                mac = user["mac_address"]
                was_home = bool(user["is_home"])
                is_now_home = mac in network_macs

                if is_now_home:
                    anyone_home = True
                    db.execute(
                        "UPDATE users SET is_home = 1, last_seen = ? WHERE id = ?",
                        (now.isoformat(), user["id"]),
                    )
                    if not was_home:
                        newly_arrived.append(user)
                else:
                    # Only mark as departed if not seen for DEPARTURE_THRESHOLD
                    if was_home:
                        row = db.execute(
                            "SELECT last_seen FROM users WHERE id = ?",
                            (user["id"],),
                        ).fetchone()
                        if row["last_seen"]:
                            last = datetime.fromisoformat(row["last_seen"])
                            if last.tzinfo is None:
                                last = last.replace(tzinfo=timezone.utc)
                            if last < departure_cutoff:
                                db.execute(
                                    "UPDATE users SET is_home = 0 WHERE id = ?",
                                    (user["id"],),
                                )
                                anyone_home = anyone_home or False

            db.commit()

            # Handle arrivals — most recent arrival (last in the list processed) wins
            if newly_arrived:
                latest = newly_arrived[-1]
                self._on_arrival(latest, db)

            # Handle everyone departed
            if not anyone_home:
                still_home = db.execute(
                    "SELECT COUNT(*) as c FROM users WHERE is_home = 1"
                ).fetchone()
                if still_home["c"] == 0 and self.eink:
                    self.eink.idle()

        finally:
            db.close()

    def _on_arrival(self, user, db):
        """Handle a user arriving home."""
        name = user["name"]

        # Update e-ink display
        if self.eink:
            try:
                self.eink.welcome(name)
            except Exception as e:
                print(f"E-ink welcome failed: {e}")

        # Apply user's Govee settings
        if self.govee:
            try:
                rows = db.execute(
                    "SELECT namespace, key, value FROM user_settings WHERE user_id = ? AND namespace LIKE 'govee.%'",
                    (user["id"],),
                ).fetchall()

                settings = {}
                for r in rows:
                    ns = r["namespace"]
                    if ns not in settings:
                        settings[ns] = {}
                    settings[ns][r["key"]] = json.loads(r["value"])

                if settings:
                    self.govee.apply_user_settings(settings)
            except Exception as e:
                print(f"Failed to apply settings for {name}: {e}")
```

**Step 3: Start the scanner in app.py**

Add to `backend/app.py` after eink initialization, inside the `if __name__` block or as a startup hook:

```python
from services.presence import PresenceScanner
from services.govee import GoveeService

govee_service = GoveeService()

scanner = PresenceScanner(eink=eink, govee=govee_service)
scanner.start()
```

Note: The scanner should be started after app setup but it runs in a background thread, so place the `scanner.start()` call just before `app.run()`.

**Step 4: Commit**

```bash
git add backend/services/presence.py backend/drivers/eink.py backend/app.py
git commit -m "feat: add presence scanner with e-ink welcome and auto-apply settings"
```

---

### Task 7: Frontend — Install Dependencies & Set Up Routing

**Files:**
- Modify: `frontend/package.json` (via npm install)
- Create: `frontend/src/context/UserContext.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/main.tsx`

**Step 1: Install React Router**

```bash
cd frontend
npm install react-router-dom
```

**Step 2: Create the User context**

Create `frontend/src/context/UserContext.tsx`:

```tsx
import { createContext, useContext, useState, useEffect, type ReactNode } from "react";

interface User {
  id: number;
  name: string;
}

interface UserContextType {
  user: User | null;
  loading: boolean;
  login: (name: string) => Promise<void>;
  logout: () => void;
}

const UserContext = createContext<UserContextType | null>(null);

export function UserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const saved = localStorage.getItem("drewtopia_user");
    if (saved) {
      // Verify the saved user still matches this device
      fetch("/api/users/me")
        .then((r) => {
          if (r.ok) return r.json();
          throw new Error("not found");
        })
        .then((data) => setUser({ id: data.id, name: data.name }))
        .catch(() => {
          localStorage.removeItem("drewtopia_user");
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  async function login(name: string) {
    const res = await fetch("/api/users/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.error || "Registration failed");
    }
    const data = await res.json();
    const u = { id: data.id, name: data.name };
    setUser(u);
    localStorage.setItem("drewtopia_user", JSON.stringify(u));
  }

  function logout() {
    setUser(null);
    localStorage.removeItem("drewtopia_user");
  }

  return (
    <UserContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser() {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within UserProvider");
  return ctx;
}
```

**Step 3: Create page components**

Create `frontend/src/pages/Welcome.tsx`:

```tsx
import { useState } from "react";
import { useUser } from "../context/UserContext";
import { useNavigate } from "react-router-dom";

export default function Welcome() {
  const { login } = useUser();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError("");
    try {
      await login(name.trim());
      navigate("/home");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center p-6">
      <div className="text-center space-y-8 max-w-sm w-full">
        <div className="space-y-2">
          <h1 className="text-4xl font-bold">Welcome to Drewtopia!</h1>
          <p className="text-gray-400">Please share your name to get started.</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            autoFocus
            className="w-full px-4 py-3 bg-gray-900 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-red-500 text-lg"
          />
          <button
            type="submit"
            disabled={!name.trim() || submitting}
            className="w-full px-6 py-3 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded-lg font-medium transition-colors text-lg"
          >
            {submitting ? "Setting up..." : "Get Started"}
          </button>
          {error && <p className="text-red-400 text-sm">{error}</p>}
        </form>
      </div>
    </div>
  );
}
```

Create `frontend/src/pages/Home.tsx`:

```tsx
import { useUser } from "../context/UserContext";
import { Link } from "react-router-dom";

const apps = [
  {
    name: "Lighting",
    description: "Control your Govee smart lights",
    path: "/lights",
    icon: "💡",
  },
];

export default function Home() {
  const { user } = useUser();

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-8">
        <div>
          <h1 className="text-3xl font-bold">Welcome, {user?.name}!</h1>
          <p className="text-gray-400 mt-1">What would you like to control?</p>
        </div>
        <div className="grid gap-4">
          {apps.map((app) => (
            <Link
              key={app.path}
              to={app.path}
              className="block p-5 bg-gray-900 border border-gray-800 rounded-xl hover:border-gray-600 transition-colors active:bg-gray-800"
            >
              <div className="flex items-center gap-4">
                <span className="text-3xl">{app.icon}</span>
                <div>
                  <h2 className="text-lg font-semibold">{app.name}</h2>
                  <p className="text-sm text-gray-400">{app.description}</p>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
```

Create `frontend/src/pages/Lights.tsx`:

```tsx
import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";

interface DeviceCapability {
  type: string;
  instance: string;
  parameters: Record<string, unknown>;
}

interface Device {
  device: string;
  sku: string;
  deviceName: string;
  capabilities: DeviceCapability[];
}

interface DeviceState {
  capabilities?: Array<{
    type: string;
    instance: string;
    state: { value: unknown };
  }>;
}

export default function Lights() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [states, setStates] = useState<Record<string, DeviceState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch("/api/govee/devices");
      if (!res.ok) throw new Error("Failed to load devices");
      const data = await res.json();
      setDevices(data);

      // Fetch state for each device
      const stateEntries = await Promise.all(
        data.map(async (d: Device) => {
          try {
            const sr = await fetch(`/api/govee/devices/${encodeURIComponent(d.device)}/state`);
            if (sr.ok) {
              const sd = await sr.json();
              return [d.device, sd] as const;
            }
          } catch { /* skip */ }
          return [d.device, {}] as const;
        })
      );
      setStates(Object.fromEntries(stateEntries));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load devices");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDevices();
  }, [fetchDevices]);

  async function sendControl(deviceId: string, capability: Record<string, unknown>) {
    try {
      await fetch(`/api/govee/devices/${encodeURIComponent(deviceId)}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capability }),
      });
      // Refresh state after control
      const sr = await fetch(`/api/govee/devices/${encodeURIComponent(deviceId)}/state`);
      if (sr.ok) {
        const sd = await sr.json();
        setStates((prev) => ({ ...prev, [deviceId]: sd }));
      }
    } catch (err) {
      console.error("Control failed:", err);
    }
  }

  function getStateValue(deviceId: string, instance: string): unknown {
    const state = states[deviceId];
    if (!state?.capabilities) return undefined;
    const cap = state.capabilities.find((c) => c.instance === instance);
    return cap?.state?.value;
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading devices...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">&larr;</Link>
          <h1 className="text-2xl font-bold">Lighting</h1>
        </div>

        {error && <p className="text-red-400">{error}</p>}

        {devices.length === 0 && !error && (
          <p className="text-gray-400">No Govee devices found.</p>
        )}

        {devices.map((device) => (
          <DeviceCard
            key={device.device}
            device={device}
            getStateValue={(instance) => getStateValue(device.device, instance)}
            onControl={(cap) => sendControl(device.device, cap)}
          />
        ))}
      </div>
    </div>
  );
}

function DeviceCard({
  device,
  getStateValue,
  onControl,
}: {
  device: Device;
  getStateValue: (instance: string) => unknown;
  onControl: (capability: Record<string, unknown>) => void;
}) {
  const hasPower = device.capabilities.some((c) => c.instance === "powerSwitch");
  const hasBrightness = device.capabilities.some((c) => c.instance === "brightness");
  const hasColor = device.capabilities.some((c) => c.instance === "colorRgb");

  const powerOn = getStateValue("powerSwitch") === 1;
  const brightness = (getStateValue("brightness") as number) ?? 100;
  const colorInt = (getStateValue("colorRgb") as number) ?? 16777215;
  const colorHex = "#" + colorInt.toString(16).padStart(6, "0");

  return (
    <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{device.deviceName || device.sku}</h2>
        {hasPower && (
          <button
            onClick={() =>
              onControl({
                type: "devices.capabilities.on_off",
                instance: "powerSwitch",
                value: powerOn ? 0 : 1,
              })
            }
            className={`w-14 h-8 rounded-full transition-colors relative ${
              powerOn ? "bg-red-600" : "bg-gray-700"
            }`}
          >
            <span
              className={`absolute top-1 w-6 h-6 rounded-full bg-white transition-transform ${
                powerOn ? "left-7" : "left-1"
              }`}
            />
          </button>
        )}
      </div>

      {hasBrightness && (
        <div className="space-y-1">
          <label className="text-sm text-gray-400">Brightness: {brightness}%</label>
          <input
            type="range"
            min={1}
            max={100}
            value={brightness}
            onChange={(e) =>
              onControl({
                type: "devices.capabilities.range",
                instance: "brightness",
                value: Number(e.target.value),
              })
            }
            className="w-full accent-red-600"
          />
        </div>
      )}

      {hasColor && (
        <div className="space-y-1">
          <label className="text-sm text-gray-400">Color</label>
          <input
            type="color"
            value={colorHex}
            onChange={(e) => {
              const rgb = parseInt(e.target.value.slice(1), 16);
              onControl({
                type: "devices.capabilities.color_setting",
                instance: "colorRgb",
                value: rgb,
              });
            }}
            className="w-full h-10 rounded-lg border border-gray-700 bg-gray-800 cursor-pointer"
          />
        </div>
      )}
    </div>
  );
}
```

**Step 4: Rewrite App.tsx with routing**

Replace `frontend/src/App.tsx`:

```tsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { UserProvider, useUser } from "./context/UserContext";
import Welcome from "./pages/Welcome";
import Home from "./pages/Home";
import Lights from "./pages/Lights";

function AppRoutes() {
  const { user, loading } = useUser();

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/" element={user ? <Navigate to="/home" /> : <Welcome />} />
      <Route path="/home" element={user ? <Home /> : <Navigate to="/" />} />
      <Route path="/lights" element={user ? <Lights /> : <Navigate to="/" />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <UserProvider>
        <AppRoutes />
      </UserProvider>
    </BrowserRouter>
  );
}
```

**Step 5: Verify frontend builds**

```bash
cd frontend
npm run build
```

Expected: successful build with no type errors.

**Step 6: Commit**

```bash
git add frontend/
git commit -m "feat: add onboarding, dashboard, and Govee lighting control UI"
```

---

### Task 8: Wire Up app.py (Final Assembly)

**Files:**
- Modify: `backend/app.py` (final version with all pieces)

**Step 1: Write the final app.py**

Replace `backend/app.py` entirely:

```python
import os
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
from db import init_db

static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
app = Flask(__name__, static_folder=static_dir, static_url_path="")
CORS(app)

init_db()

# Register blueprints
from routes.users import users_bp
from routes.settings import settings_bp
from routes.govee import govee_bp

app.register_blueprint(users_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(govee_bp)

# E-ink driver (only on Pi hardware)
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


@app.get("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(404)
def fallback(e):
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    # Start presence scanner
    from services.presence import PresenceScanner
    from routes.govee import govee

    scanner = PresenceScanner(eink=eink, govee=govee)
    scanner.start()

    app.run(host="0.0.0.0", port=5000, debug=True)
```

**Step 2: Verify everything loads in dev mode**

```bash
cd backend
LOCALWEB_ENV=dev python -c "from app import app; print('Routes:', [r.rule for r in app.url_map.iter_rules() if r.rule.startswith('/api')])"
```

Expected: lists all API routes.

**Step 3: Commit**

```bash
git add backend/app.py
git commit -m "feat: wire up all backend components in app.py"
```

---

### Task 9: End-to-End Smoke Test

**Step 1: Start backend in dev mode**

```bash
cd backend
LOCALWEB_ENV=dev python app.py
```

**Step 2: In another terminal, start frontend dev server**

```bash
cd frontend
npm run dev
```

**Step 3: Verify in browser**

1. Open `http://localhost:5173` — should see "Welcome to Drewtopia!" page
2. Enter a name, submit — note: MAC detection will fail in dev mode (expected), so verify the error message shows gracefully
3. Visit `http://localhost:5173/lights` — should redirect to welcome (not logged in)
4. Test Govee API directly: `curl http://localhost:5000/api/govee/devices` — should return device list from Govee API
5. Health check: `curl http://localhost:5000/api/health` — should return `{"status": "ok", "eink_available": false}`

**Step 4: Build frontend for production**

```bash
cd frontend
npm run build
```

**Step 5: Final commit**

```bash
git add -A
git commit -m "chore: production build with full accounts and Govee integration"
```

---

### Summary of Files Created/Modified

**Created:**
- `backend/db.py` — SQLite database layer
- `backend/.env` — Govee API key
- `backend/.gitignore` — exclude db files and .env
- `backend/data/.gitkeep` — data directory
- `backend/routes/__init__.py` — routes package
- `backend/routes/users.py` — user registration & MAC detection
- `backend/routes/settings.py` — generic settings CRUD
- `backend/routes/govee.py` — Govee device control routes
- `backend/services/__init__.py` — services package
- `backend/services/govee.py` — Govee API client
- `backend/services/presence.py` — ARP presence scanner
- `frontend/src/context/UserContext.tsx` — user auth context
- `frontend/src/pages/Welcome.tsx` — onboarding page
- `frontend/src/pages/Home.tsx` — dashboard
- `frontend/src/pages/Lights.tsx` — lighting control

**Modified:**
- `backend/app.py` — full rewrite with blueprints, scanner, dotenv
- `backend/requirements.txt` — add python-dotenv, requests
- `backend/drivers/eink.py` — add welcome() and idle() methods
- `frontend/src/App.tsx` — rewrite with React Router
- `frontend/package.json` — add react-router-dom
