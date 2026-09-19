#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sincor2.genesis.store import connect, verified_tranche

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=os.environ.get("DB_PATH", str(ROOT / "data" / "orders.db")))
    p.add_argument("--out", default=str(ROOT / "data" / "genesis_tranche.csv"))
    p.add_argument("--amount", default="100")
    args = p.parse_args()
    rows = verified_tranche(connect(args.db))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["address", "amount"])
        for row in rows:
            w.writerow([row["wallet"], args.amount])
    print(f"wrote {out} rows={len(rows)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
