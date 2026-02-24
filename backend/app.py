import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
