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
            return None  # OFF or ECO mode -- can't set temp

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
