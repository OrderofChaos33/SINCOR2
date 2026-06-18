#!/usr/bin/env python3
"""Launchpad outreach batch — one-tap emails for 5 LBP venues."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from sincor2.launchpad_outreach import due_outreach, pipeline_summary, write_batch_file


def main() -> None:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("cmd", nargs="?", default="batch", choices=["batch", "summary", "mark"])
    p.add_argument("launchpad_id", nargs="?", default="")
    p.add_argument("--status", default="contacted")
    args = p.parse_args()

    if args.cmd == "summary":
        print(json.dumps(pipeline_summary(), indent=2))
        return
    if args.cmd == "mark" and args.launchpad_id:
        from sincor2.launchpad_outreach import mark_contacted

        ok = mark_contacted(args.launchpad_id, status=args.status)
        print("ok" if ok else "failed")
        return

    path = write_batch_file(limit=5)
    print(f"Batch: {path}")
    for lp in due_outreach(limit=5):
        print(f"  • {lp['name']} — {lp.get('contact_method')}")
        if lp.get("mailto"):
            print(f"    mailto: {lp['mailto'][:80]}...")


if __name__ == "__main__":
    main()