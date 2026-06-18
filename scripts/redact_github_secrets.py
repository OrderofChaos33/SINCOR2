#!/usr/bin/env python3
"""Redact hardcoded secrets from SINCOR2 files on GitHub main."""

from __future__ import annotations

import base64
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "OrderofChaos33/SINCOR2"


def pat() -> str:
    for line in (ROOT / "onchain/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith("GITHUB_PAT="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("GITHUB_PAT missing in onchain/.env")


def headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "sincor-secret-redact",
    }


def get_sha(token: str, path: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        headers=headers(token),
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())["sha"]


def put_file(token: str, path: str, text: str, message: str) -> str:
    sha = get_sha(token, path)
    body = json.dumps(
        {
            "message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode(),
            "branch": "main",
            "sha": sha,
        }
    ).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REPO}/contents/{path}",
        data=body,
        headers={**headers(token), "Content-Type": "application/json"},
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
        return data["commit"]["html_url"]


def main() -> int:
    token = pat()
    bak = ROOT / "sincor-clean.BAK.20251015-103920"

    synd = (bak / "code_pkg/start_syndicator.py").read_text(encoding="utf-8")
    synd = synd.replace(
        "        os.environ['GOOGLE_API_KEY'] = 'AIzaSyDa4P7-8LnWfq2GJl7BpKLBJvKfNOGRvck'  # From context",
        "        print('ERROR: Set GOOGLE_API_KEY in environment (never commit API keys)')\n        sys.exit(1)",
    )
    print("start_syndicator:", put_file(token, "code_pkg/start_syndicator.py", synd, "Remove hardcoded Google API key"))

    doc = (bak / "docs/CLINTON_AUTO_DETAILING_SETUP.md").read_text(encoding="utf-8")
    doc = doc.replace(
        'GOOGLE_PLACES_API_KEY="AIzaSyBOqhPHr7rA-pxzKdCFgR0zWbwQn1Ykh0I"',
        'GOOGLE_PLACES_API_KEY="YOUR_GOOGLE_PLACES_API_KEY"',
    )
    doc = doc.replace(
        'GOOGLE_API_KEY_2="AIzaSyBQrbndbuV4Bkfj01_n4HkqdiNS9-fb_fM"',
        'GOOGLE_API_KEY_2="YOUR_GOOGLE_API_KEY"',
    )
    print("clinton doc:", put_file(token, "docs/CLINTON_AUTO_DETAILING_SETUP.md", doc, "Redact Google API keys from setup doc"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())