"""Unit tests for the P03 Intent Solver & Dark Pool reference build.

Covers src/sincor2/defi/intent_dark_pool.py — intent lifecycle, off-chain
matching, net-balance settlement (AXM/USDC only), split solver, and access
control behind SKU SINCOR-DEFI-P03-DARKPOOL. Pure logic, no chain.

Each test cites the rule it guards (auction task p03-darkpool-unit-tests).
Money math is asserted to the wei. 24/24 must pass.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.intent_dark_pool import (
    ADMIN_ROLE,
    GUARDIAN_ROLE,
    MATCHER_ROLE,
    TREASURY,
    BatchVerificationError,
    DarkFill,
    DarkPool,
    DarkPoolError,
    Intent,
    IntentReplayError,
    Match,
    Matcher,
    MinOutViolationError,
    SettlementAssetError,
    SettlementEngine,
    SplitSolver,
    UnauthorizedError,
    VenueQuote,
    match_pair,
)


def mk(iid, user, ain, aout, amt_in, min_out, nonce=0, ttl=3600):
    return Intent(iid, user, ain, aout, amt_in, min_out,
                  time.time() + ttl, nonce)


@pytest.fixture()
def pool():
    return DarkPool()


@pytest.fixture()
def matcher():
    return Matcher()


@pytest.fixture()
def engine():
    return SettlementEngine()


@pytest.fixture()
def roles():
    return {MATCHER_ROLE: {"matcher-1"}, GUARDIAN_ROLE: {"guardian-1"},
            ADMIN_ROLE: {"admin-1"}}


def _pair():
    a = mk("a", "alice", "AXM", "USDC", 1_000_000, 990_000)
    b = mk("b", "bob", "USDC", "AXM", 1_000_000, 990_000)
    return a, b


def _settle_flow(pool, matcher, engine):
    a, b = _pair()
    pool.submit(a)
    pool.submit(b)
    intents = {"a": a, "b": b}
    matches = matcher.find_matches([a, b])
    batch = matcher.build_batch("batch-1", matches)
    matcher.submit_batch(batch, intents)
    return engine.settle(batch, intents, pool), intents


# -- intent lifecycle ----------------------------------------------------------
def test_submit_stores_commitment_only(pool):
    """Rule p03-darkpool-intent-contract: only the commitment is on-chain; no plaintext."""
    intent = mk("i1", "alice", "AXM", "USDC", 1_000_000, 990_000)
    rec = pool.submit(intent)
    d = rec.to_dict()
    assert "amount_in" not in d and "min_out" not in d
    assert "AXM" not in str(d.values())
    assert rec.commitment == intent.commitment
    assert rec.status == "open"


def test_duplicate_intent_id_rejected(pool):
    """Rule p03-darkpool-intent-contract: intent ids are unique (replay surface)."""
    pool.submit(mk("i1", "alice", "AXM", "USDC", 100, 90))
    with pytest.raises(DarkPoolError):
        pool.submit(mk("i1", "alice", "AXM", "USDC", 100, 90))


def test_owner_cancel_refunds(pool):
    """Rule p03-darkpool-intent-contract: owner cancel path works."""
    pool.submit(mk("i1", "alice", "AXM", "USDC", 100, 90))
    rec = pool.cancel("i1", "alice")
    assert rec.status == "cancelled"
    assert pool.events[-1]["event"] == "IntentCancelled"


def test_cancel_by_non_owner_reverts(pool):
    """Rule p03-darkpool-intent-contract: only the owner can cancel."""
    pool.submit(mk("i1", "alice", "AXM", "USDC", 100, 90))
    with pytest.raises(UnauthorizedError):
        pool.cancel("i1", "bob")


def test_expiry_sweep_auto_refunds(pool):
    """Rule p03-darkpool-edge-cases: expired intents auto-refund via sweep."""
    pool.submit(mk("i1", "alice", "AXM", "USDC", 100, 90, ttl=3600))
    expired = pool.sweep_expiry(now=time.time() + 7200)
    assert expired == ["i1"]
    assert pool.record_of("i1").status == "expired"


def test_nonce_replay_rejected(pool):
    """Rule p03-darkpool-intent-contract: per-user nonces block replay."""
    pool.submit(mk("i1", "alice", "AXM", "USDC", 100, 90, nonce=0))
    with pytest.raises(IntentReplayError):
        pool.submit(mk("i2", "alice", "AXM", "USDC", 100, 90, nonce=0))
    pool.submit(mk("i2", "alice", "AXM", "USDC", 100, 90, nonce=1))  # ok
    assert pool.nonce_of("alice") == 2


# -- matching ------------------------------------------------------------------
def test_price_valid_full_match():
    """Rule p03-darkpool-matcher-agent: crossing limits produce a full match."""
    a, b = _pair()
    m = match_pair(a, b)
    assert m is not None
    assert (m.amount_in_buy, m.amount_in_sell) == (1_000_000, 1_000_000)
    assert m.partial is False


def test_non_crossing_spread_no_match():
    """Rule p03-darkpool-matcher-agent: spreads that do not cross never match."""
    a = mk("a", "alice", "AXM", "USDC", 1_000_000, 1_010_000)  # demands premium
    b = mk("b", "bob", "USDC", "AXM", 1_000_000, 1_010_000)    # demands premium
    assert match_pair(a, b) is None
    assert match_pair(b, a) is None


def test_partial_fill_pro_rata():
    """Rule p03-darkpool-edge-cases: partial fills use pro-rata limit accounting."""
    a = mk("a", "alice", "AXM", "USDC", 1000, 900)
    b = mk("b", "bob", "USDC", "AXM", 500, 450)
    m = match_pair(a, b)
    assert m is not None
    assert m.partial is True
    assert (m.amount_in_buy, m.amount_in_sell) == (555, 500)
    # Buyer still meets its pro-rata limit: 500 >= 900 * 555/1000 = 499.5
    assert 500 * 1000 >= 900 * 555


def test_duplicate_batch_is_safe_noop(pool, matcher):
    """Rule p03-darkpool-matcher-agent: duplicate batch submission is a no-op."""
    a, b = _pair()
    intents = {"a": a, "b": b}
    matches = matcher.find_matches([a, b])
    batch = matcher.build_batch("batch-1", matches)
    assert matcher.submit_batch(batch, intents)["status"] == "accepted"
    dup = matcher.submit_batch(batch, intents)
    assert dup["status"] == "duplicate-noop"


def test_tampered_batch_rejected_intents_reusable(pool, matcher):
    """Rule p03-darkpool-edge-cases: invalid proof rejects the batch; intents reusable."""
    a, b = _pair()
    pool.submit(a)
    pool.submit(b)
    intents = {"a": a, "b": b}
    tampered = matcher.build_batch(
        "batch-x", [Match("a", "b", 1_000_000, 500_000)])  # tampered sell amount
    with pytest.raises(BatchVerificationError):
        matcher.submit_batch(tampered, intents)
    assert pool.record_of("a").status == "open"  # still reusable
    assert pool.record_of("b").status == "open"


def test_expired_intent_never_matched(matcher):
    """Rule p03-darkpool-edge-cases: expired intents are excluded from matching."""
    a = mk("a", "alice", "AXM", "USDC", 1_000_000, 990_000, ttl=-10)
    b = mk("b", "bob", "USDC", "AXM", 1_000_000, 990_000)
    assert matcher.find_matches([a, b]) == []


# -- settlement ----------------------------------------------------------------
def test_settlement_conservation(pool, matcher, engine):
    """Invariant p03-darkpool-settlement: settled conservation (ins == outs + fees)."""
    settlement, _ = _settle_flow(pool, matcher, engine)
    assert settlement.net[("bob", "AXM")] == 1_000_000 - 800
    assert settlement.net[("alice", "USDC")] == 1_000_000 - 800
    assert settlement.fees == {"AXM": 800, "USDC": 800}
    assert pool.record_of("a").status == "settled"


def test_fee_exact_8bps_to_treasury(pool, matcher, engine):
    """Rule p03-darkpool-fee-access-control: 8 bps fee, exact to the wei."""
    settlement, _ = _settle_flow(pool, matcher, engine)
    assert settlement.fees["AXM"] == 1_000_000 * 8 // 10_000 == 800
    assert settlement.fees["USDC"] == 800
    assert settlement.treasury == TREASURY


def test_non_axm_usdc_settlement_reverts(pool, matcher, engine):
    """Rule axm_only_settlement: non-AXM/USDC settlement asset reverts."""
    a = mk("a", "alice", "WETH", "USDC", 1_000_000, 990_000)
    b = mk("b", "bob", "USDC", "WETH", 1_000_000, 990_000)
    pool.submit(a)
    pool.submit(b)
    intents = {"a": a, "b": b}
    batch = matcher.build_batch("batch-1", matcher.find_matches([a, b]))
    matcher.submit_batch(batch, intents)
    with pytest.raises(SettlementAssetError):
        engine.settle(batch, intents, pool)
    assert pool.record_of("a").status == "open"  # no state change on revert


def test_pull_claim_and_no_double_claim(pool, matcher, engine):
    """Rule p03-darkpool-settlement: pull claims; double claim pays 0."""
    _settle_flow(pool, matcher, engine)
    assert engine.claim("bob", "AXM") == 999_200
    assert engine.claim("bob", "AXM") == 0
    assert engine.balance_of("bob", "AXM") == 0


def test_failed_transfer_never_bricks(pool, matcher, engine):
    """Rule p03-darkpool-settlement: failed transfer never bricks; funds stay claimable."""
    _settle_flow(pool, matcher, engine)

    def bad_transfer(user, asset, amount):
        raise RuntimeError("simulated transfer failure")

    assert engine.claim("bob", "AXM", transfer=bad_transfer) == 0
    assert len(engine.transfer_errors) == 1
    assert engine.balance_of("bob", "AXM") == 999_200  # retained
    assert engine.claim("bob", "AXM") == 999_200       # retry succeeds


def test_replay_of_settled_intent_reverts(pool, matcher, engine):
    """Rule p03-darkpool-intent-contract: replay of a settled intent reverts."""
    settlement, intents = _settle_flow(pool, matcher, engine)
    a, b = intents["a"], intents["b"]
    batch2 = matcher.build_batch("batch-2", matcher.find_matches([a, b]))
    matcher.submit_batch(batch2, intents)
    with pytest.raises(IntentReplayError):
        engine.settle(batch2, intents, pool)


# -- split solver --------------------------------------------------------------
def _venues():
    return [VenueQuote("v1", 98, 100), VenueQuote("v2", 97, 100)]


def test_below_threshold_never_splits():
    """Rule split_threshold: small intents route single-venue, never split."""
    solver = SplitSolver(split_threshold=5_000)
    intent = mk("s1", "alice", "AXM", "USDC", 1_000, 950)
    plan = solver.route(intent, DarkFill(1_000, 99, 100), _venues())
    assert plan.split is False
    assert len(plan.legs) == 1
    assert plan.total_expected_out == 980  # best venue 0.98


def test_split_beats_single_venue():
    """Rule p03-darkpool-split-solver: split routing wins when modeled better."""
    solver = SplitSolver(split_threshold=5_000)
    intent = mk("s1", "alice", "AXM", "USDC", 10_000, 9_500)
    plan = solver.route(intent, DarkFill(4_000, 99, 100), _venues())
    assert plan.split is True
    assert plan.total_expected_out == 3_960 + 5_880 == 9_840
    dark_leg, venue_leg = plan.legs
    assert (dark_leg.amount_in, dark_leg.expected_out) == (4_000, 3_960)
    assert (venue_leg.amount_in, venue_leg.expected_out) == (6_000, 5_880)
    # min_out enforced on every leg (pro-rata).
    assert dark_leg.expected_out >= dark_leg.min_out_leg == 3_800
    assert venue_leg.expected_out >= venue_leg.min_out_leg == 5_700


def test_leg_min_out_violation_rejects_split():
    """Rule min_out: a split with a violating leg falls back to single venue."""
    solver = SplitSolver(split_threshold=5_000)
    intent = mk("s1", "alice", "AXM", "USDC", 10_000, 9_500)
    plan = solver.route(intent, DarkFill(4_000, 75, 100), _venues())
    assert plan.split is False
    assert plan.total_expected_out == 9_800  # best single venue


def test_all_dark_fill_preferred_when_best():
    """Rule p03-darkpool-split-solver: all-dark fill wins when it beats venues."""
    solver = SplitSolver(split_threshold=5_000)
    intent = mk("s1", "alice", "AXM", "USDC", 10_000, 9_500)
    plan = solver.route(intent, DarkFill(10_000, 99, 100), _venues())
    assert plan.split is False
    assert plan.legs[0].venue == "dark-pool"
    assert plan.total_expected_out == 9_900


# -- access control ------------------------------------------------------------
def test_pause_blocks_submits_not_exits(pool, matcher, engine, roles):
    """Rule p03-darkpool-fee-access-control: pause blocks new intents, not exits."""
    settlement, _ = _settle_flow(pool, matcher, engine)
    pool.submit(mk("i9", "alice", "AXM", "USDC", 100, 90, nonce=1))
    pool.pause(roles, "guardian-1")
    with pytest.raises(DarkPoolError):
        pool.submit(mk("i10", "bob", "AXM", "USDC", 100, 90))
    pool.cancel("i9", "alice")                      # cancel still works
    assert engine.claim("bob", "AXM") == 999_200     # claims still work
    pool.unpause(roles, "guardian-1")
    pool.submit(mk("i10", "bob", "AXM", "USDC", 100, 90, nonce=1))  # ok again


def test_pause_requires_guardian(pool, roles):
    """Rule p03-darkpool-fee-access-control: unauthorized pause reverts."""
    with pytest.raises(UnauthorizedError):
        pool.pause(roles, "stranger")
