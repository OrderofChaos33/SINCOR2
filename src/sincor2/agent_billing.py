"""Record SINC platform revenue and burn statistics (spec §5.3)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sincor2.platform_payments import SINC, TREASURY, _rpc_call, atomic_to_display

BURN_ADDRESS = "0x000000000000000000000000000000000000dEaD"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _log_path() -> Path:
    from sincor2.data_paths import agent_burn_log_path

    return agent_burn_log_path()


def record_platform_payment(
    *,
    tx_hash: str,
    payer_wallet: str,
    token: str,
    amount_atomic: int,
    product_name: str = "",
    plan_id: str = "",
    payment_id: str = "",
) -> dict[str, Any]:
    """Append treasury payment to public burn/revenue log."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": "platform_payment",
        "tx_hash": tx_hash,
        "payer": payer_wallet.lower(),
        "token": token.upper(),
        "amount_atomic": amount_atomic,
        "amount_display": atomic_to_display(amount_atomic, token),
        "treasury": TREASURY.lower(),
        "product_name": product_name,
        "plan_id": plan_id,
        "payment_id": payment_id,
        "burn_policy": "50% ops retention / 50% burn at treasury discretion",
        "burn_tx": None,
    }

    if token.upper() == "SINC" and os.environ.get("AGENT_BURN_AUTO", "false").lower() == "true":
        burn_result = _attempt_auto_burn(amount_atomic, tx_hash)
        entry["burn_attempt"] = burn_result

    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return entry


def _attempt_auto_burn(amount_atomic: int, source_tx: str) -> dict[str, Any]:
    """Optional forwarder burn — only when BILLING_FORWARDER_PRIVATE_KEY is set."""
    key = os.environ.get("BILLING_FORWARDER_PRIVATE_KEY", "").strip()
    if not key:
        return {"ok": False, "skipped": True, "reason": "no_forwarder_key"}

    # Signing requires web3/eth-account — not in requirements.txt; log intent only.
    return {
        "ok": False,
        "skipped": True,
        "reason": "forwarder_signing_not_deployed",
        "note": "Set AGENT_BURN_AUTO=false until forwarder wallet script is run locally",
        "would_burn_atomic": amount_atomic // 2,
        "source_tx": source_tx,
    }


def _chain_burn_total() -> float:
    """Sum SINC sent to dead address from treasury (on-chain audit)."""
    try:
        latest_hex = _rpc_call("eth_blockNumber", [])
        latest = int(latest_hex, 16)
        from_block = max(0, latest - 500_000)
        logs = _rpc_call(
            "eth_getLogs",
            [
                {
                    "fromBlock": hex(from_block),
                    "toBlock": "latest",
                    "address": SINC,
                    "topics": [
                        TRANSFER_TOPIC,
                        f"0x{'0' * 24}{TREASURY.lower().replace('0x', '')}",
                        f"0x{'0' * 24}{BURN_ADDRESS.lower().replace('0x', '')}",
                    ],
                }
            ],
        )
        total = 0
        for log in logs or []:
            total += int(log.get("data", "0x0"), 16)
        return atomic_to_display(total, "SINC")
    except Exception:
        return 0.0


def fetch_burn_stats() -> dict[str, Any]:
    log_file = _log_path()
    payments: list[dict[str, Any]] = []
    sinc_volume = 0.0
    axm_volume = 0.0
    if log_file.is_file():
        for line in log_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            payments.append(row)
            tok = (row.get("token") or "").upper()
            amt = float(row.get("amount_display") or 0)
            if tok == "SINC":
                sinc_volume += amt
            elif tok == "AXM":
                axm_volume += amt

    return {
        "treasury": TREASURY,
        "sinc_token": SINC,
        "log_path": str(log_file.relative_to(_root())) if log_file.is_file() else None,
        "platform_payments_count": len(payments),
        "sinc_platform_volume": round(sinc_volume, 4),
        "axm_platform_volume": round(axm_volume, 4),
        "on_chain_treasury_burn_sinc": _chain_burn_total(),
        "recent": payments[-10:],
        "burn_address": BURN_ADDRESS,
    }