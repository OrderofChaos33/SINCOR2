#!/usr/bin/env python3
import sqlite3
from pathlib import Path

p = Path(r"C:\Users\cjay4\OneDrive\Desktop\sincor-clean\orders.db")
conn = sqlite3.connect(p)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("tables:", tables)
for t in tables:
    cur.execute(f"PRAGMA table_info({t})")
    cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) FROM {t}")
    print(f"{t}: {cur.fetchone()[0]} rows")
    print("  cols:", cols)
conn.close()