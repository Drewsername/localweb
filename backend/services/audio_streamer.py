"""HTTP audio streaming server for forwarding librespot audio to Sonos.

Runs a lightweight HTTP server on a separate port that serves raw PCM
audio as a WAV stream.  The persistent pipe reader in the light show
engine pushes audio chunks into the shared buffer; the Sonos speaker
fetches them over HTTP.

The buffer uses a broadcast model: every HTTP connection gets its own
independent view of the audio stream so that Sonos probe requests
don't steal data from the real playback connection.
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
    """Build a WAV header for an infinite PCM stream.

    Both the RIFF size and data size are kept within signed 32-bit range
    to avoid parsers that treat them as signed integers.
    """
    byte_rate = SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE // 8
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    data_size = 0x7FFFFFDB
    file_size = 0x7FFFFFFF
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
    """Thread-safe broadcast buffer for audio chunks.

    Each consumer subscribes and gets its own queue.  ``put()``
    broadcasts every chunk to all active subscribers so no data is
    lost when multiple HTTP connections read concurrently (e.g. Sonos
    probe + real playback).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: list[deque] = []
        self._conds: list[threading.Condition] = []

    # -- producer API (called by pipe reader) --

    def put(self, chunk: bytes):
        with self._lock:
            for i, q in enumerate(self._subscribers):
                if q is None:
                    continue
                q.append(chunk)
                with self._conds[i]:
                    self._conds[i].notify_all()

    def clear(self):
        with self._lock:
            for q in self._subscribers:
                if q is not None:
                    q.clear()

    # -- consumer API (called by HTTP handlers) --

    def subscribe(self):
        """Return a subscriber id.  Call ``get(sid)`` to read."""
        q = deque(maxlen=1200)  # ~28 s of audio at 44100 Hz
        cond = threading.Condition()
        with self._lock:
            self._subscribers.append(q)
            self._conds.append(cond)
            sid = len(self._subscribers) - 1
        logger.debug("AudioBuffer: subscriber %d registered", sid)
        return sid

    def unsubscribe(self, sid):
        with self._lock:
            if 0 <= sid < len(self._subscribers):
                # Mark slot as dead (don't shift indices mid-stream)
                self._subscribers[sid] = None
                self._conds[sid] = None
        logger.debug("AudioBuffer: subscriber %d removed", sid)

    def get(self, sid, timeout=2.0):
        """Read the next chunk for subscriber *sid*."""
        with self._lock:
            if sid >= len(self._subscribers) or self._subscribers[sid] is None:
                return None
            q = self._subscribers[sid]
            cond = self._conds[sid]

        with cond:
            if timeout == 0.0:
                if q:
                    return q.popleft()
                return None
            while not q:
                if not cond.wait(timeout):
                    return None
            return q.popleft()


class _StreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    audio_buffer = None

    def do_HEAD(self):
        logger.info("Stream HEAD from %s:%d", *self.client_address)
        if self.path not in ("/stream", "/stream.wav"):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/x-wav")
        self.send_header("Accept-Ranges", "none")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_GET(self):
        logger.info("Stream GET from %s:%d path=%s",
                     self.client_address[0], self.client_address[1], self.path)
        if self.path not in ("/stream", "/stream.wav"):
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/x-wav")
        self.send_header("Accept-Ranges", "none")
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.end_headers()

        sid = self.audio_buffer.subscribe()
        total_bytes = 0
        silence_bytes = 0
        writes = 0
        try:
            self.wfile.write(_wav_header())
            self.wfile.flush()
            total_bytes += 44

            while True:
                batch = bytearray()
                chunk = self.audio_buffer.get(sid, timeout=2.0)
                if chunk is None:
                    batch.extend(b"\x00" * 4096)
                    silence_bytes += 4096
                else:
                    batch.extend(chunk)
                    for _ in range(10):
                        extra = self.audio_buffer.get(sid, timeout=0.0)
                        if extra is None:
                            break
                        batch.extend(extra)

                self.wfile.write(bytes(batch))
                self.wfile.flush()
                total_bytes += len(batch)
                writes += 1
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            logger.info("Stream ended for %s:%d after %d bytes "
                        "(%d silence, %d writes): %s",
                        self.client_address[0], self.client_address[1],
                        total_bytes, silence_bytes, writes,
                        type(exc).__name__)
        except Exception:
            logger.exception("Stream unexpected error for %s:%d after %d bytes",
                             self.client_address[0], self.client_address[1],
                             total_bytes)
        finally:
            self.audio_buffer.unsubscribe(sid)

    def log_message(self, fmt, *args):
        pass


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
            logger.info("Audio stream server stopped")

    @property
    def stream_url(self):
        return f"http://10.0.0.74:{STREAM_PORT}/stream.wav"
