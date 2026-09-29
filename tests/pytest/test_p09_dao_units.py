"""Unit tests for P09 DAO Governance Optimizer reference build.

Covers src/sincor2/defi/dao_governance.py — emission monitoring, vote
rental, bribe allocation with full cost accounting (bribe + rental +
bridge slip + gas), the 25% per-gauge cap, controlled-gauge targeting,
epoch settlement with 5 bps Treasury fee, quorum + timelock vote
simulation with immutable snapshots, and the fail-closed live-broadcast
blocker.

Pure logic, no chain access. Every test names the invariant it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import dao_governance as gov
from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.dao_governance import (
    BribeAllocator,
    BroadcastBlockedError,
    EmissionMonitor,
    EpochRecycler,
    Gauge,
    LiveBroadcastBlocker,
    Proposal,
    ProposalSimulator,
    QuorumError,
    StaleVoteError,
    TimelockError,
    VoteMarket,
)

NOW = 1_700_000_000
EPOCH_CAPITAL = 1_000_000_00  # $1M in cents


def _monitor() -> EmissionMonitor:
    m = EmissionMonitor()
    m.register(Gauge("g1", "base", "USDC/AXM", 500_000_00, 2_000_000, True))
    m.register(Gauge("g2", "optimism", "WETH/USDC", 800_000_00, 5_000_000,
                     False))  # uncontrolled
    return m


def _allocator(price_per_vote: int = 2) -> BribeAllocator:
    return BribeAllocator(_monitor(), VoteMarket(price_per_vote))


# -- emissions ---------------------------------------------------------------
def test_monitor_registers_and_ranks_gauges():
    """AC: emission monitor tracks gauges and ranks by emission."""
    m = _monitor()
    top = m.top_by_emission(1)
    assert top[0].gauge_id == "g2"  # 800k > 500k
    assert len(m.gauges()) == 2


def test_monitor_rejects_negative_emission():
    """AC: negative emissions/votes are refused at registration."""
    m = EmissionMonitor()
    with pytest.raises(ValueError):
        m.register(Gauge("bad", "base", "X/Y", -1, 0, True))


def test_rental_quote_is_linear_and_exact():
    """AC: vote rental cost = votes x price, integer cents."""
    q = VoteMarket(3).quote(1_000_000)
    assert q.cost_cents == 3_000_000
    with pytest.raises(ValueError):
        VoteMarket(3).quote(0)


# -- bribe allocation ----------------------------------------------------------
def test_profitable_bribe_plans_with_full_cost_accounting():
    """AC1: positive-net bribe produces a plan with every cost itemized."""
    a = _allocator(price_per_vote=1)
    plan = a.plan("g1", bribe_cents=10_000_00, votes=500_000,
                  epoch_capital_cents=EPOCH_CAPITAL)
    assert plan is not None
    # expected share = 500k emission * 500k / (2M + 500k)
    assert plan.expected_reward_cents == 500_000_00 * 500_000 // 2_500_000
    assert plan.rental_cost_cents == 500_000
    assert plan.bridge_slip_cents == plan.expected_reward_cents * 30 // 10_000
    assert plan.gas_cents == gov.RECYCLE_GAS_CENTS
    assert plan.net_cents == (plan.expected_reward_cents - plan.bribe_cents
                              - plan.rental_cost_cents - plan.bridge_slip_cents
                              - plan.gas_cents)
    assert plan.net_cents > 0


def test_negative_net_bribe_rejected_and_logged():
    """AC1: bribe whose costs exceed expected rewards is rejected + logged."""
    a = _allocator(price_per_vote=1)
    # tiny emission gauge vs huge bribe
    m = EmissionMonitor()
    m.register(Gauge("dust", "base", "X/Y", 1_000_00, 10_000_000, True))
    a2 = BribeAllocator(m, VoteMarket(1))
    assert a2.plan("dust", 50_000_00, 100_000, EPOCH_CAPITAL) is None
    assert len(a2.rejections) == 1
    assert "net" in a2.rejections[0]["reason"]


def test_per_gauge_cap_is_25pct_of_epoch_capital():
    """AC2: bribe above 25% of epoch capital is rejected; at-cap passes."""
    small_epoch = 100_000_00  # $100k -> cap $25k
    a = BribeAllocator(_monitor(), VoteMarket(1))
    cap = int(small_epoch * 0.25)
    assert a.plan("g1", cap + 1, 500_000, small_epoch) is None
    assert "25%" in a.rejections[-1]["reason"]
    # at exactly the cap, with profitable economics, the plan is accepted
    plan = a.plan("g1", cap, 500_000, small_epoch)
    assert plan is not None and plan.bribe_cents == cap


def test_only_controlled_gauges_targeted():
    """AC3: bribes at gauges the agent does not control are rejected."""
    a = _allocator()
    assert a.plan("g2", 1_000_00, 100_000, EPOCH_CAPITAL) is None
    assert "not controlled" in a.rejections[-1]["reason"]


def test_unknown_gauge_rejected():
    """AC: bribes at unknown gauges are rejected, not assumed."""
    a = _allocator()
    assert a.plan("nope", 1_000_00, 100_000, EPOCH_CAPITAL) is None


# -- epoch settlement ------------------------------------------------------------
def test_epoch_settle_routes_5bps_to_treasury():
    """AC7: 5 bps of captured rewards -> Treasury; remainder recycles."""
    a = _allocator(price_per_vote=1)
    p1 = a.plan("g1", 10_000_00, 500_000, EPOCH_CAPITAL)
    assert p1 is not None
    res = EpochRecycler().settle([p1], [p1.expected_reward_cents])
    assert res.fee_to == TREASURY
    assert res.fee_cents == p1.expected_reward_cents * 5 // 10_000
    costs = (p1.bribe_cents + p1.rental_cost_cents + p1.bridge_slip_cents
             + p1.gas_cents)
    assert res.costs_cents == costs
    assert res.recycled_cents == (p1.expected_reward_cents - costs
                                  - res.fee_cents)
    assert res.recycled_cents > 0


def test_epoch_loss_reported_honestly():
    """AC7: a losing epoch reports negative recycle, never hidden."""
    a = _allocator(price_per_vote=1)
    p1 = a.plan("g1", 10_000_00, 500_000, EPOCH_CAPITAL)
    res = EpochRecycler().settle([p1], [0])  # rewards never materialized
    assert res.recycled_cents < 0
    assert res.fee_cents == 0  # no capture, no fee


def test_epoch_settle_rejects_misaligned_inputs():
    """AC: plans and realized rewards must align 1:1."""
    with pytest.raises(ValueError):
        EpochRecycler().settle([], [1])


# -- quorum / timelock simulation ----------------------------------------------------
def _sim() -> ProposalSimulator:
    return ProposalSimulator(quorum_votes=1_000, timelock_delay_s=100)


def test_proposal_passes_with_quorum_and_majority():
    """AC4: quorum met + for > against -> passes."""
    s = _sim()
    p = Proposal("p1", 0, 0, vote_closed_at=NOW)
    s.cast_vote(p, True, 700)
    s.cast_vote(p, False, 400)
    s.close_vote(p)
    passed, _ = s.tally(p)
    assert passed
    assert s.execute(p, NOW + 100).startswith("executed")
    assert p.executed


def test_proposal_fails_without_quorum():
    """AC4: below-quorum proposals fail even with a majority."""
    s = _sim()
    p = Proposal("p2", 0, 0, vote_closed_at=NOW)
    s.cast_vote(p, True, 600)
    s.cast_vote(p, False, 100)
    s.close_vote(p)
    passed, reason = s.tally(p)
    assert not passed and "quorum" in reason
    with pytest.raises(QuorumError):
        s.execute(p, NOW + 10_000)


def test_tie_does_not_pass():
    """AC4: a tie is not a majority -> fails."""
    s = _sim()
    p = Proposal("p3", 0, 0, vote_closed_at=NOW)
    s.cast_vote(p, True, 600)
    s.cast_vote(p, False, 600)
    s.close_vote(p)
    passed, _ = s.tally(p)
    assert not passed


def test_timelock_blocks_early_execution():
    """AC5: execution before the timelock delay raises."""
    s = _sim()
    p = Proposal("p4", 0, 0, vote_closed_at=NOW)
    s.cast_vote(p, True, 900)
    s.cast_vote(p, False, 200)
    s.close_vote(p)
    with pytest.raises(TimelockError):
        s.execute(p, NOW + 99)
    assert s.execute(p, NOW + 100)


def test_snapshot_immutable_after_close():
    """AC5: votes cannot be mutated after the snapshot closes."""
    s = _sim()
    p = Proposal("p5", 0, 0, vote_closed_at=NOW)
    s.cast_vote(p, True, 900)
    s.cast_vote(p, False, 200)
    s.close_vote(p)
    with pytest.raises(StaleVoteError):
        s.cast_vote(p, False, 10_000)  # would flip the outcome
    passed, _ = s.tally(p)
    assert passed  # outcome unchanged by the blocked vote


def test_negative_votes_rejected():
    """AC: vote amounts must be positive."""
    s = _sim()
    p = Proposal("p6", 0, 0, vote_closed_at=NOW)
    with pytest.raises(ValueError):
        s.cast_vote(p, True, -5)


# -- live-block ------------------------------------------------------------------------
def test_broadcast_always_blocked():
    """AC6: any on-chain vote broadcast attempt raises, fail-closed."""
    blocker = LiveBroadcastBlocker()
    with pytest.raises(BroadcastBlockedError):
        blocker.broadcast_vote("p1", True)
    with pytest.raises(BroadcastBlockedError):
        blocker.broadcast_vote("p1", False)
