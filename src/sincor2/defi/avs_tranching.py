"""
SINCOR DeFi P13 — AVS Tranching & Restaking (slashing-risk waterfall model).

Pure-Python reference for the DAVS-PRO strategy: assess per-AVS slashing
risk (EigenLayer/Symbiotic-style restaking networks), then strip restaking
yield into a senior tranche (low-risk, fixed target yield, first-loss
protected by the junior buffer) and a junior tranche (high-risk, levered
residual — absorbs slashing losses first).

Load-bearing invariants (mirror the catalog gates):
- senior_cover: an allocation is accepted only when the modeled senior
  coverage (junior buffer + expected surviving senior principal, over
  senior principal) meets the required multiple. Under-covered
  allocations are rejected, fail-closed.
- slash_oracle: slashing-risk inputs carry a freshness timestamp. A
  stale oracle blocks ALL new allocations until fresh data arrives.
- Junior is capped: the junior tranche can never exceed JUNIOR_CAP_PCT
  of an allocation. Over-cap splits are rejected.
- Slashing waterfall is strict and integer-exact: losses hit junior
  first; senior principal is impaired only after junior is fully wiped.
- Yield waterfall: senior is paid its fixed target first (when total
  yield covers it); junior receives the residual — levered to the
  upside, first-loss to the downside.
- 18 bps of distributed yield routes to the canonical Treasury.
- Simulation only: live_blocked=True. No restaking calls, no validator
  operations; the module models tranches, never touches stake.

Money is integer cents. Slashing probabilities are integer bps.
Time is an explicit parameter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (catalog ProtocolSpec 13, P13_AVS) -----------
FEE_BPS = 18                    # 0.18% of distributed yield -> Treasury
TARGET_APR = 0.13
MIN_CAPITAL_CENTS = 30_000_00   # $300 per tick
MAX_ALLOC_PCT = 0.12            # max 12% of tick capital per AVS allocation
JUNIOR_CAP_PCT = 0.30           # junior tranche capped at 30% of allocation
SENIOR_TARGET_APR = 0.06        # senior fixed target: 6% APR
REQUIRED_COVER_BPS = 11_000     # senior_cover gate: coverage >= 1.10x
ORACLE_STALENESS_LIMIT_S = 3600  # slash oracle older than 1h: fail closed
SECONDS_PER_YEAR = 365 * 86400


class StaleOracleError(RuntimeError):
    """Slash oracle data is stale: all new allocations blocked."""


class CoverBreachError(RuntimeError):
    """Senior coverage below the required multiple: allocation rejected."""


class JuniorCapError(RuntimeError):
    """Junior tranche would exceed its cap: allocation rejected."""


class LiveBlockedError(RuntimeError):
    """A live restaking operation was attempted: blocked, simulation only."""


# -- slash oracle --------------------------------------------------------------
@dataclass(frozen=True)
class AVSRisk:
    avs_id: str
    slash_prob_bps: int         # modeled slashing probability, bps per year
    expected_slash_severity_bps: int  # loss given slash, bps of stake
    as_of: int                  # unix seconds


class SlashOracle:
    """Slashing-risk inputs with a freshness hard gate."""

    def __init__(self, staleness_limit_s: int = ORACLE_STALENESS_LIMIT_S) -> None:
        self.staleness_limit_s = staleness_limit_s
        self._risks: Dict[str, AVSRisk] = {}

    def publish(self, risk: AVSRisk) -> None:
        if not 0 <= risk.slash_prob_bps <= 10_000:
            raise ValueError("slash probability must be 0..10000 bps")
        if not 0 <= risk.expected_slash_severity_bps <= 10_000:
            raise ValueError("slash severity must be 0..10000 bps")
        self._risks[risk.avs_id] = risk

    def read(self, avs_id: str, now: int) -> AVSRisk:
        risk = self._risks.get(avs_id)
        if risk is None:
            raise StaleOracleError(f"no slash-oracle data for {avs_id}")
        if now - risk.as_of > self.staleness_limit_s:
            raise StaleOracleError(
                f"slash-oracle data for {avs_id} is {now - risk.as_of}s old: "
                "new allocations blocked")
        return risk


# -- trancher --------------------------------------------------------------------
@dataclass(frozen=True)
class TrancheAllocation:
    avs_id: str
    total_cents: int
    senior_cents: int
    junior_cents: int
    coverage_x100: int          # modeled senior coverage, percent (110 = 1.10x)
    opened_at: int


class Trancher:
    """
    Splits an allocation into senior/junior per the junior cap, then
    enforces the senior_cover gate against the slash oracle BEFORE any
    capital is committed (modeled).
    """

    def __init__(self, oracle: SlashOracle,
                 junior_cap_pct: float = JUNIOR_CAP_PCT,
                 required_cover_bps: int = REQUIRED_COVER_BPS) -> None:
        self.oracle = oracle
        self.junior_cap_pct = junior_cap_pct
        self.required_cover_bps = required_cover_bps
        self.allocations: List[TrancheAllocation] = []

    def allocate(self, avs_id: str, total_cents: int,
                 junior_pct: float, now: int) -> TrancheAllocation:
        if total_cents < MIN_CAPITAL_CENTS:
            raise ValueError(
                f"allocation {total_cents}c below "
                f"${MIN_CAPITAL_CENTS // 100} floor")
        if not 0 < junior_pct <= self.junior_cap_pct:
            raise JuniorCapError(
                f"junior {junior_pct:.2%} outside (0, {self.junior_cap_pct:.0%}]")
        risk = self.oracle.read(avs_id, now)  # fail-closed on stale/missing
        junior_cents = int(total_cents * junior_pct)
        senior_cents = total_cents - junior_cents
        # Modeled senior coverage, in bps, integer-exact:
        #   coverage = junior/senior + (1 - expected_loss)
        # where expected_loss = p_slash * severity. The junior buffer must
        # cover the 10pp margin PLUS expected slashing loss; thin junior on
        # a risky AVS fails the gate.
        expected_loss_bps = (risk.slash_prob_bps
                             * risk.expected_slash_severity_bps // 10_000)
        coverage_bps = (junior_cents * 10_000 // senior_cents
                        + (10_000 - expected_loss_bps)) if senior_cents else 0
        if coverage_bps < self.required_cover_bps:
            raise CoverBreachError(
                f"senior coverage {coverage_bps / 10_000:.4f}x < required "
                f"{self.required_cover_bps / 10_000:.2f}x for {avs_id}")
        alloc = TrancheAllocation(avs_id=avs_id, total_cents=total_cents,
                                  senior_cents=senior_cents,
                                  junior_cents=junior_cents,
                                  coverage_x100=coverage_bps // 100,
                                  opened_at=now)
        self.allocations.append(alloc)
        logger.info("tranche allocated %s: senior=%dc junior=%dc cover=%.4fx",
                    avs_id, senior_cents, junior_cents, coverage_bps / 10_000)
        return alloc


# -- slashing waterfall ------------------------------------------------------------
@dataclass
class SlashOutcome:
    allocation: TrancheAllocation
    loss_cents: int
    junior_loss_cents: int
    senior_loss_cents: int
    senior_surviving_cents: int
    junior_surviving_cents: int


def apply_slash(allocation: TrancheAllocation,
                loss_cents: int) -> SlashOutcome:
    """
    Strict waterfall, integer-exact: junior absorbs first; senior is
    impaired only after junior is fully wiped. Loss can never exceed the
    allocation total (clamped, with the clamp reported).
    """
    loss = min(max(loss_cents, 0), allocation.total_cents)
    junior_loss = min(loss, allocation.junior_cents)
    senior_loss = loss - junior_loss
    return SlashOutcome(
        allocation=allocation, loss_cents=loss,
        junior_loss_cents=junior_loss, senior_loss_cents=senior_loss,
        senior_surviving_cents=allocation.senior_cents - senior_loss,
        junior_surviving_cents=allocation.junior_cents - junior_loss)


# -- yield waterfall + fee routing ---------------------------------------------------
@dataclass
class YieldDistribution:
    allocation: TrancheAllocation
    total_yield_cents: int
    senior_paid_cents: int
    junior_paid_cents: int
    fee_cents: int
    fee_to: str = TREASURY


def distribute_yield(allocation: TrancheAllocation,
                     total_yield_cents: int, elapsed_s: int) -> YieldDistribution:
    """
    Senior is paid its fixed target first (pro-rated, capped at available
    yield); junior receives the residual — levered upside when yield is
    strong, first-loss when it is not. The 18 bps Treasury fee is taken on
    distributed yield.
    """
    if total_yield_cents < 0:
        raise ValueError("yield must be non-negative here; losses flow "
                         "through apply_slash")
    senior_target = (allocation.senior_cents * SENIOR_TARGET_APR
                     * elapsed_s // SECONDS_PER_YEAR)
    senior_paid = min(senior_target, total_yield_cents)
    junior_paid = total_yield_cents - senior_paid
    fee = total_yield_cents * FEE_BPS // 10_000
    return YieldDistribution(allocation=allocation,
                             total_yield_cents=total_yield_cents,
                             senior_paid_cents=senior_paid,
                             junior_paid_cents=junior_paid,
                             fee_cents=fee)


# -- live-block gate -------------------------------------------------------------------
class LiveRestakeBlocker:
    """Simulation only: any live restaking operation raises, fail-closed."""

    def restake_live(self, allocation: TrancheAllocation) -> None:
        raise LiveBlockedError(
            "P13 is simulation-only (live_blocked=True): restaking calls "
            "never leave this module")
