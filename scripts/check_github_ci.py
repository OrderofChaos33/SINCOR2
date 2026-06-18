#!/usr/bin/env python3
import json
import urllib.request
from pathlib import Path

pat = [l.split("=", 1)[1].strip() for l in Path("onchain/.env").read_text().splitlines() if l.startswith("GITHUB_PAT=")][0]
h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}

runs = json.load(
    urllib.request.urlopen(
        urllib.request.Request(
            "https://api.github.com/repos/OrderofChaos33/SINCOR2/actions/runs?per_page=8",
            headers=h,
        )
    )
)["workflow_runs"]

print("Recent runs:")
for r in runs:
    print(f"  {r['name']:18} {r['conclusion'] or r['status']:10} {r['created_at'][:16]} ({r.get('run_attempt')}x)")

ci = next(r for r in runs if r["name"] == "CI")
jobs = json.load(
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://api.github.com/repos/OrderofChaos33/SINCOR2/actions/runs/{ci['id']}/jobs",
            headers=h,
        )
    )
)["jobs"]
print(f"\nLatest CI run {ci['id']}:")
for j in jobs:
    print(f"  job {j['name']}: {j['conclusion']}")
    for s in j.get("steps", []):
        if s.get("conclusion") == "failure":
            print(f"    FAIL step: {s.get('name')}")

crs = json.load(
    urllib.request.urlopen(
        urllib.request.Request(
            f"https://api.github.com/repos/OrderofChaos33/SINCOR2/commits/{ci['head_sha']}/check-runs",
            headers=h,
        )
    )
)["check_runs"]
for cr in crs:
    out = cr.get("output") or {}
    print(f"\ncheck-run: {cr['name']} -> {cr['conclusion']}")
    for key in ("title", "summary", "text"):
        val = out.get(key)
        if val:
            print(f"  {key}: {val[:500]}")

# billing/spending limit hint
user = json.load(
    urllib.request.urlopen(
        urllib.request.Request("https://api.github.com/user", headers=h)
    )
)
print(f"\nAuthenticated as: {user.get('login')}")