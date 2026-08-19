"""
SINCOR Treasury Inflow Metrics

Single source of measured results for the CEO KPI: Treasury inflow.

- Local append-only ledger (JSONL) for every recorded inflow event
- 24h rolling totals by asset and source
- Optional live Base RPC snapshot of the canonical treasury wallet
- Safe for production: never moves funds; only observes and records

Canonical treasury: 0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac (Base 8453)
Canonical AXM: 0x4c3fb66f14fbaa2088c9ae91017ba770da53715a (corrected 2026-08-18)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical addresses (must match CANONICAL_ADDRESSES.md)
# ---------------------------------------------------------------------------
TREASURY_ADDRESS = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
).lower()
SINC_CONTRACT = os.getenv(
    "SINC_CONTRACT_ADDRESS", "0x9C8cd8d3961F445D653713dE65C6578bE11668e7"
).lower()
# CEO 2026-08-18 CORRECTION: live AXM is 0x4c3fb66f... — previous 0xfF7aF6... is dead
AXM_CONTRACT = os.getenv(
    "AXIOM_CONTRACT_ADDRESS", "0x4c3fb66f14fbaa2088c9ae91017ba770da53715a"
).lower()
USDC_CONTRACT = os.getenv(
    "USDC_CONTRACT_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
).lower()
BASE_CHAIN_ID = 8453
DEFAULT_RPC = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

# ERC-20 balanceOf(address) selector
_BALANCE_OF_SELECTOR = "0x70a08231"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LEDGER = _REPO_ROOT / "data" / "treasury_inflow.jsonl"
_LEDGER_PATH = Path(os.getenv("TREASURY_INFLOW_LEDGER", str(_DEFAULT_LEDGER)))

_lock = threading.Lock()


@dataclass
class InflowEvent:
    """One recorded inflow (or projected inflow) event."""

    ts: str
    amount: float
    asset: str
    source: str
    usd_estimate: float = 0.0
    tx_hash: Optional[str] = None
    note: str = ""
    projected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TreasurySnapshot:
    """Point-in-time view of treasury balances + 24h ledger totals."""

    timestamp: str
    treasury_address: str
    eth_balance: float = 0.0
    usdc_balance: float = 0.0
    sinc_balance: float = 0.0
    axm_balance: float = 0.0
    rpc_ok: bool = False
    rpc_detail: str = ""
    ledger_24h: Dict[str, float] = field(default_factory=dict)
    ledger_24h_usd: float = 0.0
    ledger_events_24h: int = 0
    ledger_total_events: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_ledger_dir() -> None:
    try:
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not create ledger directory: %s", exc)


def record_inflow(
    amount: float,
    *,
    asset: str = "USD",
    source: str = "unspecified",
    usd_estimate: Optional[float] = None,
    tx_hash: Optional[str] = None,
    note: str = "",
    projected: bool = False,
) -> InflowEvent:
    """
    Append an inflow event to the local ledger.

    Called by DeFi swarms, A2A settlement, subscription renewals, etc.
    Never moves funds. Pure measurement.
    """
    if amount < 0:
        raise ValueError("inflow amount must be non-negative")

    event = InflowEvent(
        ts=_utc_now_iso(),
        amount=float(amount),
        asset=asset.upper(),
        source=source,
        usd_estimate=float(usd_estimate if usd_estimate is not None else amount),
        tx_hash=tx_hash,
        note=note,
        projected=bool(projected),
    )

    _ensure_ledger_dir()
    line = json.dumps(event.to_dict(), separators=(",", ":")) + "\n"

    with _lock:
        try:
            with _LEDGER_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as exc:
            logger.error("Failed to write treasury ledger: %s", exc)
            raise

    logger.info(
        "Treasury inflow recorded: %.6f %s from %s (projected=%s) usd~%.2f",
        event.amount,
        event.asset,
        event.source,
        event.projected,
        event.usd_estimate,
    )
    return event


def _read_ledger_events(max_age_seconds: Optional[float] = None) -> List[InflowEvent]:
    if not _LEDGER_PATH.exists():
        return []

    cutoff = None
    if max_age_seconds is not None:
        cutoff = time.time() - max_age_seconds

    events: List[InflowEvent] = []
    try:
        with _LEDGER_PATH.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    ts = raw.get("ts", "")
                    if cutoff is not None and ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            if dt.timestamp() < cutoff:
                                continue
                        except ValueError:
                            pass
                    events.append(
                        InflowEvent(
                            ts=ts,
                            amount=float(raw.get("amount", 0)),
                            asset=str(raw.get("asset", "USD")),
                            source=str(raw.get("source", "")),
                            usd_estimate=float(raw.get("usd_estimate", 0)),
                            tx_hash=raw.get("tx_hash"),
                            note=str(raw.get("note", "")),
                            projected=bool(raw.get("projected", False)),
                        )
                    )
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
    except OSError as exc:
        logger.warning("Could not read ledger: %s", exc)
    return events


def ledger_summary_24h() -> Dict[str, Any]:
    """Aggregate ledger events from the last 24 hours."""
    events = _read_ledger_events(max_age_seconds=86400)
    by_asset: Dict[str, float] = {}
    by_source: Dict[str, float] = {}
    usd_total = 0.0
    projected_usd = 0.0
    realized_usd = 0.0

    for ev in events:
        by_asset[ev.asset] = by_asset.get(ev.asset, 0.0) + ev.amount
        by_source[ev.source] = by_source.get(ev.source, 0.0) + ev.usd_estimate
        usd_total += ev.usd_estimate
        if ev.projected:
            projected_usd += ev.usd_estimate
        else:
            realized_usd += ev.usd_estimate

    return {
        "events_24h": len(events),
        "by_asset": by_asset,
        "by_source": by_source,
        "usd_total_24h": round(usd_total, 6),
        "usd_realized_24h": round(realized_usd, 6),
        "usd_projected_24h": round(projected_usd, 6),
    }


def _rpc_call(rpc_url: str, method: str, params: list, timeout: float = 4.0) -> Any:
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    req = urllib_request.Request(
        rpc_url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def _hex_to_int(value: Optional[str]) -> int:
    if not value or value in ("0x", "0x0"):
        return 0
    return int(value, 16)


def _erc20_balance(rpc_url: str, token: str, holder: str, decimals: int) -> float:
    """balanceOf via eth_call. Returns human units."""
    # pad address to 32 bytes
    addr = holder.lower().replace("0x", "").zfill(64)
    data = _BALANCE_OF_SELECTOR + addr
    result = _rpc_call(
        rpc_url,
        "eth_call",
        [{"to": token, "data": data}, "latest"],
    )
    raw = _hex_to_int(result)
    return raw / (10 ** decimals)


def fetch_onchain_balances(rpc_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Live snapshot of treasury wallet on Base.
    Failures are soft: returns zeros + error detail so metrics endpoint stays up.
    """
    url = rpc_url or DEFAULT_RPC
    out: Dict[str, Any] = {
        "eth": 0.0,
        "usdc": 0.0,
        "sinc": 0.0,
        "axm": 0.0,
        "rpc_ok": False,
        "rpc_detail": "",
    }
    try:
        eth_hex = _rpc_call(url, "eth_getBalance", [TREASURY_ADDRESS, "latest"])
        out["eth"] = _hex_to_int(eth_hex) / 1e18
        out["usdc"] = _erc20_balance(url, USDC_CONTRACT, TREASURY_ADDRESS, 6)
        out["sinc"] = _erc20_balance(url, SINC_CONTRACT, TREASURY_ADDRESS, 8)
        out["axm"] = _erc20_balance(url, AXM_CONTRACT, TREASURY_ADDRESS, 18)
        out["rpc_ok"] = True
        out["rpc_detail"] = "ok"
    except (urllib_error.URLError, TimeoutError, ValueError, OSError, RuntimeError) as exc:
        out["rpc_detail"] = str(exc)[:200]
        logger.warning("Treasury on-chain snapshot failed: %s", out["rpc_detail"])
    return out


