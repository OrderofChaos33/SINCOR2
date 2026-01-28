# scripts/ingest_detailing.py
# Local-only PDF → SQLite FTS5 (full-text) index for fast snippet retrieval.
# Usage:
#   python scripts/ingest_detailing.py ingest --pdf detailing_training.pdf --db data/detailing_fts.db
#   python scripts/ingest_detailing.py query  --db data/detailing_fts.db --q "headliner stains odor ozone"

import argparse, sqlite3, os, math
from pathlib import Path

CHUNK_WORDS = 220  # ~1–2 paragraphs; tweak if needed

def ensure_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def extract_pdf_text(pdf_path: Path) -> list[str]:
    try:
        import PyPDF2  # pip install PyPDF2
    except ImportError:
        raise SystemExit("Missing dependency: run `pip install PyPDF2`")
    text_pages = []
    with open(pdf_path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            text_pages.append(t.strip())
    return text_pages

def chunk_words(text: str, chunk_words: int = CHUNK_WORDS) -> list[str]:
    words = text.split()
    if not words:
        return []
    chunks, n = [], math.ceil(len(words) / chunk_words)
    for i in range(n):
        start, end = i * chunk_words, (i + 1) * chunk_words
        chunks.append(" ".join(words[start:end]))
    return chunks

def build_chunks(pages: list[str]) -> list[tuple[int, str]]:
    chunks = []
    for pi, page in enumerate(pages, start=1):
        for ci, chunk in enumerate(chunk_words(page), start=1):
            if chunk.strip():
                chunks.append((pi, chunk))
    return chunks

def init_db(db_path: Path):
    ensure_dir(db_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    # FTS5 table for ranking + a small meta table
    cur.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(page, text)")
    cur.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    con.commit()
    return con

def ingest(pdf: Path, db: Path):
    pages = extract_pdf_text(pdf)
    chunks = build_chunks(pages)
    con = init_db(db)
    cur = con.cursor()
    cur.execute("DELETE FROM chunks")
    cur.executemany("INSERT INTO chunks(page, text) VALUES (?, ?)", chunks)
    cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('source_pdf', ?)", (str(pdf),))
    cur.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('chunk_words', ?)", (str(CHUNK_WORDS),))
    con.commit()
    con.close()
    print(f"Ingested {len(chunks)} chunks from {pdf} → {db}")

def query(db: Path, q: str, k: int = 8):
    con = sqlite3.connect(db)
    cur = con.cursor()
    # Simple FTS5 ranking by bm25()
    cur.execute(
        "SELECT page, text, bm25(chunks) AS score FROM chunks WHERE chunks MATCH ? ORDER BY score LIMIT ?",
        (q, k),
    )
    rows = cur.fetchall()
    con.close()
    for i, (page, text, score) in enumerate(rows, 1):
        print(f"\n[{i}] page={page} score={score:.3f}\n{text[:600]}...")
    if not rows:
        print("No matches.")

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_ing = sub.add_parser("ingest")
    ap_ing.add_argument("--pdf", required=True)
    ap_ing.add_argument("--db", required=True)

    ap_q = sub.add_parser("query")
    ap_q.add_argument("--db", required=True)
    ap_q.add_argument("--q", required=True)
    ap_q.add_argument("--k", type=int, default=8)

    args = ap.parse_args()
    if args.cmd == "ingest":
        ingest(Path(args.pdf), Path(args.db))
    else:
        query(Path(args.db), args.q, args.k)

if __name__ == "__main__":
    main()
