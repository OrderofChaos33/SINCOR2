"""P24 DeFi primitives: creator-token staking and collateral adapters.

Staking: stake creator tokens for a share of a rewards pool funded by 10% of
the platform fee leg; 6% target APR on staked value; 72 h unbonding (prevents
flash stake/dump around epoch snapshots).

Collateral: creator tokens usable as collateral in allowlisted lending venues
at max 50% LTV, with a per-token exposure cap of 10% of the venue sleeve.
Venue allowlist is frozen at deploy.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional

from . import PARAMS


class PrimitiveError(Exception):
    """Primitive invariant violation."""


# ---------------------------------------------------------------- staking
@dataclass
class StakePosition:
    staker: str
    amount_wei: int
    staked_at: float
    unbond_requested_at: Optional[float] = None
    # Accumulator accounting: rewards earned but not yet claimed, and the
    # accumulator value the position was last settled against. This makes
    # payouts claim-order independent (no shrinking-pool / shrinking-share
    # race between sequential claimants).
    accrued: Fraction = Fraction(0)
    reward_debt: Fraction = Fraction(0)


class CreatorStaking:
    """Stake creator tokens; rewards accrue pro-rata from the rewards pool.

    Rewards use accumulator (index) accounting: every funded reward is
    rolled into a cumulative per-staked-wei index, and each position's
    earned rewards are settled against that index. Two claimants receive
    the same total regardless of claim order.
    """

    TARGET_APR = PARAMS["target_apr"]

    def __init__(self, unbonding_s: float | None = None) -> None:
        self.unbonding_s = (unbonding_s if unbonding_s is not None
                            else PARAMS["staking_unbonding_s"])
        self._positions: Dict[str, StakePosition] = {}
        self._rewards_pool_wei: int = 0  # funded but not yet indexed
        self._rewards_distributed_wei: int = 0
        self._acc: Fraction = Fraction(0)  # cumulative reward per staked wei

    def _sync(self) -> None:
        """Roll any funded-but-unindexed rewards into the accumulator."""
        total = self.total_staked()
        if total > 0 and self._rewards_pool_wei > 0:
            self._acc += Fraction(self._rewards_pool_wei, total)
            self._rewards_pool_wei = 0

    def _settle(self, pos: StakePosition) -> None:
        pos.accrued += (Fraction(pos.amount_wei) * self._acc
                        - pos.reward_debt)
        pos.reward_debt = Fraction(pos.amount_wei) * self._acc

    def fund_rewards(self, amount_wei: int) -> None:
        """Top up the rewards pool (10% of the platform fee leg)."""
        if amount_wei < 0:
            raise PrimitiveError("rewards funding cannot be negative")
        self._rewards_pool_wei += amount_wei
        self._sync()

    def stake(self, staker: str, amount_wei: int,
              now: float | None = None) -> None:
        now = time.time() if now is None else now
        if amount_wei <= 0:
            raise PrimitiveError("stake amount must be positive")
        self._sync()
        pos = self._positions.get(staker)
        if pos is None:
            self._positions[staker] = StakePosition(
                staker, amount_wei, now,
                reward_debt=Fraction(amount_wei) * self._acc)
        else:
            if pos.unbond_requested_at is not None:
                raise PrimitiveError("cannot top up while unbonding")
            self._settle(pos)
            pos.amount_wei += amount_wei
            pos.reward_debt = Fraction(pos.amount_wei) * self._acc

    def request_unbond(self, staker: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        pos = self._positions.get(staker)
        if pos is None or pos.amount_wei <= 0:
            raise PrimitiveError("nothing staked")
        pos.unbond_requested_at = now

    def withdraw(self, staker: str, now: float | None = None) -> int:
        """Withdraw after the 72 h unbonding period. Returns principal wei."""
        now = time.time() if now is None else now
        pos = self._positions.get(staker)
        if pos is None or pos.unbond_requested_at is None:
            raise PrimitiveError("unbond not requested")
        if now - pos.unbond_requested_at < self.unbonding_s:
            raise PrimitiveError(
                f"unbonding: {now - pos.unbond_requested_at:.0f}s elapsed, "
                f"{self.unbonding_s:.0f}s required")
        amount = pos.amount_wei
        reward = self.claim_rewards(staker)
        del self._positions[staker]
        return amount + reward

    def total_staked(self) -> int:
        return sum(p.amount_wei for p in self._positions.values())

    def claim_rewards(self, staker: str) -> int:
        """Settled pro-rata share of indexed rewards (order-independent)."""
        pos = self._positions.get(staker)
        if pos is None:
            return 0
        self._sync()
        self._settle(pos)
        out = int(pos.accrued)
        pos.accrued -= out
        self._rewards_distributed_wei += out
        return out


# ------------------------------------------------------------- collateral
@dataclass(frozen=True)
class VenueSleeve:
    venue_id: str
    sleeve_value_usd: float  # total value of the lending sleeve


class CollateralAdapter:
    """Creator tokens as collateral: 50% max LTV, 10% per-token sleeve cap."""

    def __init__(self, venues: Optional[List[VenueSleeve]] = None) -> None:
        # Venue allowlist frozen at deploy.
        self._venues: Dict[str, VenueSleeve] = {
            v.venue_id: v for v in (venues or [])
        }
        self._locked: Dict[str, float] = {}  # token symbol -> USD locked

    def add_venue(self, venue: VenueSleeve) -> None:
        raise PrimitiveError("venue allowlist frozen at deploy")

    def max_borrow_usd(self, symbol: str, token_value_usd: float,
                       venue_id: str) -> float:
        """Max borrow against a creator-token position at one venue."""
        venue = self._venues.get(venue_id)
        if venue is None:
            raise PrimitiveError(f"venue {venue_id!r} not allowlisted")
        if token_value_usd < 0:
            raise PrimitiveError("token value cannot be negative")
        per_token_cap = venue.sleeve_value_usd * PARAMS["max_alloc_pct"]
        locked = self._locked.get(symbol, 0.0)
        headroom = max(0.0, per_token_cap - locked)
        collateral = min(token_value_usd, headroom)
        return collateral * PARAMS["max_ltv"]

    def lock(self, symbol: str, token_value_usd: float, venue_id: str) -> float:
        """Lock collateral and return the borrow limit. Enforces the 10% cap."""
        limit = self.max_borrow_usd(symbol, token_value_usd, venue_id)
        if limit <= 0:
            raise PrimitiveError("no borrow headroom: per-token cap exhausted")
        self._locked[symbol] = self._locked.get(symbol, 0.0) + min(
            token_value_usd,
            self._venues[venue_id].sleeve_value_usd * PARAMS["max_alloc_pct"]
            - self._locked.get(symbol, 0.0),
        )
        return limit
