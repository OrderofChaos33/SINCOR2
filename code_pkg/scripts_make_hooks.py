# scripts_make_hooks.py
# Query local FTS DB and emit ad-ready hooks for Business Owners + Seniors.
# Usage:
#   python scripts_make_hooks.py --db data_detailing_fts.db --q "odor removal pet hair interior" --k 6

import argparse, sqlite3, textwrap

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

def compress(t, n=180):
    t = " ".join(t.split())
    return (t[: n - 1] + "…") if len(t) > n else t

def make_hooks(snips, city, phone, url):
    T = [
        ("[Biz]", "Keep trucks earning. Same-day interior resets. {core} Book now: {url} · {phone}"),
        ("[Biz]", "Clean cabs = safer crews. We handle stains/odor fast. {core} → {url} · {phone}"),
        ("[Senior]", "We pick up, clean, and return. No lifting, no waiting. {core} Call {phone}"),
        ("[Senior]", "Allergens, pet hair, spills—gone. Gentle products, careful hands. {core} Call {phone}")
    ]
    hooks = []
    for page, txt, _ in snips[:6]:
        core = (" ".join(txt.split()))[:140] + ("…" if len(txt) > 140 else "")
        for tag, tmpl in T[:2]:  # 2 Biz
            hooks.append(f"{tag} " + tmpl.format(core=core, url=url, phone=phone))
        for tag, tmpl in T[2:4]: # 2 Senior
            hooks.append(f"{tag} " + tmpl.format(core=core, url=url, phone=phone))
    return hooks[:8]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--q", required=True)
    ap.add_argument("--k", type=int, default=6)
    ap.add_argument("--city", default="Clinton, IA")
    ap.add_argument("--phone", default="+1 (563) 555-1234")
    ap.add_argument("--url", default="https://clintondetailing.com/book")
    a = ap.parse_args()

    snips = top_chunks(a.db, a.q, a.k)
    if not snips:
        print("No matches.")
        return
    hooks = make_hooks(snips, a.city, a.phone, a.url)
    print("\n".join(hooks))

if __name__ == "__main__":
    main()
