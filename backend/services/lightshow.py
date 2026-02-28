"""Real-time audio-reactive light show engine.

Reads raw PCM audio from librespot's named pipe, performs lightweight FFT
analysis to extract frequency bands and detect beats, then drives Govee
floor lamps over the LAN to create synchronized light shows.

Designed for Raspberry Pi: small FFT (1024 samples), 30 Hz analysis loop,
throttled to 20 commands/sec per light.
"""

import logging
import math
import os
import threading
import time

try:
    import fcntl
except ImportError:
    fcntl = None  # Windows dev mode — pipe reader won't run

import numpy as np

logger = logging.getLogger(__name__)

# -- Audio constants -------------------------------------------------------
PIPE_PATH = "/tmp/librespot-pipe"
SAMPLE_RATE = 44100
CHANNELS = 2
BYTES_PER_SAMPLE = 2  # 16-bit signed LE
FFT_SIZE = 1024  # samples per chunk (mono)
CHUNK_BYTES = FFT_SIZE * CHANNELS * BYTES_PER_SAMPLE  # 4096 bytes

# -- FFT band boundaries (bin indices for 1024-point FFT at 44100 Hz) ------
# Bin resolution: 44100 / 1024 ~ 43.07 Hz per bin
BASS_LOW = 1    # ~43 Hz
BASS_HIGH = 6   # ~258 Hz
MID_LOW = 6     # ~258 Hz
MID_HIGH = 93   # ~4000 Hz
TREBLE_LOW = 93   # ~4000 Hz
TREBLE_HIGH = 372  # ~16000 Hz

# -- Throttling -------------------------------------------------------------
MIN_CMD_INTERVAL = 0.05  # 50ms = 20 cmd/sec per light
LOOP_PERIOD = 1.0 / 30   # ~33ms for 30 Hz analysis loop

# -- Sonos pre-buffer -------------------------------------------------------
# Accumulate this many chunks before telling Sonos to start playing, so
# it has data immediately when it connects (~0.5 s at 44100 Hz).
SONOS_PREBUFFER_CHUNKS = 20

# -- Grace period for track transitions ------------------------------------
# When librespot closes the pipe (e.g. between tracks), wait this long for
# it to reconnect before stopping Sonos forwarding.
PIPE_GRACE_PERIOD = 8.0  # seconds

# -- Light show defaults ----------------------------------------------------
WARM_WHITE = (255, 180, 100)
WARM_WHITE_BRIGHTNESS = 50
VALID_MODES = ("pulse", "ambient", "party")

# -- Spectral flux / beat detection -----------------------------------------
FLUX_HISTORY_LEN = 40        # rolling window for flux average (~1.3s at 30 Hz)
FLUX_BEAT_THRESHOLD = 1.5    # flux > this * rolling avg = beat
FLUX_SPIKE_THRESHOLD = 3.0   # flux > this * rolling avg = spike (white flash)
BEAT_COOLDOWN_FRAMES = 4     # ~133ms minimum between beats

# -- EMA smoothing -----------------------------------------------------------
EMA_ALPHA_FAST = 0.35
EMA_ALPHA_MEDIUM = 0.2
EMA_ALPHA_SLOW = 0.08

# -- Energy trend ------------------------------------------------------------
ENERGY_TREND_WINDOW = 120    # ~4s at 30 Hz

# -- Delta-based command throttling ------------------------------------------
COLOR_DELTA_THRESHOLD = 10       # skip send if RGB change < 10/255
BRIGHTNESS_DELTA_THRESHOLD = 3   # skip send if brightness change < 3/100

# -- Spectral centroid -------------------------------------------------------
CENTROID_MIN_HZ = 100
CENTROID_MAX_HZ = 8000


