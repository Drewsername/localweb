# Spotify Light Show — Design

## Overview

A new "Spotify" app for Drewtopia that combines music playback control with real-time audio-reactive light shows on two Govee floor lamps. The Pi acts as a Spotify Connect receiver via librespot, intercepts the audio stream for beat analysis, and drives lights via Govee LAN UDP.

## Architecture

```
Spotify app (phone) → selects "Drewtopia" device
                            ↓
                      librespot (Pi)
                            ↓
                      named pipe (PCM)
                            ↓
                  Python audio processor
                     ↓              ↓
              beat/energy       ALSA playback
               analysis         (Pi audio out)
                     ↓
              light show engine
                     ↓
              Govee LAN UDP → both floor lamps
```

Separately, Spotify Web API provides metadata + controls for the UI:

```
React UI ←→ Flask routes ←→ Spotify Web API
  (now playing, play/pause/skip, light show controls)
```

## Pi Performance Guardrails

The Pi is a Raspberry Pi running Raspbian Stretch with Python 3.9. Every design choice prioritizes keeping CPU/memory low:

- **librespot**: Written in Rust, tiny footprint (~5MB RSS, <5% CPU). Handles all Spotify protocol/decryption natively.
- **Audio analysis**: Process small chunks (512 samples at 44.1kHz ≈ 12ms). Use numpy FFT only — no aubio/librosa (heavy deps). ~2% CPU for basic FFT on Pi.
- **Analysis rate**: 30 Hz analysis loop (every ~33ms). Plenty for visual responsiveness, not wasteful.
- **Light commands**: Govee LAN UDP is fire-and-forget, no connection overhead. Throttle to max 20 commands/sec total (10 per light) to avoid flooding the network.
- **Single background thread**: One thread runs the analysis + light loop. No thread pool, no multiprocessing.
- **Lazy startup**: librespot + analysis thread only start when user activates the light show. Idle when not in use.

## Spotify Integration

### OAuth (Web API)
- Client ID: `<redacted>`
- Client Secret: stored in `backend/.env` as `SPOTIFY_CLIENT_SECRET`
- Redirect URI: `http://localhost:5000/api/spotify/auth/callback`
- Scopes: `user-read-playback-state`, `user-modify-playback-state`, `user-read-currently-playing`
- Token persistence: `backend/spotify_tokens.json` (same pattern as Nest)
- Auto-refresh on 401 (same pattern as Nest)

### librespot (Spotify Connect)
- Installed as a binary on the Pi
- Device name: "Drewtopia"
- Audio backend: pipe (outputs raw 16-bit 44.1kHz stereo PCM to named pipe)
- Managed as a systemd service: `localweb-librespot.service`
- Credentials: uses Spotify OAuth token or standalone auth

## Govee LAN API Migration

Replace cloud API calls with LAN UDP for all real-time controls. Cloud API kept only for device discovery fallback and scene listing.

### Discovery
- Multicast scan: send `{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}` to `239.255.255.250:4001`
- Listen on UDP port 4002 for responses containing device IP, ID, SKU
- Cache discovered devices with TTL (re-scan every 5 minutes)

### Control (port 4003, UDP to device IP)
- On/off: `{"msg":{"cmd":"turn","data":{"value":1}}}`
- Brightness: `{"msg":{"cmd":"brightness","data":{"value":50}}}`
- Color: `{"msg":{"cmd":"colorwc","data":{"color":{"r":255,"g":0,"b":0},"colorTemInKelvin":0}}}`
- Status: `{"msg":{"cmd":"devStatus","data":{}}}`

### Migration scope
- New `GoveeLanService` class handles discovery + UDP control
- Existing `GoveeService` updated: tries LAN first, falls back to cloud API
- Existing Lights page gets instant LAN control with zero code changes (same REST API, faster backend)

## Light Show Engine

### Audio Analysis (lightweight)
- Read 512-sample chunks from librespot pipe (~12ms at 44.1kHz)
- Compute RMS energy (overall loudness)
- Simple FFT: extract 3 frequency bands:
  - Bass (20-250 Hz) — drives beat detection
  - Mid (250-4000 Hz) — drives color warmth
  - Treble (4000-16000 Hz) — drives sparkle/shimmer effects
- Beat detection: bass energy spike above rolling average (simple threshold)
- All numpy, no external audio libs

### Light Show Modes

**Pulse**
- On beat: flash brightness to 100%, shift hue by 30-60 degrees
- Between beats: brightness decays smoothly back to ~40%
- Color palette follows energy: warm (reds/oranges) at high energy, cool (blues/purples) at low
- Both lights in sync

**Ambient**
- Smooth sine-wave color rotation, speed proportional to overall energy
- Each light offset 180 degrees in hue (complementary colors)
- Brightness maps to energy (30-80% range, never harsh)
- Very gentle, no sudden changes

