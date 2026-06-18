#!/usr/bin/env python3
"""CLI: value creation audit + social share packs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sincor2.value_engine import fetch_value_summary, run_value_ops, social_pack


def main() -> None:
    p = argparse.ArgumentParser(description="SINCOR value creation ops")
    p.add_argument("--audit", action="store_true", help="Print live revenue stream summary")
    p.add_argument("--social", action="store_true", help="Print share pack with live stats")
    p.add_argument("--wallet", default="", help="Wallet for personalized referral link")
    p.add_argument("--save", action="store_true", help="Write report to logs/value/")
    args = p.parse_args()

    if args.save or (not args.audit and not args.social):
        report = run_value_ops()
        print(json.dumps({"report_path": report["report_path"]}, indent=2))

    if args.audit or (not args.social and not args.save):
        summary = fetch_value_summary()
        print("\n=== VALUE STREAMS (live) ===")
        for s in summary.get("streams", []):
            print(f"\n[{s['status'].upper()}] {s['title']}")
            print(f"  {s.get('note', '')}")
            if s.get("url"):
                print(f"  {s['url']}")
        print("\n--- metrics ---")
        keys = (
            "curve_eth_accumulated",
            "graduation_pct",
            "sinc_sold_m",
            "sinc_in_curve_m",
            "sinc_in_hook_pm_m",
            "treasury_sinc",
        )
        for k in keys:
            if k in summary:
                print(f"  {k}: {summary[k]}")
        print(f"\n  earn hub: {summary.get('earn_url')}")

    if args.social:
        w = args.wallet.strip() or None
        pack = social_pack(w)
        print("\n=== SOCIAL PACK ===\n")
        print(pack.get("twitter", ""))
        print("\n--- referral ---")
        print(pack.get("referral_link", ""))


if __name__ == "__main__":
    main()