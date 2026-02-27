"""HTTP audio streaming server for forwarding librespot audio to Sonos.

Runs a lightweight HTTP server on a separate port that serves raw PCM
audio as a WAV stream.  The persistent pipe reader in the light show
engine pushes audio chunks into the shared buffer; the Sonos speaker
fetches them over HTTP.
"""

import logging
import struct
import threading
from collections import deque
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

logger = logging.getLogger(__name__)

SAMPLE_RATE = 44100
CHANNELS = 2
BITS_PER_SAMPLE = 16
STREAM_PORT = 8080


def _wav_header():
    """Build a WAV header for an infinite PCM stream."""
    byte_rate = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    data_size = 0x7FFFFFFF  # large placeholder
    file_size = data_size + 36
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        file_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        CHANNELS,
        SAMPLE_RATE,
        byte_rate,
        block_align,
        BITS_PER_SAMPLE,
        b"data",
        data_size,
    )


class AudioBuffer:
    """Thread-safe circular buffer for audio chunks."""

    def __init__(self, max_chunks=600):
        self._buf = deque(maxlen=max_chunks)
        self._cond = threading.Condition()
        self._closed = False

    def put(self, chunk: bytes):
        with self._cond:
            self._buf.append(chunk)
            self._cond.notify_all()

    def get(self, timeout=2.0):
        with self._cond:
            if timeout == 0.0:
                # Non-blocking: return immediately if nothing available
                if self._buf:
                    return self._buf.popleft()
                return None
            while not self._buf and not self._closed:
                if not self._cond.wait(timeout):
                    return None
            if self._buf:
                return self._buf.popleft()
            return None

    def clear(self):
        with self._cond:
            self._buf.clear()

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def reopen(self):
        with self._cond:
            self._closed = False


class _StreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    audio_buffer = None

    def do_HEAD(self):
        """Sonos probes with HEAD before fetching the stream."""
        if self.path not in ("/stream", "/stream.wav"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-wav")
        self.send_header("Accept-Ranges", "none")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        if self.path not in ("/stream", "/stream.wav"):
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/x-wav")
        self.send_header("Accept-Ranges", "none")
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()

        try:
            self.wfile.write(_wav_header())
            self.wfile.flush()

            while True:
                # Collect available chunks into a batch to reduce
                # syscall and TCP overhead (better for Sonos buffering).
                batch = bytearray()
                chunk = self.audio_buffer.get(timeout=0.5)
                if chunk is None:
                    # No data yet — send a small silence block to keep
                    # the TCP connection alive.
                    batch.extend(b"\x00" * 4096)
                else:
                    batch.extend(chunk)
                    # Drain up to 10 more queued chunks for this write
                    for _ in range(10):
                        extra = self.audio_buffer.get(timeout=0.0)
                        if extra is None:
                            break
                        batch.extend(extra)

                self.wfile.write(bytes(batch))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def log_message(self, fmt, *args):
        logger.debug("AudioStream: %s", fmt % args)


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class AudioStreamer:
    """Manages the HTTP audio stream server."""

    def __init__(self):
        self.buffer = AudioBuffer()
        self._server = None
        self._thread = None

    def start(self):
        if self._server is not None:
            return

        _StreamHandler.audio_buffer = self.buffer
        self._server = _ThreadingHTTPServer(("0.0.0.0", STREAM_PORT), _StreamHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()
        logger.info("Audio stream server started on port %d", STREAM_PORT)

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None
            self._thread = None
            self.buffer.close()
            logger.info("Audio stream server stopped")

    @property
    def stream_url(self):
        return f"http://10.0.0.74:{STREAM_PORT}/stream.wav"
