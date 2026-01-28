
import argparse, sqlite3, random, yaml, os

DEF_FRAMEWORKS = "training/ad_frameworks.yaml"
DEF_CORPUS     = "training/ad_corpus.yaml"

def load_yaml(path, required=True):
    if not os.path.exists(path):
        if required: raise SystemExit(f"Missing YAML: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def pick(lst, fallback):
    return random.choice(lst) if (isinstance(lst, list) and lst) else fallback

def query_snippet(db_path, q, k):
    try:
        con = sqlite3.connect(db_path); cur = con.cursor()
        cur.execute(
            "SELECT text FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
            (q, k),
        )
        rows = [r[0] for r in cur.fetchall()]
        con.close()
        return rows[0] if rows else ""
    except Exception:
        return ""

def storyboard_hbpc(fr, corpus, persona, url, phone, problem):
    hooks     = corpus.get("hooks", [])
    benefits  = corpus.get("benefits", [])
    proofs    = corpus.get("proof_snippets", corpus.get("proofs", []))
    ctas      = corpus.get("cta_lines", corpus.get("ctas", []))

    hook    = pick(hooks,    "Tired of pet hair and lingering odors?")
    benefit = pick(benefits, "Same-day interior reset that keeps your day moving.")
    proof   = pick(proofs,   "Rated 5★ — odor, pet hair, and stains removed fast.")
    cta     = pick(ctas,     f"Book now: {url} · {phone}").replace("{url}", url).replace("{phone}", phone)

    return f"""=== STORYBOARD: HBPC / {persona} ===

0-3s  [HOOK]  {hook}
3-9s  [BENEFIT]  {benefit}
9-15s  [PROOF]  {proof}
15-18s  [CTA]  {cta}
18-21s  [END CARD]  {url} · {phone}

--- Overlays / End-Card Rules ---
• large
• high contrast
• no cursive
• max 10 words per card

END CARD ≥ 3s  | required: phone, url, logo, service line

--- Checklist ---
□ Hook present in first 3s
□ One benefit in plain language
□ One proof element (demo/testimonial)
□ Single CTA with phone and/or URL
□ Phone+URL visible in final 3s
□ Text legible (big, high contrast)
□ No more than one idea per overlay
□ Persona tone respected

Shot ideas (pick 3–4):
• wipe down console 1s macro
• vacuum nozzle pass 1s
• steam burst on vent 1s
• odor neutralizer spritz 1s
• before/after seat split 2s
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frameworks", default=DEF_FRAMEWORKS)
    ap.add_argument("--corpus", default=DEF_CORPUS)
    ap.add_argument("--db", required=True)
    ap.add_argument("--q", required=True)
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--framework", choices=["HBPC"], default="HBPC")
    ap.add_argument("--persona", choices=["business_owner","senior","busy_parent"], default="business_owner")
    ap.add_argument("--phone", default="1-815-718-8936")
    ap.add_argument("--url", default="https://clintondetailing.com/booking")
    ap.add_argument("--problem", default="pet hair & odors")
    ap.add_argument("--out", help="Write storyboard to this file (UTF-8)")
    a = ap.parse_args()

    fr_yaml = load_yaml(a.frameworks, required=True)
    corpus  = load_yaml(a.corpus, required=True)
    _ = query_snippet(a.db, a.q, a.k)  # touch DB for context if needed (not used for PROOF)

    if a.framework != "HBPC":
        raise SystemExit("Only HBPC implemented in this minimal file.")

    out_text = storyboard_hbpc(fr_yaml, corpus, a.persona, a.url, a.phone, a.problem)

    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out_text)
    else:
        # Avoid Windows console encoding issues by replacing the star
        print(out_text.replace("★", "*"))

if __name__ == "__main__":
    main()
