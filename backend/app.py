import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
)

from flask import Flask, Response, jsonify, request, send_from_directory
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
from routes.nest import nest_bp
from routes.admin import admin_bp
from routes.spotify import spotify_bp

app.register_blueprint(users_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(govee_bp)
app.register_blueprint(nest_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(spotify_bp)

# E-ink driver (only on Pi hardware)
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
    from drivers.eink import render_hello, set_current
    if eink:
        eink.show_image(render_hello())
    else:
        set_current(render_hello())
    return jsonify({"message": "Hello World displayed on e-ink"})


@app.get("/api/display")
def display_image():
    from drivers.eink import get_display_png
    return Response(get_display_png(), mimetype="image/png")


@app.get("/api/display/dark-mode")
def get_dark_mode():
    from drivers.eink import is_dark_mode
    return jsonify({"enabled": is_dark_mode()})


@app.post("/api/display/dark-mode")
def set_display_dark_mode():
    from drivers.eink import set_dark_mode, is_dark_mode, current_img
    data = request.get_json()
    set_dark_mode(data.get("enabled", False))
    # Re-push to hardware with new color mode
    if eink and current_img:
        eink.show_image(current_img)
    return jsonify({"enabled": is_dark_mode()})


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
    from routes.nest import nest

    scanner = PresenceScanner(eink=eink, govee=govee, nest=nest)
    scanner.start()

    # Show dashboard on startup (shows who's currently home)
    scanner.show_dashboard(force=True)

    is_dev = os.environ.get("LOCALWEB_ENV") == "dev"
    app.run(host="0.0.0.0", port=5000, debug=is_dev)
