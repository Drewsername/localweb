import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { useUser } from "../context/UserContext";

interface NestDevice {
  id: string;
  name: string;
}

interface DeviceState {
  ambient_temp_f: number | null;
  humidity: number | null;
  mode: string;
  hvac_status: string;
  eco_mode: string;
  heat_target_f: number | null;
  cool_target_f: number | null;
}

interface OptimalTemp {
  optimal_temp_f: number | null;
  user_count: number;
}

const MODES = ["HEAT", "COOL", "HEATCOOL", "OFF"] as const;
const MODE_LABELS: Record<string, string> = {
  HEAT: "Heat",
  COOL: "Cool",
  HEATCOOL: "Heat/Cool",
  OFF: "Off",
};
const HVAC_LABELS: Record<string, string> = {
  HEATING: "Heating",
  COOLING: "Cooling",
  OFF: "Idle",
};

export default function Thermostat() {
  const { user } = useUser();
  const [devices, setDevices] = useState<NestDevice[]>([]);
  const [states, setStates] = useState<Record<string, DeviceState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [preference, setPreference] = useState<number | null>(null);
  const [optimalTemp, setOptimalTemp] = useState<OptimalTemp | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch("/api/nest/devices");
      if (res.status === 401) {
        setError("Nest not authorized. An admin needs to set up the connection.");
        setLoading(false);
        return;
      }
      if (!res.ok) throw new Error("Failed to load devices");
      const devs: NestDevice[] = await res.json();
      setDevices(devs);

      const stateEntries = await Promise.all(
        devs.map(async (d) => {
          try {
            const sr = await fetch(`/api/nest/devices/${d.id}/state`);
            if (sr.ok) return [d.id, await sr.json()] as const;
          } catch { /* skip */ }
          return [d.id, {} as DeviceState] as const;
        })
      );
      setStates(Object.fromEntries(stateEntries));

      // Fetch user preference
      const prefRes = await fetch("/api/settings");
      if (prefRes.ok) {
        const settings = await prefRes.json();
        const pref = settings?.["nest.preferences"]?.preferred_temp;
        if (pref != null) setPreference(pref);
      }

      // Fetch optimal temp
      const optRes = await fetch("/api/nest/optimal-temp");
      if (optRes.ok) setOptimalTemp(await optRes.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  async function sendControl(deviceId: string, data: Record<string, unknown>) {
    // Optimistic UI: update local state immediately
    if (data.target_temp_f != null) {
      setStates((prev) => {
        const s = prev[deviceId];
        if (!s) return prev;
        return {
          ...prev,
          [deviceId]: {
            ...s,
            heat_target_f: s.mode === "COOL" ? s.heat_target_f : data.target_temp_f as number,
            cool_target_f: s.mode === "HEAT" ? s.cool_target_f : data.target_temp_f as number,
          },
        };
      });
    }
    if (data.mode != null) {
      setStates((prev) => ({
        ...prev,
        [deviceId]: { ...prev[deviceId], mode: data.mode as string },
      }));
    }
    if (data.eco != null) {
      setStates((prev) => ({
        ...prev,
        [deviceId]: { ...prev[deviceId], eco_mode: data.eco ? "MANUAL_ECO" : "OFF" },
      }));
    }

    try {
      await fetch(`/api/nest/devices/${deviceId}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } catch (err) {
      console.error("Control failed:", err);
      fetchData();
    }
  }

  async function savePreference(temp: number) {
    setPreference(temp);
    try {
      await fetch("/api/settings/nest.preferences", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferred_temp: temp }),
      });
      const optRes = await fetch("/api/nest/optimal-temp");
      if (optRes.ok) setOptimalTemp(await optRes.json());
    } catch (err) {
      console.error("Failed to save preference:", err);
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading thermostat...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">&larr;</Link>
          <h1 className="text-2xl font-bold">Thermostat</h1>
        </div>

        {error && <p className="text-red-400">{error}</p>}

        {devices.length === 0 && !error && (
          <p className="text-gray-400">No Nest thermostats found.</p>
        )}

        {devices.map((device) => (
          <ThermostatCard
            key={device.id}
            device={device}
            state={states[device.id]}
            onControl={(data) => sendControl(device.id, data)}
          />
        ))}

        {devices.length > 0 && (
          <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-3">
            <h2 className="text-lg font-semibold">My Preference</h2>
            <p className="text-sm text-gray-400">
              Set your ideal temperature. The system optimizes for everyone at home.
            </p>
            <div className="flex items-center gap-4">
              <button
                onClick={() => savePreference((preference ?? 72) - 0.5)}
                className="w-10 h-10 rounded-lg bg-gray-800 hover:bg-gray-700 text-xl font-bold"
              >
                -
              </button>
              <span className="text-3xl font-bold min-w-[5rem] text-center">
                {preference ?? "\u2014"}{"\u00b0"}F
              </span>
              <button
                onClick={() => savePreference((preference ?? 72) + 0.5)}
                className="w-10 h-10 rounded-lg bg-gray-800 hover:bg-gray-700 text-xl font-bold"
              >
                +
              </button>
            </div>
            {optimalTemp?.optimal_temp_f != null && (
              <p className="text-sm text-gray-400">
                Optimized temp: <span className="text-white font-semibold">{optimalTemp.optimal_temp_f}{"\u00b0"}F</span>
                {" "}({optimalTemp.user_count} {optimalTemp.user_count === 1 ? "person" : "people"} home)
              </p>
            )}
          </div>
        )}

        {user?.isAdmin && devices.length > 0 && <AdminGuardrails />}
      </div>
    </div>
  );
}


function ThermostatCard({
  device,
  state,
  onControl,
}: {
  device: NestDevice;
  state?: DeviceState;
  onControl: (data: Record<string, unknown>) => void;
}) {
  if (!state) return null;

  const targetTemp =
    state.mode === "COOL" ? state.cool_target_f :
    state.mode === "HEAT" ? state.heat_target_f :
    state.heat_target_f ?? state.cool_target_f;

  const ecoActive = state.eco_mode === "MANUAL_ECO";

  return (
    <div className="p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{device.name}</h2>
        <span className={`text-xs px-2 py-1 rounded-full ${
          state.hvac_status === "HEATING" ? "bg-orange-900 text-orange-300" :
          state.hvac_status === "COOLING" ? "bg-blue-900 text-blue-300" :
          "bg-gray-800 text-gray-400"
        }`}>
          {HVAC_LABELS[state.hvac_status] ?? state.hvac_status}
        </span>
      </div>

      {state.ambient_temp_f != null && (
        <div className="text-center">
          <p className="text-5xl font-bold">{state.ambient_temp_f}{"\u00b0"}</p>
          <p className="text-sm text-gray-400 mt-1">Current temperature</p>
        </div>
      )}

      {targetTemp != null && !ecoActive && (
        <div className="flex items-center justify-center gap-6">
          <button
            onClick={() => onControl({ target_temp_f: targetTemp - 0.5 })}
            className="w-12 h-12 rounded-full bg-gray-800 hover:bg-gray-700 text-2xl font-bold"
          >
            -
          </button>
          <div className="text-center">
            <p className="text-3xl font-bold">{targetTemp}{"\u00b0"}F</p>
            <p className="text-xs text-gray-400">Target</p>
          </div>
          <button
            onClick={() => onControl({ target_temp_f: targetTemp + 0.5 })}
            className="w-12 h-12 rounded-full bg-gray-800 hover:bg-gray-700 text-2xl font-bold"
          >
            +
          </button>
        </div>
      )}

      <div className="flex gap-2">
        {MODES.map((m) => (
          <button
            key={m}
            onClick={() => onControl({ mode: m })}
            className={`flex-1 py-2 rounded-lg text-sm font-medium transition-colors ${
              state.mode === m
                ? "bg-red-600 text-white"
                : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-400">Eco Mode</span>
        <button
          onClick={() => onControl({ eco: !ecoActive })}
          className={`w-14 h-8 rounded-full transition-colors relative ${
            ecoActive ? "bg-green-600" : "bg-gray-700"
          }`}
        >
          <span
            className={`absolute top-1 w-6 h-6 rounded-full bg-white transition-transform ${
              ecoActive ? "left-7" : "left-1"
            }`}
          />
        </button>
      </div>

      {state.humidity != null && (
        <p className="text-sm text-gray-500">Humidity: {state.humidity}%</p>
      )}
    </div>
  );
}


function AdminGuardrails() {
  const [minTemp, setMinTemp] = useState(65);
  const [maxTemp, setMaxTemp] = useState(78);
  const [weights, setWeights] = useState<Record<string, { name: string; weight: number }>>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch("/api/nest/admin/guardrails");
        if (!res.ok) return;
        const data = await res.json();
        setMinTemp(data.min_temp ?? 65);
        setMaxTemp(data.max_temp ?? 78);

        const usersRes = await fetch("/api/users/home");
        if (usersRes.ok) {
          const users: Array<{ id: number; name: string }> = await usersRes.json();
          const w: Record<string, { name: string; weight: number }> = {};
          for (const u of users) {
            w[String(u.id)] = {
              name: u.name,
              weight: data.user_weights?.[String(u.id)] ?? 1.0,
            };
          }
          setWeights(w);
        }
      } catch { /* ignore */ }
      setLoaded(true);
    }
    load();
  }, []);

  async function save() {
    const userWeights: Record<string, number> = {};
    for (const [uid, { weight }] of Object.entries(weights)) {
      userWeights[uid] = weight;
    }
    try {
      await fetch("/api/nest/admin/guardrails", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ min_temp: minTemp, max_temp: maxTemp, user_weights: userWeights }),
      });
    } catch (err) {
      console.error("Failed to save guardrails:", err);
    }
  }

  if (!loaded) return null;

  return (
    <div className="p-5 bg-gray-900 border border-red-900/50 rounded-xl space-y-4">
      <h2 className="text-lg font-semibold">Admin: Algorithm Guardrails</h2>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="text-sm text-gray-400">Min Temp ({"\u00b0"}F)</label>
          <input
            type="number"
            value={minTemp}
            onChange={(e) => setMinTemp(Number(e.target.value))}
            onBlur={save}
            className="w-full mt-1 p-2 bg-gray-800 border border-gray-700 rounded-lg text-white"
          />
        </div>
        <div>
          <label className="text-sm text-gray-400">Max Temp ({"\u00b0"}F)</label>
          <input
            type="number"
            value={maxTemp}
            onChange={(e) => setMaxTemp(Number(e.target.value))}
            onBlur={save}
            className="w-full mt-1 p-2 bg-gray-800 border border-gray-700 rounded-lg text-white"
          />
        </div>
      </div>

      {Object.keys(weights).length > 0 && (
        <div className="space-y-2">
          <label className="text-sm text-gray-400">User Weights</label>
          {Object.entries(weights).map(([uid, { name, weight }]) => (
            <div key={uid} className="flex items-center gap-3">
              <span className="text-sm flex-1">{name}</span>
              <input
                type="number"
                step="0.1"
                min="0.1"
                max="5"
                value={weight}
                onChange={(e) =>
                  setWeights((prev) => ({
                    ...prev,
                    [uid]: { ...prev[uid], weight: Number(e.target.value) },
                  }))
                }
                onBlur={save}
                className="w-20 p-2 bg-gray-800 border border-gray-700 rounded-lg text-white text-center"
              />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
