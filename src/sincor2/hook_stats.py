"""Live hook + curve stats for gateway and acceptance APIs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from launch_content_engine.onchain_stats import fetch_stats  # noqa: E402

MAINNET = {
    "hook": "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0",
    "router": "0x11b86E85cC5170F4165c89ccb11332133B29E283",
    "usdc": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "curve": "0x75dE341a2BC81806198364F125d4Cde36527619C",
    "sinc": "0x9C8cd8d3961F445D653713dE65C6578bE11668e7",
    "explorer": "https://basescan.org",
}

SEPOLIA = {
    "hook": os.environ.get("BASE_SEPOLIA_SHARED_LIQUIDITY_HOOK", MAINNET["hook"]),
    "router": os.environ.get("BASE_SEPOLIA_SINC_SWAP_ROUTER", MAINNET["router"]),
    "usdc": os.environ.get("BASE_SEPOLIA_USDC", MAINNET["usdc"]),
    "curve": os.environ.get("BASE_SEPOLIA_SINC_CURVE", MAINNET["curve"]),
    "sinc": os.environ.get("BASE_SEPOLIA_SINC", MAINNET["sinc"]),
    "explorer": "https://sepolia.basescan.org",
}
GRADUATION_ETH = 0.5


def fetch_hook_status(chain_id: int | None = None) -> dict:
    active_chain_id = chain_id if chain_id is not None else int(os.environ.get("HOOK_CHAIN_ID", "8453"))
    active = SEPOLIA if active_chain_id == 84532 else MAINNET
    base = fetch_stats()
    eth = base.get("curve_eth_accumulated", 0.0)
    grad_pct = min(100.0, round(eth / GRADUATION_ETH * 100, 2)) if GRADUATION_ETH else 0.0
    return {
        **base,
        "chain_id": active_chain_id,
        "hook_address": active["hook"],
        "router_address": active["router"],
        "usdc_address": active["usdc"],
        "curve_address": active["curve"],
        "sinc_address": active["sinc"],
        "graduation_eth_target": GRADUATION_ETH,
        "graduation_pct": grad_pct,
        "discovery_ramp": {"enabled": False, "note": "Sub-floor discovery ramp closed — cancel on-chain if still live"},
        "floor_ladder_usd": base.get("official_floor_usd", 1.50),
        "minimum_buy_usd": base.get("official_floor_usd", 1.50),
        "buy_paths": {
            "eth_curve": "https://getsincor.com/sinc#buy-eth",
            "usdc_hook": "https://getsincor.com/sinc#buy-usdc",
            "referral": "https://getsincor.com/refer",
        },
        "token_list_url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
        "basescan": {
            "sinc": f"{active['explorer']}/token/{active['sinc']}",
            "hook": f"{active['explorer']}/address/{active['hook']}",
            "router": f"{active['explorer']}/address/{active['router']}",
            "curve": f"{active['explorer']}/address/{active['curve']}",
        },
    }
