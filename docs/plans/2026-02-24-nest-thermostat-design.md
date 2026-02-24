# Nest Thermostat App Design

## Overview

Add a Nest thermostat control app to Drewtopia, following existing patterns (Govee lights app). Integrates with the Google Smart Device Management (SDM) API for thermostat control, with a multi-occupant temperature optimization algorithm that computes the ideal setpoint based on everyone's preferences.

## Google Device Access Setup (One-Time)

1. Go to https://console.nest.google.com/device-access and pay the $5 developer fee
2. Create a project, note the **Project ID**
3. In Google Cloud Console, create OAuth2 credentials (Web application type)
   - Set authorized redirect URI to `http://<pi-ip>:5000/api/nest/auth/callback`
   - Note the **Client ID** and **Client Secret**
4. Add to `backend/.env`:
   ```
   NEST_PROJECT_ID=<project-id>
   NEST_CLIENT_ID=<client-id>
   NEST_CLIENT_SECRET=<client-secret>
   ```
5. Visit `http://<pi-ip>:5000/api/nest/auth/url` in a browser to authorize
6. Complete the Google OAuth2 consent flow — tokens are stored automatically

## Backend

### NestService (`backend/services/nest.py`)

Class-based service mirroring `GoveeService` pattern. Wraps the SDM REST API at `https://smartdevicemanagement.googleapis.com/v1`.

**Methods:**
- `get_devices()` — list all thermostats in the project
- `get_device_state(device_id)` — current temp, target temp, mode, humidity, HVAC status
- `set_temperature(device_id, temp_f)` — set target temperature
- `set_mode(device_id, mode)` — set HVAC mode (HEAT, COOL, HEATCOOL, OFF)
- `set_eco(device_id, enabled)` — toggle eco mode

**OAuth2 token management:**
- Tokens stored in `backend/nest_tokens.json` (gitignored)
- Auto-refresh on 401 using refresh token
- One-time auth via browser OAuth2 flow

### Routes (`backend/routes/nest.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/nest/devices` | List thermostats |
| GET | `/api/nest/devices/{id}/state` | Get device state |
| POST | `/api/nest/devices/{id}/control` | Set temp, mode, or eco |
| GET | `/api/nest/auth/url` | Get OAuth2 authorization URL |
| GET | `/api/nest/auth/callback` | Handle OAuth2 callback |
| GET | `/api/nest/optimal-temp` | Get current algorithm result |
| POST | `/api/nest/admin/guardrails` | Set min/max bounds, user weights (admin only) |

Control actions auto-save to `user_settings` with namespace `nest.{device_id}`.

### Temperature Optimization Algorithm (`backend/services/thermostat_optimizer.py`)

**Weighted asymmetric quadratic discomfort minimization.**

Each user has:
- `preferred_temp` — their ideal temperature in F (stored in `user_settings`, namespace `nest.preferences`)

Admin guardrails (namespace `nest.admin`):
- `min_temp` / `max_temp` — absolute bounds (default 65-78F)
- Per-user `weight` — influence multiplier (default 1.0)

**Algorithm: `compute_optimal_temp(present_users)`**

1. Gather preferred temps and weights for all present users who have set a preference
2. Sweep candidate temps from `min_temp` to `max_temp` in 0.5F steps
3. For each candidate temp T, compute total discomfort:
   - If T < user's pref: `weight * 1.5 * (pref - T)^2` (cold penalty, 1.5x)
   - If T >= user's pref: `weight * 1.0 * (T - pref)^2` (warm penalty, 1.0x)
4. Pick the candidate T with minimum total discomfort
5. Clamp to [min_temp, max_temp]

**Cold asymmetry rationale:** Research shows people are more sensitive to being too cold than too warm from their preferred setpoint. The 1.5x cold / 1.0x warm ratio shifts results slightly warmer than a naive average, matching real-world comfort patterns.

**Trigger:** Presence scanner arrival/departure events recompute and apply the optimal temp.

**Edge cases:**
- 1 person home: use their preference directly (clamped to bounds)
- Nobody home: no change (future: eco mode)
- User with no preference set: excluded from calculation

## Frontend

### Thermostat Page (`frontend/src/pages/Thermostat.tsx`)

Follows existing page pattern (like `Lights.tsx`).

**All users see:**
- Back button to `/home`
- Thermostat card per device:
  - Current ambient temperature (large, prominent)
  - Target temperature with +/- buttons (0.5F increments)
  - Mode selector: Heat / Cool / Heat-Cool / Off
  - Eco mode toggle
  - HVAC status indicator (heating / cooling / idle)
- "My Preference" section: temp picker to save preferred temp for the algorithm
- "Optimized temp: XF" display when algorithm is active

**Admin section (Drew only, gated by `user.isAdmin`):**
- Min/max temperature bound inputs
- Per-user weight table (user list with weight adjustments)

**Optimistic UI:** Buttons/toggles update state immediately on click, then reconcile with API response. Revert on failure.

### Home Page Registration

Add to `apps` array in `Home.tsx`:
```typescript
{ name: "Thermostat", description: "Nest climate control", path: "/thermostat", icon: "thermostat-icon" }
```

### Route Registration

Add to `App.tsx`:
```typescript
<Route path="/thermostat" element={<Thermostat />} />
```

## Settings Storage

| Namespace | Key | Value | Who Sets |
|-----------|-----|-------|----------|
| `nest.preferences` | `preferred_temp` | `72` | Any user |
| `nest.admin` | `min_temp` | `65` | Admin only |
| `nest.admin` | `max_temp` | `78` | Admin only |
| `nest.admin` | `user_weight.{user_id}` | `1.5` | Admin only |
| `nest.{device_id}` | `target_temp` | `71` | Auto-saved on control |
| `nest.{device_id}` | `mode` | `HEAT` | Auto-saved on control |

## Presence Integration

Extend `backend/services/presence.py` arrival handler:
1. After applying Govee settings, also compute optimal Nest temp
2. Query `user_settings` for `nest.preferences` namespace for all home users
3. Call `thermostat_optimizer.compute_optimal_temp()`
4. Apply result via `NestService.set_temperature()`
