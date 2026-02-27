# Spotify Light Show Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Spotify app with playback controls, real-time audio-reactive light shows via librespot + Govee LAN UDP, and migrate all existing Govee controls to LAN.

**Architecture:** librespot runs as a systemd service on the Pi, outputting raw PCM to a named pipe. A Python background thread reads the pipe, runs lightweight FFT analysis (numpy, 30 Hz), and drives two Govee floor lamps via LAN UDP. Spotify Web API provides metadata + playback controls. A new GoveeLanService replaces cloud API calls for all real-time Govee controls.

**Tech Stack:** Python 3.9, Flask, numpy (<1.24), raw UDP sockets, librespot (Rust binary), React 19, TypeScript, Tailwind CSS 4

**Pi Performance Budget:** librespot ~5% CPU, audio analysis ~2% CPU, UDP sends ~0%. Total added load: <10%. Analysis loop capped at 30 Hz. Light commands throttled to 20/sec.

---

### Task 1: Govee LAN Service

**Files:**
- Create: `backend/services/govee_lan.py`

**Step 1: Write GoveeLanService**

```python
import socket
import json
import time
import threading

MULTICAST_ADDR = "239.255.255.250"
SCAN_PORT = 4001
LISTEN_PORT = 4002
CONTROL_PORT = 4003
SCAN_TIMEOUT = 3  # seconds
DEVICE_CACHE_TTL = 300  # 5 minutes


class GoveeLanService:
    """Control Govee devices via LAN UDP — no rate limits, <50ms latency."""

    def __init__(self):
        self._devices = {}  # {device_id: {ip, sku, name}}
        self._last_scan = 0
        self._lock = threading.Lock()

    def discover_devices(self, force=False):
        """Multicast scan for Govee devices on the LAN."""
        if not force and time.time() - self._last_scan < DEVICE_CACHE_TTL and self._devices:
            return list(self._devices.values())

        scan_msg = json.dumps({
            "msg": {
                "cmd": "scan",
                "data": {"account_topic": "reserve"}
            }
        }).encode()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(SCAN_TIMEOUT)

        try:
            sock.bind(("", LISTEN_PORT))
            sock.sendto(scan_msg, (MULTICAST_ADDR, SCAN_PORT))

            devices = {}
            deadline = time.time() + SCAN_TIMEOUT
            while time.time() < deadline:
                try:
                    data, _ = sock.recvfrom(4096)
                    resp = json.loads(data.decode())
                    info = resp.get("msg", {}).get("data", {})
                    if "device" in info and "ip" in info:
                        devices[info["device"]] = {
                            "device_id": info["device"],
                            "ip": info["ip"],
                            "sku": info.get("sku", ""),
                        }
                except socket.timeout:
                    break
        finally:
            sock.close()

        with self._lock:
            self._devices = devices
            self._last_scan = time.time()

        return list(devices.values())

    def get_device_ip(self, device_id):
        """Get cached IP for a device, triggering scan if needed."""
        if device_id not in self._devices:
            self.discover_devices()
        dev = self._devices.get(device_id)
        return dev["ip"] if dev else None

    def _send(self, ip, cmd_dict):
        """Fire-and-forget UDP command to a device."""
        payload = json.dumps(cmd_dict).encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(payload, (ip, CONTROL_PORT))
        finally:
            sock.close()

    def turn(self, ip, on):
        self._send(ip, {"msg": {"cmd": "turn", "data": {"value": 1 if on else 0}}})

    def set_brightness(self, ip, value):
        self._send(ip, {"msg": {"cmd": "brightness", "data": {"value": max(1, min(100, value))}}})

    def set_color(self, ip, r, g, b):
        self._send(ip, {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": r, "g": g, "b": b},
                    "colorTemInKelvin": 0
                }
            }
        })

    def set_color_temp(self, ip, kelvin):
        self._send(ip, {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": 0, "g": 0, "b": 0},
                    "colorTemInKelvin": max(2000, min(9000, kelvin))
                }
            }
        })

    def get_status(self, ip):
        """Send status query and wait for response."""
        query = json.dumps({"msg": {"cmd": "devStatus", "data": {}}}).encode()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1)
        try:
            sock.sendto(query, (ip, CONTROL_PORT))
            data, _ = sock.recvfrom(4096)
            resp = json.loads(data.decode())
            return resp.get("msg", {}).get("data", {})
        except socket.timeout:
            return None
        finally:
            sock.close()
```

**Step 2: Commit**

```bash
git add backend/services/govee_lan.py
git commit -m "feat: add Govee LAN UDP service for instant device control"
```

---

### Task 2: Integrate LAN into Existing Govee Service

**Files:**
- Modify: `backend/services/govee.py`
- Modify: `backend/routes/govee.py`

**Step 1: Update GoveeService to try LAN first, fall back to cloud**

In `backend/services/govee.py`, add a `govee_lan` parameter and use it for control commands. The cloud API is still used for `get_devices()` (to get device names and full capabilities) and `get_scenes()`. But `control_device()` and `get_device_state()` try LAN first.

