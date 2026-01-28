# payments.py - simulates a payment record (placeholder)
import json
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"runtime"; INBOX=RUNTIME/"inbox"; WF=RUNTIME/"write_manifest.json"
OUT=ROOT/"output"
jobs=sorted(INBOX.glob("job_*.json"))
if not jobs: print("[payments] no jobs"); raise SystemExit(0)
job=json.loads(jobs[-1].read_text(encoding="utf-8-sig"))
ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
pay={"biz":job.get("biz"),"amount":job.get("price"),"status":"placeholder","ts":ts}
writes=[{"path":str(OUT/"payment_record.json"), "text": json.dumps(pay, indent=2)}]
existing=[]
if WF.exists():
    try: existing=json.loads(WF.read_text(encoding="utf-8")).get("writes",[])
    except: existing=[]
WF.write_text(json.dumps({"writes": existing + writes}, indent=2), encoding="utf-8")
print("[payments] wrote payment_record")
