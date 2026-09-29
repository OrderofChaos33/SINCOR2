"""
SINCOR DeFi P09 — DAO Governance Optimizer (ve(3,3) bribe-recycling model).

Pure-Python reference for the XB-LOOP strategy: monitor gauge emissions
across L2 Velodrome/Solidly forks, rent ve-voting power via marketplaces,
target bribes at low-liquidity pairs the agent controls, capture epoch
rewards, and recycle profit across chains. Net yield is always computed
AFTER bribe cost, vote-rental cost, bridge slippage, and gas — an epoch
recycles only genuine profit.

Hard boundaries (mirror the catalog):
- Vote-weight SIMULATION only. No on-chain vote broadcast from this
  module: any broadcast attempt raises (fail-closed, live_blocked=True).
- Quorum + timelock are simulated: a proposal executes only when total
  votes reach quorum AND the timelock delay has elapsed since the vote
  closed; the vote snapshot is immutable after close.
- 5 bps of captured epoch rewards routes to the canonical Treasury.

Money is integer cents everywhere. Time is an explicit parameter so
tests are deterministic. No network calls, no keys, no chain access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (catalog ProtocolSpec 9, P09_DAO_GOV) -----------
FEE_BPS = 5                    # 0.05% of captured rewards -> Treasury
MAX_ALLOC_PCT = 0.25           # max 25% of epoch capital per gauge
MIN_CAPITAL_CENTS = 1_000      # $10 epoch-capital floor
QUORUM_VOTES = 1_000_000       # minimum total votes for a valid proposal
TIMELOCK_DELAY_S = 2 * 86400   # 48h timelock between vote close and execution
BRIDGE_SLIP_BPS = 30           # 0.30% bridge slippage model per recycle hop
RECYCLE_GAS_CENTS = 500        # $5 modelled gas per cross-chain recycle


class BroadcastBlockedError(RuntimeError):
    """Fail-closed: on-chain vote broadcast is never allowed from the swarm."""


class StaleVoteError(RuntimeError):
    """A vote was mutated after the snapshot closed."""


class TimelockError(RuntimeError):
    """Execution attempted before the timelock delay elapsed."""


class QuorumError(RuntimeError):
    """Proposal did not reach quorum."""


# -- emissions -----------------------------------------------------------------
@dataclass(frozen=True)
class Gauge:
    gauge_id: str
    chain: str
    pool: str
    emission_per_epoch_cents: int   # total rewards this epoch
    total_votes: int                # ve-votes currently directed here
    controlled: bool                # True = low-liq pair the agent controls


class EmissionMonitor:
    """Tracks gauge emissions across chains. Fixture-injected, no network."""

    def __init__(self) -> None:
        self._gauges: Dict[str, Gauge] = {}

    def register(self, gauge: Gauge) -> None:
        if gauge.emission_per_epoch_cents < 0 or gauge.total_votes < 0:
            raise ValueError("emission and votes must be non-negative")
        self._gauges[gauge.gauge_id] = gauge

    def gauges(self) -> List[Gauge]:
        return list(self._gauges.values())

    def top_by_emission(self, n: int) -> List[Gauge]:
        return sorted(self._gauges.values(),
                      key=lambda g: g.emission_per_epoch_cents,
                      reverse=True)[:n]


# -- vote marketplace ------------------------------------------------------------
@dataclass(frozen=True)
class RentalQuote:
    votes: int
    cost_cents: int          # total rental cost for the epoch
    marketplace: str = "votemarket"


class VoteMarket:
    """Rents ve-voting power. Linear price model, fixture-injected."""

    def __init__(self, price_cents_per_vote: int) -> None:
        if price_cents_per_vote <= 0:
            raise ValueError("rental price must be positive")
        self.price_cents_per_vote = price_cents_per_vote

    def quote(self, votes: int) -> RentalQuote:
        if votes <= 0:
            raise ValueError("votes must be positive")
        return RentalQuote(votes=votes,
                           cost_cents=votes * self.price_cents_per_vote)


# -- bribe allocator ---------------------------------------------------------------
@dataclass(frozen=True)
class BribePlan:
    gauge_id: str
    bribe_cents: int
    rented_votes: int
    rental_cost_cents: int
    expected_reward_cents: int
    bridge_slip_cents: int
    gas_cents: int
    net_cents: int


class BribeAllocator:
    """
    Targets bribes ONLY at controlled gauges where the expected net
    (reward share - bribe - rental - bridge slip - gas) is positive.
    Allocation per gauge is capped at MAX_ALLOC_PCT of epoch capital.
    """

    def __init__(self, monitor: EmissionMonitor, market: VoteMarket) -> None:
        self.monitor = monitor
        self.market = market
        self.rejections: List[Dict[str, object]] = []

    def _reject(self, gauge_id: str, reason: str) -> None:
        self.rejections.append({"gauge_id": gauge_id, "reason": reason})
        logger.info("bribe reject %s: %s", gauge_id, reason)

    def plan(self, gauge_id: str, bribe_cents: int, votes: int,
             epoch_capital_cents: int) -> Optional[BribePlan]:
        gauge = next((g for g in self.monitor.gauges()
                      if g.gauge_id == gauge_id), None)
        if gauge is None:
            self._reject(gauge_id, "unknown gauge")
            return None
        if not gauge.controlled:
            self._reject(gauge_id, "gauge not controlled by agent")
            return None
        if bribe_cents <= 0:
            self._reject(gauge_id, "bribe must be positive")
            return None
        # integer-exact 25% of epoch capital (no float dust on large books)
        cap = epoch_capital_cents * int(MAX_ALLOC_PCT * 100) // 100
        if bribe_cents > cap:
            self._reject(gauge_id,
                         f"bribe {bribe_cents}c > 25% epoch-capital cap {cap}c")
            return None
        rental = self.market.quote(votes)
        # Expected reward share: rented votes over post-bribe total votes.
        # Bribed votes steer existing votes; model: share = votes /
        # (total_votes + votes) of the emission.
        denom = gauge.total_votes + votes
        expected = (gauge.emission_per_epoch_cents * votes // denom
                    if denom > 0 else 0)
        bridge_slip = expected * BRIDGE_SLIP_BPS // 10_000
        gas = RECYCLE_GAS_CENTS
        net = expected - bribe_cents - rental.cost_cents - bridge_slip - gas
        if net <= 0:
            self._reject(gauge_id,
                         f"net {net}c <= 0 after bribe+rental+bridge+gas")
            return None
        return BribePlan(gauge_id=gauge_id, bribe_cents=bribe_cents,
                         rented_votes=votes, rental_cost_cents=rental.cost_cents,
                         expected_reward_cents=expected,
                         bridge_slip_cents=bridge_slip, gas_cents=gas,
                         net_cents=net)


# -- epoch recycler ------------------------------------------------------------------
@dataclass
class EpochResult:
    plans: List[BribePlan]
    captured_cents: int
    costs_cents: int
    fee_cents: int
    recycled_cents: int
    fee_to: str = TREASURY


class EpochRecycler:
    """
    Settles an epoch: captured rewards minus ALL costs (bribe, rental,
    bridge slip, gas). The 5 bps Treasury fee is taken on captured rewards;
    only the remainder recycles to the next epoch.
    """

    def settle(self, plans: List[BribePlan],
               realized_rewards_cents: List[int]) -> EpochResult:
        if len(plans) != len(realized_rewards_cents):
            raise ValueError("plans and realized rewards must align")
        captured = sum(realized_rewards_cents)
        costs = sum(p.bribe_cents + p.rental_cost_cents + p.bridge_slip_cents
                    + p.gas_cents for p in plans)
        fee = captured * FEE_BPS // 10_000
        recycled = captured - costs - fee
        if recycled < 0:
            # An epoch can lose money on realized (not expected) rewards;
            # the loss is reported honestly, never hidden or socialized.
            logger.warning("epoch settled at a loss: %dc", recycled)
        return EpochResult(plans=list(plans), captured_cents=captured,
                           costs_cents=costs, fee_cents=fee,
                           recycled_cents=recycled)


# -- governance simulation (quorum + timelock, no broadcast) ---------------------------
@dataclass
class Proposal:
    proposal_id: str
    for_votes: int
    against_votes: int
    vote_closed_at: int
    executed: bool = False
    _snapshot_taken: bool = field(default=False, repr=False)

    @property
    def total_votes(self) -> int:
        return self.for_votes + self.against_votes


class ProposalSimulator:
    """
    Vote-weight simulation with quorum and timelock. The snapshot is
    immutable once the vote closes; execution requires quorum AND the
    full timelock delay. There is deliberately no broadcast path.
    """

    def __init__(self, quorum_votes: int = QUORUM_VOTES,
                 timelock_delay_s: int = TIMELOCK_DELAY_S) -> None:
        self.quorum_votes = quorum_votes
        self.timelock_delay_s = timelock_delay_s

    def close_vote(self, proposal: Proposal) -> None:
        proposal._snapshot_taken = True

    def cast_vote(self, proposal: Proposal, for_side: bool,
                  votes: int) -> None:
        if proposal._snapshot_taken:
            raise StaleVoteError("vote snapshot closed; votes are immutable")
        if votes <= 0:
            raise ValueError("votes must be positive")
        if for_side:
            proposal.for_votes += votes
        else:
            proposal.against_votes += votes

    def tally(self, proposal: Proposal) -> Tuple[bool, str]:
        """(passed, reason). Quorum first, then majority."""
        if proposal.total_votes < self.quorum_votes:
            return False, (f"quorum not met: {proposal.total_votes} < "
                           f"{self.quorum_votes}")
        if proposal.for_votes > proposal.against_votes:
            return True, "majority for"
        return False, "majority against or tie"

    def execute(self, proposal: Proposal, now: int) -> str:
        passed, reason = self.tally(proposal)
        if not passed:
            raise QuorumError(f"proposal {proposal.proposal_id} failed: {reason}")
        if now - proposal.vote_closed_at < self.timelock_delay_s:
            raise TimelockError(
                f"timelock: {now - proposal.vote_closed_at}s elapsed, "
                f"{self.timelock_delay_s}s required")
        proposal.executed = True
        return f"executed (simulated): {proposal.proposal_id}"


class LiveBroadcastBlocker:
    """
    The swarm never broadcasts votes on-chain. Any attempt raises,
    fail-closed. This is the code-level expression of live_blocked=True.
    """

    def broadcast_vote(self, proposal_id: str, for_side: bool) -> None:
        raise BroadcastBlockedError(
            f"on-chain vote broadcast for {proposal_id} blocked: "
            "P09 is vote-weight simulation only (live_blocked=True)")