```python
# At top of govee.py, add import
from services.govee_lan import GoveeLanService

class GoveeService:
    def __init__(self):
        self.api_key = os.environ.get("GOVEE_API_KEY", "")
        self._devices_cache = None
        self.lan = GoveeLanService()

    # ... existing get_devices(), _find_device(), get_scenes() unchanged ...

    def get_device_state(self, device_id):
        """Try LAN status first, fall back to cloud API."""
        # Try LAN
        ip = self.lan.get_device_ip(device_id)
        if ip:
            status = self.lan.get_status(ip)
            if status is not None:
                # Convert LAN format to match cloud API format
                caps = []
                if "onOff" in status:
                    caps.append({"type": "devices.capabilities.on_off", "instance": "powerSwitch", "state": {"value": status["onOff"]}})
                if "brightness" in status:
                    caps.append({"type": "devices.capabilities.range", "instance": "brightness", "state": {"value": status["brightness"]}})
                if "color" in status:
                    c = status["color"]
                    rgb_int = (c["r"] << 16) + (c["g"] << 8) + c["b"]
                    caps.append({"type": "devices.capabilities.color_setting", "instance": "colorRgb", "state": {"value": rgb_int}})
                if "colorTemInKelvin" in status and status["colorTemInKelvin"]:
                    caps.append({"type": "devices.capabilities.color_setting", "instance": "colorTemperatureK", "state": {"value": status["colorTemInKelvin"]}})
                return {"capabilities": caps}

        # Fall back to cloud
        device = self._find_device(device_id)
        if not device:
            return None
        resp = requests.post(
            f"{GOVEE_BASE}/router/api/v1/device/state",
            headers=self.headers,
            json={
                "requestId": str(uuid.uuid4()),
                "payload": {"sku": device["sku"], "device": device_id},
            },
        )
        resp.raise_for_status()
        return resp.json().get("payload", {})

    def control_device(self, device_id, capability):
        """Try LAN control first, fall back to cloud API."""
        ip = self.lan.get_device_ip(device_id)
        if ip:
            instance = capability.get("instance", "")
            value = capability.get("value")
            try:
                if instance == "powerSwitch":
                    self.lan.turn(ip, bool(value))
                    return {"ok": True, "via": "lan"}
                elif instance == "brightness":
                    self.lan.set_brightness(ip, value)
                    return {"ok": True, "via": "lan"}
                elif instance == "colorRgb":
                    r = (value >> 16) & 0xFF
                    g = (value >> 8) & 0xFF
                    b = value & 0xFF
                    self.lan.set_color(ip, r, g, b)
                    return {"ok": True, "via": "lan"}
                elif instance == "colorTemperatureK":
                    self.lan.set_color_temp(ip, value)
                    return {"ok": True, "via": "lan"}
            except Exception as e:
                print(f"LAN control failed for {device_id}, falling back to cloud: {e}")

        # Fall back to cloud API
        device = self._find_device(device_id)
        if not device:
            return None
        resp = requests.post(
            f"{GOVEE_BASE}/router/api/v1/device/control",
            headers=self.headers,
            json={
                "requestId": str(uuid.uuid4()),
                "payload": {"sku": device["sku"], "device": device_id, "capability": capability},
            },
        )
        resp.raise_for_status()
        return resp.json()
```

The route file (`backend/routes/govee.py`) needs no changes — the REST API is identical, just faster under the hood.

**Step 2: Commit**

```bash
git add backend/services/govee.py
git commit -m "feat: use Govee LAN UDP for device control with cloud fallback"
```

---

### Task 3: Spotify Service (OAuth + Web API)

**Files:**
- Create: `backend/services/spotify.py`

**Step 1: Write SpotifyService**

Follow the same patterns as NestService: token file persistence, auto-refresh on 401, raw requests.

```python
import os
import json
import requests
from urllib.parse import urlencode

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE = "https://api.spotify.com/v1"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "spotify_tokens.json")
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


class SpotifyService:
    def __init__(self):
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
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
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
        })
        return f"{SPOTIFY_AUTH_URL}?{params}"

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(SPOTIFY_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        resp.raise_for_status()
        self._save_tokens(resp.json())

    def _refresh_token(self):
        refresh = self._tokens.get("refresh_token")
        if not refresh:
            raise RuntimeError("No refresh token — re-authorize via /api/spotify/auth/url")
        resp = requests.post(SPOTIFY_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        resp.raise_for_status()
        new_tokens = resp.json()
        new_tokens.setdefault("refresh_token", refresh)
        self._save_tokens(new_tokens)

    def _request(self, method, path, **kwargs):
        """Make an authenticated Spotify API request, refreshing token on 401."""
        url = f"{SPOTIFY_API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._tokens.get('access_token', '')}"}
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self._tokens['access_token']}"
            resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 204:
            return {"ok": True}
        resp.raise_for_status()
        return resp.json()

    def get_current_track(self):
        """Get currently playing track info."""
        try:
            data = self._request("GET", "/me/player/currently-playing")
        except Exception:
            return None
        if not data or data.get("ok"):
            return None
        item = data.get("item", {})
        return {
            "title": item.get("name", ""),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name", ""),
            "art_url": (item.get("album", {}).get("images", [{}])[0].get("url", "")),
            "progress_ms": data.get("progress_ms", 0),
            "duration_ms": item.get("duration_ms", 0),
            "is_playing": data.get("is_playing", False),
            "track_id": item.get("id", ""),
        }

    def get_playback_state(self):
        """Get full playback state including device info."""
        try:
            return self._request("GET", "/me/player")
        except Exception:
            return None

    def play(self):
        return self._request("PUT", "/me/player/play")

    def pause(self):
        return self._request("PUT", "/me/player/pause")

    def next_track(self):
        return self._request("POST", "/me/player/next")

    def previous_track(self):
        return self._request("POST", "/me/player/previous")

    def get_devices(self):
        data = self._request("GET", "/me/player/devices")
        return [
            {"id": d["id"], "name": d["name"], "type": d["type"], "is_active": d["is_active"]}
            for d in data.get("devices", [])
        ]

    def transfer_playback(self, device_id):
        return self._request("PUT", "/me/player", json={"device_ids": [device_id]})
```

