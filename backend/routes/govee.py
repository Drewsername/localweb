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
    print(f"[govee control] device={device_id} body={data}")
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
