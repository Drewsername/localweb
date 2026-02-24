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
