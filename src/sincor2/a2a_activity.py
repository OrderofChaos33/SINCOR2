"""Honest activity metrics for the A2A marketplace (AXM Activity Engine, stream C).

GET /v1/a2a/activity reports REAL marketplace activity figures. Every figure
carries a ``source`` label: "ledger" or "onchain". The activity loop is
ledger-only (no on-chain trading), so "ledger" is the honest label everywhere
in this report — that honesty is the point.

ZERO fabricated numbers: every value is computed from actual ledger/store
state. An empty store reports 0 with source "ledger", never a placeholder.
Nothing here is presented or labeled as trading volume; the disclaimer says so.

Definitions (all anchored to a trailing rolling window, default 1 hour):
- auctions_per_hour: tasks created in the window, divided by window hours.
- bid_volume_axm: sum of bid_axm for bids received in the window.
- settlement_rate: of tasks created in the window that reached a terminal
  state (settled/failed/expired), the fraction in "settled".
- fees_to_treasury_axm: realized (projected=False) AXM platform-fee entries
  in the 5% fee ledger (treasury_inflow.jsonl, source=a2a_settlement or a
  "platform fee" note) timestamped inside the window.
- active_agents: distinct agent ids with in-window activity (bids received,
  tasks posted, or tasks assigned).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from flask import Blueprint, jsonify, request

SOURCE_LEDGER = "ledger"
DISCLAIMER = "Marketplace ledger activity; not onchain trading volume."

# Auction-close lifecycle states. "settled" is the success subset.
_TERMINAL_STATES = ("settled", "failed", "expired")
_MAX_WINDOW_HOURS = 168.0
_MIN_WINDOW_HOURS = 1.0 / 3600.0  # 1 second


def _now_ms() -> int:
    from sincor2.a2a_inbound import _now_ms as _ms

    return int(_ms())


def _in_window(ts_ms: Any, start_ms: int, end_ms: int) -> bool:
    try:
        t = int(ts_ms)
    except (TypeError, ValueError):
        return False
    return start_ms <= t <= end_ms


def _event_ts_ms(event: Dict[str, Any]) -> Optional[int]:
    """Parse an inflow-ledger ISO timestamp to epoch ms. None if unparseable."""
    ts = event.get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def _platform_fee_events_axm() -> List[Dict[str, Any]]:
    """Realized (projected=False) AXM platform-fee entries from the fee ledger.

    Filters to entries written by the 5% fee path: source="a2a_settlement"
    or a note mentioning "platform fee". Everything else (projected inflows,
    other assets, other sources) is excluded — no mixing, no invention.
    """
    from sincor2.treasury_inflow import list_inflow_events

    out: List[Dict[str, Any]] = []
    for ev in list_inflow_events():
        if str(ev.get("asset") or "").upper() != "AXM":
            continue
        if ev.get("projected"):
            continue
        src = str(ev.get("source") or "")
        note = str(ev.get("note") or "").lower()
        if src != "a2a_settlement" and "platform fee" not in note:
            continue
        out.append(ev)
    return out


def compute_activity(window_hours: float = 1.0) -> Dict[str, Any]:
    """Compute honest activity figures from actual ledger/store state.

    Never invents numbers: with empty stores every figure is 0, source
    "ledger".
    """
    try:
        hours = float(window_hours)
    except (TypeError, ValueError):
        hours = 1.0
    hours = max(min(hours, _MAX_WINDOW_HOURS), _MIN_WINDOW_HOURS)
    now_ms = _now_ms()
    start_ms = int(now_ms - hours * 3600_000)

    from sincor2.a2a_inbound import get_fabric

    fabric = get_fabric()
    with fabric.lock:
        tasks = [dict(t) for t in fabric.tasks.values()]
        bids = [dict(b) for b in fabric.bids.values()]

    # Cohort: auctions opened inside the window.
    cohort = [t for t in tasks if _in_window(t.get("created_at"), start_ms, now_ms)]
    closed = [t for t in cohort if t.get("state") in _TERMINAL_STATES]
    settled = [t for t in closed if t.get("state") == "settled"]
    closed_n = len(closed)
    settled_n = len(settled)
    rate = round(settled_n / closed_n, 4) if closed_n else 0.0

    # Bid volume: bids actually received inside the window.
    window_bids = [b for b in bids if _in_window(b.get("received_at"), start_ms, now_ms)]
    bid_volume = round(sum(float(b.get("bid_axm") or 0) for b in window_bids), 6)

    # Active agents: distinct ids with in-window activity.
    active: Set[str] = set()
    for t in cohort:
        if t.get("poster_id"):
            active.add(str(t["poster_id"]))
        if t.get("assigned_to"):
            active.add(str(t["assigned_to"]))
    for b in window_bids:
        if b.get("agent_id"):
            active.add(str(b["agent_id"]))

    # Treasury fees: realized AXM fee-ledger entries inside the window.
    fee_events = [
        e
        for e in _platform_fee_events_axm()
        if _in_window(_event_ts_ms(e), start_ms, now_ms)
    ]
    fees_axm = round(sum(float(e.get("amount") or 0) for e in fee_events), 6)

    return {
        "disclaimer": DISCLAIMER,
        "generated_at_ms": now_ms,
        "window": {
            "hours": hours,
            "starts_at_ms": start_ms,
            "ends_at_ms": now_ms,
        },
        "auctions_per_hour": {
            "value": round(len(cohort) / hours, 4),
            "source": SOURCE_LEDGER,
            "auctions_opened": len(cohort),
        },
        "bid_volume_axm": {
            "value": bid_volume,
            "source": SOURCE_LEDGER,
            "bids": len(window_bids),
        },
        "settlement_rate": {
            "value": rate,
            "source": SOURCE_LEDGER,
            "settled": settled_n,
            "closed": closed_n,
        },
        "fees_to_treasury_axm": {
            "value": fees_axm,
            "source": SOURCE_LEDGER,
            "fee_events": len(fee_events),
        },
        "active_agents": {
            "value": len(active),
            "source": SOURCE_LEDGER,
        },
    }


def attach_activity_routes(bp: Blueprint) -> None:
    @bp.get("/v1/a2a/activity")
    def v1_activity():
        """Real marketplace activity figures; every value labeled ledger/onchain.

        Query: window_hours (default 1, clamped to [1s, 168h]).
        """
        return jsonify(compute_activity(request.args.get("window_hours", 1.0))), 200
