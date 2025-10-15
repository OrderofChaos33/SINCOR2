import os
from importlib import import_module

app = None
candidates = [
    ("bin.run", "app"),                # unified entrypoint you ran locally
    ("sincor.app", "app"),             # fallback if available
    ("sincor.secure_v2.app", "app"),   # fallback if available
]

for mod, attr in candidates:
    try:
        m = import_module(mod)
        app = getattr(m, attr)
        break
    except Exception as e:
        print(f"[wrapper] import {mod}.{attr} failed: {e}")

if app is None:
    # Last-resort safety so the container boots (won’t be used if imports succeed)
    from flask import Flask
    app = Flask(__name__)
    @app.get("/")
    def _root():
        return "SINCOR wrapper online (main app import failed)", 200

# Always give Railway a health endpoint
try:
    @app.get("/health")
    def _health():
        return "ok", 200
except Exception:
    pass

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
