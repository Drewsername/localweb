import subprocess
import re
import platform
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from db import get_db

SCAN_INTERVAL = 30  # seconds
DEPARTURE_THRESHOLD = 300  # 5 minutes


class PresenceScanner:
    def __init__(self, eink=None, govee=None):
        self.eink = eink
        self.govee = govee
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _run(self):
        while self._running:
            try:
                self._scan()
            except Exception as e:
                print(f"Presence scan error: {e}")
            time.sleep(SCAN_INTERVAL)

    def _get_network_macs(self):
        """Parse ARP table for all MAC addresses on the local network."""
        macs = set()
        try:
            if platform.system() == "Windows":
                output = subprocess.check_output(["arp", "-a"], text=True)
            else:
                output = subprocess.check_output(["ip", "neigh"], text=True)

            for line in output.splitlines():
                match = re.search(
                    r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line
                )
                if match:
                    macs.add(match.group(0).lower().replace("-", ":"))
        except Exception as e:
            print(f"ARP scan failed: {e}")
        return macs

    def _scan(self):
        network_macs = self._get_network_macs()
        now = datetime.now(timezone.utc)
        departure_cutoff = now - timedelta(seconds=DEPARTURE_THRESHOLD)

        db = get_db()
        try:
            users = db.execute("SELECT id, name, mac_address, is_home FROM users").fetchall()

            newly_arrived = []
            anyone_home = False

            for user in users:
                mac = user["mac_address"]
                was_home = bool(user["is_home"])
                is_now_home = mac in network_macs

                if is_now_home:
                    anyone_home = True
                    db.execute(
                        "UPDATE users SET is_home = 1, last_seen = ? WHERE id = ?",
                        (now.isoformat(), user["id"]),
                    )
                    if not was_home:
                        newly_arrived.append(user)
                else:
                    # Only mark as departed if not seen for DEPARTURE_THRESHOLD
                    if was_home:
                        row = db.execute(
                            "SELECT last_seen FROM users WHERE id = ?",
                            (user["id"],),
                        ).fetchone()
                        if row["last_seen"]:
                            last = datetime.fromisoformat(row["last_seen"])
                            if last.tzinfo is None:
                                last = last.replace(tzinfo=timezone.utc)
                            if last < departure_cutoff:
                                db.execute(
                                    "UPDATE users SET is_home = 0 WHERE id = ?",
                                    (user["id"],),
                                )
                                anyone_home = anyone_home or False

            db.commit()

            # Handle arrivals — most recent arrival (last in the list processed) wins
            if newly_arrived:
                latest = newly_arrived[-1]
                self._on_arrival(latest, db)

            # Handle everyone departed
            if not anyone_home:
                still_home = db.execute(
                    "SELECT COUNT(*) as c FROM users WHERE is_home = 1"
                ).fetchone()
                if still_home["c"] == 0 and self.eink:
                    self.eink.idle()

        finally:
            db.close()

    def _on_arrival(self, user, db):
        """Handle a user arriving home."""
        name = user["name"]

        # Update e-ink display
        if self.eink:
            try:
                self.eink.welcome(name)
            except Exception as e:
                print(f"E-ink welcome failed: {e}")

        # Apply user's Govee settings
        if self.govee:
            try:
                rows = db.execute(
                    "SELECT namespace, key, value FROM user_settings WHERE user_id = ? AND namespace LIKE 'govee.%'",
                    (user["id"],),
                ).fetchall()

                settings = {}
                for r in rows:
                    ns = r["namespace"]
                    if ns not in settings:
                        settings[ns] = {}
                    settings[ns][r["key"]] = json.loads(r["value"])

                if settings:
                    self.govee.apply_user_settings(settings)
            except Exception as e:
                print(f"Failed to apply settings for {name}: {e}")
