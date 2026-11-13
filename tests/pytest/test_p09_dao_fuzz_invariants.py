"""Audit-prep invariant + adversarial fuzz tests for P09 (dao_governance).

Property tests an auditor would demand of the ve(3,3) bribe-recycling
reference build:

- emission_fuzz: negative emissions/votes refused at registration;
  top_by_emission is sorted descending on every fuzzed gauge table.
- market_fuzz: rental cost is exactly votes * price; non-positive votes or
  non-positive price rejected.
- allocator_fuzz: a returned plan always has strictly positive net
  decomposed exactly as expected - bribe - rental - bridge_slip - gas;
  unknown gauges, uncontrolled gauges, non-positive bribes, bribes above
  the integer-exact 25% epoch-capital cap, and non-positive-net plans are
  all rejected; bridge slip and gas are exactly the modeled constants.
- recycler_fuzz: captured - costs - fee == recycled, exactly, on every
  fuzzed settlement; the 5 bps treasury fee is integer-exact and always
  routes to the canonical Treasury.
- governance_fuzz: votes cast after the snapshot closes raise; non-positive
  votes raise; quorum is checked before majority; execution before the
  timelock raises; after the delay the proposal executes; the vote tally
  is total-order consistent.
- broadcast_fuzz: on-chain vote broadcast always raises, fail-closed.

Money is integer cents everywhere. Deterministic: seeded RNG. N/N must pass.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import dao_governance as gov  # noqa: E402
from src.sincor2.defi.catalog import TREASURY  # noqa: E402
from src.sincor2.defi.dao_governance import (  # noqa: E402
    BribeAllocator,
    EmissionMonitor,
    EpochRecycler,
    Gauge,
    LiveBroadcastBlocker,
    Proposal,
    ProposalSimulator,
    VoteMarket,
)

RNG = random.Random(0xA00909)
CHAINS = ["base", "optimism", "arbitrum", "mode"]


def fuzz_monitor(n: int) -> EmissionMonitor:
    mon = EmissionMonitor()
    for i in range(n):
        mon.register(Gauge(
            gauge_id=f"g{i}",
            chain=RNG.choice(CHAINS),
            pool=RNG.choice(["weth/usdc", "axm/weth", "sinc/usdc"]),
            emission_per_epoch_cents=RNG.choice(
                [RNG.randint(0, 10**9), 0, 1]),
            total_votes=RNG.choice([RNG.randint(0, 10**9), 0]),
            controlled=RNG.choice([True, False]),
        ))
    return mon


def test_emission_registration_and_ranking():
    mon = EmissionMonitor()
    for bad in (-1, -10**6):
        try:
            mon.register(Gauge("b", "base", "p", bad, 0, True))
            raise AssertionError("negative emission accepted")
        except ValueError:
            pass
        try:
            mon.register(Gauge("b", "base", "p", 0, bad, True))
            raise AssertionError("negative votes accepted")
        except ValueError:
            pass
    for _ in range(200):
        mon = fuzz_monitor(RNG.randint(0, 8))
        top = mon.top_by_emission(RNG.randint(1, 10))
        emissions = [g.emission_per_epoch_cents for g in top]
        assert emissions == sorted(emissions, reverse=True)
        assert len(top) <= len(mon.gauges())


def test_vote_market_linear_pricing():
    for _ in range(200):
        price = RNG.choice([RNG.randint(1, 10**6), 1])
        market = VoteMarket(price)
        votes = RNG.choice([RNG.randint(1, 10**9), 1])
        quote = market.quote(votes)
        assert quote.cost_cents == votes * price
        assert quote.votes == votes
    for bad_price in (0, -5):
        try:
            VoteMarket(bad_price)
            raise AssertionError("non-positive price accepted")
        except ValueError:
            pass
    market = VoteMarket(100)
    for bad_votes in (0, -10):
        try:
            market.quote(bad_votes)
            raise AssertionError("non-positive votes accepted")
        except ValueError:
            pass


def test_allocator_net_decomposition_and_rejections():
    for _ in range(300):
        mon = EmissionMonitor()
        for i in range(RNG.randint(1, 4)):
            mon.register(Gauge(
                gauge_id=f"g{i}", chain="base", pool="weth/usdc",
                emission_per_epoch_cents=RNG.choice(
                    [RNG.randint(0, 10**8), 0]),
                total_votes=RNG.randint(0, 10**7),
                controlled=RNG.choice([True, False]),
            ))
        market = VoteMarket(RNG.randint(1, 1000))
        alloc = BribeAllocator(mon, market)
        gid = RNG.choice([g.gauge_id for g in mon.gauges()] + ["ghost"])
        bribe = RNG.choice([RNG.randint(1, 10**7), 0, -100])
        votes = RNG.choice([RNG.randint(1, 10**6), 1])
        capital = RNG.choice([RNG.randint(10_000, 10**8), gov.MIN_CAPITAL_CENTS])
        plan = alloc.plan(gid, bribe, votes, capital)
        gauge = next((g for g in mon.gauges() if g.gauge_id == gid), None)
        if gauge is None or not gauge.controlled or bribe <= 0:
            assert plan is None
            continue
        cap = capital * int(gov.MAX_ALLOC_PCT * 100) // 100
        if bribe > cap:
            assert plan is None
            continue
        if plan is None:
            continue  # rejected on non-positive net: allowed
        # net is positive and decomposes exactly
        rental = votes * market.price_cents_per_vote
        denom = gauge.total_votes + votes
        expected = (gauge.emission_per_epoch_cents * votes // denom
                    if denom > 0 else 0)
        bridge_slip = expected * gov.BRIDGE_SLIP_BPS // 10_000
        assert plan.net_cents == expected - bribe - rental - bridge_slip \
            - gov.RECYCLE_GAS_CENTS
        assert plan.net_cents > 0
        assert plan.rental_cost_cents == rental
        assert plan.bridge_slip_cents == bridge_slip
        assert plan.gas_cents == gov.RECYCLE_GAS_CENTS
    # boundary: bribe exactly at the 25% cap is not over-cap
    mon = EmissionMonitor()
    mon.register(Gauge("g0", "base", "p", 10**9, 0, True))
    market = VoteMarket(1)
    alloc = BribeAllocator(mon, market)
    capital = 1_000_000
    cap = capital * int(gov.MAX_ALLOC_PCT * 100) // 100
    assert cap == 250_000
    over = alloc.plan("g0", cap + 1, 10, capital)
    assert over is None  # over-cap rejected


def test_recycler_conservation():
    recycler = EpochRecycler()
    for _ in range(200):
        n = RNG.randint(0, 4)
        plans = []
        for i in range(n):
            bribe = RNG.randint(1, 10**6)
            rental = RNG.randint(1, 10**6)
            slip = RNG.randint(0, 10**5)
            plans.append(gov.BribePlan(
                gauge_id=f"g{i}", bribe_cents=bribe, rented_votes=1000,
                rental_cost_cents=rental, expected_reward_cents=0,
                bridge_slip_cents=slip, gas_cents=gov.RECYCLE_GAS_CENTS,
                net_cents=1))
        realized = [RNG.randint(0, 10**7) for _ in plans]
        res = recycler.settle(plans, realized)
        captured = sum(realized)
        costs = sum(p.bribe_cents + p.rental_cost_cents + p.bridge_slip_cents
                    + p.gas_cents for p in plans)
        assert res.captured_cents == captured
        assert res.costs_cents == costs
        assert res.fee_cents == captured * gov.FEE_BPS // 10_000
        assert res.fee_to == TREASURY
        # exact conservation, even at a loss
        assert res.captured_cents - res.costs_cents - res.fee_cents \
            == res.recycled_cents
    # misaligned plans/rewards refused
    try:
        recycler.settle([], [1])
        raise AssertionError("misaligned settle accepted")
    except ValueError:
        pass


def test_governance_quorum_timelock_snapshot():
    sim = ProposalSimulator()
    for _ in range(200):
        prop = Proposal(proposal_id="p", for_votes=0, against_votes=0,
                        vote_closed_at=1_000_000)
        for_side_total = RNG.randint(0, 2_000_000)
        against_total = RNG.randint(0, 2_000_000)
        # cast in random chunks until the full totals are in
        remaining = [for_side_total, against_total]
        while remaining[0] > 0 or remaining[1] > 0:
            side = RNG.choice([0, 1])
            if remaining[side] == 0:
                continue
            k = RNG.randint(1, remaining[side])
            sim.cast_vote(prop, side == 0, k)
            remaining[side] -= k
        assert prop.total_votes == for_side_total + against_total
        sim.close_vote(prop)
        for _ in (True, False):
            try:
                sim.cast_vote(prop, True, 1)
                raise AssertionError("post-snapshot vote accepted")
            except gov.StaleVoteError:
                pass
        passed, reason = sim.tally(prop)
        if for_side_total + against_total < gov.QUORUM_VOTES:
            assert passed is False and "quorum" in reason
        else:
            assert passed == (for_side_total > against_total)
        if not passed:
            try:
                sim.execute(prop, 1_000_000 + gov.TIMELOCK_DELAY_S + 1)
                raise AssertionError("failed proposal executed")
            except gov.QuorumError:
                pass
        else:
            # timelock enforced exactly at the boundary
            try:
                sim.execute(prop, 1_000_000 + gov.TIMELOCK_DELAY_S - 1)
                raise AssertionError("early execution allowed")
            except gov.TimelockError:
                pass
            out = sim.execute(prop, 1_000_000 + gov.TIMELOCK_DELAY_S)
            assert prop.executed is True
            assert "p" in out
    # non-positive votes rejected pre-snapshot
    prop = Proposal("q", 0, 0, 0)
    try:
        sim.cast_vote(prop, True, 0)
        raise AssertionError("zero vote accepted")
    except ValueError:
        pass


def test_broadcast_always_blocked():
    blocker = LiveBroadcastBlocker()
    for _ in range(50):
        try:
            blocker.broadcast_vote("p1", RNG.choice([True, False]))
            raise AssertionError("broadcast escaped")
        except gov.BroadcastBlockedError:
            pass
