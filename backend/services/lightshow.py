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

# -- Beat detection ---------------------------------------------------------
BEAT_THRESHOLD = 1.4  # bass must exceed rolling avg by this factor
BEAT_HISTORY_LEN = 40  # rolling window (~1 second at 30 Hz)

# -- Throttling -------------------------------------------------------------
MIN_CMD_INTERVAL = 0.05  # 50ms = 20 cmd/sec per light
LOOP_PERIOD = 1.0 / 30   # ~33ms for 30 Hz analysis loop

# -- Light show defaults ----------------------------------------------------
WARM_WHITE = (255, 180, 100)
WARM_WHITE_BRIGHTNESS = 50
VALID_MODES = ("pulse", "ambient", "party")


class LightShowEngine:
    """Drives Govee lights reactively based on audio analysis.

    Usage:
        engine = LightShowEngine(govee_lan_service)
        engine.start(mode="pulse", device_ids=["AA:BB:...", "CC:DD:..."])
        ...
        engine.stop()
    """

    def __init__(self, govee_lan):
        self._govee = govee_lan
        self._running = False
        self._thread = None
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
        self._bass_history = []
        self._hue = 0.0  # current hue position, 0-1
        self._phase = 0.0  # phase accumulator for ambient mode
        self._beat_count = 0  # total beats detected (for party alternation)
        self._last_rms = 0.0

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

        self.mode = mode
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
        self._bass_history = []
        self._hue = 0.0
        self._phase = 0.0
        self._beat_count = 0
        self._last_rms = 0.0

        # Start background thread
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Light show started: mode=%s, lights=%d", mode, len(self._light_ips))

    def stop(self):
        """Stop the light show and reset lights to warm white."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

        # Reset lights to warm white
        for ip in self._light_ips:
            if ip:
                try:
                    r, g, b = WARM_WHITE
                    self._govee.set_color(ip, r, g, b)
                    self._govee.set_brightness(ip, WARM_WHITE_BRIGHTNESS)
                except Exception:
                    logger.exception("Failed to reset light at %s", ip)

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
        }

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self):
        """Main background thread: read audio pipe, analyze, drive lights."""
        while self._running:
            if not os.path.exists(PIPE_PATH):
                logger.debug("Audio pipe not found, falling back to pattern mode")
                self._run_pattern_only()
                return

            fd = None
            try:
                fd = os.open(PIPE_PATH, os.O_RDONLY | os.O_NONBLOCK)
                logger.info("Opened audio pipe: %s", PIPE_PATH)

                while self._running:
                    loop_start = time.time()

                    try:
                        raw = os.read(fd, CHUNK_BYTES)
                    except OSError:
                        # EAGAIN / EWOULDBLOCK — no data available yet
                        raw = b""

                    if len(raw) == CHUNK_BYTES:
                        samples = np.frombuffer(raw, dtype=np.int16)
                        # Reshape to (N, 2) stereo and mix to mono
                        stereo = samples.reshape(-1, 2)
                        mono = stereo.mean(axis=1).astype(np.float64)

                        # Apply latency compensation
                        if self.latency_ms > 0:
                            time.sleep(self.latency_ms / 1000.0)

                        self._analyze_and_drive(mono)
                    else:
                        # Incomplete read or no data — drive idle pattern
                        self._drive_idle_pattern()

                    # Maintain target loop rate
                    elapsed = time.time() - loop_start
                    sleep_time = LOOP_PERIOD - elapsed
                    if sleep_time > 0:
                        time.sleep(sleep_time)

            except OSError as exc:
                logger.warning("Error opening audio pipe: %s", exc)
                # Brief pause before retry or fallback
                if self._running:
                    time.sleep(1.0)
                    continue
            finally:
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

        logger.debug("Light show thread exiting")

    def _run_pattern_only(self):
        """Timer-based patterns when no audio pipe is available."""
        logger.info("Running in pattern-only mode (no audio pipe)")
        while self._running:
            loop_start = time.time()
            self._drive_idle_pattern()
            elapsed = time.time() - loop_start
            sleep_time = LOOP_PERIOD - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    # ------------------------------------------------------------------
    # Audio analysis
    # ------------------------------------------------------------------

    def _analyze_and_drive(self, mono):
        """Perform FFT, detect beats, and dispatch to the active mode handler.

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

        bands = {"bass": bass, "mid": mid, "treble": treble, "rms": rms}

        # Beat detection on bass energy
        self._bass_history.append(bass)
        if len(self._bass_history) > BEAT_HISTORY_LEN:
            self._bass_history = self._bass_history[-BEAT_HISTORY_LEN:]

        is_beat = False
        if len(self._bass_history) >= 4:
            avg_bass = sum(self._bass_history) / len(self._bass_history)
            if avg_bass > 0 and bass > avg_bass * BEAT_THRESHOLD:
                is_beat = True
                self._beat_count += 1

        # Dispatch to mode handler
        with self._lock:
            current_mode = self.mode

        if current_mode == "pulse":
            self._apply_pulse(bands, is_beat)
        elif current_mode == "ambient":
            self._apply_ambient(bands)
        elif current_mode == "party":
            self._apply_party(bands, is_beat)

    # ------------------------------------------------------------------
    # Mode handlers
    # ------------------------------------------------------------------

    def _apply_pulse(self, bands, is_beat):
        """Pulse mode: beat-synced flashes with energy-driven color warmth.

        On beat: flash brightness to 100% (scaled by intensity), shift hue.
        Between beats: brightness decays to ~40%, slow hue drift.
        Both lights in sync.
        """
        intensity_scale = self.intensity / 10.0
        rms = bands["rms"]

        if is_beat:
            # Shift hue by 60-80 degrees on beat
            shift = (60 + 20 * intensity_scale) / 360.0
            self._hue = (self._hue + shift) % 1.0
            brightness = int(100 * intensity_scale)
        else:
            # Slow drift between beats
            self._hue = (self._hue + 0.002) % 1.0
            brightness = max(20, int(40 * intensity_scale))

        # Map energy to color warmth: high energy = warm (hue 0-0.1),
        # low energy = cool (hue 0.55-0.8)
        energy = min(1.0, rms * 4)  # normalize rms to roughly 0-1
        if energy > 0.5:
            # Warm: reds/oranges, hue 0.0-0.1
            warmth_hue = 0.1 * (1.0 - (energy - 0.5) * 2)
        else:
            # Cool: blues/purples, hue 0.55-0.8
            warmth_hue = 0.55 + 0.25 * (1.0 - energy * 2)

        # Blend the beat-driven hue with the warmth-driven hue
        blended_hue = (self._hue * 0.6 + warmth_hue * 0.4) % 1.0

        r, g, b = self._hsv_to_rgb(blended_hue, 0.9, 1.0)
        brightness = max(1, min(100, brightness))

        for i in range(len(self._light_ips)):
            self._set_light(i, r, g, b, brightness)

    def _apply_ambient(self, bands):
        """Ambient mode: smooth sine-wave color rotation, energy-driven.

        Each light offset 180 degrees in hue (complementary colors).
        Brightness maps to energy (30-80% range). Very gentle.
        """
        rms = bands["rms"]
        energy = min(1.0, rms * 4)

        # Speed proportional to energy: base 0.003 + up to 0.012
        speed = 0.003 + energy * 0.012
        self._phase += speed

        # Sine-wave hue rotation
        hue_a = (math.sin(self._phase) * 0.5 + 0.5) % 1.0

        # Brightness: 30-80% range mapped to energy
        brightness = int(30 + 50 * energy)
        brightness = max(1, min(100, brightness))

        # Saturation varies gently with mid energy
        mid_energy = min(1.0, bands["mid"] * 3)
        saturation = 0.5 + 0.4 * mid_energy

        r_a, g_a, b_a = self._hsv_to_rgb(hue_a, saturation, 1.0)

        # Light B: complementary (180 degree offset)
        hue_b = (hue_a + 0.5) % 1.0
        r_b, g_b, b_b = self._hsv_to_rgb(hue_b, saturation, 1.0)

        if len(self._light_ips) >= 1:
            self._set_light(0, r_a, g_a, b_a, brightness)
        if len(self._light_ips) >= 2:
            self._set_light(1, r_b, g_b, b_b, brightness)

    def _apply_party(self, bands, is_beat):
        """Party mode: alternating beats, complementary rainbow, strobe spikes.

        Lights alternate flashing on every beat. Colors cycle through the
        rainbow as complementary pairs. Energy spikes trigger white strobe.
        """
        rms = bands["rms"]
        intensity_scale = self.intensity / 10.0

        # Check for energy spike -> white strobe
        if rms > 0.8:
            for i in range(len(self._light_ips)):
                self._set_light(i, 255, 255, 255, int(100 * intensity_scale))
            return

        # Rainbow hue cycling
        self._hue = (self._hue + 0.01) % 1.0

        if is_beat:
            # Which light flashes this beat?
            active = self._beat_count % 2
            inactive = 1 - active

            # Active light: bright, current hue
            r, g, b = self._hsv_to_rgb(self._hue, 1.0, 1.0)
            if active < len(self._light_ips):
                self._set_light(active, r, g, b, int(100 * intensity_scale))

            # Inactive light: dim, complementary hue
            comp_hue = (self._hue + 0.5) % 1.0
            r2, g2, b2 = self._hsv_to_rgb(comp_hue, 1.0, 1.0)
            if inactive < len(self._light_ips):
                self._set_light(inactive, r2, g2, b2, int(30 * intensity_scale))
        else:
            # Between beats: both at moderate brightness with current colors
            energy = min(1.0, rms * 4)
            brightness = max(20, int(50 * energy * intensity_scale))

            r, g, b = self._hsv_to_rgb(self._hue, 1.0, 1.0)
            comp_hue = (self._hue + 0.5) % 1.0
            r2, g2, b2 = self._hsv_to_rgb(comp_hue, 1.0, 1.0)

            if len(self._light_ips) >= 1:
                self._set_light(0, r, g, b, brightness)
            if len(self._light_ips) >= 2:
                self._set_light(1, r2, g2, b2, brightness)

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
        """Send color and brightness to a light, respecting throttle limits.

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

        now = time.time()
        if now - self._last_cmd_time[idx] < MIN_CMD_INTERVAL:
            return  # throttled

        self._last_cmd_time[idx] = now

        try:
            self._govee.set_color(ip, int(r), int(g), int(b))
            self._govee.set_brightness(ip, max(1, min(100, int(brightness))))
        except Exception:
            logger.debug("Light command failed for %s (idx %d)", ip, idx, exc_info=True)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

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
