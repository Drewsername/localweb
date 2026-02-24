import { useState, useEffect } from "react";
import { Link } from "react-router-dom";

export default function Display() {
  const [imgSrc, setImgSrc] = useState<string | null>(null);
  const [darkMode, setDarkMode] = useState(false);
  const [error, setError] = useState("");

  function refresh() {
    setError("");
    fetch("/api/display")
      .then((r) => {
        if (!r.ok) throw new Error("Display not available");
        return r.blob();
      })
      .then((blob) => {
        setImgSrc(URL.createObjectURL(blob));
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Failed to load display");
      });
  }

  useEffect(() => {
    refresh();
    fetch("/api/display/dark-mode")
      .then((r) => r.json())
      .then((data) => setDarkMode(data.enabled))
      .catch(() => {});
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

  function toggleDarkMode() {
    const next = !darkMode;
    setDarkMode(next);
    fetch("/api/display/dark-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: next }),
    })
      .then((r) => r.json())
      .then((data) => {
        setDarkMode(data.enabled);
        refresh();
      })
      .catch(() => setDarkMode(!next));
  }

  return (
    <div className="min-h-screen bg-gray-950 text-white p-6">
      <div className="max-w-lg mx-auto space-y-6">
        <div className="flex items-center gap-4">
          <Link to="/home" className="text-gray-400 hover:text-white text-2xl">&larr;</Link>
          <h1 className="text-2xl font-bold">E-Ink Display</h1>
        </div>

        {error && <p className="text-red-400">{error}</p>}

        <div className="bg-white rounded-xl overflow-hidden border border-gray-700">
          {imgSrc ? (
            <img
              src={imgSrc}
              alt="E-ink display mirror"
              className="w-full h-auto"
            />
          ) : (
            <div className="aspect-[4/3] flex items-center justify-center text-gray-400 bg-gray-900">
              Loading display...
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <button
            onClick={refresh}
            className="flex-1 px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-lg font-medium transition-colors"
          >
            Refresh
          </button>
          <button
            onClick={toggleDarkMode}
            className={`flex-1 px-4 py-3 rounded-lg font-medium transition-colors ${
              darkMode
                ? "bg-gray-200 text-gray-900 hover:bg-gray-300"
                : "bg-gray-800 hover:bg-gray-700"
            }`}
          >
            {darkMode ? "Light Mode" : "Dark Mode"}
          </button>
        </div>
      </div>
    </div>
  );
}
