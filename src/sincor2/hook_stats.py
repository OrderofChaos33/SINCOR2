"""Live hook stats for gateway and acceptance APIs.

CEO 2026-08-19: SINC updated to new 8-decimal live contract 0xe1D836087F6573b665d25CE088793E916D7892f8.
Chain-aware overrides: HOOK_CHAIN_ID / BASE_SEPOLIA_* env (testnet only).
"""

from __future__ import annotations

import os
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
SEPOLIA_CHAIN_ID = 84532
MAINNET_CHAIN_ID = 8453


def _addresses_for_chain(chain_id: int) -> dict:
    if chain_id == SEPOLIA_CHAIN_ID:
        return {
            "hook": os.environ.get("BASE_SEPOLIA_SHARED_LIQUIDITY_HOOK", HOOK),
            "router": os.environ.get("BASE_SEPOLIA_SINC_SWAP_ROUTER", ROUTER),
            "usdc": os.environ.get("BASE_SEPOLIA_USDC", USDC),
            "sinc": os.environ.get("BASE_SEPOLIA_SINC", SINC),
            "explorer": "https://sepolia.basescan.org",
        }
    return {
        "hook": HOOK,
        "router": ROUTER,
        "usdc": USDC,
        "sinc": SINC,
        "explorer": "https://basescan.org",
    }


def fetch_hook_status(chain_id: int | None = None) -> dict:
    active_chain_id = (
        chain_id if chain_id is not None else int(os.environ.get("HOOK_CHAIN_ID", str(MAINNET_CHAIN_ID)))
    )
    active = _addresses_for_chain(active_chain_id)
    base = fetch_stats()
    return {
        **base,
        "chain_id": active_chain_id,
        "hook_address": active["hook"],
        "router_address": active["router"],
        "usdc_address": active["usdc"],
        "sinc_address": active["sinc"],
        "floor_ladder_usd": base.get("official_floor_usd", 0.15),
        "minimum_buy_usd": base.get("official_floor_usd", 0.15),
        "buy_paths": {
            "checkout": "https://getsincor.com/buy",
        },
        "token_list_url": "https://getsincor.com/tokenlists/sincor.tokenlist.json",
        "basescan": {
            "sinc": f"{active['explorer']}/token/{active['sinc']}",
            "hook": f"{active['explorer']}/address/{active['hook']}",
            "router": f"{active['explorer']}/address/{active['router']}",
        },
    }
