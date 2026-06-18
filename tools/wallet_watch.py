#!/usr/bin/env python3
"""Watch flagged wallets for balance drift (compromised / abandoned EOAs)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "logs" / "ops" / "wallet_watch_state.json"
SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"

DEFAULT_WATCH = [
    ("creator", "0x35cb3bf1b29F81d325EB9A7225a3E87fE7B37D6f"),
    ("treasury_af9b", "0xAf9B539D8043C634b7E611818518BA7E850F289e"),
    ("safe_ops", "0x2d61752adF5092052Ff7D366a9884823C07Cdaf8"),
]

RPC_CANDIDATES = [
    os.environ.get("BASE_RPC_URL", ""),
    "https://mainnet.base.org",
    "https://base-rpc.publicnode.com",
]
RPC_CANDIDATES = [u for u in RPC_CANDIDATES if u]


def _rpc(method: str, params: list) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "SINCOR-wallet-watch/1.0"}
    last_err: Exception | None = None
    for rpc in RPC_CANDIDATES:
        req = urllib.request.Request(rpc, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            if "error" in data:
                raise RuntimeError(data["error"])
            return data["result"]
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, TimeoutError) as e:
            last_err = e
    raise RuntimeError(f"RPC failed: {last_err}")


def _balance_of(token: str, wallet: str) -> int:
    w = wallet.lower().replace("0x", "").zfill(64)
    data = "0x70a08231" + w
    return int(_rpc("eth_call", [{"to": token, "data": data}, "latest"]), 16)


def _eth_balance(wallet: str) -> int:
    return int(_rpc("eth_getBalance", [wallet, "latest"]), 16)


def _load_watch() -> list[tuple[str, str]]:
    extra = os.environ.get("WALLET_WATCH_LIST", "")
    rows = list(DEFAULT_WATCH)
    for part in extra.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        label, addr = part.split(":", 1)
        rows.append((label.strip(), addr.strip()))
    return rows


def run_watch() -> dict:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if STATE_PATH.exists():
        prev = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    alerts: list[str] = []
    snapshot: dict[str, dict] = {}
    now = datetime.now(timezone.utc).isoformat()

    for label, addr in _load_watch():
        addr_l = addr.lower()
        eth = _eth_balance(addr)
        sinc = _balance_of(SINC, addr)
        row = {"eth_wei": eth, "sinc_raw": sinc, "checked_at": now}
        snapshot[label] = {"address": addr, **row}

        old = prev.get(label, {})
        if old:
            if old.get("eth_wei", 0) != eth:
                alerts.append(f"{label} ETH balance changed")
            if old.get("sinc_raw", 0) != sinc and sinc < old.get("sinc_raw", 0):
                alerts.append(f"{label} SINC decreased ({old.get('sinc_raw')} -> {sinc})")

    out = {"checked_at": now, "wallets": snapshot, "alerts": alerts}
    STATE_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> None:
    result = run_watch()
    print(json.dumps(result, indent=2))
    if result["alerts"]:
        print("\nALERTS:")
        for a in result["alerts"]:
            print(f"  - {a}")


if __name__ == "__main__":
    main()