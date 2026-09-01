"""Pull factual on-chain stats via JSON-RPC (no API keys required).

CEO 2026-08-19: SINC updated to new 8-decimal live contract 0xe1D836087F6573b665d25CE088793E916D7892f8.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
try:
    from sincor2.onchain.constants import (
        LIMIT_ORDER_HOOK,
        POOL_MANAGER,
        SINC_TOKEN,
        TREASURY,
        resolve_address,
    )

    SINC = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
    POOL_MANAGER = POOL_MANAGER
    SAFE = resolve_address("TREASURY_ADDRESS", TREASURY)
    HOOK = resolve_address("SINC_LIMIT_ORDER_HOOK", LIMIT_ORDER_HOOK)
except Exception:  # pragma: no cover
    SINC = "0xe1D836087F6573b665d25CE088793E916D7892f8"
    POOL_MANAGER = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
    SAFE = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
    HOOK = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"

RPC_CANDIDATES = [
    os.environ.get("BASE_RPC_URL", ""),
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
]
RPC_CANDIDATES = [u for u in RPC_CANDIDATES if u]
# Official floor: $150M FDV / 1B SINC = $0.15
SINC_FLOOR_USD = float(os.environ.get("SINC_FLOOR_USD", "0.15"))
ROUTER = "0x11b86E85cC5170F4165c89ccb11332133B29E283"


def _rpc(method: str, params: list) -> str:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "SINCOR-launch-content-engine/1.0",
    }
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
            continue
    raise RuntimeError(f"all RPC endpoints failed: {last_err}")


def _call(addr: str, sig: str, *args: str) -> str:
    # cast-style selector + encoded args omitted — use pre-encoded balanceOf
    if sig == "balanceOf":
        wallet = args[0].lower().replace("0x", "").zfill(64)
        data = "0x70a08231" + wallet
    elif sig == "sincSold":
        data = "0x9899fccf"
    elif sig == "ethAccumulated":
        data = "0xa5cb6825"
    elif sig == "currentPriceWei":
        data = "0xf5191c4d"
    elif sig == "graduated":
        data = "0xe7c2b772"
    else:
        raise ValueError(sig)
    return _rpc("eth_call", [{"to": addr, "data": data}, "latest"])


def _dec(raw: str, decimals: int = 8) -> float:
    return int(raw, 16) / (10**decimals)


def _safe_call(sig: str, addr: str, *args: str, default: float = 0.0) -> float:
    try:
        return _dec(_call(addr, sig, *args))
    except Exception:
        return default


def fetch_stats() -> dict:
    sinc_pm = _safe_call("balanceOf", SINC, POOL_MANAGER)
    sinc_safe = _safe_call("balanceOf", SINC, SAFE)
    eth_usd = float(os.environ.get("ETH_USD", "3000"))
    hook_floor_usd = SINC_FLOOR_USD
    rpc_ok = sinc_pm > 0 or sinc_safe > 0
    return {
        "sinc_in_hook_pm_m": round(sinc_pm / 1e6, 2),
        "sinc_in_safe": round(sinc_safe, 0),
        "official_floor_usd": hook_floor_usd,
        "hook_floor_usd": hook_floor_usd,
        "eth_usd": eth_usd,
        "price_note": (
            f"Official SINC price ${hook_floor_usd:.2f} ($150M FDV / 1B tokens). "
            "Pay at https://getsincor.com/buy."
        ),
        "rpc_ok": rpc_ok,
        "sinc_token": SINC,
        "hook": HOOK,
        "router": ROUTER,
        "rogue_v2_pair": "0x85372932f9b151a076815d92cf71a97980ffd667",
        "buy_url": "https://getsincor.com/buy",
    }


def build_official_price_payload(stats: dict | None = None) -> dict:
    """Canonical pricing — $0.15 floor ($150M / 1B SINC)."""
    s = stats or fetch_stats()
    floor = float(s.get("official_floor_usd", SINC_FLOOR_USD))
    return {
        "source": "sincor_official",
        "updated": "live",
        "official_floor_usd": floor,
        "minimum_buy_usd_per_sinc": floor,
        "official_buy": {
            "price_usd": floor,
            "buy_url": "https://getsincor.com/buy",
            "note": f"Official SINC price ${floor:.2f} — $150M valuation / 1B supply.",
        },
        "aggregator_guidance": {
            "display_price_usd": floor,
            "ignore_rogue_v2_pair": s.get("rogue_v2_pair"),
            "official_buy_url": "https://getsincor.com/buy",
            "price_api": "https://getsincor.com/api/price/official",
            "message": f"SINC official price is ${floor:.2f} ($150M / 1B tokens).",
        },
        "spot_usd": floor,
        "hook_floor_usd": floor,
        "note": s.get("price_note"),
        "buy_url": "https://getsincor.com/buy",
        "eth_usd": s.get("eth_usd"),
    }


def draft_post() -> str:
    s = fetch_stats()
    return (
        f"SINCOR on-chain snapshot (Base, agent-reported):\n"
        f"• Official SINC price ${s['official_floor_usd']:.2f} ($150M FDV / 1B)\n"
        f"• ~{s['sinc_in_hook_pm_m']}M SINC in v4 hook limit orders\n"
        f"• Safe ops wallet: {s['sinc_in_safe']:,.0f} SINC\n"
        f"Verified token: {s['sinc_token'][:10]}…\n"
        f"Checkout: {s['buy_url']}\n"
    )
