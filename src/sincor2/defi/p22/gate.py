"""P22 stable_only gate.

Absolute: no code path in this package may hold, quote, or allocate a
non-USDC/USDT asset, or anything off Base (chain id 8453). The gate runs at
three entrypoints — scan, adapter, and allocation — and every rejection logs
a security event naming the asset and venue.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Tuple

from . import PARAMS
from .interfaces import PoolQuote

logger = logging.getLogger(__name__)


class StableGateViolation(Exception):
    """Raised when a non-stablecoin or off-chain asset touches the allocator."""


@dataclass
class SecurityEvent:
    kind: str            # "stable_only_violation"
    asset: str
    chain_id: int
    venue_id: str
    entrypoint: str      # "scan" | "adapter" | "allocation"
    detail: str = ""


# In-memory security-event sink for tests and the dashboard feed.
SECURITY_EVENTS: List[SecurityEvent] = []


def _emit(event: SecurityEvent) -> None:
    SECURITY_EVENTS.append(event)
    logger.warning(
        "stable_only_violation asset=%s chain=%s venue=%s entrypoint=%s",
        event.asset, event.chain_id, event.venue_id, event.entrypoint,
    )


def assert_stable(
    asset: str,
    chain_id: int,
    venue_id: str = "",
    entrypoint: str = "allocation",
) -> None:
    """Raise StableGateViolation unless asset is USDC/USDT on Base."""
    ok_asset = asset in PARAMS["allowed_assets"]
    ok_chain = chain_id == PARAMS["chain_id"]
    if ok_asset and ok_chain:
        return
    _emit(SecurityEvent(
        kind="stable_only_violation",
        asset=asset,
        chain_id=chain_id,
        venue_id=venue_id,
        entrypoint=entrypoint,
        detail=f"allowed={PARAMS['allowed_assets']} on chain {PARAMS['chain_id']}",
    ))
    raise StableGateViolation(
        f"stable_only gate: asset {asset!r} on chain {chain_id} rejected "
        f"at {entrypoint} (venue {venue_id!r})"
    )


def filter_quotes(
    quotes: List[PoolQuote],
    entrypoint: str = "scan",
) -> Tuple[List[PoolQuote], List[SecurityEvent]]:
    """Split quotes into accepted / rejected. Rejections are security events."""
    accepted: List[PoolQuote] = []
    rejected: List[SecurityEvent] = []
    before = len(SECURITY_EVENTS)
    for q in quotes:
        try:
            assert_stable(q.asset, q.chain_id, q.venue_id, entrypoint)
            accepted.append(q)
        except StableGateViolation:
            rejected.extend(SECURITY_EVENTS[before:])
            before = len(SECURITY_EVENTS)
    return accepted, rejected


def clear_events() -> None:
    del SECURITY_EVENTS[:]
