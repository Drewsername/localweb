# Nest Thermostat App Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Nest thermostat control app with multi-occupant temperature optimization to Drewtopia.

**Architecture:** NestService wraps Google SDM API (mirroring GoveeService), thermostat_optimizer computes optimal temp via weighted asymmetric quadratic discomfort, Flask routes expose REST endpoints, React frontend provides thermostat controls + preference setting + admin guardrails.

**Tech Stack:** Python/Flask, SQLite (existing), Google SDM REST API, OAuth2, React/TypeScript/Tailwind

**Design doc:** `docs/plans/2026-02-24-nest-thermostat-design.md`

---

### Task 1: Temperature Optimizer Service

The algorithm is pure logic with no external dependencies — easiest to test first.

**Files:**
- Create: `backend/services/thermostat_optimizer.py`
- Create: `backend/tests/test_thermostat_optimizer.py`

**Step 1: Write the failing tests**

```python
# backend/tests/test_thermostat_optimizer.py
import pytest
from services.thermostat_optimizer import compute_optimal_temp


def test_single_user_gets_their_preference():
    users = [{"preferred_temp": 72, "weight": 1.0}]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 72.0


def test_two_users_symmetric_weights():
    """Two users with equal weight — result should be slightly above midpoint due to cold asymmetry."""
    users = [
        {"preferred_temp": 68, "weight": 1.0},
        {"preferred_temp": 74, "weight": 1.0},
    ]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    # Midpoint is 71.0, cold asymmetry should push it slightly warmer
    assert 71.0 < result <= 72.0


def test_higher_weight_pulls_result():
    users = [
        {"preferred_temp": 68, "weight": 2.0},
        {"preferred_temp": 74, "weight": 1.0},
    ]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    # Should be pulled toward 68 compared to equal-weight case
    assert result < 71.0


def test_clamped_to_min():
    users = [{"preferred_temp": 60, "weight": 1.0}]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 65.0


def test_clamped_to_max():
    users = [{"preferred_temp": 85, "weight": 1.0}]
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 78.0


def test_empty_users_returns_none():
    result = compute_optimal_temp([], min_temp=65, max_temp=78)
    assert result is None


def test_cold_asymmetry_bias():
    """Verify cold side is penalized more than warm side.
    User at 72: candidate 70 (2 below) should cost more than candidate 74 (2 above)."""
    users = [{"preferred_temp": 72, "weight": 1.0}]
    # With only one user, result should be exactly their pref (within bounds)
    result = compute_optimal_temp(users, min_temp=65, max_temp=78)
    assert result == 72.0
```

**Step 2: Run tests to verify they fail**

Run: `cd /c/Users/Drew/Desktop/drewbermudezdotcom/localweb/backend && python -m pytest tests/test_thermostat_optimizer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.thermostat_optimizer'`

**Step 3: Write the implementation**

```python
# backend/services/thermostat_optimizer.py
"""Weighted asymmetric quadratic discomfort minimizer for multi-occupant thermostat."""

COLD_SENSITIVITY = 1.5  # Being too cold hurts 1.5x more than being too warm
WARM_SENSITIVITY = 1.0
STEP = 0.5  # Sweep in 0.5F increments


def compute_optimal_temp(users, min_temp=65, max_temp=78):
    """Compute the optimal temperature for all present users.

    Args:
        users: list of dicts with 'preferred_temp' (float) and 'weight' (float)
        min_temp: absolute minimum allowed temperature
        max_temp: absolute maximum allowed temperature

    Returns:
        Optimal temperature as float, or None if no users provided.
    """
    if not users:
        return None

    best_temp = None
    best_cost = float("inf")

    # Sweep candidate temps from min to max in STEP increments
    candidate = min_temp
    while candidate <= max_temp + 0.01:  # small epsilon for float comparison
        total_cost = 0.0
        for u in users:
            pref = u["preferred_temp"]
            w = u["weight"]
            diff = candidate - pref
            if diff < 0:
                # Too cold
                total_cost += w * COLD_SENSITIVITY * diff * diff
            else:
                # Too warm (or exact)
                total_cost += w * WARM_SENSITIVITY * diff * diff
        if total_cost < best_cost:
            best_cost = total_cost
            best_temp = candidate
        candidate += STEP

    return round(best_temp, 1) if best_temp is not None else None
```

**Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Drew/Desktop/drewbermudezdotcom/localweb/backend && python -m pytest tests/test_thermostat_optimizer.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add backend/services/thermostat_optimizer.py backend/tests/test_thermostat_optimizer.py
git commit -m "feat: add temperature optimization algorithm with asymmetric quadratic discomfort"
```

---

### Task 2: NestService — OAuth2 Token Management

**Files:**
- Create: `backend/services/nest.py`
- Modify: `backend/.gitignore` (or root `.gitignore`) — add `nest_tokens.json`

**Step 1: Write the NestService class with OAuth2 token handling**

```python
# backend/services/nest.py
import os
import json
import requests
from urllib.parse import urlencode

SDM_BASE = "https://smartdevicemanagement.googleapis.com/v1"
OAUTH_AUTH_URL = "https://nestservices.google.com/partnerconnections"
OAUTH_TOKEN_URL = "https://www.googleapis.com/oauth2/v4/token"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "nest_tokens.json")
SCOPES = "https://www.googleapis.com/auth/sdm.service"


class NestService:
    def __init__(self):
        self.project_id = os.environ.get("NEST_PROJECT_ID", "")
        self.client_id = os.environ.get("NEST_CLIENT_ID", "")
        self.client_secret = os.environ.get("NEST_CLIENT_SECRET", "")
        self._tokens = self._load_tokens()

    def _load_tokens(self):
        try:
            with open(TOKEN_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_tokens(self, tokens):
        self._tokens = tokens
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f)

    @property
    def is_authenticated(self):
        return bool(self._tokens.get("access_token"))

    def get_auth_url(self, redirect_uri):
        params = urlencode({
            "redirect_uri": redirect_uri,
            "access_type": "offline",
            "prompt": "consent",
            "client_id": self.client_id,
            "response_type": "code",
            "scope": SCOPES,
        })
        return f"{OAUTH_AUTH_URL}/{self.project_id}/auth?{params}"

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(OAUTH_TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        resp.raise_for_status()
        self._save_tokens(resp.json())

    def _refresh_token(self):
        refresh = self._tokens.get("refresh_token")
        if not refresh:
            raise RuntimeError("No refresh token — re-authorize via /api/nest/auth/url")
        resp = requests.post(OAUTH_TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        new_tokens = resp.json()
        # Preserve existing refresh_token if not returned
        new_tokens.setdefault("refresh_token", refresh)
        self._save_tokens(new_tokens)

    def _request(self, method, path, **kwargs):
        """Make an authenticated SDM API request, refreshing token on 401."""
        url = f"{SDM_BASE}/enterprises/{self.project_id}{path}"
        headers = {"Authorization": f"Bearer {self._tokens.get('access_token', '')}"}
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self._tokens['access_token']}"
            resp = requests.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()
```

**Step 2: Add `nest_tokens.json` to .gitignore**

Add this line to the root `.gitignore`:
```
nest_tokens.json
```

**Step 3: Commit**

```bash
git add backend/services/nest.py .gitignore
git commit -m "feat: add NestService with OAuth2 token management"
```

---

### Task 3: NestService — Device Methods

**Files:**
- Modify: `backend/services/nest.py` — add device methods

**Step 1: Add get_devices, get_device_state, set_temperature, set_mode, set_eco**

Append to the `NestService` class in `backend/services/nest.py`:

```python
    def get_devices(self):
        """List all thermostat devices."""
        data = self._request("GET", "/devices")
        devices = []
        for d in data.get("devices", []):
            traits = d.get("traits", {})
            # Only include thermostats
            if "sdm.devices.traits.ThermostatMode" not in traits:
                continue
            devices.append({
                "id": d["name"].split("/")[-1],
                "name": traits.get("sdm.devices.traits.Info", {}).get("customName", "Thermostat"),
                "full_name": d["name"],
            })
        return devices

    def get_device_state(self, device_id):
        """Get current thermostat state: ambient temp, target temp, mode, humidity, hvac status."""
        data = self._request("GET", f"/devices/{device_id}")
        traits = data.get("traits", {})

        ambient_c = traits.get("sdm.devices.traits.Temperature", {}).get("ambientTemperatureCelsius")
        humidity = traits.get("sdm.devices.traits.Humidity", {}).get("ambientHumidityPercent")
        mode = traits.get("sdm.devices.traits.ThermostatMode", {}).get("mode", "OFF")
        hvac_status = traits.get("sdm.devices.traits.ThermostatHvac", {}).get("status", "OFF")
        eco_mode = traits.get("sdm.devices.traits.ThermostatEco", {}).get("mode", "OFF")

        # Target temp depends on mode
        setpoint = traits.get("sdm.devices.traits.ThermostatTemperatureSetpoint", {})
        heat_c = setpoint.get("heatCelsius")
        cool_c = setpoint.get("coolCelsius")

        return {
            "ambient_temp_f": round(ambient_c * 9 / 5 + 32, 1) if ambient_c is not None else None,
            "humidity": humidity,
            "mode": mode,
            "hvac_status": hvac_status,
            "eco_mode": eco_mode,
            "heat_target_f": round(heat_c * 9 / 5 + 32, 1) if heat_c is not None else None,
            "cool_target_f": round(cool_c * 9 / 5 + 32, 1) if cool_c is not None else None,
        }

    def set_temperature(self, device_id, temp_f, mode=None):
        """Set target temperature in Fahrenheit. Auto-detects heat/cool from current mode if not specified."""
        temp_c = round((temp_f - 32) * 5 / 9, 2)

        if mode is None:
            state = self.get_device_state(device_id)
            mode = state["mode"]

        if mode == "HEAT":
            command = "sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat"
            params = {"heatCelsius": temp_c}
        elif mode == "COOL":
            command = "sdm.devices.commands.ThermostatTemperatureSetpoint.SetCool"
            params = {"coolCelsius": temp_c}
        elif mode == "HEATCOOL":
            # For heat-cool, set both points centered around target with 3F spread
            spread_c = round(3 * 5 / 9, 2)
            command = "sdm.devices.commands.ThermostatTemperatureSetpoint.SetRange"
            params = {"heatCelsius": temp_c - spread_c, "coolCelsius": temp_c + spread_c}
        else:
            return None  # OFF or ECO mode — can't set temp

        return self._request("POST", f"/devices/{device_id}:executeCommand",
            json={"command": command, "params": params})

    def set_mode(self, device_id, mode):
        """Set HVAC mode: HEAT, COOL, HEATCOOL, OFF."""
        return self._request("POST", f"/devices/{device_id}:executeCommand",
            json={
                "command": "sdm.devices.commands.ThermostatMode.SetMode",
                "params": {"mode": mode},
            })

    def set_eco(self, device_id, enabled):
        """Toggle eco mode."""
        eco_mode = "MANUAL_ECO" if enabled else "OFF"
        return self._request("POST", f"/devices/{device_id}:executeCommand",
            json={
                "command": "sdm.devices.commands.ThermostatEco.SetMode",
                "params": {"mode": eco_mode},
            })
```

**Step 2: Commit**

```bash
git add backend/services/nest.py
git commit -m "feat: add Nest device methods (get_devices, state, set_temp, mode, eco)"
```

---

### Task 4: Backend Routes

**Files:**
- Create: `backend/routes/nest.py`
- Modify: `backend/app.py:16-25` — register nest blueprint

**Step 1: Create the Nest routes blueprint**

```python
# backend/routes/nest.py
import json
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request
from services.nest import NestService
from services.thermostat_optimizer import compute_optimal_temp
from routes.settings import get_current_user_id
from routes.users import admin_required
from db import get_db

nest_bp = Blueprint("nest", __name__)
nest = NestService()


@nest_bp.get("/api/nest/auth/url")
def auth_url():
    redirect_uri = request.host_url.rstrip("/") + "/api/nest/auth/callback"
    return jsonify({"url": nest.get_auth_url(redirect_uri)})


@nest_bp.get("/api/nest/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400
    redirect_uri = request.host_url.rstrip("/") + "/api/nest/auth/callback"
    try:
        nest.exchange_code(code, redirect_uri)
        return "<h1>Nest authorized successfully!</h1><p>You can close this tab.</p>"
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@nest_bp.get("/api/nest/devices")
def list_devices():
    if not nest.is_authenticated:
        return jsonify({"error": "Nest not authorized. Visit /api/nest/auth/url first."}), 401
    try:
        devices = nest.get_devices()
        return jsonify(devices)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@nest_bp.get("/api/nest/devices/<device_id>/state")
def device_state(device_id):
    try:
        state = nest.get_device_state(device_id)
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@nest_bp.post("/api/nest/devices/<device_id>/control")
def control_device(device_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    try:
        result = None
        if "target_temp_f" in data:
            result = nest.set_temperature(device_id, data["target_temp_f"])
        if "mode" in data:
            result = nest.set_mode(device_id, data["mode"])
        if "eco" in data:
            result = nest.set_eco(device_id, data["eco"])

        # Save as user preference
        user_id = get_current_user_id()
        if user_id:
            _save_nest_setting(user_id, device_id, data)

        return jsonify(result or {"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@nest_bp.get("/api/nest/optimal-temp")
def get_optimal_temp():
    """Compute and return the current optimal temperature based on who's home."""
    db = get_db()
    try:
        users = _get_present_user_prefs(db)
        guardrails = _get_guardrails(db)
        temp = compute_optimal_temp(users, **guardrails)
        return jsonify({
            "optimal_temp_f": temp,
            "user_count": len(users),
            "guardrails": guardrails,
        })
    finally:
        db.close()


@nest_bp.get("/api/nest/admin/guardrails")
@admin_required
def get_guardrails_route():
    db = get_db()
    try:
        guardrails = _get_guardrails(db)
        weights = _get_all_user_weights(db)
        return jsonify({**guardrails, "user_weights": weights})
    finally:
        db.close()


@nest_bp.post("/api/nest/admin/guardrails")
@admin_required
def set_guardrails():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        # Use a fixed admin user_id (Drew = user 1, but we look it up to be safe)
        admin = db.execute(
            "SELECT id FROM users WHERE LOWER(name) = 'drew'"
        ).fetchone()
        if not admin:
            return jsonify({"error": "Admin user not found"}), 500
        admin_id = admin["id"]

        for key in ("min_temp", "max_temp"):
            if key in data:
                db.execute(
                    """INSERT INTO user_settings (user_id, namespace, key, value, updated_at)
                       VALUES (?, 'nest.admin', ?, ?, ?)
                       ON CONFLICT(user_id, namespace, key)
                       DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (admin_id, key, json.dumps(data[key]), now),
                )

        if "user_weights" in data:
            for uid_str, weight in data["user_weights"].items():
                db.execute(
                    """INSERT INTO user_settings (user_id, namespace, key, value, updated_at)
                       VALUES (?, 'nest.admin', ?, ?, ?)
                       ON CONFLICT(user_id, namespace, key)
                       DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (admin_id, f"user_weight.{uid_str}", json.dumps(weight), now),
                )

        db.commit()
        return jsonify({"ok": True})
    finally:
        db.close()


def _save_nest_setting(user_id, device_id, data):
    namespace = f"nest.{device_id}"
    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    try:
        for key in ("target_temp_f", "mode"):
            if key in data:
                db.execute(
                    """INSERT INTO user_settings (user_id, namespace, key, value, updated_at)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(user_id, namespace, key)
                       DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
                    (user_id, namespace, key, json.dumps(data[key]), now),
                )
        db.commit()
    finally:
        db.close()


def _get_present_user_prefs(db):
    """Get preferred temps + weights for all users currently at home."""
    rows = db.execute("""
        SELECT u.id, us.value
        FROM users u
        JOIN user_settings us ON us.user_id = u.id
            AND us.namespace = 'nest.preferences'
            AND us.key = 'preferred_temp'
        WHERE u.is_home = 1
    """).fetchall()

    # Look up admin user_id for weight lookups
    admin = db.execute("SELECT id FROM users WHERE LOWER(name) = 'drew'").fetchone()
    admin_id = admin["id"] if admin else None

    users = []
    for r in rows:
        weight = 1.0
        if admin_id:
            w_row = db.execute(
                "SELECT value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key = ?",
                (admin_id, f"user_weight.{r['id']}"),
            ).fetchone()
            if w_row:
                weight = json.loads(w_row["value"])
        users.append({"preferred_temp": json.loads(r["value"]), "weight": weight})
    return users


def _get_guardrails(db):
    """Get admin min/max temp bounds."""
    admin = db.execute("SELECT id FROM users WHERE LOWER(name) = 'drew'").fetchone()
    if not admin:
        return {"min_temp": 65, "max_temp": 78}

    result = {"min_temp": 65, "max_temp": 78}
    for key in ("min_temp", "max_temp"):
        row = db.execute(
            "SELECT value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key = ?",
            (admin["id"], key),
        ).fetchone()
        if row:
            result[key] = json.loads(row["value"])
    return result


def _get_all_user_weights(db):
    """Get all per-user weight overrides."""
    admin = db.execute("SELECT id FROM users WHERE LOWER(name) = 'drew'").fetchone()
    if not admin:
        return {}

    rows = db.execute(
        "SELECT key, value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key LIKE 'user_weight.%'",
        (admin["id"],),
    ).fetchall()

    weights = {}
    for r in rows:
        uid = r["key"].replace("user_weight.", "")
        weights[uid] = json.loads(r["value"])
    return weights
```

**Step 2: Register the blueprint in `backend/app.py`**

In `backend/app.py`, after line 19 (`from routes.govee import govee_bp`), add:
```python
from routes.nest import nest_bp
```

After line 24 (`app.register_blueprint(govee_bp)`), add:
```python
app.register_blueprint(nest_bp)
```

**Step 3: Commit**

```bash
git add backend/routes/nest.py backend/app.py
git commit -m "feat: add Nest thermostat API routes with admin guardrails"
```

---

### Task 5: Presence Integration

**Files:**
- Modify: `backend/services/presence.py:18-21` — accept nest service
- Modify: `backend/services/presence.py:234-252` — apply nest settings on arrival
- Modify: `backend/app.py:88-90` — pass nest service to scanner

**Step 1: Update PresenceScanner.__init__ to accept nest service**

In `backend/services/presence.py`, change the `__init__` signature (line 19):

```python
    def __init__(self, eink=None, govee=None, nest=None):
        self.eink = eink
        self.govee = govee
        self.nest = nest
```

**Step 2: Add Nest arrival handler in `_on_arrival`**

After the Govee settings block (after line 252), add:

```python
        # Apply optimal Nest temperature based on all home users
        if self.nest:
            try:
                from services.thermostat_optimizer import compute_optimal_temp
                import json as _json

                # Get all home users' preferences
                home_users = db.execute("""
                    SELECT u.id, us.value
                    FROM users u
                    JOIN user_settings us ON us.user_id = u.id
                        AND us.namespace = 'nest.preferences'
                        AND us.key = 'preferred_temp'
                    WHERE u.is_home = 1
                """).fetchall()

                # Get admin guardrails
                admin = db.execute("SELECT id FROM users WHERE LOWER(name) = 'drew'").fetchone()
                guardrails = {"min_temp": 65, "max_temp": 78}
                if admin:
                    for key in ("min_temp", "max_temp"):
                        row = db.execute(
                            "SELECT value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key = ?",
                            (admin["id"], key),
                        ).fetchone()
                        if row:
                            guardrails[key] = _json.loads(row["value"])

                # Build user list with weights
                users = []
                for r in home_users:
                    weight = 1.0
                    if admin:
                        w_row = db.execute(
                            "SELECT value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key = ?",
                            (admin["id"], f"user_weight.{r['id']}"),
                        ).fetchone()
                        if w_row:
                            weight = _json.loads(w_row["value"])
                    users.append({"preferred_temp": _json.loads(r["value"]), "weight": weight})

                if users:
                    optimal = compute_optimal_temp(users, **guardrails)
                    if optimal is not None:
                        devices = self.nest.get_devices()
                        for d in devices:
                            self.nest.set_temperature(d["id"], optimal)
                        print(f"Set Nest to {optimal}F (optimal for {len(users)} home users)")
            except Exception as e:
                print(f"Failed to apply Nest settings: {e}")
```

**Step 3: Update app.py to pass nest service to scanner**

In `backend/app.py`, after line 88 (`from routes.govee import govee`), add:
```python
    from routes.nest import nest
```

Change line 90 from:
```python
    scanner = PresenceScanner(eink=eink, govee=govee)
```
to:
```python
    scanner = PresenceScanner(eink=eink, govee=govee, nest=nest)
```

**Step 4: Commit**

```bash
git add backend/services/presence.py backend/app.py
git commit -m "feat: apply optimal Nest temp on user arrival via presence scanner"
```

---

### Task 6: Frontend — Thermostat Page

**Files:**
- Create: `frontend/src/pages/Thermostat.tsx`
- Modify: `frontend/src/App.tsx:1-27` — add route + import
- Modify: `frontend/src/pages/Home.tsx:4-17` — add to apps array

**Step 1: Create the Thermostat page component**

```tsx
// frontend/src/pages/Thermostat.tsx
import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { useUser } from "../context/UserContext";

interface NestDevice {
  id: string;
  name: string;
}

interface DeviceState {
  ambient_temp_f: number | null;
  humidity: number | null;
  mode: string;
  hvac_status: string;
  eco_mode: string;
  heat_target_f: number | null;
  cool_target_f: number | null;
}

interface OptimalTemp {
  optimal_temp_f: number | null;
  user_count: number;
}

const MODES = ["HEAT", "COOL", "HEATCOOL", "OFF"] as const;
const MODE_LABELS: Record<string, string> = {
  HEAT: "Heat",
  COOL: "Cool",
  HEATCOOL: "Heat/Cool",
  OFF: "Off",
};
const HVAC_LABELS: Record<string, string> = {
  HEATING: "Heating",
  COOLING: "Cooling",
  OFF: "Idle",
};

export default function Thermostat() {
  const { user } = useUser();
  const [devices, setDevices] = useState<NestDevice[]>([]);
  const [states, setStates] = useState<Record<string, DeviceState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [preference, setPreference] = useState<number | null>(null);
  const [optimalTemp, setOptimalTemp] = useState<OptimalTemp | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/nest/devices");
      if (res.status === 401) {
        setError("Nest not authorized. An admin needs to set up the connection.");
        setLoading(false);
        return;
      }
      if (!res.ok) throw new Error("Failed to load devices");
      const devs: NestDevice[] = await res.json();
      setDevices(devs);

      const stateEntries = await Promise.all(
        devs.map(async (d) => {
          try {
            const sr = await fetch(`/api/nest/devices/${d.id}/state`);
            if (sr.ok) return [d.id, await sr.json()] as const;
          } catch { /* skip */ }
          return [d.id, {} as DeviceState] as const;
        })
      );
      setStates(Object.fromEntries(stateEntries));

      // Fetch user preference
      const prefRes = await fetch("/api/settings");
      if (prefRes.ok) {
        const settings = await prefRes.json();
        const pref = settings?.["nest.preferences"]?.preferred_temp;
        if (pref != null) setPreference(pref);
      }

      // Fetch optimal temp
      const optRes = await fetch("/api/nest/optimal-temp");
      if (optRes.ok) setOptimalTemp(await optRes.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function sendControl(deviceId: string, data: Record<string, unknown>) {
    // Optimistic UI: update local state immediately
    if (data.target_temp_f != null) {
      setStates((prev) => {
        const s = prev[deviceId];
        if (!s) return prev;
        return {
          ...prev,
          [deviceId]: {
            ...s,
            heat_target_f: s.mode === "COOL" ? s.heat_target_f : data.target_temp_f as number,
            cool_target_f: s.mode === "HEAT" ? s.cool_target_f : data.target_temp_f as number,
          },
        };
      });
    }
    if (data.mode != null) {
      setStates((prev) => ({
        ...prev,
        [deviceId]: { ...prev[deviceId], mode: data.mode as string },
      }));
    }
    if (data.eco != null) {
      setStates((prev) => ({
        ...prev,
        [deviceId]: { ...prev[deviceId], eco_mode: data.eco ? "MANUAL_ECO" : "OFF" },
      }));
    }

    try {
      await fetch(`/api/nest/devices/${deviceId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } catch (err) {
      console.error("Control failed:", err);
      // Refresh state to revert
      fetchData();
    }
  }

  async function savePreference(temp: number) {
    setPreference(temp);
    try {
      await fetch("/api/settings/nest.preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferred_temp: temp }),
      });
      // Refresh optimal temp
      const optRes = await fetch("/api/nest/optimal-temp");
      if (optRes.ok) setOptimalTemp(await optRes.json());
    } catch (err) {
      console.error("Failed to save preference:", err);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading thermostat...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">&larr;</Link>
          <h1 className="text-2xl font-bold">Thermostat</h1>
        </div>

        {error && <p className="text-red-400">{error}</p>}

        {devices.length === 0 && !error && (
          <p className="text-gray-400">No Nest thermostats found.</p>
        )}

        {devices.map((device) => (
          <ThermostatCard
            key={device.id}
            device={device}
            state={states[device.id]}
            onControl={(data) => sendControl(device.id, data)}
          />
        ))}

        {/* Preference section */}
        {devices.length > 0 && (
          <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-3">
            <h2 className="text-lg font-semibold">My Preference</h2>
            <p className="text-sm text-gray-400">
              Set your ideal temperature. The system optimizes for everyone at home.
            </p>
            <div className="flex items-center gap-4">
              <button
                onClick={() => savePreference((preference ?? 72) - 0.5)}
                className="w-10 h-10 rounded-lg bg-gray-800 hover:bg-gray-700 text-xl font-bold"
              >
                -
              </button>
              <span className="text-3xl font-bold min-w-[5rem] text-center">
                {preference ?? "—"}°F
              </span>
              <button
                onClick={() => savePreference((preference ?? 72) + 0.5)}
                className="w-10 h-10 rounded-lg bg-gray-800 hover:bg-gray-700 text-xl font-bold"
              >
                +
              </button>
            </div>
            {optimalTemp?.optimal_temp_f != null && (
              <p className="text-sm text-gray-400">
                Optimized temp: <span className="text-white font-semibold">{optimalTemp.optimal_temp_f}°F</span>
                {" "}({optimalTemp.user_count} {optimalTemp.user_count === 1 ? "person" : "people"} home)
              </p>
            )}
          </div>
        )}

        {/* Admin guardrails */}
        {user?.isAdmin && devices.length > 0 && <AdminGuardrails />}
      </div>
    </div>
  );
}


function ThermostatCard({
  device,
  state,
  onControl,
}: {
  device: NestDevice;
  state?: DeviceState;
  onControl: (data: Record<string, unknown>) => void;
}) {
  if (!state) return null;

  const targetTemp =
    state.mode === "COOL" ? state.cool_target_f :
    state.mode === "HEAT" ? state.heat_target_f :
    state.heat_target_f ?? state.cool_target_f;

  const ecoActive = state.eco_mode === "MANUAL_ECO";

  return (
    <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{device.name}</h2>
        <span className={`text-xs px-2 py-1 rounded-full ${
          state.hvac_status === "HEATING" ? "bg-orange-900 text-orange-300" :
          state.hvac_status === "COOLING" ? "bg-blue-900 text-blue-300" :
          "bg-gray-800 text-gray-400"
        }`}>
          {HVAC_LABELS[state.hvac_status] ?? state.hvac_status}
        </span>
      </div>

      {/* Ambient temperature */}
      {state.ambient_temp_f != null && (
        <div className="text-center">
          <p className="text-5xl font-bold">{state.ambient_temp_f}°</p>
          <p className="text-sm text-gray-400 mt-1">Current temperature</p>
        </div>
      )}

      {/* Target temperature controls */}
      {targetTemp != null && !ecoActive && (
        <div className="flex items-center justify-center gap-6">
          <button
            onClick={() => onControl({ target_temp_f: targetTemp - 0.5 })}
            className="w-12 h-12 rounded-full bg-gray-800 hover:bg-gray-700 text-2xl font-bold"
          >
            -
          </button>
          <div className="text-center">
            <p className="text-3xl font-bold">{targetTemp}°F</p>
            <p className="text-xs text-gray-400">Target</p>
          </div>
          <button
            onClick={() => onControl({ target_temp_f: targetTemp + 0.5 })}
            className="w-12 h-12 rounded-full bg-gray-800 hover:bg-gray-700 text-2xl font-bold"
          >
            +
          </button>
        </div>
      )}

      {/* Mode selector */}
      <div className="flex gap-2">
        {MODES.map((m) => (
          <button
            key={m}
            onClick={() => onControl({ mode: m })}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              state.mode === m
                ? "bg-red-600 text-white"
                : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>

      {/* Eco toggle */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-400">Eco Mode</span>
        <button
          onClick={() => onControl({ eco: !ecoActive })}
          className={`w-14 h-8 rounded-full transition-colors relative ${
            ecoActive ? "bg-green-600" : "bg-gray-700"
          }`}
        >
          <span
            className={`absolute top-1 w-6 h-6 rounded-full bg-white transition-transform ${
              ecoActive ? "left-7" : "left-1"
            }`}
          />
        </button>
      </div>

      {/* Humidity */}
      {state.humidity != null && (
        <p className="text-sm text-gray-500">Humidity: {state.humidity}%</p>
      )}
    </div>
  );
}


function AdminGuardrails() {
  const [minTemp, setMinTemp] = useState(65);
  const [maxTemp, setMaxTemp] = useState(78);
  const [weights, setWeights] = useState<Record<string, { name: string; weight: number }>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch("/api/nest/admin/guardrails");
        if (!res.ok) return;
        const data = await res.json();
        setMinTemp(data.min_temp ?? 65);
        setMaxTemp(data.max_temp ?? 78);

        // Load all users with their weights
        const usersRes = await fetch("/api/users/home");
        if (usersRes.ok) {
          const users: Array<{ id: number; name: string }> = await usersRes.json();
          const w: Record<string, { name: string; weight: number }> = {};
          for (const u of users) {
            w[String(u.id)] = {
              name: u.name,
              weight: data.user_weights?.[String(u.id)] ?? 1.0,
            };
          }
          setWeights(w);
        }
      } catch { /* ignore */ }
      setLoaded(true);
    }
    load();
  }, []);

  async function save() {
    const userWeights: Record<string, number> = {};
    for (const [uid, { weight }] of Object.entries(weights)) {
      userWeights[uid] = weight;
    }
    try {
      await fetch("/api/nest/admin/guardrails", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_temp: minTemp, max_temp: maxTemp, user_weights: userWeights }),
      });
    } catch (err) {
      console.error("Failed to save guardrails:", err);
    }
  }

  if (!loaded) return null;

  return (
    <div className="p-5 bg-gray-900 border border-red-900/50 rounded-xl space-y-4">
      <h2 className="text-lg font-semibold">Admin: Algorithm Guardrails</h2>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-sm text-gray-400">Min Temp (°F)</label>
          <input
            type="number"
            value={minTemp}
            onChange={(e) => setMinTemp(Number(e.target.value))}
            onBlur={save}
            className="w-full mt-1 p-2 bg-gray-800 border border-gray-700 rounded-lg text-white"
          />
        </div>
        <div>
          <label className="text-sm text-gray-400">Max Temp (°F)</label>
          <input
            type="number"
            value={maxTemp}
            onChange={(e) => setMaxTemp(Number(e.target.value))}
            onBlur={save}
            className="w-full mt-1 p-2 bg-gray-800 border border-gray-700 rounded-lg text-white"
          />
        </div>
      </div>

      {Object.keys(weights).length > 0 && (
        <div className="space-y-2">
          <label className="text-sm text-gray-400">User Weights</label>
          {Object.entries(weights).map(([uid, { name, weight }]) => (
            <div key={uid} className="flex items-center gap-3">
              <span className="text-sm flex-1">{name}</span>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="5"
                value={weight}
                onChange={(e) =>
                  setWeights((prev) => ({
                    ...prev,
                    [uid]: { ...prev[uid], weight: Number(e.target.value) },
                  }))
                }
                onBlur={save}
                className="w-20 p-2 bg-gray-800 border border-gray-700 rounded-lg text-white text-center"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

**Step 2: Add route in `frontend/src/App.tsx`**

After line 6 (`import Display from "./pages/Display";`), add:
```typescript
import Thermostat from "./pages/Thermostat";
```

After line 25 (`<Route path="/display" ...>`), add:
```typescript
      <Route path="/thermostat" element={user ? <Thermostat /> : <Navigate to="/" />} />
```

**Step 3: Add to Home page apps array in `frontend/src/pages/Home.tsx`**

After the Display entry (line 12-16), add:
```typescript
  {
    name: "Thermostat",
    description: "Nest climate control",
    path: "/thermostat",
    icon: "\u{1F321}\u{FE0F}",
  },
```

**Step 4: Commit**

```bash
git add frontend/src/pages/Thermostat.tsx frontend/src/App.tsx frontend/src/pages/Home.tsx
git commit -m "feat: add Thermostat page with device controls, preferences, and admin guardrails"
```

---

### Task 7: Gitignore + Final Wiring Verification

**Files:**
- Modify: `.gitignore` — add `nest_tokens.json`

**Step 1: Add nest_tokens.json to .gitignore**

Add to `.gitignore`:
```
nest_tokens.json
```

**Step 2: Run the frontend build to verify no TypeScript errors**

Run: `cd /c/Users/Drew/Desktop/drewbermudezdotcom/localweb/frontend && npx tsc --noEmit`
Expected: No errors

**Step 3: Run backend tests**

Run: `cd /c/Users/Drew/Desktop/drewbermudezdotcom/localweb/backend && python -m pytest tests/ -v`
Expected: All tests pass

**Step 4: Final commit**

```bash
git add .gitignore
git commit -m "chore: add nest_tokens.json to gitignore"
```

---

### Task 8: Push + Deploy

**Step 1: Push to GitHub**

```bash
git push origin main
```

**Step 2: Force-update Pi**

```bash
ssh pi@10.0.0.74 '/home/pi/localweb/deploy/force-update.sh'
```

**Step 3: Manual verification checklist**

- [ ] Visit `http://10.0.0.74:5000/home` — Thermostat card appears
- [ ] Click Thermostat — page loads (will show auth error until Google setup)
- [ ] After Google Device Access setup: OAuth flow works via `/api/nest/auth/url`
- [ ] Devices appear, temp controls work
- [ ] Preference saves and optimal temp displays
- [ ] Admin guardrails accessible to Drew only
