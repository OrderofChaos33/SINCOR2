# profile_sync.py  minimal stub to satisfy AE-22 by writing the manifest
import json, base64, time
from pathlib import Path
from datetime import datetime

def b64(b: bytes) -> str: 
    import base64; return base64.b64encode(b).decode("ascii")

ROOT = Path(__file__).resolve().parents[1]   # ...\sincor_33
RUNTIME = ROOT / "runtime"
INBOX   = RUNTIME / "inbox"
WF      = RUNTIME / "write_manifest.json"
PATHS   = ROOT / "paths"
OUTDIR  = ROOT / "output"

RUNTIME.mkdir(parents=True, exist_ok=True)
INBOX.mkdir(parents=True, exist_ok=True)
OUTDIR.mkdir(parents=True, exist_ok=True)

# grab newest job (BOM tolerant)
jobs = sorted(INBOX.glob("job_*.json"))
if not jobs:
    print("[profile_sync] no jobs"); raise SystemExit(0)
job = json.loads(jobs[-1].read_text(encoding="utf-8-sig"))
pid = job.get("path","UNKNOWN")
pfile = PATHS / f"{pid}.json"
if not pfile.exists():
    print(f"[profile_sync] path file missing: {pfile}"); raise SystemExit(0)
p = json.loads(pfile.read_text(encoding="utf-8"))

ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
biz = job.get("biz","Biz"); city = job.get("city","City")

writes = []
for rel in p.get("deliverables", []):
    tgt = ROOT / rel
    ext = tgt.suffix.lower()
    if ext in (".json",):
        payload = {"note":"AE-22 placeholder","biz":biz,"city":city,"generated_at":ts}
        writes.append({"path": str(tgt), "text": json.dumps(payload, indent=2)})
    elif ext in (".csv",".html",".txt",".svg"):
        writes.append({"path": str(tgt), "text": f"PLACEHOLDER for {biz} @ {ts}\n"})
    elif ext in (".pdf",".zip"):
        writes.append({"path": str(tgt), "payload_b64": b64(f\"{ext.upper()} PLACEHOLDER for {biz} @ {ts}\".encode())})
    else:
        writes.append({"path": str(tgt), "text": f"PLACEHOLDER for {biz} @ {ts}\n"})

# merge with existing writes if present
existing = []
if WF.exists():
    try: existing = json.loads(WF.read_text(encoding="utf-8")).get("writes", [])
    except Exception: existing = []
WF.write_text(json.dumps({"writes": existing + writes}, indent=2), encoding="utf-8")
print(f"[profile_sync] wrote {len(writes)} items to {WF}")
