import subprocess
import re
import platform
from flask import Blueprint, jsonify, request
from db import get_db

users_bp = Blueprint("users", __name__)


def get_mac_for_ip(ip):
    """Look up MAC address for an IP via the system ARP table."""
    try:
        if platform.system() == "Windows":
            output = subprocess.check_output(["arp", "-a"], text=True)
        else:
            output = subprocess.check_output(["ip", "neigh"], text=True)

        for line in output.splitlines():
            if ip in line:
                match = re.search(
                    r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line
                )
                if match:
                    return match.group(0).lower().replace("-", ":")
    except Exception:
        pass
    return None


def get_client_ip():
    """Get the real client IP, respecting X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


@users_bp.post("/api/users/register")
def register():
    data = request.get_json()
    if not data or not data.get("name"):
        return jsonify({"error": "Name is required"}), 400

    name = data["name"].strip()
    if not name:
        return jsonify({"error": "Name is required"}), 400

    ip = get_client_ip()
    mac = get_mac_for_ip(ip)

    if not mac:
        return jsonify({"error": "Could not detect your device. Make sure you're on the same WiFi network."}), 400

    db = get_db()
    try:
        existing = db.execute(
            "SELECT id, name FROM users WHERE mac_address = ?", (mac,)
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE users SET name = ?, ip_address = ? WHERE id = ?",
                (name, ip, existing["id"]),
            )
            db.commit()
            user_id = existing["id"]
        else:
            cursor = db.execute(
                "INSERT INTO users (name, mac_address, ip_address) VALUES (?, ?, ?)",
                (name, mac, ip),
            )
            db.commit()
            user_id = cursor.lastrowid

        return jsonify({"id": user_id, "name": name})
    finally:
        db.close()


@users_bp.get("/api/users/me")
def me():
    ip = get_client_ip()
    mac = get_mac_for_ip(ip)

    if not mac:
        return jsonify({"error": "Device not recognized"}), 404

    db = get_db()
    try:
        user = db.execute(
            "SELECT id, name, is_home FROM users WHERE mac_address = ?", (mac,)
        ).fetchone()

        if not user:
            return jsonify({"error": "User not registered"}), 404

        return jsonify({"id": user["id"], "name": user["name"], "is_home": bool(user["is_home"])})
    finally:
        db.close()


@users_bp.get("/api/users/home")
def users_home():
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, name, last_seen FROM users WHERE is_home = 1 ORDER BY last_seen DESC"
        ).fetchall()
        return jsonify([{"id": r["id"], "name": r["name"], "last_seen": r["last_seen"]} for r in rows])
    finally:
        db.close()
