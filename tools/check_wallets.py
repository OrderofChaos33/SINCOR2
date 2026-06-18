#!/usr/bin/env python3
"""Quick wallet / signup audit for SINC user lists."""
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

WALLET_RE = re.compile(r"0x[a-fA-F0-9]{40}")

paths = {
    "waitlist_db": Path(r"C:\Users\cjay4\OneDrive\Desktop\sincor-clean\data\waitlist.db"),
    "orders_db": Path(r"C:\Users\cjay4\OneDrive\Desktop\sincor-clean\orders.db"),
    "leads_json": Path(r"C:\Users\cjay4\OneDrive\Desktop\sincor-clean\data\customer_acquisition\leads.json"),
    "wallets_csv": Path(r"C:\Users\cjay4\OneDrive\Desktop\-wallets.csv"),
    "flask_log": Path(r"C:\Users\cjay4\OneDrive\Desktop\sincor-clean\flask_final.log"),
}


def scan_wallets(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [m.group(0).lower() for m in WALLET_RE.finditer(text)]


def main() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== wallet audit ({today}) ===\n")

    db = paths["waitlist_db"]
    if db.exists():
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        print(f"waitlist.db tables: {tables}")
        if "waitlist" in tables:
            cur.execute("SELECT COUNT(*) FROM waitlist")
            total = cur.fetchone()[0]
            print(f"  total signups: {total}")
            for day in ("2026-06-13", "2026-06-12", "2026-06-11"):
                cur.execute("SELECT COUNT(*) FROM waitlist WHERE signup_date LIKE ?", (f"{day}%",))
                print(f"  signups {day}: {cur.fetchone()[0]}")
            cur.execute(
                "SELECT product_interest, COUNT(*) FROM waitlist GROUP BY product_interest ORDER BY 2 DESC LIMIT 15"
            )
            print("  product_interest:", cur.fetchall())
            cur.execute("PRAGMA table_info(waitlist)")
            print("  columns:", [c[1] for c in cur.fetchall()])
        conn.close()
    else:
        print("waitlist.db: missing")

    for label, p in [("leads.json", paths["leads_json"]), ("wallets.csv", paths["wallets_csv"])]:
        wallets = scan_wallets(p)
        print(f"\n{label}: {len(wallets)} wallet addresses")
        if wallets:
            print(f"  sample: {wallets[:3]}")

    log = paths["flask_log"]
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="ignore").splitlines()
        airdrop = [ln for ln in lines if "[AIRDROP]" in ln]
        print(f"\nflask_final.log AIRDROP lines: {len(airdrop)}")
        for ln in airdrop[-5:]:
            print(f"  {ln[:160]}")


if __name__ == "__main__":
    main()