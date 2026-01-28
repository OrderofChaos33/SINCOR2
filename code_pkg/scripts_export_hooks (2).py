# scripts_export_hooks.py
# Export ad hooks to CSV from the local FTS DB.
# Usage:
#   python scripts_export_hooks.py --db data_detailing_fts.db --q "pet OR hair OR odor" --out hooks.csv

import argparse, sqlite3, csv

def top_chunks(db, q, k):
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute(
        "SELECT page, text, bm25(chunks) AS s FROM chunks WHERE chunks MATCH ? ORDER BY s LIMIT ?",
        (q, k),
    )
    rows = cur.fetchall()
    con.close()
    return rows

def make_hooks(snips, phone, url):
    T = [
        ("[Biz]", "Keep trucks earning. Same-day interior resets. {core} Book now: {url} · {phone}"),
        ("[Biz]", "Clean cabs = safer crews. We handle stains/odor fast. {core} → {url} · {phone}"),
        ("[Senior]", "We pick up, clean, and return. No lifting, no waiting. {core} Call {phone}"),
        ("[Senior]", "Allergens, pet hair, spills—gone. Gentle products, careful hands. {core} Call {phone}")
    ]
    hooks = []
    for page, txt, _ in snips[:6]:
        core = (" ".join(txt.split()))[:140] + ("…" if len(txt) > 140 else "")
        for tag, tmpl in T:
            hooks.append([tag, tmpl.format(core=core, url=url, phone=phone)])
    return hooks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--q", required=True)
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--out", default="hooks.csv")
    ap.add_argument("--phone", default="+1 (563) 555-1234")
    ap.add_argument("--url", default="https://clintondetailing.com/book")
    a = ap.parse_args()

    snips = top_chunks(a.db, a.q, a.k)
    if not snips:
        print("No matches.")
        return

    rows = make_hooks(snips, a.phone, a.url)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["persona", "text"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} hooks → {a.out}")

if __name__ == "__main__":
    main()
