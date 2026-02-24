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
WELCOME_DURATION = 60  # seconds before switching to dashboard


class PresenceScanner:
    def __init__(self, eink=None, govee=None):
        self.eink = eink
        self.govee = govee
        self._thread = None
        self._running = False
        self._welcome_timer = None
        self._showing_welcome = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._welcome_timer:
            self._welcome_timer.cancel()

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

    def _get_home_users(self, db):
        """Get list of names of users currently home, ordered by last_seen DESC."""
        rows = db.execute(
            "SELECT name FROM users WHERE is_home = 1 ORDER BY last_seen DESC"
        ).fetchall()
        return [r["name"] for r in rows]

    def _update_display(self, img):
        """Send an image to the e-ink display (or just update the web preview)."""
        if self.eink:
            try:
                self.eink.show_image(img)
            except Exception as e:
                print(f"E-ink display failed: {e}")
        else:
            from drivers.eink import set_current
            set_current(img)

    def show_dashboard(self):
        """Render and display the dashboard showing who's home."""
        self._showing_welcome = False
        from drivers.eink import render_dashboard
        db = get_db()
        try:
            home_users = self._get_home_users(db)
            self._update_display(render_dashboard(home_users))
        finally:
            db.close()

    def _scan(self):
        network_macs = self._get_network_macs()
        now = datetime.now(timezone.utc)
        departure_cutoff = now - timedelta(seconds=DEPARTURE_THRESHOLD)

        db = get_db()
        try:
            users = db.execute("SELECT id, name, mac_address, is_home FROM users").fetchall()

            newly_arrived = []
            someone_departed = False

            for user in users:
                mac = user["mac_address"]
                was_home = bool(user["is_home"])
                is_now_home = mac in network_macs

                if is_now_home:
                    db.execute(
                        "UPDATE users SET is_home = 1, last_seen = ? WHERE id = ?",
                        (now.isoformat(), user["id"]),
                    )
                    if not was_home:
                        newly_arrived.append(user)
                else:
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
                                someone_departed = True

            db.commit()

            # Handle arrivals — most recent arrival wins
            if newly_arrived:
                latest = newly_arrived[-1]
                self._on_arrival(latest, db)
            elif someone_departed and not self._showing_welcome:
                # Someone left — refresh dashboard
                self.show_dashboard()

        finally:
            db.close()

    def _on_arrival(self, user, db):
        """Handle a user arriving home."""
        name = user["name"]

        # Cancel any existing welcome timer
        if self._welcome_timer:
            self._welcome_timer.cancel()

        # Show welcome screen
        from drivers.eink import render_welcome
        self._showing_welcome = True
        self._update_display(render_welcome(name))

        # After WELCOME_DURATION seconds, switch to dashboard
        self._welcome_timer = threading.Timer(WELCOME_DURATION, self.show_dashboard)
        self._welcome_timer.daemon = True
        self._welcome_timer.start()

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