def get_treasury_snapshot(include_onchain: bool = True) -> TreasurySnapshot:
    """Full KPI snapshot used by /api/metrics/treasury and CEO briefs."""
    summary = ledger_summary_24h()
    all_events = _read_ledger_events()

    eth = usdc = sinc = axm = 0.0
    rpc_ok = False
    rpc_detail = "skipped"

    if include_onchain:
        bal = fetch_onchain_balances()
        eth = bal["eth"]
        usdc = bal["usdc"]
        sinc = bal["sinc"]
        axm = bal["axm"]
        rpc_ok = bal["rpc_ok"]
        rpc_detail = bal["rpc_detail"]

    return TreasurySnapshot(
        timestamp=_utc_now_iso(),
        treasury_address=TREASURY_ADDRESS,
        eth_balance=round(eth, 8),
        usdc_balance=round(usdc, 6),
        sinc_balance=round(sinc, 4),
        axm_balance=round(axm, 6),
        rpc_ok=rpc_ok,
        rpc_detail=rpc_detail,
        ledger_24h=summary.get("by_asset", {}),
        ledger_24h_usd=summary.get("usd_total_24h", 0.0),
        ledger_events_24h=summary.get("events_24h", 0),
        ledger_total_events=len(all_events),
    )


# Convenience alias used by older scheduler code paths
def record_inflow_usd(amount: float, source: str = "defi_swarms", projected: bool = True) -> InflowEvent:
    return record_inflow(
        amount,
        asset="USD",
        source=source,
        usd_estimate=amount,
        projected=projected,
        note="scheduler_projection",
    )
