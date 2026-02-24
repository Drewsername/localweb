import os
import json
import requests
from urllib.parse import urlencode

SDM_BASE = "https://smartdevicemanagement.googleapis.com/v1"
OAUTH_AUTH_URL = "https://nestservices.google.com/partnerconnections"
OAUTH_TOKEN_URL = "https://www.googleapis.com/oauth2/v4/token"
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", "nest_tokens.json")
SCOPES = "https://www.googleapis.com/auth/sdm.service"


class NestService:
    def __init__(self):
        self.project_id = os.environ.get("NEST_PROJECT_ID", "")
        self.client_id = os.environ.get("NEST_CLIENT_ID", "")
        self.client_secret = os.environ.get("NEST_CLIENT_SECRET", "")
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
            "redirect_uri": redirect_uri,
            "access_type": "offline",
            "prompt": "consent",
            "client_id": self.client_id,
            "response_type": "code",
            "scope": SCOPES,
        })
        return f"{OAUTH_AUTH_URL}/{self.project_id}/auth?{params}"

    def exchange_code(self, code, redirect_uri):
        resp = requests.post(OAUTH_TOKEN_URL, data={
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
            raise RuntimeError("No refresh token — re-authorize via /api/nest/auth/url")
        resp = requests.post(OAUTH_TOKEN_URL, data={
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
        """Make an authenticated SDM API request, refreshing token on 401."""
        url = f"{SDM_BASE}/enterprises/{self.project_id}{path}"
        headers = {"Authorization": f"Bearer {self._tokens.get('access_token', '')}"}
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self._tokens['access_token']}"
            resp = requests.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()