**Party**
- Lights alternate on every beat (light A flashes, then light B)
- Colors are complementary pairs, cycling through the rainbow
- Energy spikes trigger both lights to strobe white briefly
- Maximum saturation, maximum fun

### Configurable Parameters
- **Latency offset** (ms): shifts light commands forward/back to compensate for audio delay. Default 0. Range: -500 to +500.
- **Intensity** (1-10): scales brightness range and color saturation. Default 7.
- **Mode**: Off / Pulse / Ambient / Party

## Backend Components

### `backend/services/spotify.py` — SpotifyService
```
- __init__(): load client_id, client_secret, tokens from file
- get_auth_url(redirect_uri) → str
- exchange_code(code, redirect_uri)
- _refresh_token()
- _request(method, path, **kwargs) → dict (auto-refresh on 401)
- get_current_track() → {title, artist, album, art_url, progress_ms, duration_ms, is_playing, device}
- play() / pause() / next() / previous()
- get_devices() → [{id, name, type, is_active}]
- transfer_playback(device_id)
```

### `backend/services/govee_lan.py` — GoveeLanService
```
- __init__()
- discover_devices() → [{device_id, ip, sku, name}]  (UDP multicast scan)
- _send_command(ip, command_dict)  (UDP to port 4003)
- turn(ip, on: bool)
- set_brightness(ip, value: int)
- set_color(ip, r, g, b)
- get_status(ip) → {onOff, brightness, color, colorTemInKelvin}
```

### `backend/services/lightshow.py` — LightShowEngine
```
- __init__(govee_lan: GoveeLanService)
- start(mode, light_ips, latency_ms, intensity)
- stop()
- set_mode(mode) / set_latency(ms) / set_intensity(level)
- _analysis_loop()  (background thread: read PCM → FFT → drive lights)
- _compute_bands(samples) → {bass, mid, treble, rms}
- _detect_beat(bass_energy) → bool
- _apply_pulse(bands, beat) / _apply_ambient(bands) / _apply_party(bands, beat)
```

### `backend/routes/spotify.py` — Blueprint
```
GET  /api/spotify/auth/url          → {url}
GET  /api/spotify/auth/callback     → redirects after token exchange
GET  /api/spotify/auth/status       → {authenticated: bool}
GET  /api/spotify/now-playing       → {title, artist, album, art_url, progress_ms, ...}
POST /api/spotify/play              → 204
POST /api/spotify/pause             → 204
POST /api/spotify/next              → 204
POST /api/spotify/previous          → 204
GET  /api/spotify/devices           → [{id, name, type, is_active}]
POST /api/spotify/transfer          → 204  (body: {device_id})
GET  /api/spotify/lightshow/status  → {mode, latency_ms, intensity, active}
POST /api/spotify/lightshow/start   → 204  (body: {mode, latency_ms, intensity})
POST /api/spotify/lightshow/stop    → 204
POST /api/spotify/lightshow/config  → 204  (body: {mode?, latency_ms?, intensity?})
```

## Frontend

### `frontend/src/pages/Spotify.tsx`

Sections:
1. **Auth gate**: If not authenticated, show "Connect Spotify" button → OAuth flow
2. **Now Playing**: Album art (large), track title, artist, progress bar
3. **Playback Controls**: Previous / Play|Pause / Next buttons
4. **Light Show Controls**:
   - Mode selector: Off / Pulse / Ambient / Party (pill buttons)
   - Latency slider: -500ms to +500ms
   - Intensity slider: 1-10
   - Start/Stop button

### Home page
- Add Spotify card to apps array with music icon

### Router
- Add `/spotify` route in `App.tsx`

## Deployment

### New systemd service: `localweb-librespot.service`
```ini
[Unit]
Description=librespot Spotify Connect
After=network.target

[Service]
Type=simple
User=pi
ExecStart=/usr/local/bin/librespot --name "Drewtopia" --backend pipe --device /tmp/librespot-pipe --bitrate 160 --initial-volume 80
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Updated `force-update.sh`
- Install librespot if not present
- Create named pipe `/tmp/librespot-pipe` if not exists
- Restart both services

### New Python dependencies
- None for audio analysis (numpy already pinned, stdlib socket for UDP)
- `spotipy` NOT used — raw requests like Nest service (fewer deps on Pi)

### Environment variables (backend/.env)
```
SPOTIFY_CLIENT_ID=<redacted>
SPOTIFY_CLIENT_SECRET=<redacted>
```

## Risk Mitigations

- **Pi CPU**: librespot is Rust (fast), audio analysis is 30Hz numpy (light). Total added load: ~10% CPU.
- **Govee rate**: LAN UDP has no rate limit. Throttle to 20 cmd/sec as a courtesy.
- **Audio pipe**: If librespot isn't running, light show gracefully reports "no audio source" — doesn't crash.
- **Token expiry**: Both Spotify and Govee use auto-refresh. Stored tokens survive reboots.
- **Network**: UDP is fire-and-forget. If a light command is lost, the next one (33ms later) corrects it. No retries needed.
