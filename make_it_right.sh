# make_it_right.sh — ONE CLEAN PROJECT (with rsync→cp fallback)
set -euo pipefail

git rev-parse --is-inside-work-tree >/dev/null
git status --porcelain | grep . && { echo "❌ Commit or stash first (repo not clean)"; exit 1; } || true

TAG="pre-refactor-$(date +%Y%m%d-%H%M%S)"
git tag "$TAG"
echo "✅ Tag created: $TAG"
mkdir -p .backup && cp -a app.py requirements.txt .backup/ 2>/dev/null || true

cat > .gitattributes <<'EOF'
* text=auto
*.py text eol=lf
*.sh text eol=lf
*.md text eol=lf
*.html text eol=lf
*.json text eol=lf
*.yml text eol=lf
*.yaml text eol=lf
*.bat text eol=crlf
*.ps1 text eol=crlf
EOF

cat >> .gitignore <<'EOF'
__pycache__/
*.pyc
*.pyo
.venv/
.env
.DS_Store
logs/
outputs/
data/
*.db
sincor/
sincor2-backup/
tatus
EOF

git rm -r --cached sincor sincor2-backup 2>/dev/null || true
git rm -r --cached __pycache__ */__pycache__ *.pyc *.pyo logs outputs data *.db .env .venv tatus 2>/dev/null || true

mkdir -p src/sincor/secure_v2 src/sincor/local_legacy bin
: > src/sincor/__init__.py
: > src/sincor/secure_v2/__init__.py
: > src/sincor/local_legacy/__init__.py

# copy sincor2 → secure_v2 (prefer rsync, fallback to cp -a)
if command -v rsync >/dev/null; then
  rsync -a --exclude='.git' --exclude='__pycache__' --exclude='logs' --exclude='outputs' --exclude='data' sincor2/ src/sincor/secure_v2/
else
  (cd sincor2 && find . -type f ! -path "./.git/*" ! -path "./__pycache__/*" ! -path "./logs/*" ! -path "./outputs/*" ! -path "./data/*" -print0 \
     | xargs -0 -I{} sh -c 'mkdir -p "../src/sincor/secure_v2/$(dirname "{}")"; cp -a "{}" "../src/sincor/secure_v2/{}"')
fi

if [ -f app.py ]; then
  mv app.py src/sincor/local_legacy/app_local.py
fi

cat > src/sincor/app.py <<'PY'
#!/usr/bin/env python3
import os
# Prefer secure_v2; fall back to legacy
try:
    from .secure_v2 import app as secure_app
    app = secure_app.app if hasattr(secure_app, "app") else secure_app
except Exception as e:
    print(f"[SINCOR] secure_v2 app unavailable: {e}")
    try:
        from .local_legacy import app_local as legacy
        app = getattr(legacy, "app", legacy)
        print("[SINCOR] Running legacy app fallback.")
    except Exception as e2:
        raise RuntimeError(f"No runnable app found: secure_v2 error={e}; legacy error={e2}")
if __name__ == "__main__":
    try:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
    except AttributeError:
        app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
PY

cat > bin/run.py <<'PY'
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path: sys.path.insert(0, SRC)
from sincor.app import app
if hasattr(app, "run"):
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
else:
    app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
PY

REQ_MERGED=requirements.txt
touch requirements.txt
touch src/sincor/secure_v2/requirements.txt
( cat requirements.txt; echo; cat src/sincor/secure_v2/requirements.txt ) \
  | sed 's/\r$//' | sed '/^\s*$/d' | sort -u > requirements.merged.txt
mv requirements.merged.txt "$REQ_MERGED"

python -m pip install --upgrade pip >/dev/null 2>&1 || true
python -m pip install -r "$REQ_MERGED" || true

python - <<'PY'
import sys, os
ROOT=os.getcwd()
SRC=os.path.join(ROOT,"src")
sys.path.insert(0,SRC)
from sincor.app import app
print("✅ Import succeeded; app object:", type(app))
PY

git add -A
git commit -m "Refactor: single-package layout (src/sincor), secure_v2 import, legacy preserved; unified entrypoint; EOL & ignores normalized; merged requirements"

echo "✅ ONE PROJECT READY"
echo "Run locally:"
echo "  python bin/run.py"
