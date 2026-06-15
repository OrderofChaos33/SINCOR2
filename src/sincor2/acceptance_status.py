"""Wallet acceptance + whitelist listing status."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
TOKEN_LIST = "https://getsincor.com/tokenlists/sincor.tokenlist.json"


def _http_get(url: str, timeout: int = 25) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SINCOR-acceptance/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None


def _listed(url: str, addr: str) -> bool:
    body = _http_get(url)
    return bool(body and addr.lower() in body.lower())


def _blockscout_certified(addr: str) -> bool:
    body = _http_get(f"https://base.blockscout.com/api/v2/addresses/{addr}")
    return bool(body and '"certified":true' in body.replace(" ", ""))


def fetch_whitelist_status() -> list[dict]:
    addr = SINC.lower()
    sc_listed = _listed(
        "https://raw.githubusercontent.com/ethereum-optimism/ethereum-optimism.github.io/master/data/SINC/data.json", addr
    )
    rows = [
        ("Self-hosted token list", TOKEN_LIST, _http_get(TOKEN_LIST) is not None),
        ("Balancer", "balancer/tokenlists base.ts", _listed(
            "https://raw.githubusercontent.com/balancer/tokenlists/main/src/tokenlists/balancer/tokens/base.ts", addr
        )),
        ("Superchain / Coinbase Wallet", "PR #1329 open" if not sc_listed else "ethereum-optimism.github.io", sc_listed),
        ("Trust Wallet", "trustwallet/assets", _listed(
            f"https://raw.githubusercontent.com/trustwallet/assets/master/blockchains/base/assets/{SINC}/info.json", addr
        )),
        ("Rainbow", "rainbow metadata", _listed(
            "https://metadata.p.rainbow.me/token-list/rainbow-token-list.json", addr
        )),
        ("CoW Swap", "issue #1444 open", _listed(
            "https://raw.githubusercontent.com/cowprotocol/token-lists/main/src/public/CowSwap.json", addr
        )),
        ("Li.Fi Base", "customized-token-list BAS.json", _listed(
            "https://raw.githubusercontent.com/lifinance/customized-token-list/main/tokens/BAS.json", addr
        )),
        ("CoinGecko", "api contract", bool(_http_get(
            f"https://api.coingecko.com/api/v3/coins/base/contract/{addr}"
        ) and '"id"' in (_http_get(f"https://api.coingecko.com/api/v3/coins/base/contract/{addr}") or ""))),
        ("Blockscout certified", "verify deployer + TKN", _blockscout_certified(addr)),
        ("TKN registry", "tkn.xyz", bool(_http_get(f"https://tkn.xyz/token/base/{SINC}") and "SINCOR" in (_http_get(f"https://tkn.xyz/token/base/{SINC}") or ""))),
    ]
    return [{"name": n, "ref": r, "listed": ok} for n, r, ok in rows]


def fetch_acceptance() -> dict:
    listings = fetch_whitelist_status()
    listed_count = sum(1 for x in listings if x["listed"])
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "token": SINC,
        "chain_id": 8453,
        "token_list_url": TOKEN_LIST,
        "logo_url": "https://getsincor.com/static/tokenlists/assets/logo-256.png",
        "blockscout_url": f"https://base.blockscout.com/token/{SINC}",
        "blockscout_certify": "https://base.blockscout.com/my-account/verified-addresses",
        "listings": listings,
        "listed_count": listed_count,
        "listing_total": len(listings),
        "wallet_import": {
            "metamask": f"Settings → Token lists → Add → paste URL: {TOKEN_LIST} (or use Add SINC button on /sinc)",
            "rabby": f"Settings → Token list → Add custom list → {TOKEN_LIST}",
            "coinbase_wallet": "Pending Superchain PR #1329 — import contract " + SINC + " or token list URL above",
            "rainbow": "Add token → paste contract " + SINC + " on Base (8 decimals)",
        },
        "pending_submissions": [
            {"name": "Superchain PR", "url": "https://github.com/ethereum-optimism/ethereum-optimism.github.io/pull/1329"},
            {"name": "CoW Swap", "url": "https://github.com/cowprotocol/token-lists/issues/1444"},
            {"name": "Blockscout certify", "url": "https://base.blockscout.com/my-account/verified-addresses"},
            {"name": "TKN registry", "url": f"https://tkn.xyz/token/base/{SINC}"},
        ],
        "pr_packages": str(_ROOT / "tokenlists" / "pr-packages"),
        "prepare_cmd": "python scripts/whitelist_token.py prepare",
        "launch_cmd": "python scripts/whitelist_token.py launch",
    }


def run_whitelist_prepare() -> dict:
    script = _ROOT / "scripts" / "whitelist_token.py"
    proc = subprocess.run(
        [sys.executable, str(script), "prepare"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return {"ok": proc.returncode == 0, "stdout": proc.stdout[-1500:], "stderr": proc.stderr[-500:]}