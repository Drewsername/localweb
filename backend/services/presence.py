import subprocess
import re
import platform
import threading
import time
import json
import traceback
from datetime import datetime, timezone, timedelta
from db import get_db

SCAN_INTERVAL = 5  # seconds — lightweight, runs often
DEPARTURE_THRESHOLD = 15  # seconds — 3 missed scans = gone
WELCOME_DURATION = 60  # seconds before switching to dashboard


REWELCOME_COOLDOWN = 300  # seconds — don't re-welcome same user within 5 min


def trigger_arrival_music(spotify_service):
    """Start arrival playlist on Spotify. Returns result dict for logging."""
    db = get_db()
    try:
        # Find Drew's user ID
        drew = db.execute("SELECT id FROM users WHERE LOWER(name) = 'drew'").fetchone()
        if not drew:
            return {"skipped": True, "reason": "drew user not found"}

        # Read arrival settings
        rows = db.execute(
            "SELECT key, value FROM user_settings WHERE user_id = ? AND namespace = 'spotify.arrival'",
            (drew["id"],),
        ).fetchall()
        settings = {r["key"]: json.loads(r["value"]) for r in rows}

        if not settings.get("enabled"):
            return {"skipped": True, "reason": "arrival music disabled"}
        playlist_uri = settings.get("playlist_uri", "")
        if not playlist_uri:
            return {"skipped": True, "reason": "no playlist configured"}

        shuffle = settings.get("shuffle", True)

        # Find target device — prefer "Drewtopia", fall back to first available
        devices = spotify_service.get_devices()
        device_id = None
        for d in devices:
            if d["name"].lower() == "drewtopia":
                device_id = d["id"]
                break
        if not device_id and devices:
            device_id = devices[0]["id"]

        # Set shuffle then start playback
        try:
            spotify_service.set_shuffle(shuffle)
        except Exception:
            pass  # shuffle may fail if no active device yet
        spotify_service.play_context(playlist_uri, device_id=device_id)
        return {"ok": True, "device": device_id, "playlist": playlist_uri, "shuffle": shuffle}
    finally:
        db.close()


class PresenceScanner:
    def __init__(self, eink=None, govee=None, nest=None, spotify=None):
        self.eink = eink
        self.govee = govee
        self.nest = nest
        self.spotify = spotify
        self._thread = None
        self._running = False
        self._welcome_timer = None
        self._showing_welcome = False
        self._last_dashboard_users = None  # track displayed home list
        self._last_welcome_names = None  # set of recently-welcomed names
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
                traceback.print_exc()
            time.sleep(SCAN_INTERVAL)

    def _get_current_ipv4_for_macs(self):
        """Look up current IPv4 addresses from the ARP/NDP table by MAC.

        Stored IPs go stale (especially IPv6 with privacy extensions),
        so we find the actual current IPv4 for each MAC from `ip neigh`.
        """
        mac_to_ip = {}
        try:
            output = subprocess.check_output(["ip", "neigh"], text=True)
            for line in output.splitlines():
                parts = line.split()
                if len(parts) < 5:
                    continue
                ip = parts[0]
                if ":" in ip:
                    continue  # skip IPv6 — only want IPv4 for reliable ping
                mac_match = re.search(
                    r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", line
                )
                if mac_match:
                    mac_to_ip[mac_match.group(0).lower()] = ip
        except Exception:
            pass
        return mac_to_ip

    def _ping_users(self, users):
        """Ping all known user IPs to force ARP table refresh.

        Without this, disconnected devices stay STALE in the ARP table
        for minutes. A failed ping makes the entry go to FAILED state,
        which we then filter out.
        """
        is_windows = platform.system() == "Windows"
        # On Linux, prefer current IPv4 from ARP table over stored IP
        # (stored IPs go stale, especially IPv6 with privacy extensions)
        mac_to_ipv4 = {} if is_windows else self._get_current_ipv4_for_macs()

        for user in users:
            mac = user["mac_address"]
            ip = mac_to_ipv4.get(mac) or user["ip_address"]
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

        On Linux, only includes REACHABLE entries from `ip neigh` — devices
        that responded to a recent ARP/NDP probe after our ping.
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
                    # Only count REACHABLE entries — device confirmed via ARP/NDP.
                    # STALE/DELAY/PROBE entries retain stale MACs (especially
                    # IPv6 link-local entries we never ping) and cause false
                    # "home" detections.
                    if "REACHABLE" not in line:
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

            # Handle arrivals — welcome all who arrived this scan
            if newly_arrived:
                self._on_arrival(newly_arrived, db)
            elif someone_departed and not self._showing_welcome:
                # Someone left — refresh dashboard
                self.show_dashboard()

        finally:
            db.close()

    def _on_arrival(self, users, db):
        """Handle one or more users arriving home."""
        now = time.time()

        # Filter out recently-welcomed users (phone oscillation prevention)
        new_arrivals = []
        for user in users:
            name = user["name"]
            if (
                self._last_welcome_names
                and name in self._last_welcome_names
                and self._last_welcome_time
                and now - self._last_welcome_time < REWELCOME_COOLDOWN
            ):
                continue
            new_arrivals.append(user)

        if not new_arrivals:
            # All users were recently welcomed — just refresh dashboard
            self._last_dashboard_users = None
            if not self._showing_welcome:
                self.show_dashboard()
            return

        # Cancel any existing welcome timer
        if self._welcome_timer:
            self._welcome_timer.cancel()

        # Show welcome screen with all new arrival names
        names = [u["name"] for u in new_arrivals]
        from drivers.eink import render_welcome
        self._showing_welcome = True
        self._last_welcome_names = set(names)
        self._last_welcome_time = now
        self._update_display(render_welcome(names))

        # After WELCOME_DURATION seconds, switch to dashboard
        self._welcome_timer = threading.Timer(WELCOME_DURATION, self.show_dashboard)
        self._welcome_timer.daemon = True
        self._welcome_timer.start()

        # Apply each arriving user's Govee settings
        for user in new_arrivals:
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
                    print(f"Failed to apply settings for {user['name']}: {e}")

        # Trigger arrival music for Drew
        if self.spotify and any(u["name"].lower() == "drew" for u in new_arrivals):
            try:
                result = trigger_arrival_music(self.spotify)
                print(f"Arrival music: {result}")
            except Exception as e:
                print(f"Arrival music failed: {e}")

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
