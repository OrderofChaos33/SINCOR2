#!/usr/bin/env python3
"""CLI entry for daily safe ops."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sincor2.daily_ops import run_daily


def main() -> None:
    p = argparse.ArgumentParser(description="SINCOR daily safe ops")
    p.add_argument("--buyers", action="store_true", help="Include full curve buyer scan")
    args = p.parse_args()
    report = run_daily(include_buyers=args.buyers)
    print(json.dumps(report, indent=2))
    if report.get("wallet_watch", {}).get("alerts"):
        print("\nWALLET ALERTS:", report["wallet_watch"]["alerts"])
    if report["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()