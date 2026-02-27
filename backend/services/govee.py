import os
import uuid
import requests

from services.govee_lan import GoveeLanService

GOVEE_BASE = "https://openapi.api.govee.com"


class GoveeService:
    def __init__(self):
        self.api_key = os.environ.get("GOVEE_API_KEY", "")
        self._devices_cache = None
        self.lan = GoveeLanService()

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
        """Get current device state, trying LAN first then cloud API."""
        # --- LAN attempt ---
        try:
            ip = self.lan.get_device_ip(device_id)
            if ip:
                lan_resp = self.lan.get_status(ip)
                if lan_resp is not None:
                    return self._lan_state_to_cloud_format(lan_resp)
        except Exception as e:
            print(f"[govee] LAN state query failed for {device_id}, falling back to cloud: {e}")

        # --- Cloud fallback ---
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

    @staticmethod
    def _lan_state_to_cloud_format(lan_resp):
        """Convert a LAN devStatus response to cloud-style capabilities list.

        LAN response shape:
            {"msg": {"cmd": "devStatus", "data": {
                "onOff": 1, "brightness": 100,
                "color": {"r": 255, "g": 0, "b": 0},
                "colorTemInKelvin": 0
            }}}

        Cloud format:
            {"capabilities": [
                {"type": "devices.capabilities.on_off",
                 "instance": "powerSwitch", "state": {"value": 1}},
                ...
            ]}
        """
        data = lan_resp.get("msg", {}).get("data", {})
        capabilities = []

        if "onOff" in data:
            capabilities.append({
                "type": "devices.capabilities.on_off",
                "instance": "powerSwitch",
                "state": {"value": data["onOff"]},
            })

        if "brightness" in data:
            capabilities.append({
                "type": "devices.capabilities.range",
                "instance": "brightness",
                "state": {"value": data["brightness"]},
            })

        if "color" in data:
            c = data["color"]
            rgb_int = (c.get("r", 0) << 16) | (c.get("g", 0) << 8) | c.get("b", 0)
            capabilities.append({
                "type": "devices.capabilities.color_setting",
                "instance": "colorRgb",
                "state": {"value": rgb_int},
            })

        if "colorTemInKelvin" in data:
            capabilities.append({
                "type": "devices.capabilities.color_setting",
                "instance": "colorTemperatureK",
                "state": {"value": data["colorTemInKelvin"]},
            })

        return {"capabilities": capabilities}

    def control_device(self, device_id, capability):
        """Send a control command, trying LAN first then cloud API.

        capability should be a dict with: type, instance, value
        e.g. {"type": "devices.capabilities.on_off", "instance": "powerSwitch", "value": 1}
        """
        instance = capability.get("instance", "")
        value = capability.get("value")

        # --- LAN attempt ---
        try:
            ip = self.lan.get_device_ip(device_id)
            if ip:
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
                # For unrecognized instances (e.g. dynamic_scene), fall through to cloud
        except Exception as e:
            print(f"[govee] LAN control failed for {device_id}, falling back to cloud: {e}")

        # --- Cloud fallback ---
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
