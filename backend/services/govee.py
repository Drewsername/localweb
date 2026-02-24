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
