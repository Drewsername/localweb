"""Sonos speaker discovery and playback control via SoCo."""

import logging
import threading

import soco

logger = logging.getLogger(__name__)

SPEAKER_NAME = "Living Room"


class SonosService:
    """Discover and control the Sonos speaker for audio forwarding."""

    def __init__(self):
        self._speaker = None
        self._lock = threading.Lock()
        self._forwarding = False

    def _discover(self):
        """Find the target Sonos speaker on the network."""
        try:
            zones = soco.discover(timeout=5)
            if not zones:
                logger.warning("Sonos: no speakers found on network")
                return None
            for zone in zones:
                if zone.player_name.lower() == SPEAKER_NAME.lower():
                    logger.info("Sonos: found '%s' at %s", zone.player_name, zone.ip_address)
                    return zone
            names = [z.player_name for z in zones]
            logger.warning("Sonos: '%s' not found. Available: %s", SPEAKER_NAME, names)
            return None
        except Exception:
            logger.exception("Sonos: discovery failed")
            return None

    @property
    def speaker(self):
        with self._lock:
            if self._speaker is None:
                self._speaker = self._discover()
            return self._speaker

    @property
    def is_forwarding(self):
        return self._forwarding

    def start_forwarding(self, stream_url):
        """Tell the Sonos speaker to play from our HTTP audio stream."""
        sp = self.speaker
        if sp is None:
            logger.error("Sonos: cannot forward — speaker not found")
            return False
        try:
            sp.play_uri(stream_url, title="Drewtopia")
            self._forwarding = True
            logger.info("Sonos: forwarding audio from %s", stream_url)
            return True
        except Exception:
            logger.exception("Sonos: failed to start forwarding")
            return False

    def stop_forwarding(self):
        """Stop playback on the Sonos speaker."""
        sp = self.speaker
        if sp is None:
            return
        try:
            sp.stop()
            self._forwarding = False
            logger.info("Sonos: stopped forwarding")
        except Exception:
            logger.exception("Sonos: failed to stop forwarding")
        self._forwarding = False

    def get_volume(self):
        """Return the current Sonos volume (0-100)."""
        sp = self.speaker
        if sp is None:
            return None
        try:
            return sp.volume
        except Exception:
            logger.exception("Sonos: failed to get volume")
            return None

    def set_volume(self, level):
        """Set Sonos volume (0-100)."""
        sp = self.speaker
        if sp is None:
            return False
        level = max(0, min(100, int(level)))
        try:
            sp.volume = level
            logger.info("Sonos: volume set to %d", level)
            return True
        except Exception:
            logger.exception("Sonos: failed to set volume")
            return False
