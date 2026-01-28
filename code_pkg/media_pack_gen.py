# media_pack_gen.py
# Waits for a job in runtime/inbox (polls up to 2 minutes), then emits runtime/write_manifest.json

import json, time
from pathlib import Path
from datetime import datetime
import base64

def b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")

def main():
    # ROOT = ...\sincor_33
    SERVICES_DIR = Path(__file__).resolve().parent
    ROOT = SERVICES_DIR.parent

    RUNTIME = ROOT / "runtime"
    INBOX   = RUNTIME / "inbox"
    WF      = RUNTIME / "write_manifest.json"
    OUTDIR  = ROOT / "output"

    RUNTIME.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)

    # Remove any stale write_manifest so the runner knows this run produced a fresh one
    try:
        if WF.exists():
            WF.unlink()
    except Exception:
        pass

    # Poll for a job for up to 120 seconds
    job_file = None
    deadline = time.time() + 120
    while time.time() < deadline:
        jobs = sorted(INBOX.glob("job_*.json"))
        if jobs:
            job_file = jobs[-1]
            break
        time.sleep(0.3)

    if not job_file:
        print("[media_pack_gen] No jobs found within timeout.")
        return

    job = json.loads(job_file.read_text(encoding="utf-8-sig"))

    path_id = job.get("path", "UNKNOWN")
    biz     = job.get("biz", "Test Business")
    city    = job.get("city", "City")
    email   = job.get("email", "owner@example.com")
    phone   = job.get("phone", "000-000-0000")
    price   = job.get("price", 0)
    payURL  = job.get("payURL", "")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Placeholder deliverables (enough to prove the harness)
    flyer_bytes = f"PDF PLACEHOLDER\nBusiness: {biz}\nCity: {city}\nPrice: {price}\nGenerated: {ts}\n".encode("utf-8")
    pack_zip_bytes = f"ZIP PLACEHOLDER for {biz} ({path_id}) @ {ts}".encode("utf-8")
    asset_links_json = {
        "biz": biz,
        "city": city,
        "contact": {"email": email, "phone": phone},
        "payURL": payURL,
        "notes": "Placeholder links for harness validation.",
        "generated_at": ts
    }
    asset_links_text = json.dumps(asset_links_json, indent=2)

    writes = [
        {"path": str(OUTDIR / "flyer.pdf"), "payload_b64": b64(flyer_bytes)},
        {"path": str(OUTDIR / "pack.zip"), "payload_b64": b64(pack_zip_bytes)},
        {"path": str(OUTDIR / "asset_links.json"), "text": asset_links_text},
        {"path": str(OUTDIR / "generation_manifest.json"),
         "text": json.dumps({
             "path": path_id, "job_id": job.get("id"), "ts": ts,
             "deliverables": ["output/flyer.pdf", "output/pack.zip", "output/asset_links.json"]
         }, indent=2)}
    ]

    WF.write_text(json.dumps({"writes": writes}, indent=2), encoding="utf-8")
    print(f"[media_pack_gen] Wrote write manifest with {len(writes)} items -> {WF}")

if __name__ == "__main__":
    main()
