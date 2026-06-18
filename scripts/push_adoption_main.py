#!/usr/bin/env python3
"""Push token adoption files to OrderofChaos33/SINCOR2 main via GitHub API."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from push_github_main import pat, put_file  # noqa: E402


def main() -> int:
    token = pat()
    files = [
        ("scripts/token_metadata.json", "Token metadata: 43 agents, adoption copy"),
        ("scripts/whitelist_token.py", "Whitelist orchestrator: bump cmd, Li.Fi + PR tracking"),
        ("scripts/register_blockscout_token.py", "Blockscout certification guide refresh"),
        ("static/tokenlists/sincor.tokenlist.json", "Token list v1.0.2 with logo mirror extension"),
        ("static/tokenlists/assets/logo-256.png", "Token list logo asset"),
        ("static/tokenlists/assets/logo.png", "Token list logo asset (png)"),
        ("static/tokenlists/assets/logo.svg", "Token list logo asset (svg)"),
        ("tokenlists/sincor.tokenlist.json", "Mirror token list package"),
        ("tokenlists/blockscout/SUBMIT.md", "Blockscout certification steps"),
        ("tokenlists/blockscout/status.json", "Blockscout API status snapshot"),
        ("src/sincor2/acceptance_status.py", "Acceptance API: Li.Fi, TKN, pending PRs"),
        ("templates/sinc_gateway.html", "SINC gateway: copy token list URL button"),
        ("templates/sinc_acceptance.html", "Acceptance hub: pending submission links"),
    ]
    for rel, msg in files:
        path = ROOT / rel
        if not path.exists():
            print("SKIP missing", rel)
            continue
        if rel.endswith(".png"):
            import base64
            from push_github_main import get_sha, headers
            import json
            import urllib.request

            sha = get_sha(token, rel)
            body = {
                "message": msg,
                "content": base64.b64encode(path.read_bytes()).decode(),
                "branch": "main",
            }
            if sha:
                body["sha"] = sha
            req = urllib.request.Request(
                f"https://api.github.com/repos/OrderofChaos33/SINCOR2/contents/{rel}",
                data=json.dumps(body).encode(),
                headers={**headers(token), "Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                url = json.loads(resp.read().decode())["commit"]["html_url"]
            print(rel, "->", url)
        else:
            text = path.read_text(encoding="utf-8")
            url = put_file(token, rel, text, msg)
            print(rel, "->", url)
    return 0


if __name__ == "__main__":
    sys.exit(main())