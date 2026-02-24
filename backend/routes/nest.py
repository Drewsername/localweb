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


OAUTH_REDIRECT_URI = "http://localhost:5000/api/nest/auth/callback"


@nest_bp.get("/api/nest/auth/url")
def auth_url():
    return jsonify({"url": nest.get_auth_url(OAUTH_REDIRECT_URI)})


@nest_bp.get("/api/nest/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400
    redirect_uri = OAUTH_REDIRECT_URI
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
    rows = db.execute("""
        SELECT u.id, us.value
        FROM users u
        JOIN user_settings us ON us.user_id = u.id
            AND us.namespace = 'nest.preferences'
            AND us.key = 'preferred_temp'
        WHERE u.is_home = 1
    """).fetchall()

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
