#!/usr/bin/env python3
"""Diagnose why SINC may show as suspicious in wallets and list fix steps."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sincor2.token_security import diagnose  # noqa: E402


def main() -> int:
    report = diagnose()
    print(json.dumps(report, indent=2))
    print()
    if report["verdict"] == "ok":
        print("OK: no suspicious UI signals detected.")
        return 0
    print(f"Verdict: {report['verdict']}")
    print("Why wallets/scanners may flag SINC:")
    for r in report["reasons"]:
        print(f"  - {r}")
    print("\nFix (in order):")
    for a in report["actions"]:
        who = f"[{a['who']}] " if a.get("who") else ""
        print(f"  {a['priority']}. {who}{a['title']}")
        print(f"     {a['url']}")
        if a.get("wallet"):
            print(f"     wallet: {a['wallet']}")
        if a.get("detail"):
            print(f"     {a['detail']}")
    return 0 if report["contract_clean"] else 1


if __name__ == "__main__":
    sys.exit(main())