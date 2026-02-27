"""Govee LAN UDP service for instant device control.

Uses the Govee LAN protocol (UDP multicast discovery + direct UDP commands)
to control devices on the local network with zero rate limits and minimal
latency. Complements the cloud API service in govee.py.

Protocol reference:
  - Discovery: multicast to 239.255.255.250:4001, listen on 4002
  - Control:   unicast UDP to device IP on port 4003
"""

import json
import logging
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

MULTICAST_ADDR = "239.255.255.250"
SCAN_PORT = 4001
LISTEN_PORT = 4002
CONTROL_PORT = 4003
SCAN_TIMEOUT = 3  # seconds to wait for discovery responses
DEVICE_CACHE_TTL = 300  # 5 minutes
STATUS_TIMEOUT = 1  # seconds to wait for devStatus response


class GoveeLanService:
    """Controls Govee lights over the local network via UDP.

    Thread-safe: all access to the device cache is guarded by a lock.
    All control methods are fire-and-forget UDP sends (except get_status).
    """

    def __init__(self):
        self._device_cache = {}  # device_id -> {device_id, ip, sku}
        self._cache_time = 0.0  # timestamp of last successful scan
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_devices(self, force=False):
        """Multicast scan for Govee devices on the LAN.

        Returns a list of dicts: [{device_id, ip, sku}, ...]
        Results are cached for DEVICE_CACHE_TTL seconds unless force=True.
        """
        with self._lock:
            if not force and self._device_cache and (
                time.time() - self._cache_time < DEVICE_CACHE_TTL
            ):
                return list(self._device_cache.values())

        # Run discovery outside the lock to avoid blocking other threads
        # for the full scan duration.
        devices = self._run_scan()

        with self._lock:
            self._device_cache = {d["device_id"]: d for d in devices}
            self._cache_time = time.time()
            return list(self._device_cache.values())

    def _run_scan(self):
        """Execute the UDP multicast scan and collect responses."""
        scan_msg = json.dumps({
            "msg": {
                "cmd": "scan",
                "data": {"account_topic": "reserve"},
            }
        }).encode("utf-8")

        devices = []

        # Create the send socket (multicast to SCAN_PORT)
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Set multicast TTL to 1 (local network only)
            send_sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_MULTICAST_TTL,
                struct.pack("b", 1),
            )
            send_sock.sendto(scan_msg, (MULTICAST_ADDR, SCAN_PORT))
        except OSError as exc:
            logger.error("Failed to send discovery multicast: %s", exc)
            return devices
        finally:
            send_sock.close()

        # Listen for responses on LISTEN_PORT
        listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen_sock.bind(("", LISTEN_PORT))

            # Join the multicast group so we receive responses
            mreq = struct.pack(
                "4sl",
                socket.inet_aton(MULTICAST_ADDR),
                socket.INADDR_ANY,
            )
            listen_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            listen_sock.settimeout(SCAN_TIMEOUT)

            deadline = time.time() + SCAN_TIMEOUT
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                listen_sock.settimeout(remaining)
                try:
                    data, _addr = listen_sock.recvfrom(4096)
                except socket.timeout:
                    break

                device = self._parse_scan_response(data)
                if device:
                    devices.append(device)
                    logger.debug("Discovered Govee device: %s at %s", device["device_id"], device["ip"])
        except OSError as exc:
            logger.error("Discovery listen error: %s", exc)
        finally:
            listen_sock.close()

        logger.info("Govee LAN scan complete: found %d device(s)", len(devices))
        return devices

    @staticmethod
    def _parse_scan_response(data):
        """Parse a scan response payload into a device dict, or None."""
        try:
            payload = json.loads(data.decode("utf-8"))
            msg = payload.get("msg", {})
            if msg.get("cmd") != "scan":
                return None
            d = msg.get("data", {})
            ip = d.get("ip")
            device_id = d.get("device")
            sku = d.get("sku", "")
            if ip and device_id:
                return {"device_id": device_id, "ip": ip, "sku": sku}
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
            logger.warning("Failed to parse scan response: %s", exc)
        return None

    def get_device_ip(self, device_id):
        """Look up the LAN IP for a device, triggering a scan on cache miss.

        Returns the IP string, or None if the device is not found.
        """
        with self._lock:
            cached = self._device_cache.get(device_id)
            if cached and (time.time() - self._cache_time < DEVICE_CACHE_TTL):
                return cached["ip"]

        # Cache miss or stale — run a fresh scan
        self.discover_devices(force=True)

        with self._lock:
            cached = self._device_cache.get(device_id)
            return cached["ip"] if cached else None

    # ------------------------------------------------------------------
    # Control (fire-and-forget UDP)
    # ------------------------------------------------------------------

    @staticmethod
    def _send(ip, cmd_dict):
        """Send a command dict to a device via UDP (fire-and-forget).

        cmd_dict is the full message envelope, e.g.:
            {"msg": {"cmd": "turn", "data": {"value": 1}}}
        """
        payload = json.dumps(cmd_dict).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.sendto(payload, (ip, CONTROL_PORT))
        except OSError as exc:
            logger.error("Failed to send command to %s: %s", ip, exc)
        finally:
            sock.close()

    def turn(self, ip, on):
        """Turn a device on or off.

        Args:
            ip: Device LAN IP address.
            on: True for on, False for off.
        """
        self._send(ip, {
            "msg": {
                "cmd": "turn",
                "data": {"value": 1 if on else 0},
            }
        })

    def set_brightness(self, ip, value):
        """Set device brightness.

        Args:
            ip: Device LAN IP address.
            value: Brightness level, clamped to 1-100.
        """
        value = max(1, min(100, int(value)))
        self._send(ip, {
            "msg": {
                "cmd": "brightness",
                "data": {"value": value},
            }
        })

    def set_color(self, ip, r, g, b):
        """Set device color via RGB values.

        Args:
            ip: Device LAN IP address.
            r, g, b: Color channel values, each clamped to 0-255.
        """
        r = max(0, min(255, int(r)))
        g = max(0, min(255, int(g)))
        b = max(0, min(255, int(b)))
        self._send(ip, {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": r, "g": g, "b": b},
                    "colorTemInKelvin": 0,
                },
            }
        })

    def set_color_temp(self, ip, kelvin):
        """Set device color temperature in Kelvin.

        Args:
            ip: Device LAN IP address.
            kelvin: Color temperature, clamped to 2000-9000.
        """
        kelvin = max(2000, min(9000, int(kelvin)))
        self._send(ip, {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": 0, "g": 0, "b": 0},
                    "colorTemInKelvin": kelvin,
                },
            }
        })

    # ------------------------------------------------------------------
    # Status query (request-response over UDP)
    # ------------------------------------------------------------------

    def get_status(self, ip):
        """Query device status and wait for a response.

        Returns the parsed response dict, or None on timeout/error.
        Blocks for up to STATUS_TIMEOUT seconds.
        """
        query = json.dumps({
            "msg": {
                "cmd": "devStatus",
                "data": {},
            }
        }).encode("utf-8")

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.settimeout(STATUS_TIMEOUT)
            sock.sendto(query, (ip, CONTROL_PORT))
            data, _addr = sock.recvfrom(4096)
            return json.loads(data.decode("utf-8"))
        except socket.timeout:
            logger.debug("Status query to %s timed out", ip)
            return None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("Status query to %s failed: %s", ip, exc)
            return None
        finally:
            sock.close()