**Step 2: Commit**

```bash
git add backend/services/spotify.py
git commit -m "feat: add Spotify service with OAuth and playback controls"
```

---

### Task 4: Spotify Routes

**Files:**
- Create: `backend/routes/spotify.py`
- Modify: `backend/app.py` (register blueprint)

**Step 1: Write the route blueprint**

```python
from flask import Blueprint, jsonify, request
from services.spotify import SpotifyService

spotify_bp = Blueprint("spotify", __name__)
spotify = SpotifyService()

OAUTH_REDIRECT_URI = "http://10.0.0.74:5000/api/spotify/auth/callback"


@spotify_bp.get("/api/spotify/auth/url")
def auth_url():
    return jsonify({"url": spotify.get_auth_url(OAUTH_REDIRECT_URI)})


@spotify_bp.get("/api/spotify/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400
    try:
        spotify.exchange_code(code, OAUTH_REDIRECT_URI)
        return "<h1>Spotify authorized successfully!</h1><p>You can close this tab.</p>"
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.get("/api/spotify/auth/status")
def auth_status():
    return jsonify({"authenticated": spotify.is_authenticated})


@spotify_bp.get("/api/spotify/now-playing")
def now_playing():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    track = spotify.get_current_track()
    if track is None:
        return jsonify({"nothing_playing": True})
    return jsonify(track)


@spotify_bp.post("/api/spotify/play")
def play():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.play()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/pause")
def pause():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.pause()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/next")
def next_track():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.next_track()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/previous")
def previous_track():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.previous_track()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.get("/api/spotify/devices")
def list_devices():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        return jsonify(spotify.get_devices())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/transfer")
def transfer_playback():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    data = request.get_json()
    device_id = data.get("device_id") if data else None
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    try:
        spotify.transfer_playback(device_id)
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502
```

**Step 2: Register blueprint in app.py**

Add after the existing blueprint imports in `backend/app.py`:

```python
from routes.spotify import spotify_bp
app.register_blueprint(spotify_bp)
```

Also export the spotify service instance for use by lightshow engine. In `backend/routes/spotify.py`, the `spotify` object is already module-level, same pattern as `govee` in `routes/govee.py`.

**Step 3: Add env vars to backend/.env**

```
SPOTIFY_CLIENT_ID=<your-client-id>
SPOTIFY_CLIENT_SECRET=<your-client-secret>
```

**Step 4: Commit**

```bash
git add backend/routes/spotify.py backend/app.py
git commit -m "feat: add Spotify OAuth and playback control routes"
```

---

### Task 5: Light Show Engine

**Files:**
- Create: `backend/services/lightshow.py`

**Step 1: Write LightShowEngine**

This is the core — reads PCM from librespot's named pipe, runs FFT, drives lights. Designed to be gentle on the Pi: 30 Hz loop, small FFT window, numpy only.

