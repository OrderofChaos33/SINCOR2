#!/usr/bin/env python3
"""Seed the CHROMA shop dashboard with a sale-ready demo state.

  PYTHONPATH=. python scripts/chroma_seed_demo.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from verticals.auto_detailing.seed import seed  # noqa: E402
from verticals.auto_detailing.store import get_store, reset_store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed CHROMA demo state")
    parser.add_argument("--db", default=None, help="SQLite path (default CHROMA_DB_PATH)")
    parser.add_argument("--keep", action="store_true", help="Don't wipe existing rows")
    args = parser.parse_args()
    store = reset_store(args.db) if args.db else get_store()
    result = seed(store=store, reset=not args.keep)
    print(
        f"CHROMA demo seeded: {result['leads']} leads, "
        f"{result['quotes']} quotes, {result['bookings']} bookings, "
        f"{result['pending_sends']} waiting on you to send."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
