import os
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

# Serve built frontend from frontend/dist
static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
app = Flask(__name__, static_folder=static_dir, static_url_path="")
CORS(app)

# Only import e-ink driver on the Pi (it requires hardware)
eink = None
if os.environ.get("LOCALWEB_ENV") != "dev":
    try:
        from drivers.eink import InkyHandler
        eink = InkyHandler()
    except Exception as e:
        print(f"E-ink not available: {e}")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "eink_available": eink is not None})


@app.post("/api/eink/hello")
def eink_hello():
    if eink is None:
        return jsonify({"error": "E-ink display not available"}), 503
    eink.hello_world()
    return jsonify({"message": "Hello World displayed on e-ink"})


@app.get("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(404)
def fallback(e):
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
