# scripts_tag_media.py
# Usage:
#   python scripts_tag_media.py --path "assets/video/yourclip.mp4" --slot HOOK --labels "foam,suds,exterior" --rating 5
import argparse, json, os
from pathlib import Path

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True)
    ap.add_argument("--slot", choices=["HOOK","BENEFIT","PROOF","CTA"], required=True)
    ap.add_argument("--labels", default="")
    ap.add_argument("--rating", type=int, default=4)
    a = ap.parse_args()

    p = Path(a.path)
    if not p.exists(): raise SystemExit(f"Not found: {p}")
    meta = {
        "file": str(p).replace("\\","/"),
        "slot_bias": a.slot,
        "labels": [s.strip() for s in a.labels.split(",") if s.strip()],
        "rating": max(1, min(5, a.rating)),
        "nsfw": False
    }
    out = p.with_suffix(p.suffix + ".meta.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"Wrote {out}")
if __name__ == "__main__":
    main()
