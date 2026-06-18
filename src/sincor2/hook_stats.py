"""Live hook + curve stats for gateway and acceptance APIs."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from launch_content_engine.onchain_stats import fetch_stats  # noqa: E402

HOOK = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"
ROUTER = "0x11b86E85cC5170F4165c89ccb11332133B29E283"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
GRADUATION_ETH = 0.5


def fetch_hook_status() -> dict:
    base = fetch_stats()
    eth = base.get("curve_eth_accumulated", 0.0)
    grad_pct = min(100.0, round(eth / GRADUATION_ETH * 100, 2)) if GRADUATION_ETH else 0.0
    return {
        **base,
        "hook_address": HOOK,
        "router_address": ROUTER,
        "usdc_address": USDC,
        "curve_address": CURVE,
        "sinc_address": SINC,
        "graduation_eth_target": GRADUATION_ETH,
        "graduation_pct": grad_pct,
        "discovery_ramp": {"enabled": False, "note": "Sub-floor discovery ramp closed — cancel on-chain if still live"},
        "floor_ladder_usd": base.get("official_floor_usd", 1.50),
        "minimum_buy_usd": base.get("official_floor_usd", 1.50),
        "buy_paths": {
            "usdc_hook": "https://getsincor.com/sinc#buy-usdc",
            "eth_curve": None,
            "eth_curve_note": "CLOSED — bonding curve micro-price is not a valid buy path",
            "referral": "https://getsincor.com/refer",
        },
        "token_list_url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
        "basescan": {
            "sinc": f"https://basescan.org/token/{SINC}",
            "hook": f"https://basescan.org/address/{HOOK}",
            "router": f"https://basescan.org/address/{ROUTER}",
            "curve": f"https://basescan.org/address/{CURVE}",
        },
    }