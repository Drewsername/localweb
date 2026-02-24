import os
from dotenv import load_dotenv

load_dotenv()

from flask import Flask, Response, jsonify, send_from_directory
from flask_cors import CORS
from db import init_db

static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
app = Flask(__name__, static_folder=static_dir, static_url_path="")
CORS(app)

init_db()

# Register blueprints
from routes.users import users_bp
from routes.settings import settings_bp
from routes.govee import govee_bp

app.register_blueprint(users_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(govee_bp)

# E-ink driver (only on Pi hardware)
eink = None
if os.environ.get("LOCALWEB_ENV") != "dev":
    try:
        from drivers.eink import InkyHandler
        eink = InkyHandler()
        eink.idle()  # Show default screen on startup
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


@app.get("/api/display")
def display_image():
    from drivers.eink import get_display_png
    return Response(get_display_png(), mimetype="image/png")


@app.get("/")
def serve_frontend():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(404)
def fallback(e):
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    # Start presence scanner
    from services.presence import PresenceScanner
    from routes.govee import govee

    scanner = PresenceScanner(eink=eink, govee=govee)
    scanner.start()

    app.run(host="0.0.0.0", port=5000, debug=True)
