#!/usr/bin/env python3
"""Check Blockscout token status and emit registration steps for SINC."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
BLOCKSCOUT_API = f"https://base.blockscout.com/api/v2/addresses/{SINC}"
OUT = ROOT / "tokenlists" / "blockscout"


def fetch(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SINCOR-blockscout/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def main() -> int:
    data = fetch(BLOCKSCOUT_API)
    if not data:
        print("ERROR: could not reach Blockscout API")
        return 1

    token = data.get("token") or {}
    status = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "address": SINC,
        "reputation": data.get("reputation"),
        "is_scam": data.get("is_scam"),
        "is_verified": data.get("is_verified"),
        "certified": token.get("certified") if isinstance(token, dict) else None,
        "icon_url": token.get("icon_url") if isinstance(token, dict) else None,
        "creator": data.get("creator_address_hash"),
    }

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "status.json").write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    meta_path = ROOT / "scripts" / "token_metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

    submission = {
        "chain": "Base",
        "chain_id": 8453,
        "contract": SINC,
        "name": meta.get("name", "SINC"),
        "symbol": meta.get("symbol", "SINC"),
        "decimals": meta.get("decimals", 8),
        "website": meta.get("website", "https://getsincor.com"),
        "logo_url": "https://getsincor.com/static/tokenlists/assets/logo-256.png",
        "token_list": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
        "metadata_url": "https://getsincor.com/.well-known/sinc-token.json",
        "description": meta.get("descriptionShort", ""),
        "twitter": meta.get("twitter", ""),
        "deployer_to_verify": status.get("creator"),
        "steps": [
            "1. Deploy latest getsincor.com (token list + metadata routes must return 200).",
            "2. Blockscout → My Account → Verified addresses → Add address → sign as deployer.",
            "3. Update token icon URL to https://getsincor.com/static/tokenlists/assets/logo-256.png",
            "4. Submit public tag: https://base.blockscout.com/public-tags/submit",
            "5. Register on TKN: https://tkn.xyz/token/base/" + SINC,
            "6. Open Superchain PR: python scripts/whitelist_token.py launch (or submit_superchain_pr.ps1)",
        ],
    }
    (OUT / "SUBMIT.md").write_text(
        "\n".join(
            [
                "# Blockscout — clear unverified / suspicious UI for SINC",
                "",
                f"Checked: {status['checked_at']}",
                "",
                "## Current API status",
                f"- reputation: `{status.get('reputation')}`",
                f"- is_scam: `{status.get('is_scam')}`",
                f"- contract verified: `{status.get('is_verified')}`",
                f"- certified: `{status.get('certified')}`",
                f"- icon_url: `{status.get('icon_url')}`",
                "",
                "Blockscout shows **suspicious / unverified** when `certified` is false and `icon_url` is null.",
                "Contract is verified on-chain; you need **explorer certification** (steps below).",
                "",
                "## Fix (in order)",
                "",
            ]
            + [f"{s}" for s in submission["steps"]]
            + [
                "",
                "## Deployer wallet (verify ownership in Blockscout)",
                f"`{status.get('creator')}`",
                "",
                "## Paste into Blockscout token update",
                f"- Logo: `{submission['logo_url']}`",
                f"- Website: `{submission['website']}`",
                f"- Token list: `{submission['token_list']}`",
            ]
        ),
        encoding="utf-8",
    )
    (OUT / "submission.json").write_text(json.dumps(submission, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(status, indent=2))
    print()
    if status.get("is_scam"):
        print("WARNING: Blockscout has is_scam=true — open a support ticket with contract proof.")
    elif not status.get("certified"):
        print("Action: certify on Blockscout (deployer signs) + submit public tag + TKN registration.")
        print(f"Guide written: {OUT / 'SUBMIT.md'}")
    else:
        print("OK: token is Blockscout-certified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())