```python
import os
import struct
import threading
import time
import math
import numpy as np

# Audio format from librespot pipe: 16-bit signed LE stereo, 44100 Hz
SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # 16-bit
CHUNK_SAMPLES = 1024  # ~23ms at 44100
CHUNK_BYTES = CHUNK_SAMPLES * CHANNELS * BYTES_PER_SAMPLE
ANALYSIS_HZ = 30  # target analysis rate
ANALYSIS_INTERVAL = 1.0 / ANALYSIS_HZ

PIPE_PATH = "/tmp/librespot-pipe"

# Frequency band boundaries (FFT bin indices for 1024-sample FFT at 44100 Hz)
# Each bin = 44100/1024 ≈ 43 Hz
BASS_RANGE = (1, 6)      # ~43-258 Hz
MID_RANGE = (6, 93)      # ~258-4000 Hz
TREBLE_RANGE = (93, 372)  # ~4000-16000 Hz

# Beat detection
BEAT_HISTORY_SIZE = 40  # ~1.3 seconds of history at 30 Hz
BEAT_THRESHOLD = 1.4     # bass must be 1.4x rolling average to count as beat

# Light throttle
MIN_CMD_INTERVAL = 0.05  # 50ms = 20 commands/sec per light


class LightShowEngine:
    """Real-time audio-reactive light controller. Reads PCM from librespot pipe."""

    def __init__(self, govee_lan):
        self.govee_lan = govee_lan
        self._thread = None
        self._running = False
        self.mode = "off"  # off, pulse, ambient, party
        self.light_ips = []  # IPs of the two floor lamps
        self.light_device_ids = []  # device IDs for discovery
        self.latency_ms = 0
        self.intensity = 7  # 1-10

        # Analysis state
        self._bass_history = []
        self._beat_count = 0
        self._hue = 0.0  # current hue position (0-1)
        self._last_cmd_time = [0.0, 0.0]  # per-light throttle

    @property
    def is_active(self):
        return self._running and self.mode != "off"

    def start(self, mode, device_ids, latency_ms=0, intensity=7):
        """Start the light show."""
        self.mode = mode
        self.light_device_ids = device_ids
        self.latency_ms = latency_ms
        self.intensity = max(1, min(10, intensity))

        # Resolve device IPs
        self.light_ips = []
        for did in device_ids:
            ip = self.govee_lan.get_device_ip(did)
            if ip:
                self.light_ips.append(ip)

        if not self.light_ips:
            raise RuntimeError("No Govee devices found on LAN")

        # Ensure lights are on
        for ip in self.light_ips:
            self.govee_lan.turn(ip, True)

        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop the light show and reset lights."""
        self._running = False
        self.mode = "off"
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

        # Reset lights to warm white
        for ip in self.light_ips:
            try:
                self.govee_lan.set_color(ip, 255, 180, 100)
                self.govee_lan.set_brightness(ip, 50)
            except Exception:
                pass

    def set_mode(self, mode):
        self.mode = mode

    def set_latency(self, ms):
        self.latency_ms = ms

    def set_intensity(self, level):
        self.intensity = max(1, min(10, level))

    def get_status(self):
        return {
            "active": self.is_active,
            "mode": self.mode,
            "latency_ms": self.latency_ms,
            "intensity": self.intensity,
            "lights_connected": len(self.light_ips),
            "pipe_exists": os.path.exists(PIPE_PATH),
        }

    def _run(self):
        """Main analysis loop. Reads PCM from pipe, analyzes, drives lights."""
        pipe_fd = None
        try:
            # Open pipe (blocks until librespot writes)
            if not os.path.exists(PIPE_PATH):
                print(f"LightShow: pipe {PIPE_PATH} does not exist")
                self._run_pattern_only()
                return

            pipe_fd = os.open(PIPE_PATH, os.O_RDONLY | os.O_NONBLOCK)
        except Exception as e:
            print(f"LightShow: cannot open pipe: {e}")
            self._run_pattern_only()
            return

        try:
            while self._running and self.mode != "off":
                loop_start = time.time()

                # Read audio chunk
                try:
                    raw = os.read(pipe_fd, CHUNK_BYTES)
                except OSError:
                    raw = b""

                if len(raw) >= CHUNK_BYTES:
                    samples = np.frombuffer(raw[:CHUNK_BYTES], dtype=np.int16)
                    # Mix stereo to mono
                    mono = samples.reshape(-1, CHANNELS).mean(axis=1)
                    self._analyze_and_drive(mono)
                else:
                    # No audio data — run pattern without audio reactivity
                    self._drive_idle_pattern()

                # Maintain target rate
                elapsed = time.time() - loop_start
                sleep_time = ANALYSIS_INTERVAL - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)
        finally:
            if pipe_fd is not None:
                os.close(pipe_fd)

    def _run_pattern_only(self):
        """Fallback: run light patterns without audio (timer-based)."""
        while self._running and self.mode != "off":
            loop_start = time.time()
            self._drive_idle_pattern()
            elapsed = time.time() - loop_start
            sleep_time = ANALYSIS_INTERVAL - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _analyze_and_drive(self, mono):
        """Run FFT on mono samples, detect beats, drive lights."""
        # Normalize to -1..1
        samples = mono.astype(np.float32) / 32768.0

        # FFT
        fft = np.abs(np.fft.rfft(samples))
        n = len(fft)

        # Extract energy bands
        bass = np.mean(fft[BASS_RANGE[0]:min(BASS_RANGE[1], n)]) if n > BASS_RANGE[0] else 0
        mid = np.mean(fft[MID_RANGE[0]:min(MID_RANGE[1], n)]) if n > MID_RANGE[0] else 0
        treble = np.mean(fft[TREBLE_RANGE[0]:min(TREBLE_RANGE[1], n)]) if n > TREBLE_RANGE[0] else 0
        rms = np.sqrt(np.mean(samples ** 2))

        # Beat detection
        self._bass_history.append(bass)
        if len(self._bass_history) > BEAT_HISTORY_SIZE:
            self._bass_history.pop(0)

        avg_bass = np.mean(self._bass_history) if self._bass_history else 0
        is_beat = bass > avg_bass * BEAT_THRESHOLD and len(self._bass_history) >= 5

        if is_beat:
            self._beat_count += 1

        bands = {"bass": bass, "mid": mid, "treble": treble, "rms": rms}

        # Apply latency offset
        if self.latency_ms > 0:
            time.sleep(self.latency_ms / 1000.0)

        # Drive lights based on mode
        if self.mode == "pulse":
            self._apply_pulse(bands, is_beat)
        elif self.mode == "ambient":
            self._apply_ambient(bands)
        elif self.mode == "party":
            self._apply_party(bands, is_beat)

    def _drive_idle_pattern(self):
        """Timer-based pattern when no audio is available."""
        t = time.time()
        if self.mode == "pulse":
            # Slow breathing
            brightness = int(40 + 30 * math.sin(t * 2))
            self._hue = (self._hue + 0.003) % 1.0
            r, g, b = self._hsv_to_rgb(self._hue, 0.8, 1.0)
            self._set_light(0, r, g, b, brightness)
            self._set_light(1, r, g, b, brightness)
        elif self.mode == "ambient":
            self._hue = (self._hue + 0.001) % 1.0
            r1, g1, b1 = self._hsv_to_rgb(self._hue, 0.6, 1.0)
            r2, g2, b2 = self._hsv_to_rgb((self._hue + 0.5) % 1.0, 0.6, 1.0)
            self._set_light(0, r1, g1, b1, 50)
            self._set_light(1, r2, g2, b2, 50)
        elif self.mode == "party":
            self._hue = (self._hue + 0.01) % 1.0
            idx = int(t * 4) % 2
            r, g, b = self._hsv_to_rgb(self._hue, 1.0, 1.0)
            self._set_light(idx, r, g, b, 100)
            self._set_light(1 - idx, 0, 0, 0, 10)

    def _apply_pulse(self, bands, is_beat):
        """Pulse mode: flash on beats, color follows energy."""
        intensity_scale = self.intensity / 10.0

        if is_beat:
            self._hue = (self._hue + 0.08) % 1.0
            brightness = int(100 * intensity_scale)
        else:
            self._hue = (self._hue + 0.002) % 1.0
            brightness = int(max(20, min(70, bands["rms"] * 500)) * intensity_scale)

        # Warmth from energy: high energy = warm (red/orange), low = cool (blue/purple)
        energy = min(1.0, bands["rms"] * 5)
        warm_hue = 0.0 + energy * 0.1  # red to orange
        cool_hue = 0.6 + (1 - energy) * 0.15  # blue to purple
        hue = warm_hue if energy > 0.5 else cool_hue
        hue = (hue + self._hue * 0.3) % 1.0

        saturation = 0.7 + 0.3 * intensity_scale
        r, g, b = self._hsv_to_rgb(hue, saturation, 1.0)

        self._set_light(0, r, g, b, brightness)
        self._set_light(1, r, g, b, brightness)

    def _apply_ambient(self, bands):
        """Ambient mode: slow flowing gradients mapped to energy."""
        intensity_scale = self.intensity / 10.0
        energy = min(1.0, bands["rms"] * 5)

        # Speed proportional to energy
        speed = 0.001 + energy * 0.005
        self._hue = (self._hue + speed) % 1.0

        saturation = 0.4 + 0.3 * energy
        brightness = int((30 + 50 * energy) * intensity_scale)

        # Two lights offset by 180 degrees
        r1, g1, b1 = self._hsv_to_rgb(self._hue, saturation, 1.0)
        r2, g2, b2 = self._hsv_to_rgb((self._hue + 0.5) % 1.0, saturation, 1.0)

        self._set_light(0, r1, g1, b1, brightness)
        self._set_light(1, r2, g2, b2, brightness)

    def _apply_party(self, bands, is_beat):
        """Party mode: alternating lights on beats, strobe on energy spikes."""
        intensity_scale = self.intensity / 10.0
        energy = min(1.0, bands["rms"] * 5)

        if is_beat:
            self._hue = (self._hue + 0.15) % 1.0
            # Alternate which light flashes
            flash_idx = self._beat_count % 2
            r, g, b = self._hsv_to_rgb(self._hue, 1.0, 1.0)
            comp_r, comp_g, comp_b = self._hsv_to_rgb((self._hue + 0.5) % 1.0, 1.0, 1.0)

            self._set_light(flash_idx, r, g, b, int(100 * intensity_scale))
            self._set_light(1 - flash_idx, comp_r, comp_g, comp_b, int(40 * intensity_scale))
        elif energy > 0.8:
            # Energy spike — both lights strobe white
            self._set_light(0, 255, 255, 255, int(100 * intensity_scale))
            self._set_light(1, 255, 255, 255, int(100 * intensity_scale))
        else:
            # Dim between beats
            r, g, b = self._hsv_to_rgb(self._hue, 0.8, 1.0)
            brightness = int(max(10, 40 * energy) * intensity_scale)
            self._set_light(0, r, g, b, brightness)
            self._set_light(1, r, g, b, brightness)

    def _set_light(self, idx, r, g, b, brightness):
        """Send color + brightness to a light, respecting throttle."""
        if idx >= len(self.light_ips):
            return
        now = time.time()
        if now - self._last_cmd_time[idx] < MIN_CMD_INTERVAL:
            return
        self._last_cmd_time[idx] = now

        ip = self.light_ips[idx]
        try:
            self.govee_lan.set_color(ip, int(r), int(g), int(b))
            self.govee_lan.set_brightness(ip, max(1, min(100, brightness)))
        except Exception:
            pass  # UDP fire-and-forget, skip failures

    @staticmethod
    def _hsv_to_rgb(h, s, v):
        """Convert HSV (0-1 range) to RGB (0-255 range)."""
        if s == 0:
            val = int(v * 255)
            return val, val, val
        i = int(h * 6)
        f = h * 6 - i
        p = int(v * (1 - s) * 255)
        q = int(v * (1 - s * f) * 255)
        t = int(v * (1 - s * (1 - f)) * 255)
        v = int(v * 255)
        i %= 6
        if i == 0: return v, t, p
        if i == 1: return q, v, p
        if i == 2: return p, v, t
        if i == 3: return p, q, v
        if i == 4: return t, p, v
        return v, p, q
```

