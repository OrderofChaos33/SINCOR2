import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)
from sincor.app import app
if hasattr(app, "run"):
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
else:
    app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
