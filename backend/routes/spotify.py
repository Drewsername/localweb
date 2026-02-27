from flask import Blueprint, jsonify, request
from services.spotify import SpotifyService
from services.govee_lan import GoveeLanService
from services.audio_streamer import AudioStreamer
from services.sonos import SonosService
from services.lightshow import LightShowEngine
from routes.users import admin_required
from services.presence import trigger_arrival_music

spotify_bp = Blueprint("spotify", __name__)
spotify = SpotifyService()
govee_lan = GoveeLanService()
audio_streamer = AudioStreamer()
sonos = SonosService()
lightshow = LightShowEngine(govee_lan, audio_streamer, sonos)

OAUTH_REDIRECT_URI = "https://10.0.0.74/api/spotify/auth/callback"


@spotify_bp.get("/api/spotify/auth/url")
def auth_url():
    return jsonify({"url": spotify.get_auth_url(OAUTH_REDIRECT_URI)})


@spotify_bp.get("/api/spotify/auth/callback")
def auth_callback():
    code = request.args.get("code")
    if not code:
        return jsonify({"error": "Missing authorization code"}), 400
    try:
        spotify.exchange_code(code, OAUTH_REDIRECT_URI)
        return "<h1>Spotify authorized successfully!</h1><p>You can close this tab.</p>"
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/auth/exchange")
def auth_exchange():
    """Manual code exchange — paste the code from the redirect URL."""
    data = request.get_json()
    code = data.get("code") if data else None
    if not code:
        return jsonify({"error": "code is required"}), 400
    try:
        spotify.exchange_code(code, OAUTH_REDIRECT_URI)
        return jsonify({"authenticated": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.get("/api/spotify/auth/status")
def auth_status():
    return jsonify({"authenticated": spotify.is_authenticated})


@spotify_bp.get("/api/spotify/now-playing")
def now_playing():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    track = spotify.get_current_track()
    if track is None:
        return jsonify({"nothing_playing": True})
    return jsonify(track)


@spotify_bp.post("/api/spotify/play")
def play():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.play()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/pause")
def pause():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.pause()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/next")
def next_track():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.next_track()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/previous")
def previous_track():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        spotify.previous_track()
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.get("/api/spotify/devices")
def list_devices():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        return jsonify(spotify.get_devices())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/transfer")
def transfer_playback():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    data = request.get_json()
    device_id = data.get("device_id") if data else None
    if not device_id:
        return jsonify({"error": "device_id required"}), 400
    try:
        spotify.transfer_playback(device_id)
        return "", 204
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# --- Light Show Endpoints ---


@spotify_bp.get("/api/spotify/lightshow/status")
def lightshow_status():
    return jsonify(lightshow.get_status())


@spotify_bp.post("/api/spotify/lightshow/start")
def lightshow_start():
    data = request.get_json() or {}
    mode = data.get("mode", "pulse")
    device_ids = data.get("device_ids", [])
    latency_ms = data.get("latency_ms", 0)
    intensity = data.get("intensity", 7)

    if not device_ids:
        return jsonify({"error": "device_ids required (list of Govee device IDs)"}), 400
    if mode not in ("pulse", "ambient", "party"):
        return jsonify({"error": "mode must be pulse, ambient, or party"}), 400

    try:
        lightshow.start(mode, device_ids, latency_ms, intensity)
        return jsonify(lightshow.get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/lightshow/stop")
def lightshow_stop():
    lightshow.stop()
    return "", 204


@spotify_bp.get("/api/spotify/sonos/volume")
def sonos_volume_get():
    vol = sonos.get_volume()
    if vol is None:
        return jsonify({"error": "Speaker not available"}), 503
    return jsonify({"volume": vol})


@spotify_bp.post("/api/spotify/sonos/volume")
def sonos_volume_set():
    data = request.get_json() or {}
    level = data.get("volume")
    if level is None:
        return jsonify({"error": "volume required"}), 400
    if sonos.set_volume(level):
        return jsonify({"volume": max(0, min(100, int(level)))})
    return jsonify({"error": "Speaker not available"}), 503


@spotify_bp.post("/api/spotify/lightshow/config")
def lightshow_config():
    data = request.get_json() or {}
    if "mode" in data:
        lightshow.set_mode(data["mode"])
    if "latency_ms" in data:
        lightshow.set_latency(data["latency_ms"])
    if "intensity" in data:
        lightshow.set_intensity(data["intensity"])
    return jsonify(lightshow.get_status())


# --- Arrival Music Endpoints ---


@spotify_bp.get("/api/spotify/playlists")
@admin_required
def list_playlists():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        return jsonify(spotify.get_playlists())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@spotify_bp.post("/api/spotify/arrival/test")
@admin_required
def test_arrival_music():
    if not spotify.is_authenticated:
        return jsonify({"error": "Not authorized"}), 401
    try:
        result = trigger_arrival_music(spotify)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