**Step 2: Commit**

```bash
git add backend/services/lightshow.py
git commit -m "feat: add real-time audio-reactive light show engine"
```

---

### Task 6: Light Show Routes

**Files:**
- Modify: `backend/routes/spotify.py` (add lightshow endpoints)
- Modify: `backend/app.py` (wire up lightshow engine at startup)

**Step 1: Add lightshow endpoints to spotify routes**

Append to `backend/routes/spotify.py`:

```python
from services.lightshow import LightShowEngine
from services.govee_lan import GoveeLanService

govee_lan = GoveeLanService()
lightshow = LightShowEngine(govee_lan)


@spotify_bp.get("/api/spotify/lightshow/status")
def lightshow_status():
    return jsonify(lightshow.get_status())


@spotify_bp.post("/api/spotify/lightshow/start")
def lightshow_start():
    data = request.get_json() or {}
    mode = data.get("mode", "pulse")
    device_ids = data.get("device_ids", [])
    latency_ms = data.get("latency_ms", 0)
    intensity = data.get("intensity", 7)

    if not device_ids:
        return jsonify({"error": "device_ids required (list of Govee device IDs)"}), 400
    if mode not in ("pulse", "ambient", "party"):
        return jsonify({"error": "mode must be pulse, ambient, or party"}), 400

    try:
        lightshow.start(mode, device_ids, latency_ms, intensity)
        return jsonify(lightshow.get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/lightshow/stop")
def lightshow_stop():
    lightshow.stop()
    return "", 204


@spotify_bp.post("/api/spotify/lightshow/config")
def lightshow_config():
    data = request.get_json() or {}
    if "mode" in data:
        lightshow.set_mode(data["mode"])
    if "latency_ms" in data:
        lightshow.set_latency(data["latency_ms"])
    if "intensity" in data:
        lightshow.set_intensity(data["intensity"])
    return jsonify(lightshow.get_status())
```

