import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import { useUser } from "../context/UserContext";

// --- Types ---

interface NowPlaying {
  title: string;
  artist: string;
  album: string;
  art_url: string;
  progress_ms: number;
  duration_ms: number;
  is_playing: boolean;
  track_id: string;
}

interface SpotifyDevice {
  id: string;
  name: string;
  type: string;
  is_active: boolean;
}

interface GoveeDevice {
  device: string;
  sku: string;
  deviceName: string;
  capabilities: Array<{ type: string; instance: string; parameters: Record<string, unknown> }>;
}

interface LightShowStatus {
  active: boolean;
  mode: string;
  latency_ms: number;
  intensity: number;
  lights_connected: number;
  pipe_exists: boolean;
}

// --- Helpers ---

function formatTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

interface SpotifyPlaylist {
  uri: string;
  name: string;
  image_url: string | null;
}

// --- Arrival Music Settings (admin only) ---

function ArrivalMusicSettings({ onReconnect }: { onReconnect: () => void }) {
  const [enabled, setEnabled] = useState(false);
  const [playlistUri, setPlaylistUri] = useState("");
  const [playlistName, setPlaylistName] = useState("");
  const [shuffle, setShuffle] = useState(true);
  const [playlists, setPlaylists] = useState<SpotifyPlaylist[]>([]);
  const [playlistsError, setPlaylistsError] = useState(false);
  const [testing, setTesting] = useState(false);
  const [loaded, setLoaded] = useState(false);

  // Load settings + playlists on mount
  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then((data) => {
        const arrival = data["spotify.arrival"] || {};
        setEnabled(!!arrival.enabled);
        setPlaylistUri(arrival.playlist_uri || "");
        setPlaylistName(arrival.playlist_name || "");
        setShuffle(arrival.shuffle !== undefined ? !!arrival.shuffle : true);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));

    fetch("/api/spotify/playlists")
      .then((r) => {
        if (!r.ok) throw new Error("failed");
        return r.json();
      })
      .then((data) => setPlaylists(data))
      .catch(() => setPlaylistsError(true));
  }, []);

  function saveSetting(updates: Record<string, unknown>) {
    fetch("/api/settings/spotify.arrival", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }).catch(() => {});
  }

  function handleToggleEnabled() {
    const next = !enabled;
    setEnabled(next);
    saveSetting({ enabled: next });
  }

  function handlePlaylistChange(uri: string) {
    const pl = playlists.find((p) => p.uri === uri);
    setPlaylistUri(uri);
    setPlaylistName(pl?.name || "");
    saveSetting({ playlist_uri: uri, playlist_name: pl?.name || "" });
  }

  function handleToggleShuffle() {
    const next = !shuffle;
    setShuffle(next);
    saveSetting({ shuffle: next });
  }

  async function handleTest() {
    setTesting(true);
    try {
      await fetch("/api/spotify/arrival/test", { method: "POST" });
    } catch {
      /* ignore */
    } finally {
      setTesting(false);
    }
  }

  if (!loaded) return null;

  return (
    <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-5">
      <h2 className="text-lg font-semibold">Arrival Music</h2>

      {playlistsError ? (
        <div className="space-y-3">
          <p className="text-sm text-gray-400">
            Could not load playlists. Spotify may need to be re-authorized with updated permissions.
          </p>
          <button
            onClick={onReconnect}
            className="px-4 py-2 bg-green-600 hover:bg-green-500 text-white text-sm font-semibold rounded-lg transition-colors"
          >
            Re-authorize Spotify
          </button>
        </div>
      ) : (
        <>
          {/* Enable toggle */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-300">Enable</span>
            <button
              onClick={handleToggleEnabled}
              className={`relative w-14 h-8 rounded-full transition-colors ${
                enabled ? "bg-green-600" : "bg-gray-700"
              }`}
            >
              <span
                className={`absolute top-1 left-1 w-6 h-6 bg-white rounded-full transition-transform ${
                  enabled ? "translate-x-6" : ""
                }`}
              />
            </button>
          </div>

          {/* Playlist dropdown */}
          <div className="space-y-2">
            <label className="text-sm text-gray-400">Playlist</label>
            <select
              value={playlistUri}
              onChange={(e) => handlePlaylistChange(e.target.value)}
              className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white text-sm focus:border-green-600 focus:outline-none"
            >
              <option value="">Select a playlist...</option>
              {playlists.map((pl) => (
                <option key={pl.uri} value={pl.uri}>
                  {pl.name}
                </option>
              ))}
            </select>
            {playlistName && !playlists.find((p) => p.uri === playlistUri) && (
              <p className="text-xs text-gray-500">Currently: {playlistName}</p>
            )}
          </div>

          {/* Shuffle toggle */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-300">Shuffle</span>
            <button
              onClick={handleToggleShuffle}
              className={`relative w-14 h-8 rounded-full transition-colors ${
                shuffle ? "bg-green-600" : "bg-gray-700"
              }`}
            >
              <span
                className={`absolute top-1 left-1 w-6 h-6 bg-white rounded-full transition-transform ${
                  shuffle ? "translate-x-6" : ""
                }`}
              />
            </button>
          </div>

          {/* Test button */}
          <button
            onClick={handleTest}
            disabled={testing || !playlistUri}
            className="w-full py-3 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-lg transition-colors disabled:opacity-50"
          >
            {testing ? "Starting..." : "Test Arrival Music"}
          </button>
        </>
      )}
    </div>
  );
}

// --- Component ---

export default function Spotify() {
  const { user } = useUser();
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [nowPlaying, setNowPlaying] = useState<NowPlaying | null>(null);
  const [nothingPlaying, setNothingPlaying] = useState(false);
  const [showDevices, setShowDevices] = useState(false);
  const [spotifyDevices, setSpotifyDevices] = useState<SpotifyDevice[]>([]);
  const [goveeDevices, setGoveeDevices] = useState<GoveeDevice[]>([]);
  const [selectedLights, setSelectedLights] = useState<Set<string>>(new Set());
  const [lightShowMode, setLightShowMode] = useState<string>("pulse");
  const [intensity, setIntensity] = useState(5);
  const [latency, setLatency] = useState(0);
  const [lightShowStatus, setLightShowStatus] = useState<LightShowStatus | null>(null);
  const [actionPending, setActionPending] = useState(false);
  const [sonosVolume, setSonosVolume] = useState<number | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const skipPollUntil = useRef(0);
  const intensityTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const latencyTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const volumeTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // --- Auth check ---

  useEffect(() => {
    fetch("/api/spotify/auth/status")
      .then((r) => r.json())
      .then((d) => setAuthenticated(d.authenticated))
      .catch(() => setAuthenticated(false));
  }, []);

  // --- Now playing polling ---

  const fetchNowPlaying = useCallback(async () => {
    try {
      const res = await fetch("/api/spotify/now-playing");
      if (!res.ok) return;
      const data = await res.json();
      if (data.nothing_playing) {
        setNowPlaying(null);
        setNothingPlaying(true);
      } else {
        setNowPlaying(data);
        setNothingPlaying(false);
      }
    } catch {
      /* ignore polling errors */
    }
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    fetchNowPlaying();
    pollRef.current = setInterval(() => {
      if (Date.now() < skipPollUntil.current) return;
      fetchNowPlaying();
    }, 3000);
    return () => clearInterval(pollRef.current);
  }, [authenticated, fetchNowPlaying]);

  // --- Fetch light show status ---

  const fetchLightShowStatus = useCallback(async () => {
    try {
      const res = await fetch("/api/spotify/lightshow/status");
      if (res.ok) {
        const data = await res.json();
        setLightShowStatus(data);
        if (data.active) {
          setLightShowMode(data.mode);
          setIntensity(data.intensity);
          setLatency(data.latency_ms);
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  // --- Fetch Govee devices ---

  useEffect(() => {
    if (!authenticated) return;
    fetchLightShowStatus();
    fetch("/api/govee/devices")
      .then((r) => r.json())
      .then((devices: GoveeDevice[]) => {
        setGoveeDevices(devices);
        // Auto-select devices whose name includes "floor lamp"
        const autoSelected = new Set<string>();
        devices.forEach((d) => {
          if (d.deviceName.toLowerCase().includes("floor lamp")) {
            autoSelected.add(d.device);
          }
        });
        setSelectedLights(autoSelected);
      })
      .catch(() => {});
  }, [authenticated, fetchLightShowStatus]);

  // --- Sonos volume ---

  useEffect(() => {
    if (!authenticated) return;
    fetch("/api/spotify/sonos/volume")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d) setSonosVolume(d.volume); })
      .catch(() => {});
  }, [authenticated]);

  function handleVolumeChange(value: number) {
    setSonosVolume(value);
    clearTimeout(volumeTimer.current);
    volumeTimer.current = setTimeout(async () => {
      try {
        await fetch("/api/spotify/sonos/volume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ volume: value }),
        });
      } catch {
        /* ignore */
      }
    }, 150);
  }

  // --- Playback controls ---

  async function handlePlayPause() {
    if (!nowPlaying) return;
    const endpoint = nowPlaying.is_playing ? "/api/spotify/pause" : "/api/spotify/play";
    // Optimistic update — suppress polls so stale API data doesn't revert it
    setNowPlaying((prev) => (prev ? { ...prev, is_playing: !prev.is_playing } : prev));
    skipPollUntil.current = Date.now() + 3000;
    try {
      const res = await fetch(endpoint, { method: "POST" });
      if (!res.ok) {
        // Revert — the command actually failed
        setNowPlaying((prev) => (prev ? { ...prev, is_playing: !prev.is_playing } : prev));
        skipPollUntil.current = 0;
      }
    } catch {
      setNowPlaying((prev) => (prev ? { ...prev, is_playing: !prev.is_playing } : prev));
      skipPollUntil.current = 0;
    }
  }

  async function handlePrevious() {
    setActionPending(true);
    skipPollUntil.current = Date.now() + 2000;
    try {
      await fetch("/api/spotify/previous", { method: "POST" });
      setTimeout(fetchNowPlaying, 1000);
    } catch {
      /* ignore */
    } finally {
      setActionPending(false);
    }
  }

  async function handleNext() {
    setActionPending(true);
    skipPollUntil.current = Date.now() + 2000;
    try {
      await fetch("/api/spotify/next", { method: "POST" });
      setTimeout(fetchNowPlaying, 1000);
    } catch {
      /* ignore */
    } finally {
      setActionPending(false);
    }
  }

  // --- Device transfer ---

  async function fetchSpotifyDevices() {
    try {
      const res = await fetch("/api/spotify/devices");
      if (res.ok) {
        const data = await res.json();
        setSpotifyDevices(data);
      }
    } catch {
      /* ignore */
    }
  }

  async function transferPlayback(deviceId: string) {
    try {
      await fetch("/api/spotify/transfer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device_id: deviceId }),
      });
      setSpotifyDevices((prev) =>
        prev.map((d) => ({ ...d, is_active: d.id === deviceId }))
      );
    } catch {
      /* ignore */
    }
  }

  // --- Light show controls ---

  function toggleLight(deviceId: string) {
    setSelectedLights((prev) => {
      const next = new Set(prev);
      if (next.has(deviceId)) {
        next.delete(deviceId);
      } else {
        next.add(deviceId);
      }
      return next;
    });
  }

  async function handleModeChange(mode: string) {
    setLightShowMode(mode);
    if (lightShowStatus?.active) {
      try {
        const res = await fetch("/api/spotify/lightshow/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode }),
        });
        if (res.ok) setLightShowStatus(await res.json());
      } catch {
        /* ignore */
      }
    }
  }

  function handleIntensityChange(value: number) {
    setIntensity(value);
    if (lightShowStatus?.active) {
      clearTimeout(intensityTimer.current);
      intensityTimer.current = setTimeout(async () => {
        try {
          const res = await fetch("/api/spotify/lightshow/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ intensity: value }),
          });
          if (res.ok) setLightShowStatus(await res.json());
        } catch {
          /* ignore */
        }
      }, 300);
    }
  }

  function handleLatencyChange(value: number) {
    setLatency(value);
    if (lightShowStatus?.active) {
      clearTimeout(latencyTimer.current);
      latencyTimer.current = setTimeout(async () => {
        try {
          const res = await fetch("/api/spotify/lightshow/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ latency_ms: value }),
          });
          if (res.ok) setLightShowStatus(await res.json());
        } catch {
          /* ignore */
        }
      }, 300);
    }
  }

  async function handleStartLightShow() {
    setActionPending(true);
    try {
      const res = await fetch("/api/spotify/lightshow/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: lightShowMode,
          device_ids: Array.from(selectedLights),
          latency_ms: latency,
          intensity,
        }),
      });
      if (res.ok) setLightShowStatus(await res.json());
    } catch {
      /* ignore */
    } finally {
      setActionPending(false);
    }
  }

  async function handleStopLightShow() {
    setActionPending(true);
    try {
      await fetch("/api/spotify/lightshow/stop", { method: "POST" });
      setLightShowStatus((prev) => (prev ? { ...prev, active: false, lights_connected: 0 } : prev));
    } catch {
      /* ignore */
    } finally {
      setActionPending(false);
    }
  }

  const [authStep, setAuthStep] = useState<"idle" | "waiting_for_code">("idle");
  const [authCode, setAuthCode] = useState("");
  const [authError, setAuthError] = useState("");

  async function handleConnectSpotify() {
    try {
      const res = await fetch("/api/spotify/auth/url");
      if (res.ok) {
        const data = await res.json();
        window.open(data.url, "_blank");
        setAuthStep("waiting_for_code");
        setAuthError("");
      }
    } catch {
      /* ignore */
    }
  }

  async function handleSubmitCode() {
    // Extract code from full URL or bare code
    let code = authCode.trim();
    const match = code.match(/[?&]code=([^&]+)/);
    if (match) code = match[1];

    if (!code) return;
    setAuthError("");
    try {
      const res = await fetch("/api/spotify/auth/exchange", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await res.json();
      if (data.authenticated) {
        setAuthenticated(true);
      } else {
        setAuthError(data.error || "Authorization failed");
      }
    } catch {
      setAuthError("Failed to connect");
    }
  }

  // --- Loading state ---

  if (authenticated === null) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  // --- Auth gate ---

  if (!authenticated) {
    return (
      <div className="min-h-screen bg-gray-950 text-white p-6">
        <div className="max-w-lg mx-auto space-y-6">
          <div className="flex items-center gap-4">
            <Link to="/home" className="text-gray-400 hover:text-white text-2xl transition-colors">&larr;</Link>
            <h1 className="text-2xl font-bold">Spotify</h1>
          </div>
          <div className="flex flex-col items-center gap-6 py-8">
            {authStep === "idle" ? (
              <>
                <p className="text-gray-400 text-center">
                  Connect your Spotify account to control playback and run light shows.
                </p>
                <button
                  onClick={handleConnectSpotify}
                  className="px-6 py-3 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-full transition-colors"
                >
                  Connect Spotify
                </button>
              </>
            ) : (
              <>
                <p className="text-gray-400 text-center text-sm">
                  Authorize in the Spotify tab, then paste the URL it redirects you to (it won't load — that's fine).
                </p>
                <input
                  type="text"
                  value={authCode}
                  onChange={(e) => setAuthCode(e.target.value)}
                  placeholder="Paste redirect URL or code here"
                  className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 text-sm focus:border-green-600 focus:outline-none"
                />
                {authError && (
                  <p className="text-red-400 text-sm">{authError}</p>
                )}
                <div className="flex gap-3 w-full">
                  <button
                    onClick={() => { setAuthStep("idle"); setAuthCode(""); setAuthError(""); }}
                    className="flex-1 py-3 bg-gray-800 hover:bg-gray-700 text-gray-300 font-semibold rounded-lg transition-colors"
                  >
                    Back
                  </button>
                  <button
                    onClick={handleSubmitCode}
                    disabled={!authCode.trim()}
                    className="flex-1 py-3 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-semibold rounded-lg transition-colors"
                  >
                    Connect
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  // --- Main page ---

  const progressPercent = nowPlaying
    ? Math.min((nowPlaying.progress_ms / nowPlaying.duration_ms) * 100, 100)
    : 0;

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl transition-colors">&larr;</Link>
          <h1 className="text-2xl font-bold">Spotify</h1>
        </div>

        {/* Now Playing */}
        <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4">
          {nowPlaying ? (
            <>
              <div className="flex items-center gap-4">
                <img
                  src={nowPlaying.art_url}
                  alt={nowPlaying.album}
                  className="w-20 h-20 rounded-lg object-cover flex-shrink-0"
                />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-white truncate">{nowPlaying.title}</p>
                  <p className="text-sm text-gray-400 truncate">{nowPlaying.artist}</p>
                  <p className="text-xs text-gray-500 truncate">{nowPlaying.album}</p>
                </div>
              </div>

              {/* Progress bar */}
              <div className="space-y-1">
                <div className="w-full h-1.5 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-green-500 rounded-full transition-all duration-1000"
                    style={{ width: `${progressPercent}%` }}
                  />
                </div>
                <div className="flex justify-between text-xs text-gray-500">
                  <span>{formatTime(nowPlaying.progress_ms)}</span>
                  <span>{formatTime(nowPlaying.duration_ms)}</span>
                </div>
              </div>

              {/* Playback controls */}
              <div className="flex items-center justify-center gap-6">
                <button
                  onClick={handlePrevious}
                  disabled={actionPending}
                  className="text-2xl text-gray-300 hover:text-white transition-colors disabled:opacity-50"
                  aria-label="Previous track"
                >
                  &#x23EE;
                </button>
                <button
                  onClick={handlePlayPause}
                  className="w-12 h-12 rounded-full bg-white text-gray-950 flex items-center justify-center text-xl hover:scale-105 transition-transform"
                  aria-label={nowPlaying.is_playing ? "Pause" : "Play"}
                >
                  {nowPlaying.is_playing ? "\u23F8" : "\u25B6"}
                </button>
                <button
                  onClick={handleNext}
                  disabled={actionPending}
                  className="text-2xl text-gray-300 hover:text-white transition-colors disabled:opacity-50"
                  aria-label="Next track"
                >
                  &#x23ED;
                </button>
              </div>

              {/* Volume slider */}
              {sonosVolume !== null && (
                <div className="flex items-center gap-3">
                  <span className="text-gray-400 text-xs">Vol</span>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={sonosVolume}
                    onChange={(e) => handleVolumeChange(Number(e.target.value))}
                    className="flex-1 accent-green-600"
                  />
                  <span className="text-xs text-gray-400 w-7 text-right">{sonosVolume}</span>
                </div>
              )}

              {/* Device selector */}
              <div className="pt-2 border-t border-gray-800">
                <button
                  onClick={() => {
                    setShowDevices(!showDevices);
                    if (!showDevices) fetchSpotifyDevices();
                  }}
                  className="text-sm text-gray-400 hover:text-green-400 transition-colors"
                >
                  {showDevices ? "Hide speakers" : "Change speaker..."}
                </button>
                {showDevices && (
                  <div className="mt-3 space-y-2">
                    {spotifyDevices.length === 0 && (
                      <p className="text-xs text-gray-500">No devices found</p>
                    )}
                    {spotifyDevices.map((device) => (
                      <button
                        key={device.id}
                        onClick={() => transferPlayback(device.id)}
                        className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                          device.is_active
                            ? "bg-green-600/20 text-green-400 border border-green-600/40"
                            : "bg-gray-800 text-gray-300 hover:bg-gray-700 border border-gray-700"
                        }`}
                      >
                        <span className="font-medium">{device.name}</span>
                        <span className="text-xs text-gray-500 ml-2">{device.type}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </>
          ) : (
            <div className="text-center py-6">
              <p className="text-gray-400">
                {nothingPlaying ? "Nothing playing" : "Loading..."}
              </p>
            </div>
          )}
        </div>

        {/* Light Show Controls */}
        <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-5">
          <h2 className="text-lg font-semibold">Light Show</h2>

          {/* Light selection */}
          <div className="space-y-2">
            <label className="text-sm text-gray-400">Lights</label>
            <div className="flex flex-wrap gap-2">
              {goveeDevices.length === 0 && (
                <p className="text-xs text-gray-500">No Govee devices found</p>
              )}
              {goveeDevices.map((device) => (
                <button
                  key={device.device}
                  onClick={() => toggleLight(device.device)}
                  className={`px-3 py-1.5 rounded-full text-sm transition-colors ${
                    selectedLights.has(device.device)
                      ? "bg-green-600 text-white"
                      : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                  }`}
                >
                  {device.deviceName || device.sku}
                </button>
              ))}
            </div>
          </div>

          {/* Mode selector */}
          <div className="space-y-2">
            <label className="text-sm text-gray-400">Mode</label>
            <div className="flex gap-2">
              {["pulse", "ambient", "party"].map((mode) => (
                <button
                  key={mode}
                  onClick={() => handleModeChange(mode)}
                  className={`flex-1 px-3 py-2 rounded-lg text-sm font-medium capitalize transition-colors ${
                    lightShowMode === mode
                      ? "bg-green-600 text-white"
                      : "bg-gray-800 text-gray-400 hover:bg-gray-700"
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>
          </div>

          {/* Intensity slider */}
          <div className="space-y-1">
            <div className="flex justify-between">
              <label className="text-sm text-gray-400">Intensity</label>
              <span className="text-sm text-gray-400">{intensity}</span>
            </div>
            <input
              type="range"
              min={1}
              max={10}
              value={intensity}
              onChange={(e) => handleIntensityChange(Number(e.target.value))}
              className="w-full accent-green-600"
            />
          </div>

          {/* Latency slider */}
          <div className="space-y-1">
            <div className="flex justify-between">
              <label className="text-sm text-gray-400">Latency offset</label>
              <span className="text-sm text-gray-400">{latency}ms</span>
            </div>
            <input
              type="range"
              min={-500}
              max={500}
              step={10}
              value={latency}
              onChange={(e) => handleLatencyChange(Number(e.target.value))}
              className="w-full accent-green-600"
            />
          </div>

          {/* Start/Stop button */}
          {lightShowStatus?.active ? (
            <button
              onClick={handleStopLightShow}
              disabled={actionPending}
              className="w-full py-3 bg-red-600 hover:bg-red-500 text-white font-semibold rounded-lg transition-colors disabled:opacity-50"
            >
              Stop Light Show
            </button>
          ) : (
            <button
              onClick={handleStartLightShow}
              disabled={actionPending || selectedLights.size === 0}
              className="w-full py-3 bg-green-600 hover:bg-green-500 text-white font-semibold rounded-lg transition-colors disabled:opacity-50"
            >
              Start Light Show
            </button>
          )}

          {/* Status line */}
          {lightShowStatus?.active && (
            <p className="text-xs text-gray-400 text-center">
              {lightShowStatus.lights_connected} light{lightShowStatus.lights_connected !== 1 ? "s" : ""} connected
              {" \u00B7 "}
              {lightShowStatus.pipe_exists ? "Audio stream active" : "Pattern mode (no audio)"}
            </p>
          )}
        </div>

        {/* Arrival Music Settings (admin only) */}
        {user?.isAdmin && authenticated && (
          <ArrivalMusicSettings onReconnect={handleConnectSpotify} />
        )}
      </div>
    </div>
  );
}