class LightShowEngine:
    """Drives Govee lights reactively based on audio analysis.

    A persistent pipe reader thread always drains librespot's named pipe so
    playback never stalls.  When a show is active the audio is analysed and
    used to drive lights; otherwise the data is simply discarded.

    Usage:
        engine = LightShowEngine(govee_lan_service)
        engine.start(mode="pulse", device_ids=["AA:BB:...", "CC:DD:..."])
        ...
        engine.stop()
    """

    def __init__(self, govee_lan, audio_streamer=None, sonos_service=None):
        self._govee = govee_lan
        self._streamer = audio_streamer
        self._sonos = sonos_service
        self._running = False
        self._lock = threading.Lock()

        # Configuration (mutable at runtime via setters)
        self.mode = "off"
        self.latency_ms = 0
        self.intensity = 7  # 1-10

        # Light state
        self._device_ids = []
        self._light_ips = []  # resolved IPs, parallel to _device_ids
        self._last_cmd_time = []  # per-light throttle tracker

        # Audio analysis state
        self._hue = 0.0  # current hue position, 0-1
        self._phase = 0.0  # phase accumulator for ambient mode
        self._beat_count = 0  # total beats detected (for party alternation)
        self._last_rms = 0.0
        self._audio_connected = False  # True when pipe reader has a live fd
        self._sonos_started = False  # True once Sonos forwarding began
        self._chunks_since_connect = 0

        # Advanced audio features
        self._prev_spectrum = None
        self._ema_bass = 0.0
        self._ema_mid = 0.0
        self._ema_treble = 0.0
        self._ema_rms = 0.0
        self._ema_centroid = 0.0
        self._ema_flux = 0.0
        self._flux_history = []
        self._frames_since_beat = 0
        self._rms_trend_buffer = []
        self._energy_trend = 0.0

        # Delta-based command throttling state
        self._last_sent_state = {}  # {idx: (r, g, b, brightness)}

        # Start the HTTP audio stream server
        if self._streamer:
            self._streamer.start()

        # Start persistent pipe reader (keeps pipe drained so librespot
        # never blocks, and feeds audio to analysis when show is active)
        self._pipe_thread = threading.Thread(
            target=self._persistent_pipe_reader, daemon=True
        )
        self._pipe_thread.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_active(self):
        return self._running and self.mode != "off"

    def start(self, mode, device_ids, latency_ms=0, intensity=7):
        """Start the light show.

        Args:
            mode: One of 'pulse', 'ambient', 'party'.
            device_ids: List of Govee device IDs to control.
            latency_ms: Delay after analysis before sending commands.
            intensity: Show intensity 1-10.
        """
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of {VALID_MODES}")

        # Stop any running show first
        if self._running:
            self.stop()

        self.latency_ms = max(0, int(latency_ms))
        self.intensity = max(1, min(10, int(intensity)))
        self._device_ids = list(device_ids)

        # Resolve device IPs via LAN discovery
        self._light_ips = []
        for did in self._device_ids:
            ip = self._govee.get_device_ip(did)
            if ip:
                self._light_ips.append(ip)
                logger.info("Light show: resolved %s -> %s", did, ip)
            else:
                self._light_ips.append(None)
                logger.warning("Light show: could not resolve IP for %s", did)

        self._last_cmd_time = [0.0] * len(self._light_ips)

        # Turn lights on
        for ip in self._light_ips:
            if ip:
                try:
                    self._govee.turn(ip, True)
                except Exception:
                    logger.exception("Failed to turn on light at %s", ip)

        # Reset analysis state
        self._hue = 0.0
        self._phase = 0.0
        self._beat_count = 0
        self._last_rms = 0.0
        self._prev_spectrum = None
        self._ema_bass = 0.0
        self._ema_mid = 0.0
        self._ema_treble = 0.0
        self._ema_rms = 0.0
        self._ema_centroid = 0.0
        self._ema_flux = 0.0
        self._flux_history = []
        self._frames_since_beat = 0
        self._rms_trend_buffer = []
        self._energy_trend = 0.0
        self._last_sent_state = {}

        # Activate — the persistent pipe reader will begin analysis
        with self._lock:
            self.mode = mode
        self._running = True
        logger.info("Light show started: mode=%s, lights=%d", mode, len(self._light_ips))

    def stop(self):
        """Stop the light show and reset lights to warm white."""
        self._running = False

        # Reset lights to warm white
        for ip in self._light_ips:
            if ip:
                try:
                    r, g, b = WARM_WHITE
                    self._govee.set_color(ip, r, g, b)
                    self._govee.set_brightness(ip, WARM_WHITE_BRIGHTNESS)
                except Exception:
                    logger.exception("Failed to reset light at %s", ip)

        with self._lock:
            self.mode = "off"
        logger.info("Light show stopped")

    def set_mode(self, mode):
        """Change the active mode without restarting."""
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Must be one of {VALID_MODES}")
        with self._lock:
            self.mode = mode
            # Reset state for clean transition
            self._hue = 0.0
            self._phase = 0.0
            self._beat_count = 0
            self._prev_spectrum = None
            self._ema_bass = 0.0
            self._ema_mid = 0.0
            self._ema_treble = 0.0
            self._ema_rms = 0.0
            self._ema_centroid = 0.0
            self._ema_flux = 0.0
            self._flux_history = []
            self._frames_since_beat = 0
            self._rms_trend_buffer = []
            self._energy_trend = 0.0
            self._last_sent_state = {}
        logger.info("Light show mode changed to %s", mode)

    def set_latency(self, ms):
        """Adjust latency compensation in milliseconds."""
        self.latency_ms = max(0, int(ms))

    def set_intensity(self, level):
        """Set show intensity (1-10)."""
        self.intensity = max(1, min(10, int(level)))

    def get_status(self):
        """Return current engine status."""
        return {
            "active": self.is_active,
            "mode": self.mode,
            "latency_ms": self.latency_ms,
            "intensity": self.intensity,
            "lights_connected": sum(1 for ip in self._light_ips if ip),
            "pipe_exists": os.path.exists(PIPE_PATH),
            "audio_connected": self._audio_connected,
        }

    # ------------------------------------------------------------------
    # Persistent pipe reader
    # ------------------------------------------------------------------

    def _read_pipe(self, fd):
        """Read from an open pipe fd until EOF or error.

        Returns normally on EOF.  Raises OSError on pipe errors.
        """
        last_analysis = 0.0
        while True:
            raw = os.read(fd, CHUNK_BYTES)
            if not raw:
                return  # EOF — writer closed

            if self._streamer:
                self._streamer.buffer.put(raw)
            self._chunks_since_connect += 1
            latest = raw

            # Drain any additional data already in the pipe
            if fcntl is not None:
                orig_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, orig_flags | os.O_NONBLOCK)
                try:
                    while True:
                        extra = os.read(fd, CHUNK_BYTES)
                        if not extra:
                            break
                        if self._streamer:
                            self._streamer.buffer.put(extra)
                        self._chunks_since_connect += 1
                        if len(extra) == CHUNK_BYTES:
                            latest = extra
                except BlockingIOError:
                    pass
                finally:
                    fcntl.fcntl(fd, fcntl.F_SETFL, orig_flags)

            # Pre-buffer then start Sonos forwarding
            if (not self._sonos_started
                    and self._sonos and self._streamer
                    and self._chunks_since_connect >= SONOS_PREBUFFER_CHUNKS):
                self._sonos_started = True
                threading.Thread(
                    target=self._start_sonos_forwarding, daemon=True
                ).start()

            # Light show analysis at 30 Hz
            now = time.time()
            if self._running and len(latest) == CHUNK_BYTES:
                if now - last_analysis >= LOOP_PERIOD:
                    last_analysis = now
                    samples = np.frombuffer(latest, dtype=np.int16)
                    stereo = samples.reshape(-1, 2)
                    mono = stereo.mean(axis=1).astype(np.float64)
                    if self.latency_ms > 0:
                        time.sleep(self.latency_ms / 1000.0)
                    self._analyze_and_drive(mono)
            elif self._running:
                if now - last_analysis >= LOOP_PERIOD:
                    last_analysis = now
                    self._drive_idle_pattern()

    def _persistent_pipe_reader(self):
        """Always drain the audio pipe so librespot never blocks.

        When the light show is active, audio data is analysed and used to
        drive lights.  Audio is always forwarded to the Sonos streamer
        buffer so the speaker can play it.  The thread runs for the
        lifetime of the process.

        When the pipe closes (e.g. between tracks), the reader waits up to
        PIPE_GRACE_PERIOD seconds for librespot to reconnect before
        stopping Sonos forwarding.  This keeps the stream alive during
        track transitions.
        """
        while True:
            if not os.path.exists(PIPE_PATH):
                if self._running:
                    self._drive_idle_pattern()
                time.sleep(0.5)
                continue

            fd = None
            is_first_connect = not self._sonos_started
            try:
                fd = os.open(PIPE_PATH, os.O_RDONLY)
                self._audio_connected = True
                self._chunks_since_connect = 0

                if is_first_connect:
                    if self._streamer:
                        self._streamer.buffer.clear()
                    logger.info("Pipe reader: connected to %s", PIPE_PATH)
                else:
                    logger.info("Pipe reader: reconnected (track transition)")

                self._read_pipe(fd)

            except OSError as exc:
                logger.debug("Pipe reader: %s, retrying...", exc)
            finally:
                self._audio_connected = False
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    fd = None

            # --- Grace period: keep Sonos alive during track transitions ---
            if not self._sonos_started:
                time.sleep(1.0)
                continue

            logger.info("Pipe reader: pipe closed, waiting %.0fs for reconnect",
                        PIPE_GRACE_PERIOD)
            deadline = time.time() + PIPE_GRACE_PERIOD
            while time.time() < deadline:
                if not os.path.exists(PIPE_PATH):
                    time.sleep(0.3)
                    continue
                try:
                    fd = os.open(PIPE_PATH, os.O_RDONLY | os.O_NONBLOCK)
                    if fcntl is not None:
                        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
                        fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
                    break
                except OSError:
                    time.sleep(0.3)

            if fd is not None:
                # Reconnected within grace period
                self._audio_connected = True
                self._chunks_since_connect = 0
                logger.info("Pipe reader: reconnected within grace period")
                try:
                    self._read_pipe(fd)
                except OSError:
                    pass
                finally:
                    self._audio_connected = False
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    fd = None
                # Pipe closed again — loop back to check grace period
                continue

            # Grace period expired — stop Sonos
            logger.info("Pipe reader: grace period expired, stopping Sonos")
            if self._sonos:
                self._sonos.stop_forwarding()
            self._sonos_started = False

    def _start_sonos_forwarding(self):
        """Tell the Sonos speaker to play from our audio stream (background)."""
        try:
            self._sonos.start_forwarding(self._streamer.stream_url)
        except Exception:
            logger.exception("Failed to start Sonos forwarding")

    # ------------------------------------------------------------------
    # Audio analysis
    # ------------------------------------------------------------------

    def _analyze_and_drive(self, mono):
        """Perform FFT, compute spectral features, detect beats, and dispatch.

        Extracts band energies, spectral flux, spectral centroid, applies EMA
        smoothing to all features, computes energy trend, and uses flux-based
        beat detection with cooldown.  Passes a rich features dict to mode
        handlers.

        Args:
            mono: 1D numpy array of float64 mono samples (FFT_SIZE length).
        """
        # Normalize to -1..1 range
        mono = mono / 32768.0

        # RMS energy
        rms = float(np.sqrt(np.mean(mono ** 2)))
        self._last_rms = rms

        # FFT (real-valued input, take magnitude of positive frequencies)
        spectrum = np.abs(np.fft.rfft(mono))

        # Extract band energies (mean magnitude in each range)
        bass = float(np.mean(spectrum[BASS_LOW:BASS_HIGH + 1]))
        mid = float(np.mean(spectrum[MID_LOW:MID_HIGH + 1]))
        treble = float(np.mean(spectrum[TREBLE_LOW:min(TREBLE_HIGH + 1, len(spectrum))]))

        # --- Spectral flux (onset detection) --------------------------------
        if self._prev_spectrum is not None:
            diff = spectrum - self._prev_spectrum
            flux = float(np.sum(diff[diff > 0]))  # sum of positive diffs
        else:
            flux = 0.0
        self._prev_spectrum = spectrum.copy()

        # --- Spectral centroid (timbral brightness) -------------------------
        freq_bins = np.fft.rfftfreq(FFT_SIZE, d=1.0 / SAMPLE_RATE)
        # Clamp to useful range
        mask = (freq_bins >= CENTROID_MIN_HZ) & (freq_bins <= CENTROID_MAX_HZ)
        masked_spectrum = spectrum[mask]
        masked_freqs = freq_bins[mask]
        total_energy = float(np.sum(masked_spectrum))
        if total_energy > 0:
            centroid = float(np.sum(masked_freqs * masked_spectrum) / total_energy)
        else:
            centroid = (CENTROID_MIN_HZ + CENTROID_MAX_HZ) / 2.0
        # Normalize centroid to 0-1 range
        centroid_norm = (centroid - CENTROID_MIN_HZ) / (CENTROID_MAX_HZ - CENTROID_MIN_HZ)
        centroid_norm = max(0.0, min(1.0, centroid_norm))

        # --- EMA smoothing --------------------------------------------------
        self._ema_bass = self._ema(self._ema_bass, bass, EMA_ALPHA_FAST)
        self._ema_mid = self._ema(self._ema_mid, mid, EMA_ALPHA_MEDIUM)
        self._ema_treble = self._ema(self._ema_treble, treble, EMA_ALPHA_MEDIUM)
        self._ema_rms = self._ema(self._ema_rms, rms, EMA_ALPHA_FAST)
        self._ema_centroid = self._ema(self._ema_centroid, centroid_norm, EMA_ALPHA_MEDIUM)
        self._ema_flux = self._ema(self._ema_flux, flux, EMA_ALPHA_FAST)

        # --- Energy trend (rising/falling) ----------------------------------
        self._rms_trend_buffer.append(rms)
        if len(self._rms_trend_buffer) > ENERGY_TREND_WINDOW:
            self._rms_trend_buffer = self._rms_trend_buffer[-ENERGY_TREND_WINDOW:]
        if len(self._rms_trend_buffer) >= ENERGY_TREND_WINDOW:
            half = ENERGY_TREND_WINDOW // 2
            recent = sum(self._rms_trend_buffer[half:]) / half
            previous = sum(self._rms_trend_buffer[:half]) / half
            self._energy_trend = recent - previous  # positive = rising
        else:
            self._energy_trend = 0.0

        # --- Flux-based beat detection with cooldown ------------------------
        self._flux_history.append(flux)
        if len(self._flux_history) > FLUX_HISTORY_LEN:
            self._flux_history = self._flux_history[-FLUX_HISTORY_LEN:]

        self._frames_since_beat += 1
        is_beat = False
        is_spike = False
        if len(self._flux_history) >= 4:
            avg_flux = sum(self._flux_history) / len(self._flux_history)
            if avg_flux > 0:
                if flux > avg_flux * FLUX_SPIKE_THRESHOLD:
                    is_spike = True
                if (flux > avg_flux * FLUX_BEAT_THRESHOLD
                        and self._frames_since_beat >= BEAT_COOLDOWN_FRAMES):
                    is_beat = True
                    self._frames_since_beat = 0
                    self._beat_count += 1

        # --- Build features dict --------------------------------------------
        features = {
            "bass": bass,
            "mid": mid,
            "treble": treble,
            "rms": rms,
            "flux": flux,
            "centroid": centroid_norm,
            "ema_bass": self._ema_bass,
            "ema_mid": self._ema_mid,
            "ema_treble": self._ema_treble,
            "ema_rms": self._ema_rms,
            "ema_centroid": self._ema_centroid,
            "ema_flux": self._ema_flux,
            "energy_trend": self._energy_trend,
            "is_beat": is_beat,
            "is_spike": is_spike,
        }

        # Dispatch to mode handler
        with self._lock:
            current_mode = self.mode

        if current_mode == "pulse":
            self._apply_pulse(features)
        elif current_mode == "ambient":
            self._apply_ambient(features)
        elif current_mode == "party":
            self._apply_party(features)

    # ------------------------------------------------------------------
    # Mode handlers
    # ------------------------------------------------------------------

    def _apply_pulse(self, features):
        """Pulse mode: musically-driven color with flux-proportional beats.

        Hue from spectral centroid (dark sounds = cool, bright = warm).
        Beat hue shifts proportional to flux magnitude.
        Between beats: smooth drift toward centroid hue.
        Brightness from EMA-smoothed RMS.
        Saturation adapts to energy trend (rising = vivid, falling = muted).
        """
        intensity_scale = self.intensity / 10.0
        centroid = features["ema_centroid"]
        is_beat = features["is_beat"]
        flux = features["flux"]
        ema_flux = features["ema_flux"]

        # Centroid-driven hue: low centroid (bassy) = cool blues (0.6),
        # high centroid (bright) = warm reds/oranges (0.05)
        centroid_hue = 0.6 - centroid * 0.55  # 0.6 (blue) -> 0.05 (red)

        if is_beat:
            # Shift proportional to flux magnitude
            flux_ratio = flux / max(ema_flux, 0.001)
            shift = min(0.25, 0.05 * flux_ratio) * intensity_scale
            self._hue = (self._hue + shift) % 1.0
            brightness = int(100 * intensity_scale)
        else:
            # Drift toward centroid hue
            diff = centroid_hue - self._hue
            # Wrap around the shortest path on the hue wheel
            if diff > 0.5:
                diff -= 1.0
            elif diff < -0.5:
                diff += 1.0
            self._hue = (self._hue + diff * 0.05) % 1.0
            brightness = max(20, int((30 + 30 * features["ema_rms"] * 4) * intensity_scale))

        # Saturation adapts to energy trend
        trend = features["energy_trend"]
        saturation = 0.7 + 0.3 * max(-1.0, min(1.0, trend * 20))
        saturation = max(0.4, min(1.0, saturation))

        r, g, b = self._hsv_to_rgb(self._hue, saturation, 1.0)
        brightness = max(1, min(100, brightness))

        for i in range(len(self._light_ips)):
            self._set_light(i, r, g, b, brightness)

    def _apply_ambient(self, features):
        """Ambient mode: role-based lights with double-EMA ultra-smooth color.

        Light 0: bass warmth (warm hues scaled by bass energy).
        Light 1: mid harmonics (centroid-driven hue).
        Light 2: treble shimmer (cool hues, brightness from treble).
        Gracefully degrades from 3 -> 2 -> 1 lights.
        Base hue drifts slowly via sine to prevent static color.
        """
        centroid = features["ema_centroid"]
        ema_rms = features["ema_rms"]

        # Slow sine drift for base hue variation
        energy = min(1.0, ema_rms * 4)
        speed = 0.003 + energy * 0.008
        self._phase += speed
        sine_offset = math.sin(self._phase) * 0.08  # gentle +/- 0.08 hue drift

        # Double-EMA: apply ambient-level slow smoothing on top of analysis EMA
        ambient_centroid = self._ema(centroid, centroid, EMA_ALPHA_SLOW)
        ambient_bass = self._ema(features["ema_bass"], features["ema_bass"], EMA_ALPHA_SLOW)

        # Base brightness from energy, gentle range
        brightness = max(1, min(100, int(30 + 50 * energy)))

        # Mid energy for saturation
        mid_energy = min(1.0, features["ema_mid"] * 3)
        saturation = 0.5 + 0.4 * mid_energy

        n_lights = len(self._light_ips)

        if n_lights >= 1:
            # Light 0: bass warmth — warm reds/oranges when bass is strong
            bass_energy = min(1.0, ambient_bass * 3)
            bass_hue = (0.05 + 0.08 * (1.0 - bass_energy) + sine_offset) % 1.0
            bass_sat = 0.6 + 0.35 * bass_energy
            r, g, b = self._hsv_to_rgb(bass_hue, bass_sat, 1.0)
            bass_bright = max(1, min(100, int(25 + 55 * bass_energy)))
            self._set_light(0, r, g, b, bass_bright)

        if n_lights >= 2:
            # Light 1: mid harmonics — centroid-driven hue
            mid_hue = (0.6 - ambient_centroid * 0.55 + sine_offset) % 1.0
            r, g, b = self._hsv_to_rgb(mid_hue, saturation, 1.0)
            self._set_light(1, r, g, b, brightness)

        if n_lights >= 3:
            # Light 2: treble shimmer — cool blues/cyans
            treble_energy = min(1.0, features["ema_treble"] * 3)
            treble_hue = (0.55 + 0.1 * treble_energy + sine_offset) % 1.0
            treble_bright = max(1, min(100, int(20 + 40 * treble_energy)))
            r, g, b = self._hsv_to_rgb(treble_hue, 0.6 + 0.3 * treble_energy, 1.0)
            self._set_light(2, r, g, b, treble_bright)

    def _apply_party(self, features):
        """Party mode: flux-driven strobes, N-light alternation, adaptive cycling.

        White flash on is_spike (flux > 3x avg).
        N-light beat alternation (not hardcoded for 2).
        Hue cycling speed adapts to flux energy.
        Centroid-blended hue (65% cycling + 35% centroid).
        """
        intensity_scale = self.intensity / 10.0
        is_beat = features["is_beat"]
        is_spike = features["is_spike"]
        centroid = features["ema_centroid"]
        ema_flux = features["ema_flux"]
        n_lights = len(self._light_ips)

        if n_lights == 0:
            return

        # White flash on spike
        if is_spike:
            for i in range(n_lights):
                self._set_light(i, 255, 255, 255, int(100 * intensity_scale))
            return

        # Adaptive hue cycling: faster when flux is high
        flux_speed = 0.005 + min(0.03, ema_flux * 0.0005)
        self._hue = (self._hue + flux_speed) % 1.0

        # Blend cycling hue with centroid for musical color
        centroid_hue = 0.6 - centroid * 0.55
        blended_hue = (self._hue * 0.65 + centroid_hue * 0.35) % 1.0

        if is_beat:
            # N-light alternation: active light gets full brightness
            active = self._beat_count % n_lights

            for i in range(n_lights):
                offset = i / n_lights  # distribute hues evenly
                hue = (blended_hue + offset) % 1.0

                if i == active:
                    r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
                    self._set_light(i, r, g, b, int(100 * intensity_scale))
                else:
                    r, g, b = self._hsv_to_rgb(hue, 0.8, 0.7)
                    self._set_light(i, r, g, b, int(25 * intensity_scale))
        else:
            # Between beats: moderate brightness
            energy = min(1.0, features["ema_rms"] * 4)
            brightness = max(20, int(50 * energy * intensity_scale))

            for i in range(n_lights):
                offset = i / n_lights
                hue = (blended_hue + offset) % 1.0
                r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
                self._set_light(i, r, g, b, brightness)

    # ------------------------------------------------------------------
    # Idle / fallback patterns
    # ------------------------------------------------------------------

    def _drive_idle_pattern(self):
        """Drive lights with a timer-based pattern when no audio data arrives."""
        with self._lock:
            current_mode = self.mode

        t = time.time()

        if current_mode == "pulse":
            # Slow breathing effect
            breath = (math.sin(t * 1.5) + 1.0) / 2.0  # 0-1
            brightness = int(30 + 40 * breath)
            hue = (t * 0.02) % 1.0
            r, g, b = self._hsv_to_rgb(hue, 0.7, 1.0)
            for i in range(len(self._light_ips)):
                self._set_light(i, r, g, b, brightness)

        elif current_mode == "ambient":
            # Slow complementary rotation
            hue_a = (t * 0.015) % 1.0
            hue_b = (hue_a + 0.5) % 1.0
            r_a, g_a, b_a = self._hsv_to_rgb(hue_a, 0.6, 1.0)
            r_b, g_b, b_b = self._hsv_to_rgb(hue_b, 0.6, 1.0)
            if len(self._light_ips) >= 1:
                self._set_light(0, r_a, g_a, b_a, 50)
            if len(self._light_ips) >= 2:
                self._set_light(1, r_b, g_b, b_b, 50)

        elif current_mode == "party":
            # Alternating color flash at fixed tempo (~120 BPM = 2 Hz)
            beat_phase = int(t * 2) % 2
            hue = (t * 0.05) % 1.0
            comp_hue = (hue + 0.5) % 1.0
            intensity_scale = self.intensity / 10.0

            if beat_phase == 0:
                r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
                if len(self._light_ips) >= 1:
                    self._set_light(0, r, g, b, int(90 * intensity_scale))
                r2, g2, b2 = self._hsv_to_rgb(comp_hue, 1.0, 1.0)
                if len(self._light_ips) >= 2:
                    self._set_light(1, r2, g2, b2, int(30 * intensity_scale))
            else:
                r, g, b = self._hsv_to_rgb(hue, 1.0, 1.0)
                if len(self._light_ips) >= 1:
                    self._set_light(0, r, g, b, int(30 * intensity_scale))
                r2, g2, b2 = self._hsv_to_rgb(comp_hue, 1.0, 1.0)
                if len(self._light_ips) >= 2:
                    self._set_light(1, r2, g2, b2, int(90 * intensity_scale))

    # ------------------------------------------------------------------
    # Light control (with throttling)
    # ------------------------------------------------------------------

    def _set_light(self, idx, r, g, b, brightness):
        """Send color and brightness to a light, with delta + rate throttling.

        Skips sending if color changed < COLOR_DELTA_THRESHOLD and brightness
        changed < BRIGHTNESS_DELTA_THRESHOLD.  When a send is needed, only the
        property that actually changed is transmitted to reduce UDP traffic.

        Args:
            idx: Index into self._light_ips.
            r, g, b: Color values 0-255.
            brightness: Brightness 1-100.
        """
        if idx >= len(self._light_ips):
            return

        ip = self._light_ips[idx]
        if not ip:
            return

        r, g, b, brightness = int(r), int(g), int(b), max(1, min(100, int(brightness)))

        # Delta check against last-sent state
        prev = self._last_sent_state.get(idx)
        if prev is not None:
            pr, pg, pb, pbr = prev
            color_delta = abs(r - pr) + abs(g - pg) + abs(b - pb)
            bright_delta = abs(brightness - pbr)
            if color_delta < COLOR_DELTA_THRESHOLD and bright_delta < BRIGHTNESS_DELTA_THRESHOLD:
                return  # change too small to bother
        else:
            color_delta = 999  # force send on first call
            bright_delta = 999

        # Rate throttle
        now = time.time()
        if now - self._last_cmd_time[idx] < MIN_CMD_INTERVAL:
            return

        self._last_cmd_time[idx] = now
        self._last_sent_state[idx] = (r, g, b, brightness)

        try:
            if color_delta >= COLOR_DELTA_THRESHOLD:
                self._govee.set_color(ip, r, g, b)
            if bright_delta >= BRIGHTNESS_DELTA_THRESHOLD:
                self._govee.set_brightness(ip, brightness)
        except Exception:
            logger.debug("Light command failed for %s (idx %d)", ip, idx, exc_info=True)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _ema(current, sample, alpha):
        """Exponential moving average: single scalar update."""
        return current + alpha * (sample - current)

    @staticmethod
    def _hsv_to_rgb(h, s, v):
        """Convert HSV to RGB.

        Args:
            h: Hue, 0.0-1.0 (wraps around).
            s: Saturation, 0.0-1.0.
            v: Value, 0.0-1.0.

        Returns:
            Tuple of (r, g, b) with values 0-255.
        """
        h = h % 1.0
        if s == 0.0:
            val = int(v * 255)
            return (val, val, val)

        i = int(h * 6.0)
        f = (h * 6.0) - i
        p = v * (1.0 - s)
        q = v * (1.0 - s * f)
        t = v * (1.0 - s * (1.0 - f))

        i = i % 6
        if i == 0:
            r, g, b = v, t, p
        elif i == 1:
            r, g, b = q, v, p
        elif i == 2:
            r, g, b = p, v, t
        elif i == 3:
            r, g, b = p, q, v
        elif i == 4:
            r, g, b = t, p, v
        else:
            r, g, b = v, p, q

        return (int(r * 255), int(g * 255), int(b * 255))
