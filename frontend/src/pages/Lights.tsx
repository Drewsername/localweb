import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";

interface DeviceCapability {
  type: string;
  instance: string;
  parameters: Record<string, unknown>;
}

interface Device {
  device: string;
  sku: string;
  deviceName: string;
  capabilities: DeviceCapability[];
}

interface DeviceState {
  capabilities?: Array<{
    type: string;
    instance: string;
    state: { value: unknown };
  }>;
}

export default function Lights() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [states, setStates] = useState<Record<string, DeviceState>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchDevices = useCallback(async () => {
    try {
      const res = await fetch("/api/govee/devices");
      if (!res.ok) throw new Error("Failed to load devices");
      const data = await res.json();
      setDevices(data);

      // Fetch state for each device
      const stateEntries = await Promise.all(
        data.map(async (d: Device) => {
          try {
            const sr = await fetch(`/api/govee/devices/${encodeURIComponent(d.device)}/state`);
            if (sr.ok) {
              const sd = await sr.json();
              return [d.device, sd] as const;
            }
          } catch { /* skip */ }
          return [d.device, {}] as const;
        })
      );
      setStates(Object.fromEntries(stateEntries));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load devices");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchDevices();
  }, [fetchDevices]);

  function updateLocalState(deviceId: string, instance: string, value: unknown) {
    setStates((prev) => {
      const existing = prev[deviceId]?.capabilities ?? [];
      const idx = existing.findIndex((c) => c.instance === instance);
      const updated = [...existing];
      if (idx >= 0) {
        updated[idx] = { ...updated[idx], state: { value } };
      } else {
        updated.push({ type: "", instance, state: { value } });
      }
      return { ...prev, [deviceId]: { capabilities: updated } };
    });
  }

  async function sendControl(deviceId: string, capability: Record<string, unknown>) {
    // Optimistic: update UI immediately
    updateLocalState(deviceId, capability.instance as string, capability.value);
    try {
      await fetch(`/api/govee/devices/${encodeURIComponent(deviceId)}/control`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ capability }),
      });
    } catch (err) {
      console.error("Control failed:", err);
    }
  }

  function getStateValue(deviceId: string, instance: string): unknown {
    const state = states[deviceId];
    if (!state?.capabilities) return undefined;
    const cap = state.capabilities.find((c) => c.instance === instance);
    return cap?.state?.value;
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
        <p className="text-gray-400">Loading devices...</p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">&larr;</Link>
          <h1 className="text-2xl font-bold">Lighting</h1>
        </div>

        {error && <p className="text-red-400">{error}</p>}

        {devices.length === 0 && !error && (
          <p className="text-gray-400">No Govee devices found.</p>
        )}

        {devices.map((device) => (
          <DeviceCard
            key={device.device}
            device={device}
            getStateValue={(instance) => getStateValue(device.device, instance)}
            onControl={(cap) => sendControl(device.device, cap)}
          />
        ))}
      </div>
    </div>
  );
}

function DeviceCard({
  device,
  getStateValue,
  onControl,
}: {
  device: Device;
  getStateValue: (instance: string) => unknown;
  onControl: (capability: Record<string, unknown>) => void;
}) {
  const hasPower = device.capabilities.some((c) => c.instance === "powerSwitch");
  const hasBrightness = device.capabilities.some((c) => c.instance === "brightness");
  const hasColor = device.capabilities.some((c) => c.instance === "colorRgb");

  const online = getStateValue("online") !== false;
  const powerOn = getStateValue("powerSwitch") === 1;
  const brightness = (getStateValue("brightness") as number) ?? 100;
  const colorInt = (getStateValue("colorRgb") as number) ?? 16777215;
  const colorHex = "#" + colorInt.toString(16).padStart(6, "0");

  // Local state for slider/color so dragging feels instant
  const [localBrightness, setLocalBrightness] = useState(brightness);
  const [draggingBrightness, setDraggingBrightness] = useState(false);
  const [localColor, setLocalColor] = useState(colorHex);
  const [pickingColor, setPickingColor] = useState(false);
  const brightnessTimer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const colorTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Sync from server state when not actively interacting
  useEffect(() => {
    if (!draggingBrightness) setLocalBrightness(brightness);
  }, [brightness, draggingBrightness]);

  useEffect(() => {
    if (!pickingColor) setLocalColor(colorHex);
  }, [colorHex, pickingColor]);

  return (
    <div className={`p-5 bg-gray-900 border border-gray-800 rounded-xl space-y-4 ${!online ? "opacity-50" : ""}`}>
      {!online && (
        <p className="text-xs text-red-400">Device offline</p>
      )}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{device.deviceName || device.sku}</h2>
        {hasPower && (
          <button
            onClick={() =>
              onControl({
                type: "devices.capabilities.on_off",
                instance: "powerSwitch",
                value: powerOn ? 0 : 1,
              })
            }
            className={`w-14 h-8 rounded-full transition-colors relative ${
              powerOn ? "bg-red-600" : "bg-gray-700"
            }`}
          >
            <span
              className={`absolute top-1 w-6 h-6 rounded-full bg-white transition-transform ${
                powerOn ? "left-7" : "left-1"
              }`}
            />
          </button>
        )}
      </div>

      {hasBrightness && (
        <div className="space-y-1">
          <label className="text-sm text-gray-400">Brightness: {localBrightness}%</label>
          <input
            type="range"
            min={1}
            max={100}
            value={localBrightness}
            onChange={(e) => {
              const val = Number(e.target.value);
              setLocalBrightness(val);
              setDraggingBrightness(true);
              clearTimeout(brightnessTimer.current);
              brightnessTimer.current = setTimeout(() => {
                onControl({
                  type: "devices.capabilities.range",
                  instance: "brightness",
                  value: val,
                });
                setDraggingBrightness(false);
              }, 300);
            }}
            className="w-full accent-red-600"
          />
        </div>
      )}

      {hasColor && (
        <div className="space-y-1">
          <label className="text-sm text-gray-400">Color</label>
          <input
            type="color"
            value={localColor}
            onChange={(e) => {
              setLocalColor(e.target.value);
              setPickingColor(true);
              clearTimeout(colorTimer.current);
              colorTimer.current = setTimeout(() => {
                const rgb = parseInt(e.target.value.slice(1), 16);
                onControl({
                  type: "devices.capabilities.color_setting",
                  instance: "colorRgb",
                  value: rgb,
                });
                setPickingColor(false);
              }, 300);
            }}
            className="w-full h-10 rounded-lg border border-gray-700 bg-gray-800 cursor-pointer"
          />
        </div>
      )}
    </div>
  );
}
