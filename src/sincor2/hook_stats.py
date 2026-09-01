"""Live hook stats for gateway and acceptance APIs.

CEO 2026-08-19: SINC updated to new 8-decimal live contract 0xe1D836087F6573b665d25CE088793E916D7892f8.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from launch_content_engine.onchain_stats import fetch_stats  # noqa: E402
from sincor2.onchain.constants import (
    LIMIT_ORDER_HOOK,
    SINC_TOKEN,
    USDC_TOKEN,
)

HOOK = LIMIT_ORDER_HOOK
ROUTER = "0x11b86E85cC5170F4165c89ccb11332133B29E283"
USDC = USDC_TOKEN
SINC = SINC_TOKEN


def fetch_hook_status() -> dict:
    base = fetch_stats()
    return {
        **base,
        "hook_address": HOOK,
        "router_address": ROUTER,
        "usdc_address": USDC,
        "sinc_address": SINC,
        "floor_ladder_usd": base.get("official_floor_usd", 0.15),
        "minimum_buy_usd": base.get("official_floor_usd", 0.15),
        "buy_paths": {
            "checkout": "https://getsincor.com/buy",
        },
        "token_list_url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
        "basescan": {
            "sinc": f"https://basescan.org/token/{SINC}",
            "hook": f"https://basescan.org/address/{HOOK}",
            "router": f"https://basescan.org/address/{ROUTER}",
        },
    }
