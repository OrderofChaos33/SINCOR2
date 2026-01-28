# template_renderer.py - renders placeholder files listed in path manifest
import json, time, base64
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"runtime"; INBOX=RUNTIME/"inbox"; WF=RUNTIME/"write_manifest.json"
PATHS=ROOT/"paths"; OUT=ROOT/"output"
def b64(b): import base64; return base64.b64encode(b).decode()
jobs=sorted(INBOX.glob("job_*.json"))
if not jobs: print("[template_renderer] no jobs"); raise SystemExit(0)
job=json.loads(jobs[-1].read_text(encoding="utf-8-sig"))
pid=job.get("path"); pfile=PATHS/f"{pid}.json"
if not pfile.exists(): print("[template_renderer] missing path"); raise SystemExit(0)
p=json.loads(pfile.read_text(encoding="utf-8"))
ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
writes=[]
for rel in p.get("deliverables",[]):
    tgt=ROOT/rel
    if tgt.suffix.lower() in (".pdf",".zip"):
        writes.append({"path":str(tgt),"payload_b64":b64(f"RENDERED {tgt.name} for {job.get('biz')} @ {ts}".encode())})
    else:
        writes.append({"path":str(tgt),"text":f"RENDERED placeholder for {tgt.name} @ {ts}\n"})
existing=[]
if WF.exists():
    try: existing=json.loads(WF.read_text(encoding="utf-8")).get("writes",[])
    except: existing=[]
WF.write_text(json.dumps({"writes": existing + writes}, indent=2), encoding="utf-8")
print("[template_renderer] wrote", len(writes))
