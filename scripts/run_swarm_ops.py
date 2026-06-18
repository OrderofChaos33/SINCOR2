#!/usr/bin/env python3
"""Run one operational swarm cycle locally (5 agents, real tasks)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sincor2.grant_targets import borrow_reality_check, list_grant_targets
from sincor2.swarm_ops import run_swarm_cycle


def main() -> int:
    print("=== SINCOR Swarm Ops Cycle ===\n")
    summary = run_swarm_cycle()
    print(json.dumps(summary, indent=2))
    print("\n=== Grant targets ===")
    for g in list_grant_targets()[:3]:
        print(f"  • {g['name']}: {g['url']}")
    print("\n=== Borrow reality ===")
    print(json.dumps(borrow_reality_check(), indent=2))
    print(f"\nReview drafts: https://getsincor.com/launch/review")
    return 0 if summary.get("agents_ok", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())