import { useState, useEffect } from "react";
import { Link } from "react-router-dom";

export default function Display() {
  const [imgSrc, setImgSrc] = useState<string | null>(null);
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
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, []);

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

        <button
          onClick={refresh}
          className="w-full px-4 py-3 bg-gray-800 hover:bg-gray-700 rounded-lg font-medium transition-colors"
        >
          Refresh
        </button>
      </div>
    </div>
  );
}
