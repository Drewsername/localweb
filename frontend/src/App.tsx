import { useState } from "react";

function App() {
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function sendHello() {
    setLoading(true);
    setStatus(null);
    try {
      const res = await fetch("/api/eink/hello", { method: "POST" });
      const data = await res.json();
      setStatus(res.ok ? data.message : data.error);
    } catch {
      setStatus("Failed to connect to backend");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white flex items-center justify-center">
      <div className="text-center space-y-6">
        <h1 className="text-4xl font-bold">localweb</h1>
        <p className="text-gray-400">Home control system</p>
        <button
          onClick={sendHello}
          disabled={loading}
          className="px-6 py-3 bg-red-600 hover:bg-red-700 disabled:opacity-50 rounded-lg font-medium transition-colors"
        >
          {loading ? "Sending..." : "Say Hello (E-Ink)"}
        </button>
        {status && (
          <p className="text-sm text-gray-300">{status}</p>
        )}
      </div>
    </div>
  );
}

export default App;
