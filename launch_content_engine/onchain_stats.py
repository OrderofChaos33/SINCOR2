"""Pull factual on-chain stats via JSON-RPC (no API keys required)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

RPC_CANDIDATES = [
    os.environ.get("BASE_RPC_URL", ""),
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://base-rpc.publicnode.com",
]
RPC_CANDIDATES = [u for u in RPC_CANDIDATES if u]
SINC = "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
CURVE = "0x75dE341a2BC81806198364F125d4Cde36527619C"
POOL_MANAGER = "0x498581fF718922c3f8e6A244956aF099B2652b2b"
SAFE = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
HOOK = "0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0"
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
    sinc_curve = _safe_call("balanceOf", SINC, CURVE)
    sinc_pm = _safe_call("balanceOf", SINC, POOL_MANAGER)
    sinc_safe = _safe_call("balanceOf", SINC, SAFE)
    try:
        eth_acc = int(_call(CURVE, "ethAccumulated"), 16) / 1e18
    except Exception:
        eth_acc = 0.0
    sinc_sold = _safe_call("sincSold", CURVE)
    try:
        price_wei = int(_call(CURVE, "currentPriceWei"), 16)
        price_eth_per_sinc = price_wei / 1e18
    except Exception:
        price_eth_per_sinc = 0.0
    eth_usd = float(os.environ.get("ETH_USD", "3000"))
    spot_usd = price_eth_per_sinc * eth_usd
    try:
        graduated = int(_call(CURVE, "graduated"), 16) != 0
    except Exception:
        graduated = False
    rpc_ok = sinc_curve > 0 or sinc_pm > 0
    return {
        "sinc_in_curve_m": round(sinc_curve / 1e6, 2),
        "sinc_in_hook_pm_m": round(sinc_pm / 1e6, 2),
        "sinc_in_safe": round(sinc_safe, 0),
        "sinc_sold_m": round(sinc_sold / 1e6, 2),
        "curve_eth_accumulated": round(eth_acc, 4),
        "curve_spot_eth": price_eth_per_sinc,
        "curve_spot_usd": round(spot_usd, 8),
        "graduated": graduated,
        "hook_floor_usd": 1.50,
        "price_note": "Official spot = bonding curve. Hook $0.10+ are sell walls, not spot.",
        "rpc_ok": rpc_ok,
        "sinc_token": SINC,
        "curve": CURVE,
        "hook": HOOK,
        "router": ROUTER,
        "buy_url": "https://getsincor.com/sinc",
        "refer_url": "https://getsincor.com/refer",
    }


def draft_post() -> str:
    s = fetch_stats()
    return (
        f"SINCOR on-chain snapshot (Base, agent-reported):\n"
        f"• ~{s['sinc_in_curve_m']}M SINC in bonding curve\n"
        f"• ~{s['sinc_in_hook_pm_m']}M SINC in v4 hook limit orders\n"
        f"• Safe ops wallet: {s['sinc_in_safe']:,.0f} SINC\n"
        f"• Curve ETH accumulated: {s['curve_eth_accumulated']} ETH · ~{s['sinc_sold_m']}M SINC sold\n"
        f"Verified token: {s['sinc_token'][:10]}…\n"
        f"Buy (self-serve): {s['buy_url']}\n"
        f"Earn 3% referring buyers: {s['refer_url']}"
    )