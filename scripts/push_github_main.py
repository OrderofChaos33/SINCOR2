#!/usr/bin/env python3
"""Push selected files to OrderofChaos33/SINCOR2 main via GitHub API."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "OrderofChaos33/SINCOR2"


def pat() -> str:
    for line in (ROOT / "onchain/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_PAT="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("GITHUB_PAT missing")


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "sincor-push",
    }


def get_sha(token: str, path: str) -> str | None:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=headers(token),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def put_file(token: str, path: str, text: str, message: str) -> str:
    sha = get_sha(token, path)
    body: dict = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "branch": "main",
    }
    if sha:
        body["sha"] = sha
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        data=data,
        headers={**headers(token), "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())["commit"]["html_url"]


def resolve_alert(token: str, num: int) -> None:
    body = json.dumps({"state": "resolved", "resolution": "revoked"}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/secret-scanning/alerts/{num}",
        data=body,
        headers={**headers(token), "Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
        print(f"alert #{num}: {data.get('state')} ({data.get('resolution')})")


def main() -> int:
    token = pat()
    files = [
        ("src/sincor2/app.py", "Fix ruff lint in app.py"),
        ("src/sincor2/mvp_app.py", "Fix ruff lint in mvp_app.py"),
        ("templates/home.html", "Enrich homepage with TOA, SINAX, and platform docs"),
        (".gitleaks.toml", "Allowlist remediated historical secret paths"),
        (".github/workflows/security.yml", "Wire gitleaks config for CI"),
        (".github/workflows/ci.yml", "Sync CI workflow"),
    ]
    for rel, msg in files:
        path = ROOT / rel.replace("/", "\\") if "\\" in str(ROOT) else ROOT / rel
        if not path.exists():
            print("SKIP missing", rel)
            continue
        text = path.read_text(encoding="utf-8")
        url = put_file(token, rel.replace("\\", "/"), text, msg)
        print(rel, "->", url)

    print("\nResolving secret-scanning alerts...")
    for n in (1, 2, 3, 4):
        try:
            resolve_alert(token, n)
        except Exception as e:
            print(f"alert #{n} failed:", e)

    return 0


if __name__ == "__main__":
    sys.exit(main())