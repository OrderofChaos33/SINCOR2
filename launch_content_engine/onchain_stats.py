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
# SINC is the platform access token: 1B fixed supply. SINCOR publishes
# supply and contract facts here — no token price or valuation.
SINC_TOTAL_SUPPLY = 1_000_000_000
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
    rpc_ok = sinc_pm > 0 or sinc_safe > 0
    return {
        "sinc_in_hook_pm_m": round(sinc_pm / 1e6, 2),
        "sinc_in_safe": round(sinc_safe, 0),
        "sinc_total_supply": SINC_TOTAL_SUPPLY,
        "eth_usd": eth_usd,
        "price_note": (
            "SINC is the platform access token with 1B fixed supply, "
            "available at https://getsincor.com/buy. "
            "SINCOR does not publish a token price or valuation."
        ),
        "rpc_ok": rpc_ok,
        "sinc_token": SINC,
        "hook": HOOK,
        "router": ROUTER,
        "rogue_v2_pair": "0x85372932f9b151a076815d92cf71a97980ffd667",
        "buy_url": "https://getsincor.com/buy",
    }


def build_official_price_payload(stats: dict | None = None) -> dict:
    """Canonical SINC token facts plus live on-chain facts.

    SINC is platform access with 1B fixed supply. This payload publishes
    supply, contract and inventory facts only — no token price or
    valuation — and ignores any price fields a caller passes in stats.
    """
    s = stats or fetch_stats()
    payload = {
        "source": "sincor_official",
        "updated": "live",
        "token": {
            "symbol": "SINC",
            "address": s.get("sinc_token", SINC),
            "chain_id": 8453,
            "decimals": 8,
            "total_supply": s.get("sinc_total_supply", SINC_TOTAL_SUPPLY),
        },
        "access": {
            "buy_url": "https://getsincor.com/buy",
            "note": (
                "SINC is the platform access token — 1B fixed supply. "
                "SINCOR does not publish a token price or valuation."
            ),
        },
        "aggregator_guidance": {
            "ignore_rogue_v2_pair": s.get("rogue_v2_pair"),
            "official_buy_url": "https://getsincor.com/buy",
            "price_api": "https://getsincor.com/api/price/official",
            "message": (
                "SINC is platform access — 1B fixed supply, "
                "no published price."
            ),
        },
        "note": s.get("price_note"),
        "buy_url": "https://getsincor.com/buy",
        "eth_usd": s.get("eth_usd"),
    }
    try:
        from sincor2.onchain.live_snapshot import attach_official_price_fields
        attach_official_price_fields(payload)
    except Exception:
        payload.setdefault("onchain", {"stale": True, "source": "unavailable"})
    return payload


def draft_post() -> str:
    s = fetch_stats()
    return (
        f"SINCOR on-chain snapshot (Base, agent-reported):\n"
        f"• SINC: platform access token, 1B fixed supply, no published price\n"
        f"• ~{s['sinc_in_hook_pm_m']}M SINC in v4 hook limit orders\n"
        f"• Safe ops wallet: {s['sinc_in_safe']:,.0f} SINC\n"
        f"Verified token: {s['sinc_token'][:10]}…\n"
        f"Checkout: {s['buy_url']}\n"
    )
