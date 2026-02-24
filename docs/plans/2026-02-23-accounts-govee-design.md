# Accounts, Presence Detection, and Govee Lighting Control

## Overview

Add a multi-user accounts system to Drewtopia with WiFi presence detection, e-ink welcome greetings, and Govee smart lighting control as the first app module.

## Data Model

### SQLite Database (`backend/data/localweb.db`)

**users**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PRIMARY KEY | |
| name | TEXT NOT NULL | |
| mac_address | TEXT UNIQUE NOT NULL | Auto-detected from request IP via ARP |
| ip_address | TEXT | Last known IP |
| is_home | BOOLEAN DEFAULT 0 | Currently on WiFi |
| last_seen | DATETIME | Last ARP detection time |
| created_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |

**user_settings**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PRIMARY KEY | |
| user_id | INTEGER REFERENCES users(id) | |
| namespace | TEXT NOT NULL | e.g. "govee.device_ABC", "theme", "display" |
| key | TEXT NOT NULL | e.g. "brightness", "color", "power" |
| value | TEXT NOT NULL | JSON-encoded |
| updated_at | DATETIME DEFAULT CURRENT_TIMESTAMP | |
| | UNIQUE(user_id, namespace, key) | |

The namespace+key pattern supports arbitrary future settings without schema changes. Device-specific settings use the device ID in the namespace (e.g. `govee.AB:CD:12:34`).

## Backend API

### User Routes

| Method | Route | Purpose |
|--------|-------|---------|
| POST | `/api/users/register` | Register name; backend auto-detects MAC from request IP via ARP table |
| GET | `/api/users/me` | Get current user by IP -> MAC lookup |
| GET | `/api/users/home` | List all users currently on WiFi |

### Settings Routes

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/settings` | Get all settings for current user |
| PUT | `/api/settings/:namespace` | Bulk-update settings in a namespace |

### Govee Routes

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/govee/devices` | List all Govee devices + their capabilities |
| GET | `/api/govee/devices/:device/state` | Get live device state from Govee API |
| POST | `/api/govee/devices/:device/control` | Control a device; also saves as user's preferred setting |
| GET | `/api/govee/devices/:device/scenes` | Get available dynamic scenes |

### Govee Service Layer

Python class wrapping all Govee API endpoints:
- `get_devices()` — GET `openapi.api.govee.com/router/api/v1/user/devices`
- `get_device_state(sku, device)` — POST `/router/api/v1/device/state`
- `control_device(sku, device, capability)` — POST `/router/api/v1/device/control`
- `get_scenes(sku, device)` — POST `/router/api/v1/device/scenes`

All capabilities supported: on_off, brightness (range), colorRgb, colorTemperatureK, segments, work_mode, dynamic_scene, toggles. The service is a thin wrapper — it forwards whatever capability the frontend sends.

API key stored in `backend/.env`, loaded via `os.environ`, never exposed to frontend.

## Presence Detection

- Background thread in Flask, runs every 30 seconds
- Parses `arp -a` (or `ip neigh` on Linux) for MAC addresses on the local network
- Compares against users table, updates `is_home` and `last_seen`
- **Arrival**: user was `is_home=0`, now detected -> mark home, trigger welcome
- **Departure**: user not seen for 5 minutes -> mark `is_home=0`

### On Arrival (most recent arrival takes priority)

1. Update e-ink: "Welcome home, [Name]!"
2. Load user's stored settings for all `govee.*` namespaces
3. Apply each setting to the corresponding device via Govee API

### On Everyone Departs

- E-ink reverts to idle screen ("Drewtopia")

## Frontend

### Routing

| Path | Component | Purpose |
|------|-----------|---------|
| `/` | Welcome/Onboarding OR redirect to /home | Entry point |
| `/home` | Dashboard | "Welcome, [Name]!" + app cards grid |
| `/lights` | Lighting Control | Govee device controls |

### Onboarding Flow

1. App checks localStorage for saved user ID
2. Calls `GET /api/users/me` to verify (MAC must still match)
3. If no user: show "Welcome to Drewtopia! Please share your name to get started."
4. User enters name -> `POST /api/users/register` -> save user ID to localStorage
5. Redirect to `/home`

### Home Dashboard

- "Welcome, [Name]!" header
- Grid of app cards (Lighting card first; grid pattern makes adding more trivial)
- Mobile-optimized: full-width cards, large touch targets

### Lighting Control (`/lights`)

- Discovers all Govee devices dynamically from API
- Per device card showing device name
- Controls rendered based on device capabilities: power toggle, brightness slider, color picker
- **Always shows live state** fetched from Govee API (not stored preferences)
- Every control change: (1) sends command to Govee, (2) saves as user's preferred setting for next arrival

### State Management

- React context for current user identity
- Per-page data fetching for live state (no global state library)

## Core Principle: Live State vs. Stored Preferences

- **Live state**: what devices are actually doing right now (fetched from APIs). This is what every app screen displays.
- **Stored preferences**: a user's settings, saved when they make changes. Only applied automatically on arrival.
- Multiple users can be home and control things simultaneously. Last write wins on devices. Each user's stored preferences remain independent.
- This principle applies to ALL app modules, not just lighting.

## E-ink Display

- Extend `InkyHandler` with `welcome(name)` and `idle()` methods
- Only update on actual state changes (e-ink refresh is ~15s)
- Welcome screen: "Welcome home, [Name]!"
- Idle screen: "Drewtopia" (when no one is home or default state)

## Security

- Govee API key in `.env`, proxied through backend
- Network trust model: if you're on the WiFi, you're trusted
- No authentication beyond MAC-based identity (appropriate for private home network)
