import os, sys, json, hashlib, base64
from pathlib import Path

"""
write_gate.py
--------------
Usage:
  python write_gate.py preview manifest.json
  python write_gate.py apply manifest.json

The manifest.json must look like:
{
  "writes": [
    {"path": "output/test.txt", "text": "hello world"},
    {"path": "output/file.bin", "payload_b64": "<base64bytes>"}
  ]
}
"""

def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("preview", "apply"):
        print("Usage: write_gate.py [preview|apply] manifest.json")
        sys.exit(2)

    mode, mf = sys.argv[1], Path(sys.argv[2])
    if not mf.exists():
        print(f"Manifest not found: {mf}")
        sys.exit(1)

    data = json.loads(mf.read_text(encoding="utf-8"))
    planned = []
    for w in data.get("writes", []):
        p = Path(w["path"])
        if "payload_b64" in w:
            content = base64.b64decode(w["payload_b64"])
        else:
            content = (w.get("text") or "").encode("utf-8")

        planned.append({
            "path": str(p),
            "size": len(content),
            "sha256": sha256(content),
            "exists": p.exists()
        })

    print(json.dumps({"planned_writes": planned}, indent=2))

    if mode == "apply":
        for w in data.get("writes", []):
            p = Path(w["path"])
            p.parent.mkdir(parents=True, exist_ok=True)

            if "payload_b64" in w:
                content = base64.b64decode(w["payload_b64"])
                p.write_bytes(content)
            else:
                p.write_text(w.get("text", ""), encoding="utf-8")
        print("APPLIED")

if __name__ == "__main__":
    main()

