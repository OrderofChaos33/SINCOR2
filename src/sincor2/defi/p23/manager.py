"""P23 pool liquidity manager: dry-run advisory only. Never signs.

Monitors utilization per whitelisted collection and proposes advisory
rebalances across collections within the per-collection cap (max_alloc_pct =
0.08 of managed capital). Any live-intent path raises LiveBlocked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import PARAMS
from .live_block import guard_live
from .registry import CollectionRegistry
from .vault import FractionalVault


@dataclass
class AdvisoryAction:
    kind: str            # "deploy" | "recall" | "hold"
    collection: str
    amount_wei: int
    reason: str


class LiquidityManager:
    """Advisory rebalancer. propose() returns actions; execute_live() refuses."""

    def __init__(self, registry: CollectionRegistry, vault: FractionalVault) -> None:
        self.registry = registry
        self.vault = vault

    def utilization_report(self) -> Dict[str, float]:
        return {
            c: self.vault.utilization(c)
            for c in self.registry.whitelisted_collections()
        }

    def propose(
        self,
        managed_capital_wei: int,
        now: Optional[float] = None,
    ) -> List[AdvisoryAction]:
        """Advisory rebalance within the 0.08 per-collection cap."""
        now = time.time() if now is None else now
        actions: List[AdvisoryAction] = []
        per_collection_cap = int(managed_capital_wei * PARAMS["max_alloc_pct"])
        for collection in self.registry.whitelisted_collections():
            nav = self.vault.pool_nav(collection)
            util = self.vault.utilization(collection)
            if nav > per_collection_cap:
                actions.append(AdvisoryAction(
                    "recall", collection, nav - per_collection_cap,
                    f"pool NAV exceeds 8% managed-capital cap"))
            elif util > 0.70:
                actions.append(AdvisoryAction(
                    "hold", collection, 0,
                    f"utilization {util:.2%} near cap 0.75: alert"))
            else:
                actions.append(AdvisoryAction(
                    "hold", collection, 0,
                    f"utilization {util:.2%} within band"))
        return actions

    def execute_live(self, actions: List[AdvisoryAction]) -> None:
        guard_live("LiquidityManager.execute_live")
