# scripts_generate_storyboards.py
# Batch-generate multiple storyboards to UTF-8 files (no console encoding issues).
# Usage:
#   python scripts_generate_storyboards.py

import subprocess, pathlib, itertools

STORYBOARD = "scripts_make_storyboard.py"
DB = "data_detailing_fts.db"
OUTDIR = pathlib.Path("storyboards")
OUTDIR.mkdir(exist_ok=True)

personas = ["business_owner", "senior", "busy_parent"]
queries = {
    "business_owner": ["odor OR stains OR fleet", "work trucks OR downtime", "invoice OR predictable"],
    "senior": ["pickup OR gentle OR allergies", "no lifting OR return", "fresh OR odor free"],
    "busy_parent": ["family OR kids OR crumbs", "pet hair OR spills", "allergies OR smell"],
}

def run(persona, q, idx):
    outfile = OUTDIR / f"{persona}_{idx}.txt"
    cmd = [
        "python", STORYBOARD,
        "--db", DB,
        "--q", q,
        "--framework", "HBPC",
        "--persona", persona,
        "--out", str(outfile),
    ]
    subprocess.run(cmd, check=True)

def main():
    for persona in personas:
        for idx, q in enumerate(queries[persona], start=1):
            run(persona, q, idx)
    print(f"Wrote storyboards → {OUTDIR.resolve()}")

if __name__ == "__main__":
    main()
