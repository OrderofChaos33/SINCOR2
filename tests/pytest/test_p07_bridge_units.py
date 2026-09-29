"""Unit tests for the P07 Cross-Chain Bridge Optimizer reference build.

Covers src/sincor2/defi/bridge_optimizer.py — bridge allowlist, route
scoring, slippage caps, async storage-proof settlement, treasury fees,
capital caps, and the live gate behind SKU SINCOR-DEFI-P07-BRIDGE.

Acceptance criteria are derived from the A-SINC deep spec
(docs/DEFI_PROJECTS_COORDINATION.md §6) and the catalog gates
(bridge_whitelist, slippage_cap, max_alloc_pct=0.20, min_capital=$100,
fee_bps=10) — no auction spec file exists for P07. Pure logic, no chain.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.bridge_optimizer import (
    TREASURY,
    AsyncSettlement,
    BridgeConfig,
    BridgeError,
    BridgeNotAllowlistedError,
    BridgeOptimizer,
    BridgeQuote,
    BridgeWhitelist,
    CapitalCapError,
    LiveBlockedError,
    LiveGate,
    ProofError,
    ProofVerifierRegistry,
    RouteScorer,
    SecurityFloorError,
    SettlementError,
    SlippageCapError,
    StorageProof,
    UnauthorizedError,
)

W = 1_000_000  # 6dp stablecoin wei per USD
G = ["GUARDIAN_ROLE"]


def quote(bridge_id="bridge-a", amount_in=10_000 * W, est_out=None,
          fee_wei=5 * W, slippage_bps=20, eta=600, security=0.85):
    est_out = amount_in - 10 * W if est_out is None else est_out
    return BridgeQuote(
        bridge_id=bridge_id, from_chain="base", to_chain="arbitrum",
        asset="USDC", amount_in_wei=amount_in, est_out_wei=est_out,
        fee_wei=fee_wei, slippage_bps=slippage_bps, eta_seconds=eta,
        security_score=security, quote_ts=time.time())


@pytest.fixture()
def wl():
    w = BridgeWhitelist()
    w.add(BridgeConfig("bridge-a"), G)
    w.add(BridgeConfig("bridge-b"), G)
    return w


@pytest.fixture()
def opt(wl):
    return BridgeOptimizer(whitelist=wl)


@pytest.fixture()
def reg():
    r = ProofVerifierRegistry()
    r.add("verifier-1", G)
    return r


# -- allowlist ---------------------------------------------------------------
def test_non_allowlisted_bridge_reverts(wl):
    """Gate bridge_whitelist: no arbitrary-bridge path exists."""
    scorer = RouteScorer(wl)
    with pytest.raises(BridgeNotAllowlistedError):
        scorer.score(quote(bridge_id="evil-bridge"))


def test_disabled_bridge_reverts(wl):
    wl.add(BridgeConfig("bridge-a", enabled=False), G)
    with pytest.raises(BridgeNotAllowlistedError):
        RouteScorer(wl).score(quote(bridge_id="bridge-a"))


def test_allowlist_admin_is_guardian_only(wl):
    with pytest.raises(UnauthorizedError):
        wl.add(BridgeConfig("bridge-x"), ["SOMEONE_ELSE"])
    with pytest.raises(UnauthorizedError):
        wl.remove("bridge-a", ["SOMEONE_ELSE"])
    assert "bridge-a" in wl


def test_plan_route_with_unknown_bridge_fails(opt):
    """A route touching a non-allowlisted bridge never plans."""
    with pytest.raises(BridgeError):
        opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                       [quote(bridge_id="rogue")], 1_000_000 * W)


# -- scoring -------------------------------------------------------------------
def test_best_net_outcome_wins(opt):
    """Route scoring: the highest net outcome is chosen."""
    qa = quote("bridge-a", est_out=9_990 * W, fee_wei=5 * W)   # net ~9985 - costs
    qb = quote("bridge-b", est_out=9_995 * W, fee_wei=1 * W)   # better
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [qa, qb], 1_000_000 * W)
    assert plan.quote.bridge_id == "bridge-b"
    assert plan.scored.rank == 1


def test_scoring_is_deterministic(opt):
    qa = quote("bridge-a", est_out=9_990 * W)
    qb = quote("bridge-b", est_out=9_990 * W, fee_wei=5 * W)
    first = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                           [qa, qb], 1_000_000 * W)
    second = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                            [qb, qa], 1_000_000 * W)
    assert first.quote.bridge_id == second.quote.bridge_id == "bridge-a"
    # tie (identical nets) breaks on bridge_id ascending
    assert first.scored.net_out_wei == second.scored.net_out_wei


def test_slippage_cost_and_latency_penalty_math():
    # math check with raised caps so the 100 bps quote is routable
    wl = BridgeWhitelist()
    wl.add(BridgeConfig("bridge-a", max_slippage_bps=200), G)
    opt = BridgeOptimizer(whitelist=wl,
                          scorer=RouteScorer(wl, slippage_cap_bps=200))
    q = quote("bridge-a", amount_in=10_000 * W, est_out=10_000 * W,
              fee_wei=0, slippage_bps=100, eta=600, security=0.9)
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [q], 1_000_000 * W)
    slip = 10_000 * W * 100 // 10_000   # 1% of amount_in
    latency = 600 * 1000                 # LATENCY_COST_WEI_PER_SEC
    assert plan.scored.slippage_cost_wei == slip
    assert plan.scored.latency_penalty_wei == latency
    assert plan.scored.net_out_wei == 10_000 * W - slip - latency


def test_same_chain_route_reverts(opt):
    q = quote("bridge-a")
    q2 = BridgeQuote("bridge-a", "base", "base", "USDC", q.amount_in_wei,
                     q.est_out_wei, q.fee_wei, q.slippage_bps, q.eta_seconds,
                     q.security_score, q.quote_ts)
    with pytest.raises(BridgeError):
        opt.plan_route(10_000 * W, "base", "base", "USDC", [q2],
                       1_000_000 * W)


# -- slippage cap ---------------------------------------------------------------
def test_quote_above_slippage_cap_rejected(opt):
    """Gate slippage_cap: over-cap quotes are never routed."""
    bad = quote("bridge-a", slippage_bps=500)  # 5% > 50 bps cap
    with pytest.raises(BridgeError, match="no routable quote"):
        opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                       [bad], 1_000_000 * W)


def test_mixed_quotes_over_cap_dropped(opt):
    bad = quote("bridge-a", slippage_bps=500)
    good = quote("bridge-b", slippage_bps=10)
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [bad, good], 1_000_000 * W)
    assert plan.quote.bridge_id == "bridge-b"
    assert plan.details["quotes_routable"] == 1


def test_security_floor_rejects_weak_bridge(opt):
    weak = quote("bridge-a", security=0.10)
    with pytest.raises(BridgeError, match="no routable quote"):
        opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                       [weak], 1_000_000 * W)


def test_negative_slippage_quote_rejected(wl):
    with pytest.raises(BridgeError):
        RouteScorer(wl).score(quote("bridge-a", slippage_bps=-5))


# -- capital caps / fees ----------------------------------------------------------
def test_min_capital_100_usd(opt):
    """Risk cap: routes below $100 revert."""
    with pytest.raises(CapitalCapError):
        opt.plan_route(99 * W, "base", "arbitrum", "USDC",
                       [quote("bridge-a", amount_in=99 * W)],
                       1_000_000 * W)


def test_max_alloc_20pct_of_tick_capital(opt):
    """Risk cap: a route above 20% of tick capital reverts."""
    with pytest.raises(CapitalCapError):
        opt.plan_route(300_000 * W, "base", "arbitrum", "USDC",
                       [quote("bridge-a", amount_in=300_000 * W)],
                       1_000_000 * W)
    # exactly 20% is allowed
    plan = opt.plan_route(200_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a", amount_in=200_000 * W)],
                          1_000_000 * W)
    assert plan.quote.bridge_id == "bridge-a"


def test_treasury_fee_exactly_10bps(opt):
    """fee_bps=10 on routed volume, integer-exact."""
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a")], 1_000_000 * W)
    assert plan.treasury_fee_wei == 10_000 * W * 10 // 10_000
    assert plan.details["treasury"] == TREASURY


def test_plan_is_dry_run(opt):
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a")], 1_000_000 * W)
    assert plan.dry_run is True
    assert plan.executed is False


# -- async settlement ---------------------------------------------------------------
def _settled(opt, reg, recipient="alice", reverting=None):
    stl = AsyncSettlement(reg, reverting=reverting or set())
    opt.settlement = stl
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a")], 1_000_000 * W)
    rec = stl.fulfill(plan.scored, recipient)
    proof = StorageProof("proof-1", "arbitrum", 12345, "0xroot",
                         rec.commitment, "verifier-1",
                         time.time() + 3600)
    return stl, rec, proof


def test_settle_with_valid_proof_pays_exact(opt, reg):
    """Fulfill-now/settle-later: valid storage proof -> exact payout."""
    stl, rec, proof = _settled(opt, reg)
    out = stl.settle(rec.route_id, proof, "alice")
    assert out["status"] == "settled"
    expected_payout = rec.net_out_wei - rec.treasury_fee_wei
    assert out["settled_wei"] == expected_payout
    assert out["treasury_fee_wei"] == rec.treasury_fee_wei
    assert out["treasury"] == TREASURY
    # conservation: payout + fee == net_out, exact
    assert out["settled_wei"] + out["treasury_fee_wei"] == rec.net_out_wei
    assert stl.treasury_collected_wei == rec.treasury_fee_wei


def test_settle_unknown_verifier_reverts(opt, reg):
    stl, rec, proof = _settled(opt, reg)
    bad = StorageProof("p2", "arbitrum", 12345, "0xroot", rec.commitment,
                       "verifier-evil", time.time() + 3600)
    with pytest.raises(ProofError):
        stl.settle(rec.route_id, bad, "alice")


def test_settle_expired_proof_reverts(opt, reg):
    stl, rec, proof = _settled(opt, reg)
    old = StorageProof("p3", "arbitrum", 12345, "0xroot", rec.commitment,
                       "verifier-1", time.time() - 1)
    with pytest.raises(ProofError):
        stl.settle(rec.route_id, old, "alice")


def test_settle_commitment_mismatch_reverts(opt, reg):
    stl, rec, proof = _settled(opt, reg)
    wrong = StorageProof("p4", "arbitrum", 12345, "0xroot", "0xdeadbeef",
                         "verifier-1", time.time() + 3600)
    with pytest.raises(ProofError):
        stl.settle(rec.route_id, wrong, "alice")


def test_double_settle_is_safe_noop(opt, reg):
    stl, rec, proof = _settled(opt, reg)
    first = stl.settle(rec.route_id, proof, "alice")
    assert first["status"] == "settled"
    second = stl.settle(rec.route_id, proof, "alice")
    assert second["status"] == "already-settled"
    assert second["settled_wei"] == 0
    # treasury fee collected exactly once
    assert stl.treasury_collected_wei == rec.treasury_fee_wei


def test_settle_past_deadline_reverts(opt, reg):
    stl = AsyncSettlement(reg)
    opt.settlement = stl
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a")], 1_000_000 * W)
    rec = stl.fulfill(plan.scored, "alice", now=1_000_000.0)
    proof = StorageProof("p5", "arbitrum", 1, "0xroot", rec.commitment,
                         "verifier-1", 2_000_000.0)
    with pytest.raises(SettlementError):
        stl.settle(rec.route_id, proof, "alice",
                   now=1_000_000.0 + 21600 + 1)


def test_reverting_recipient_parks_for_pull(opt, reg):
    """Non-bricking: a failed recipient transfer parks funds, pull-claimable."""
    stl, rec, proof = _settled(opt, reg, recipient="bad-actor",
                               reverting={"bad-actor"})
    out = stl.settle(rec.route_id, proof, "bad-actor")
    assert out["settled_wei"] == 0
    assert out["parked_wei"] == rec.net_out_wei - rec.treasury_fee_wei
    assert stl.claim("bad-actor", "USDC") == out["parked_wei"]
    assert stl.claim("bad-actor", "USDC") == 0  # idempotent


def test_settle_unknown_route_reverts(opt, reg):
    stl = AsyncSettlement(reg)
    proof = StorageProof("p6", "arbitrum", 1, "0xroot", "0xabc",
                         "verifier-1", time.time() + 3600)
    with pytest.raises(SettlementError):
        stl.settle("route-999", proof, "alice")


def test_verifier_registry_guardian_only(reg):
    with pytest.raises(UnauthorizedError):
        reg.add("verifier-evil", ["SOMEONE_ELSE"])


# -- live gate -----------------------------------------------------------------------
def test_live_routing_blocked_by_default(opt):
    """live_blocked: live fulfill reverts until the founder release."""
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a")], 1_000_000 * W)
    with pytest.raises(LiveBlockedError):
        opt.fulfill_live(plan, "alice")


def test_live_release_path(opt):
    opt.live_gate.release("founder-marker")
    plan = opt.plan_route(10_000 * W, "base", "arbitrum", "USDC",
                          [quote("bridge-a")], 1_000_000 * W)
    rec = opt.fulfill_live(plan, "alice")
    assert rec.route_id.startswith("route-")


def test_live_release_requires_marker():
    gate = LiveGate()
    with pytest.raises(BridgeError):
        gate.release("")


# -- fuzz / invariants ------------------------------------------------------------------
def test_fuzz_ranking_never_routes_rejected_quotes():
    """Seeded fuzz: allowlist, slippage cap, security floor, conservation."""
    import random
    rng = random.Random(20260929)
    wl = BridgeWhitelist()
    wl.add(BridgeConfig("bridge-a"), G)
    wl.add(BridgeConfig("bridge-b", max_slippage_bps=25), G)
    scorer = RouteScorer(wl)
    for i in range(500):
        n = rng.randint(1, 4)
        quotes = []
        for j in range(n):
            bid = rng.choice(["bridge-a", "bridge-b", "bridge-rogue"])
            quotes.append(BridgeQuote(
                bridge_id=bid, from_chain="base", to_chain="arbitrum",
                asset="USDC", amount_in_wei=10_000 * W,
                est_out_wei=rng.randint(9_800 * W, 10_000 * W),
                fee_wei=rng.randint(0, 20 * W),
                slippage_bps=rng.randint(0, 200),
                eta_seconds=rng.randint(60, 3600),
                security_score=rng.choice([0.9, 0.7, 0.5, 0.2]),
                quote_ts=time.time()))
        ranked = scorer.rank(quotes)
        for s in ranked:
            assert s.quote.bridge_id in ("bridge-a", "bridge-b"), \
                f"rogue bridge routed at iter {i}"
            assert s.quote.slippage_bps <= 50, \
                f"over-cap quote routed at iter {i}"
            assert s.quote.security_score >= 0.60
        if ranked:
            nets = [s.net_out_wei for s in ranked]
            assert nets == sorted(nets, reverse=True), \
                f"ranking not best-first at iter {i}"
            # conservation of the scoring identity
            best = ranked[0]
            q = best.quote
            assert best.net_out_wei == q.est_out_wei - q.fee_wei \
                - q.amount_in_wei * q.slippage_bps // 10_000 \
                - q.eta_seconds * 1000


def test_fuzz_settlement_conservation():
    """Seeded fuzz: settle accounting conserves exactly across routes."""
    import random
    rng = random.Random(777)
    wl = BridgeWhitelist()
    wl.add(BridgeConfig("bridge-a"), G)
    reg = ProofVerifierRegistry()
    reg.add("verifier-1", G)
    opt = BridgeOptimizer(whitelist=wl)
    stl = AsyncSettlement(reg)
    opt.settlement = stl
    for i in range(120):
        amount = rng.randint(100, 50_000) * W
        q = BridgeQuote("bridge-a", "base", "arbitrum", "USDC", amount,
                        amount - rng.randint(0, 50 * W),
                        rng.randint(0, 10 * W),
                        rng.randint(0, 50), rng.randint(60, 1800),
                        0.9, time.time())
        plan = opt.plan_route(amount, "base", "arbitrum", "USDC",
                              [q], amount * 10)  # 10% of tick capital
        rec = stl.fulfill(plan.scored, f"user-{i}")
        proof = StorageProof(f"p{i}", "arbitrum", i, "0xroot",
                             rec.commitment, "verifier-1",
                             time.time() + 3600)
        out = stl.settle(rec.route_id, proof, f"user-{i}")
        assert out["settled_wei"] + out["treasury_fee_wei"] == \
            rec.net_out_wei, f"conservation broke at iter {i}"
    assert stl.treasury_collected_wei == sum(
        r.treasury_fee_wei for r in stl._routes.values())
