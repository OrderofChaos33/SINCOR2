#!/usr/bin/env python3
"""Campaign ops — milestone check, announcement drafts, whale list."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from sincor2.launch_campaign import (
    campaign_status,
    init_campaign_db,
    run_campaign_ops,
    whale_capture_list,
)


def main() -> None:
    init_campaign_db()
    ops = run_campaign_ops()
    for d in ops.get("drafts", []):
        print(f"Milestone draft: {d['milestone_id']} -> {d['path']}")

    status = campaign_status()
    print(json.dumps(status, indent=2)[:4000])

    whales = whale_capture_list()
    if whales:
        print(f"\nWhales ({len(whales)}):")
        for w in whales[:10]:
            print(f"  {w['wallet'][:12]}… ${w['predeposit_usdc']}")


if __name__ == "__main__":
    main()