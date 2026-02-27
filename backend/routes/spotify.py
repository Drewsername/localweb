from flask import Blueprint, jsonify, request
from services.spotify import SpotifyService

spotify_bp = Blueprint("spotify", __name__)
spotify = SpotifyService()

OAUTH_REDIRECT_URI = "http://10.0.0.74:5000/api/spotify/auth/callback"


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
