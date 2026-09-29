"""P22 shared interfaces: quote schema + the StableVenueAdapter protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Protocol, runtime_checkable


@dataclass(frozen=True)
class PoolQuote:
    venue_id: str
    asset: str            # "USDC" | "USDT" (anything else is gate-rejected)
    chain_id: int         # must be 8453
    net_apr: float        # venue supply APR minus curator perf fee, as a fraction
    depth_usd: float       # available liquidity for the asset at this venue
    ts: float              # quote timestamp (unix seconds)
    is_morpho: bool = False  # Morpho venues win ties within 5 bps


@runtime_checkable
class StableVenueAdapter(Protocol):
    """Every venue adapter implements this. New venues are ~120 lines each."""

    venue_id: str
    asset: str

    def quote(self) -> PoolQuote:
        """Current spot quote (the scanner turns these into TWAPs)."""
        ...

    def build_supply_tx(self, amount_wei: int, receiver: str) -> Dict:
        """Unsigned supply/deposit transaction payload (never broadcast here)."""
        ...

    def build_withdraw_tx(self, shares_wei: int, receiver: str) -> Dict:
        """Unsigned withdraw/redeem transaction payload (never broadcast here)."""
        ...
