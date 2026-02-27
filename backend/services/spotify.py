import os
import json
import requests
from urllib.parse import urlencode

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "spotify_tokens.json")
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private"


class SpotifyService:
    def __init__(self):
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID", "")
        self.client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
        self._tokens = self._load_tokens()

    def _load_tokens(self):
        try:
            with open(TOKEN_FILE) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_tokens(self, tokens):
        self._tokens = tokens
        with open(TOKEN_FILE, "w") as f:
            json.dump(tokens, f)

    @property
    def is_authenticated(self):
        return bool(self._tokens.get("access_token"))

    def get_auth_url(self, redirect_uri):
        params = urlencode({
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
        })
        return f"{AUTH_URL}?{params}"

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        })
        resp.raise_for_status()
        self._save_tokens(resp.json())

    def _refresh_token(self):
        refresh = self._tokens.get("refresh_token")
        if not refresh:
            raise RuntimeError("No refresh token -- re-authorize via /api/spotify/auth/url")
        resp = requests.post(TOKEN_URL, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        new_tokens = resp.json()
        # Preserve existing refresh_token if not returned
        new_tokens.setdefault("refresh_token", refresh)
        self._save_tokens(new_tokens)

    def _request(self, method, path, **kwargs):
        """Make an authenticated Spotify API request, refreshing token on 401."""
        url = f"{API_BASE}{path}"
        headers = {"Authorization": f"Bearer {self._tokens.get('access_token', '')}"}
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self._tokens['access_token']}"
            resp = requests.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        # Spotify returns 204 No Content for successful play/pause/next/prev
        if resp.status_code == 204:
            return {"ok": True}
        return resp.json()

    def get_current_track(self):
        """GET /me/player/currently-playing, return normalized dict or None."""
        try:
            data = self._request("GET", "/me/player/currently-playing")
        except requests.exceptions.HTTPError:
            return None
        if not data or data.get("ok"):
            return None
        item = data.get("item")
        if not item:
            return None
        return {
            "title": item["name"],
            "artist": ", ".join(a["name"] for a in item["artists"]),
            "album": item["album"]["name"],
            "art_url": item["album"]["images"][0]["url"] if item["album"].get("images") else None,
            "progress_ms": data["progress_ms"],
            "duration_ms": item["duration_ms"],
            "is_playing": data["is_playing"],
            "track_id": item["id"],
        }

    def get_playback_state(self):
        """GET /me/player, return raw response or None."""
        try:
            data = self._request("GET", "/me/player")
        except requests.exceptions.HTTPError:
            return None
        if not data or data.get("ok"):
            return None
        return data

    def play(self):
        """PUT /me/player/play -- resume playback."""
        return self._request("PUT", "/me/player/play")

    def pause(self):
        """PUT /me/player/pause -- pause playback."""
        return self._request("PUT", "/me/player/pause")

    def next_track(self):
        """POST /me/player/next -- skip to next track."""
        return self._request("POST", "/me/player/next")

    def previous_track(self):
        """POST /me/player/previous -- skip to previous track."""
        return self._request("POST", "/me/player/previous")

    def get_devices(self):
        """GET /me/player/devices, return list of {id, name, type, is_active}."""
        data = self._request("GET", "/me/player/devices")
        devices = []
        for d in data.get("devices", []):
            devices.append({
                "id": d["id"],
                "name": d["name"],
                "type": d["type"],
                "is_active": d["is_active"],
            })
        return devices

    def transfer_playback(self, device_id):
        """PUT /me/player -- transfer playback to a device."""
        return self._request("PUT", "/me/player", json={"device_ids": [device_id]})

    def get_playlists(self, limit=50):
        """GET /me/playlists -- return list of {uri, name, image_url}."""
        data = self._request("GET", f"/me/playlists?limit={limit}")
        playlists = []
        for item in data.get("items", []):
            playlists.append({
                "uri": item["uri"],
                "name": item["name"],
                "image_url": item["images"][0]["url"] if item.get("images") else None,
            })
        return playlists

    def set_shuffle(self, state: bool):
        """PUT /me/player/shuffle -- set shuffle on or off."""
        return self._request("PUT", f"/me/player/shuffle?state={'true' if state else 'false'}")

    def play_context(self, context_uri, device_id=None):
        """PUT /me/player/play -- start playback of a context (playlist/album)."""
        params = f"?device_id={device_id}" if device_id else ""
        return self._request("PUT", f"/me/player/play{params}", json={"context_uri": context_uri})
