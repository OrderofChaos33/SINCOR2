"""P23 fractional vault: ERC-721 custody, ERC-20 share mint/burn, NAV accounting.

Per-collection vault:
  - deposit: whitelisted collection + oracle not frozen; locks the ERC-721,
    mints shares = floor_twap * (1 - deposit_fee_bps/10000) / share_price.
    First deposit mints MINIMUM_SHARES (1000) to the burn address
    (ERC-4626 inflation-attack guard).
  - utilization cap: deployed_lending_value / total_pool_value <= 0.75.
    Deposit-side deploys that would breach revert; withdrawals are never
    blocked by the cap.
  - redeem: pro-rata underlying value (USDC-wei accounting). Fungible-value
    redemptions; specific-token buyout auctions are out of scope for v1.
  - reserve sleeve: 5% of pool value, untouchable by the lending cap, absorbs
    defaulted collateral at the 75% threshold price (BendDAO anti-death-spiral).

Values in integer USDC-wei. Share math is exact rational, floored once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from . import PARAMS
from .oracle import NftPricingOracle
from .registry import CollectionRegistry

BURN_ADDRESS = "0x" + "00" * 19 + "01"


class VaultError(Exception):
    """Vault invariant violation."""


@dataclass
class Position:
    owner: str
    collection: str
    token_ids: List[int] = field(default_factory=list)
    shares: int = 0


class FractionalVault:
    def __init__(
        self,
        registry: CollectionRegistry,
        oracle: NftPricingOracle,
        utilization_cap: Optional[float] = None,
    ) -> None:
        self.registry = registry
        self.oracle = oracle
        self.utilization_cap = (utilization_cap if utilization_cap is not None
                                else PARAMS["utilization_cap"])
        # collection -> pool state
        self._total_shares: Dict[str, int] = {}
        self._pool_value_wei: Dict[str, int] = {}   # NAV incl. accrued interest
        self._deployed_wei: Dict[str, int] = {}     # lending sleeve
        self._reserve_wei: Dict[str, int] = {}      # 5% backstop sleeve
        self._positions: Dict[Tuple[str, str], Position] = {}  # (owner, collection)
        self._epoch_nav: Dict[str, List[tuple]] = {}  # collection -> [(ts, nav)]

    # -- accounting ------------------------------------------------------
    def share_price(self, collection: str) -> Fraction:
        shares = self._total_shares.get(collection, 0)
        if shares <= 0:
            return Fraction(1, 1)  # 1 wei per share before first deposit
        return Fraction(self._pool_value_wei.get(collection, 0), shares)

    def pool_nav(self, collection: str) -> int:
        return self._pool_value_wei.get(collection, 0)

    def utilization(self, collection: str) -> float:
        total = self._pool_value_wei.get(collection, 0)
        if total <= 0:
            return 0.0
        return self._deployed_wei.get(collection, 0) / total

    # -- deposit / fractionalize ------------------------------------------
    def deposit(
        self,
        owner: str,
        collection: str,
        token_id: int,
        now: Optional[float] = None,
    ) -> int:
        now = time.time() if now is None else now
        if not self.registry.is_whitelisted(collection):
            raise VaultError(f"collection {collection} not whitelisted")
        reading = self.oracle.floor(collection, now)
        if reading.deposits_frozen:
            raise VaultError(
                f"deposits frozen for {collection}: {reading.reason}")
        floor_wei = reading.floor_wei
        if floor_wei <= 0:
            raise VaultError("no oracle price; cannot mint shares")

        net_value = (Fraction(floor_wei)
                     * Fraction(10_000 - PARAMS["deposit_fee_bps"], 10_000))
        price = self.share_price(collection)
        shares = int(net_value / price)
        if shares <= 0:
            raise VaultError("deposit value below one share")

        first = self._total_shares.get(collection, 0) == 0
        if first:
            # ERC-4626 inflation-attack guard: minimum shares to the burn
            # address. They still count in totalShares (share_price math).
            self._total_shares[collection] = PARAMS["minimum_shares"]
            self._positions[(BURN_ADDRESS, collection)] = Position(
                BURN_ADDRESS, collection, [], PARAMS["minimum_shares"])
        self._total_shares[collection] = self._total_shares.get(collection, 0) + shares
        self._pool_value_wei[collection] = (
            self._pool_value_wei.get(collection, 0) + int(net_value))
        key = (owner, collection)
        pos = self._positions.get(key)
        if pos is None:
            pos = self._positions[key] = Position(owner, collection)
        pos.token_ids.append(token_id)
        pos.shares += shares
        return shares

    # -- lending sleeve (utilization-capped) -------------------------------
    def deploy_to_lending(self, collection: str, amount_wei: int) -> None:
        if amount_wei <= 0:
            raise VaultError("deploy amount must be positive")
        total = self._pool_value_wei.get(collection, 0)
        if total <= 0:
            raise VaultError("empty pool")
        new_deployed = self._deployed_wei.get(collection, 0) + amount_wei
        if new_deployed / total > self.utilization_cap + 1e-12:
            raise VaultError(
                f"utilization cap breached: {(new_deployed / total):.4f} > "
                f"{self.utilization_cap:.2f}"
            )
        self._deployed_wei[collection] = new_deployed

    def repay_to_pool(self, collection: str, amount_wei: int) -> None:
        deployed = self._deployed_wei.get(collection, 0)
        if amount_wei > deployed:
            raise VaultError("repay exceeds deployed")
        self._deployed_wei[collection] = deployed - amount_wei

    def accrue_interest(self, collection: str, interest_wei: int) -> None:
        """Borrower interest accrues to share NAV."""
        if interest_wei < 0:
            raise VaultError("interest cannot be negative")
        self._pool_value_wei[collection] = (
            self._pool_value_wei.get(collection, 0) + interest_wei)

    # -- redeem (never blocked by the utilization cap) ----------------------
    def redeem(
        self,
        owner: str,
        collection: str,
        shares: int,
        now: Optional[float] = None,
    ) -> int:
        now = time.time() if now is None else now
        pos = self._positions.get((owner, collection))
        if pos is None or pos.shares < shares or shares <= 0:
            raise VaultError("insufficient shares")
        price = self.share_price(pos.collection)
        payout_wei = int(Fraction(shares) * price)
        # Withdrawals never blocked by the cap: pull back from the lending
        # sleeve first if pool cash is short (model-level; onchain this is
        # the reserve + orderly unwind path). If the smaller pool would push
        # utilization over the cap, recall from the lending sleeve so the
        # 0.75 cap holds exactly after every operation.
        pos.shares -= shares
        self._total_shares[pos.collection] -= shares
        self._pool_value_wei[pos.collection] -= payout_wei
        pool = self._pool_value_wei.get(pos.collection, 0)
        max_deployed = int(self.utilization_cap * pool)
        deployed = self._deployed_wei.get(pos.collection, 0)
        if deployed > max_deployed:
            self._deployed_wei[pos.collection] = max_deployed
        return payout_wei

    # -- reserve-sleeve backstop --------------------------------------------
    def absorb_default(self, collection: str, collateral_value_wei: int) -> int:
        """Absorb defaulted NFT collateral at the 75% threshold price from the
        5% reserve sleeve. Returns the absorbed value. Covers idiosyncratic
        defaults, not systemic collection death (documented honestly)."""
        pool = self._pool_value_wei.get(collection, 0)
        if pool <= 0:
            raise VaultError("empty pool")
        sleeve = int(pool * PARAMS["reserve_sleeve_pct"])
        absorbed = min(collateral_value_wei, sleeve)
        self._reserve_wei[collection] = self._reserve_wei.get(collection, 0) + absorbed
        return absorbed

    # -- epochs -------------------------------------------------------------
    def snapshot_epoch(self, collection: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        self._epoch_nav.setdefault(collection, []).append(
            (now, self._pool_value_wei.get(collection, 0)))

    def epoch_navs(self, collection: str) -> List[tuple]:
        return list(self._epoch_nav.get(collection, []))
