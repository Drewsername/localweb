import subprocess
import re
import platform
import threading
import time
import json
from datetime import datetime, timezone, timedelta
from db import get_db

SCAN_INTERVAL = 5  # seconds — lightweight, runs often
DEPARTURE_THRESHOLD = 15  # seconds — 3 missed scans = gone
WELCOME_DURATION = 60  # seconds before switching to dashboard


REWELCOME_COOLDOWN = 300  # seconds — don't re-welcome same user within 5 min


class PresenceScanner:
    def __init__(self, eink=None, govee=None, nest=None):
        self.eink = eink
        self.govee = govee
        self.nest = nest
        self._thread = None
        self._running = False
        self._welcome_timer = None
        self._showing_welcome = False
        self._last_dashboard_users = None  # track displayed home list
        self._last_welcome_name = None
        self._last_welcome_time = None

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

    def _ping_users(self, users):
        """Ping all known user IPs to force ARP table refresh.

        Without this, disconnected devices stay STALE in the ARP table
        for minutes. A failed ping makes the entry go to FAILED state,
        which we then filter out.
        """
        is_windows = platform.system() == "Windows"
        for user in users:
            ip = user["ip_address"]
            if not ip:
                continue
            try:
                if is_windows:
                    subprocess.run(
                        ["ping", "-n", "1", "-w", "500", ip],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                else:
                    subprocess.run(
                        ["ping", "-c", "1", "-W", "1", ip],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
            except Exception:
                pass

    def _get_reachable_macs(self):
        """Get MAC addresses that are actually reachable on the network.

        On Linux, filters out FAILED entries from `ip neigh` so we only
        count devices that responded to recent pings.
        """
        macs = set()
        try:
            if platform.system() == "Windows":
                output = subprocess.check_output(["arp", "-a"], text=True)
                for line in output.splitlines():
                    match = re.search(
                        r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line
                    )
                    if match:
                        macs.add(match.group(0).lower().replace("-", ":"))
            else:
                output = subprocess.check_output(["ip", "neigh"], text=True)
                for line in output.splitlines():
                    # Skip entries that are FAILED (device not responding)
                    if "FAILED" in line:
                        continue
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

    def show_dashboard(self, force=False):
        """Render and display the dashboard showing who's home."""
        self._showing_welcome = False
        from drivers.eink import render_dashboard
        db = get_db()
        try:
            home_users = self._get_home_users(db)
            if not force and home_users == self._last_dashboard_users:
                return
            self._last_dashboard_users = home_users
            self._update_display(render_dashboard(home_users))
        finally:
            db.close()

    def _scan(self):
        db = get_db()
        try:
            users = db.execute(
                "SELECT id, name, mac_address, ip_address, is_home FROM users"
            ).fetchall()

            # Ping all known IPs to refresh ARP entries
            self._ping_users(users)

            # Now read the ARP table — stale/failed entries are filtered
            network_macs = self._get_reachable_macs()
            now = datetime.now(timezone.utc)
            departure_cutoff = now - timedelta(seconds=DEPARTURE_THRESHOLD)

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
        now = time.time()

        # Skip re-welcome if same user just arrived recently (phone oscillation)
        if (
            self._last_welcome_name == name
            and self._last_welcome_time
            and now - self._last_welcome_time < REWELCOME_COOLDOWN
        ):
            # Silently mark as home, update dashboard without welcome fanfare
            self._last_dashboard_users = None  # force dashboard refresh
            if not self._showing_welcome:
                self.show_dashboard()
            return

        # Cancel any existing welcome timer
        if self._welcome_timer:
            self._welcome_timer.cancel()

        # Show welcome screen
        from drivers.eink import render_welcome
        self._showing_welcome = True
        self._last_welcome_name = name
        self._last_welcome_time = now
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

        # Apply optimal Nest temperature based on all home users
        if self.nest:
            try:
                from services.thermostat_optimizer import compute_optimal_temp

                # Get all home users' preferences
                home_users = db.execute("""
                    SELECT u.id, us.value
                    FROM users u
                    JOIN user_settings us ON us.user_id = u.id
                        AND us.namespace = 'nest.preferences'
                        AND us.key = 'preferred_temp'
                    WHERE u.is_home = 1
                """).fetchall()

                # Get admin guardrails
                admin = db.execute("SELECT id FROM users WHERE LOWER(name) = 'drew'").fetchone()
                guardrails = {"min_temp": 65, "max_temp": 78}
                if admin:
                    for key in ("min_temp", "max_temp"):
                        row = db.execute(
                            "SELECT value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key = ?",
                            (admin["id"], key),
                        ).fetchone()
                        if row:
                            guardrails[key] = json.loads(row["value"])

                # Build user list with weights
                users = []
                for r in home_users:
                    weight = 1.0
                    if admin:
                        w_row = db.execute(
                            "SELECT value FROM user_settings WHERE user_id = ? AND namespace = 'nest.admin' AND key = ?",
                            (admin["id"], f"user_weight.{r['id']}"),
                        ).fetchone()
                        if w_row:
                            weight = json.loads(w_row["value"])
                    users.append({"preferred_temp": json.loads(r["value"]), "weight": weight})

                if users:
                    optimal = compute_optimal_temp(users, **guardrails)
                    if optimal is not None:
                        devices = self.nest.get_devices()
                        for d in devices:
                            self.nest.set_temperature(d["id"], optimal)
                        print(f"Set Nest to {optimal}F (optimal for {len(users)} home users)")
            except Exception as e:
                print(f"Failed to apply Nest settings: {e}")
