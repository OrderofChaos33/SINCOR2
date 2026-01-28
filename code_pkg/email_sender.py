# email_sender.py - simulates sending and appends a receipt file
import json
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/"runtime"; INBOX=RUNTIME/"inbox"; WF=RUNTIME/"write_manifest.json"
OUT=ROOT/"output"
jobs=sorted(INBOX.glob("job_*.json"))
if not jobs: print("[email_sender] no jobs"); raise SystemExit(0)
job=json.loads(jobs[-1].read_text(encoding="utf-8-sig"))
ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
receipt={"to":job.get("email"),"biz":job.get("biz"),"sent_at":ts}
writes=[{"path":str(OUT/"email_receipt.json"), "text": json.dumps(receipt, indent=2)}]
existing=[]
if WF.exists():
    try: existing=json.loads(WF.read_text(encoding="utf-8")).get("writes",[])
    except: existing=[]
WF.write_text(json.dumps({"writes": existing + writes}, indent=2), encoding="utf-8")
print("[email_sender] wrote receipt")
