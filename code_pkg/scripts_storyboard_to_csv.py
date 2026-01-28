# scripts_storyboard_to_csv.py
# Convert a storyboard .txt to a shot-list CSV (UTF-8).
# Usage:
#   python scripts_storyboard_to_csv.py --in storyboards/biz_1.txt --out shots/biz_1.csv
import argparse, csv, re, pathlib

LINE = re.compile(r"^(\d+)-(\d+)s\s+\[([A-Z ]+)\]\s+(.*)$")

def parse(in_path):
    rows = []
    for line in open(in_path, "r", encoding="utf-8"):
        m = LINE.match(line.strip())
        if not m: 
            continue
        start, end, label, text = m.groups()
        rows.append({
            "start_sec": int(start),
            "end_sec": int(end),
            "label": label.strip(),
            "overlay_text": text.strip()
        })
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", dest="outp", required=True)
    a = ap.parse_args()

    rows = parse(a.inp)
    pathlib.Path(a.outp).parent.mkdir(parents=True, exist_ok=True)
    with open(a.outp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["start_sec","end_sec","label","overlay_text"])
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows → {a.outp}")

if __name__ == "__main__":
    main()