**Step 2: Commit**

```bash
git add backend/routes/spotify.py
git commit -m "feat: add light show control endpoints"
```

---

### Task 7: Frontend — Spotify Page

**Files:**
- Create: `frontend/src/pages/Spotify.tsx`
- Modify: `frontend/src/App.tsx` (add route)
- Modify: `frontend/src/pages/Home.tsx` (add app card)

**Step 1: Write the Spotify page**

```tsx
import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";

interface Track {
  title: string;
  artist: string;
  album: string;
  art_url: string;
  progress_ms: number;
  duration_ms: number;
  is_playing: boolean;
  track_id: string;
}

interface SpotifyDevice {
  id: string;
  name: string;
  type: string;
  is_active: boolean;
}

interface LightShowStatus {
  active: boolean;
  mode: string;
  latency_ms: number;
  intensity: number;
  lights_connected: number;
  pipe_exists: boolean;
}

interface GoveeDevice {
  device: string;
  deviceName: string;
}

export default function Spotify() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [track, setTrack] = useState<Track | null>(null);
  const [devices, setDevices] = useState<SpotifyDevice[]>([]);
  const [showDevices, setShowDevices] = useState(false);
  const [lightStatus, setLightStatus] = useState<LightShowStatus | null>(null);
  const [goveeDevices, setGoveeDevices] = useState<GoveeDevice[]>([]);
  const [selectedLights, setSelectedLights] = useState<string[]>([]);
  const [lightMode, setLightMode] = useState("pulse");
  const [latency, setLatency] = useState(0);
  const [intensity, setIntensity] = useState(7);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);

  // Check auth status
  useEffect(() => {
    fetch("/api/spotify/auth/status")
      .then((r) => r.json())
      .then((d) => setAuthenticated(d.authenticated))
      .catch(() => setAuthenticated(false));
  }, []);

  // Poll now-playing every 3 seconds
  useEffect(() => {
    if (!authenticated) return;

    const poll = () => {
      fetch("/api/spotify/now-playing")
        .then((r) => r.json())
        .then((d) => {
          if (d.nothing_playing) setTrack(null);
          else if (d.title) setTrack(d);
        })
        .catch(() => {});

      fetch("/api/spotify/lightshow/status")
        .then((r) => r.json())
        .then(setLightStatus)
        .catch(() => {});
    };

    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => clearInterval(pollRef.current);
  }, [authenticated]);

  // Load Govee devices for light selection
  useEffect(() => {
    if (!authenticated) return;
    fetch("/api/govee/devices")
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d)) {
          setGoveeDevices(d);
          // Auto-select floor lamps
          const floorLamps = d.filter(
            (dev: GoveeDevice) =>
              dev.deviceName?.toLowerCase().includes("floor lamp")
          );
          if (floorLamps.length > 0 && selectedLights.length === 0) {
            setSelectedLights(floorLamps.map((l: GoveeDevice) => l.device));
          }
        }
      })
      .catch(() => {});
  }, [authenticated]);

  const doAction = useCallback(async (endpoint: string, method = "POST") => {
    try {
      await fetch(`/api/spotify/${endpoint}`, { method });
      // Quick refresh
      const r = await fetch("/api/spotify/now-playing");
      const d = await r.json();
      if (d.nothing_playing) setTrack(null);
      else if (d.title) setTrack(d);
    } catch {}
  }, []);

  const loadDevices = useCallback(async () => {
    try {
      const r = await fetch("/api/spotify/devices");
      const d = await r.json();
      if (Array.isArray(d)) setDevices(d);
      setShowDevices(true);
    } catch {}
  }, []);

  const transferTo = useCallback(async (deviceId: string) => {
    await fetch("/api/spotify/transfer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: deviceId }),
    });
    setShowDevices(false);
  }, []);

  const startLightShow = useCallback(async () => {
    await fetch("/api/spotify/lightshow/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: lightMode,
        device_ids: selectedLights,
        latency_ms: latency,
        intensity,
      }),
    });
    const r = await fetch("/api/spotify/lightshow/status");
    setLightStatus(await r.json());
  }, [lightMode, selectedLights, latency, intensity]);

  const stopLightShow = useCallback(async () => {
    await fetch("/api/spotify/lightshow/stop", { method: "POST" });
    const r = await fetch("/api/spotify/lightshow/status");
    setLightStatus(await r.json());
  }, []);

  const updateConfig = useCallback(
    async (updates: Record<string, unknown>) => {
      await fetch("/api/spotify/lightshow/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      const r = await fetch("/api/spotify/lightshow/status");
      setLightStatus(await r.json());
    },
    []
  );

  function formatTime(ms: number) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, "0")}`;
  }

  if (authenticated === null) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">
            &larr;
          </Link>
          <h1 className="text-2xl font-bold">Spotify</h1>
        </div>

        {/* Auth Gate */}
        {!authenticated && (
          <div className="p-8 bg-gray-900 border border-gray-800 rounded-xl text-center space-y-4">
            <p className="text-gray-400">Connect your Spotify account</p>
            <button
              onClick={async () => {
                const r = await fetch("/api/spotify/auth/url");
                const d = await r.json();
                window.open(d.url, "_blank");
              }}
              className="px-6 py-3 bg-green-600 hover:bg-green-500 rounded-full font-semibold transition-colors"
            >
              Connect Spotify
            </button>
          </div>
        )}

        {authenticated && (
          <>
            {/* Now Playing */}
            <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4">
              {track ? (
                <>
                  <div className="flex gap-4">
                    {track.art_url && (
                      <img
                        src={track.art_url}
                        alt={track.album}
                        className="w-20 h-20 rounded-lg shadow-lg"
                      />
                    )}
                    <div className="flex-1 min-w-0">
                      <h2 className="font-semibold truncate">{track.title}</h2>
                      <p className="text-sm text-gray-400 truncate">
                        {track.artist}
                      </p>
                      <p className="text-xs text-gray-500 truncate">
                        {track.album}
                      </p>
                    </div>
                  </div>

                  {/* Progress bar */}
                  <div className="space-y-1">
                    <div className="h-1 bg-gray-700 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-green-500 rounded-full transition-all"
                        style={{
                          width: `${(track.progress_ms / track.duration_ms) * 100}%`,
                        }}
                      />
                    </div>
                    <div className="flex justify-between text-xs text-gray-500">
                      <span>{formatTime(track.progress_ms)}</span>
                      <span>{formatTime(track.duration_ms)}</span>
                    </div>
                  </div>

                  {/* Controls */}
                  <div className="flex items-center justify-center gap-6">
                    <button
                      onClick={() => doAction("previous")}
                      className="text-2xl text-gray-400 hover:text-white transition-colors"
                    >
                      &#x23EE;
                    </button>
                    <button
                      onClick={() =>
                        doAction(track.is_playing ? "pause" : "play")
                      }
                      className="w-12 h-12 bg-white text-black rounded-full flex items-center justify-center text-xl hover:scale-105 transition-transform"
                    >
                      {track.is_playing ? "\u23F8" : "\u25B6"}
                    </button>
                    <button
                      onClick={() => doAction("next")}
                      className="text-2xl text-gray-400 hover:text-white transition-colors"
                    >
                      &#x23ED;
                    </button>
                  </div>
                </>
              ) : (
                <p className="text-gray-400 text-center py-4">
                  Nothing playing
                </p>
              )}

              {/* Device selector */}
              <div className="pt-2 border-t border-gray-800">
                <button
                  onClick={loadDevices}
                  className="text-sm text-green-400 hover:text-green-300"
                >
                  {showDevices ? "Hide devices" : "Change speaker..."}
                </button>
                {showDevices && (
                  <div className="mt-2 space-y-1">
                    {devices.map((d) => (
                      <button
                        key={d.id}
                        onClick={() => transferTo(d.id)}
                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                          d.is_active
                            ? "bg-green-900/30 border border-green-700"
                            : "bg-gray-800 hover:bg-gray-700"
                        }`}
                      >
                        {d.name}{" "}
                        <span className="text-gray-500 text-xs">
                          ({d.type})
                        </span>
                        {d.is_active && (
                          <span className="text-green-400 text-xs ml-2">
                            Active
                          </span>
                        )}
                      </button>
                    ))}
                    {devices.length === 0 && (
                      <p className="text-gray-500 text-xs">
                        No Spotify devices found. Open Spotify on a device
                        first.
                      </p>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Light Show Controls */}
            <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4">
              <h2 className="text-lg font-semibold">Light Show</h2>

              {/* Light selection */}
              <div className="space-y-2">
                <label className="text-sm text-gray-400">Lights</label>
                <div className="flex flex-wrap gap-2">
                  {goveeDevices.map((d) => (
                    <button
                      key={d.device}
                      onClick={() => {
                        setSelectedLights((prev) =>
                          prev.includes(d.device)
                            ? prev.filter((id) => id !== d.device)
                            : [...prev, d.device]
                        );
                      }}
                      className={`px-3 py-1 rounded-full text-sm transition-colors ${
                        selectedLights.includes(d.device)
                          ? "bg-green-600 text-white"
                          : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                      }`}
                    >
                      {d.deviceName || d.device}
                    </button>
                  ))}
                </div>
              </div>

              {/* Mode selector */}
              <div className="space-y-2">
                <label className="text-sm text-gray-400">Mode</label>
                <div className="flex gap-2">
                  {["pulse", "ambient", "party"].map((m) => (
                    <button
                      key={m}
                      onClick={() => {
                        setLightMode(m);
                        if (lightStatus?.active) updateConfig({ mode: m });
                      }}
                      className={`flex-1 py-2 rounded-lg text-sm font-medium capitalize transition-colors ${
                        (lightStatus?.active ? lightStatus.mode : lightMode) ===
                        m
                          ? "bg-green-600 text-white"
                          : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                      }`}
                    >
                      {m}
                    </button>
                  ))}
                </div>
              </div>

              {/* Intensity */}
              <div className="space-y-1">
                <label className="text-sm text-gray-400">
                  Intensity: {intensity}
                </label>
                <input
                  type="range"
                  min={1}
                  max={10}
                  value={intensity}
                  onChange={(e) => {
                    const val = Number(e.target.value);
                    setIntensity(val);
                    if (lightStatus?.active) updateConfig({ intensity: val });
                  }}
                  className="w-full accent-green-600"
                />
              </div>

              {/* Latency */}
              <div className="space-y-1">
                <label className="text-sm text-gray-400">
                  Latency offset: {latency}ms
                </label>
                <input
                  type="range"
                  min={-500}
                  max={500}
                  step={10}
                  value={latency}
                  onChange={(e) => {
                    const val = Number(e.target.value);
                    setLatency(val);
                    if (lightStatus?.active)
                      updateConfig({ latency_ms: val });
                  }}
                  className="w-full accent-green-600"
                />
              </div>

              {/* Start/Stop */}
              {lightStatus?.active ? (
                <button
                  onClick={stopLightShow}
                  className="w-full py-3 bg-red-600 hover:bg-red-500 rounded-xl font-semibold transition-colors"
                >
                  Stop Light Show
                </button>
              ) : (
                <button
                  onClick={startLightShow}
                  disabled={selectedLights.length === 0}
                  className="w-full py-3 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-xl font-semibold transition-colors"
                >
                  Start Light Show
                </button>
              )}

              {lightStatus?.active && (
                <p className="text-xs text-gray-500 text-center">
                  {lightStatus.lights_connected} light(s) connected
                  {lightStatus.pipe_exists
                    ? " \u00b7 Audio stream active"
                    : " \u00b7 Pattern mode (no audio)"}
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
```

**Step 2: Add route to App.tsx**

Add import and route:

```tsx
import Spotify from "./pages/Spotify";
// In Routes:
<Route path="/spotify" element={user ? <Spotify /> : <Navigate to="/" />} />
```

**Step 3: Add app card to Home.tsx**

Add to the `apps` array:

```tsx
{
  name: "Spotify",
  description: "Music & light show",
  path: "/spotify",
  icon: "\u{1F3B5}",
},
```

**Step 4: Commit**

```bash
git add frontend/src/pages/Spotify.tsx frontend/src/App.tsx frontend/src/pages/Home.tsx
git commit -m "feat: add Spotify page with playback controls and light show UI"
```

---

### Task 8: Deployment — librespot + systemd

**Files:**
- Create: `deploy/localweb-librespot.service`
- Modify: `deploy/force-update.sh`

**Step 1: Write librespot systemd service**

```ini
[Unit]
Description=librespot Spotify Connect receiver
After=network.target

[Service]
Type=simple
User=pi
ExecStartPre=/bin/bash -c 'test -p /tmp/librespot-pipe || mkfifo /tmp/librespot-pipe'
ExecStart=/usr/local/bin/librespot --name Drewtopia --backend pipe --device /tmp/librespot-pipe --bitrate 160 --initial-volume 80 --enable-volume-normalisation
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**Step 2: Update force-update.sh**

Add librespot install check and service restart:

```bash
# After "Restarting localweb..." section, add:

# Install librespot if not present
if ! command -v librespot &> /dev/null; then
    echo "Installing librespot..."
    curl -sL https://github.com/librespot-org/librespot/releases/latest/download/librespot-linux-armhf.tar.gz | tar xz -C /usr/local/bin/
fi

# Create audio pipe if needed
test -p /tmp/librespot-pipe || mkfifo /tmp/librespot-pipe

# Enable and restart librespot
sudo cp "$REPO_DIR/deploy/localweb-librespot.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable localweb-librespot
sudo systemctl restart localweb-librespot

echo "Done! localweb and librespot are up to date."
```

**Step 3: Commit**

```bash
git add deploy/localweb-librespot.service deploy/force-update.sh
git commit -m "feat: add librespot systemd service and deployment"
```

---

### Task 9: Environment Setup + Frontend Build

**Step 1: Add Spotify env vars to backend/.env**

```
SPOTIFY_CLIENT_ID=<your-client-id>
SPOTIFY_CLIENT_SECRET=<your-client-secret>
```

**Step 2: Build frontend**

```bash
cd frontend && npm run build
```

**Step 3: Commit everything**

```bash
git add frontend/dist/ backend/.env.example
git commit -m "chore: build frontend and update env example"
```

---

### Task 10: Test Locally + Deploy

**Step 1: Start backend locally to verify routes**

```bash
cd backend
LOCALWEB_ENV=dev python app.py
```

Verify these endpoints respond:
- `GET http://localhost:5000/api/spotify/auth/status` → `{"authenticated": false}`
- `GET http://localhost:5000/api/spotify/auth/url` → `{"url": "https://accounts.spotify.com/authorize?..."}`
- `GET http://localhost:5000/api/spotify/lightshow/status` → `{"active": false, ...}`

**Step 2: Push and deploy**

```bash
git push origin main
ssh pi@10.0.0.74 '/home/pi/localweb/deploy/force-update.sh'
```

**Step 3: Authorize Spotify on Pi**

Open `http://localhost:5000/api/spotify/auth/url` from the Pi itself (or port-forward), click through Spotify OAuth, token gets saved.

---

## Redirect URI

You need to add this redirect URI in your [Spotify Developer Dashboard](https://developer.spotify.com/dashboard):

```
http://10.0.0.74:5000/api/spotify/auth/callback
```
