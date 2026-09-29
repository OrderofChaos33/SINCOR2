"""Unit tests for the P18 Structured Product Vaults reference build.

Covers src/sincor2/defi/structured_products.py — PT/YT tranche mint
conservation, the cap-rate payoff calculator against the canonical
worked example, principal-floor enforcement under rate shocks,
cap clamping, full lifecycle settlement, 12 bps fee routing, the 48h
parameter timelock, stale-oracle reverts, and non-bricking yield-source
failures behind SKU SINCOR-DEFI-P18-STRUCTURED. Pure logic, no chain.

Each test cites the auction-task acceptance criterion it guards.
"""

from __future__ import annotations

import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.structured_products import (
    EXAMPLE,
    FEE_BPS,
    TIMELOCK_SECONDS,
    CapBreach,
    FloorBreach,
    PriceFeed,
    ProductTerms,
    StaleOracle,
    StructuredError,
    StructuredVault,
    TimelockPending,
    TrancheLedger,
    Unauthorized,
    YieldSourceNotAllowlisted,
    discount_factor_bps,
    metrics_payload,
    payoff,
)
from src.sincor2.defi import catalog as catalog_mod

W = 10**18
NOW = 1_800_000_000.0


def make_vault() -> StructuredVault:
    return StructuredVault(lister="lister", allowlister="allowlister",
                           pauser="pauser")


def make_terms(product_id: str = "note-1", **kw) -> ProductTerms:
    base = dict(product_id=product_id, floor_bps=10_000, cap_bps=2_000,
                participation_bps=10_000, discount_rate_bps=500,
                sleeve_rate_bps=500, tenor_years=1.0,
                maturity_ts=NOW + 31_536_000, listed_by="lister")
    base.update(kw)
    return ProductTerms(**base)


def listed_vault(product_id: str = "note-1", **kw) -> StructuredVault:
    v = make_vault()
    v.list_product(make_terms(product_id, **kw), caller="lister", now=NOW)
    v.allow_yield_source(product_id, "morpho", caller="allowlister")
    return v


# -- payoff calculator ----------------------------------------------------------------
def test_payoff_matches_worked_example_exactly():
    # AC(p18-structured-products-payoff-calculator): calculator output
    # matches the spec's worked payoff examples exactly.
    payout, clamped = payoff(
        EXAMPLE["deposit_wei"], EXAMPLE["floor_bps"],
        EXAMPLE["participation_bps"], EXAMPLE["cap_bps"],
        EXAMPLE["underlying_return_bps"])
    assert payout == EXAMPLE["expected_payout_wei"]
    assert clamped is True  # 35% return capped at 20%


def test_payoff_below_cap_not_clamped():
    payout, clamped = payoff(1_000 * W, 10_000, 10_000, 2_000, 1_500)
    assert payout == 1_150 * W  # floor 1000 + 15% upside
    assert clamped is False


def test_payoff_negative_return_floors():
    payout, clamped = payoff(1_000 * W, 10_000, 10_000, 2_000, -3_000)
    assert payout == 1_000 * W  # floor holds, no negative upside
    assert clamped is False


def test_payoff_never_exceeds_cap():
    # AC(p18-structured-products-cap-rate-enforcement): payouts never
    # exceed the cap rate.
    for ret in (2_000, 2_001, 5_000, 100_000):
        payout, _ = payoff(1_000 * W, 10_000, 10_000, 2_000, ret)
        assert payout <= 1_200 * W
    # Participation >100% is rejected outright.
    with pytest.raises(ValueError):
        payoff(1_000 * W, 10_000, 10_001, 2_000, 1_000)


def test_payoff_rounding_favors_vault():
    # Integer division floors the upside — the vault never overpays the
    # exact rational value (computed with Fraction, no float error).
    from fractions import Fraction
    deposit = 1_000 * W + 1
    payout, _ = payoff(deposit, 10_000, 3_333, 2_000, 1_234)
    exact = deposit * (1 + Fraction(3_333, 10_000) * Fraction(1_234, 10_000))
    assert payout <= exact
    # Exact integer replication of the calculator's op order.
    assert payout == deposit + (deposit * 3_333 * 1_234 // 10_000 // 10_000)


# -- tranche conservation --------------------------------------------------------------
def test_tranche_mint_conservation():
    # AC(p18-structured-products-tranche-tokens): PT+YT always minted in
    # exact proportion to the deposit with zero supply drift.
    v = listed_vault()
    r1 = v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    r2 = v.deposit("note-1", "bob", 500 * W, now=NOW)
    assert r1["pt"] == r1["yt"] == 1_000 * W
    assert r2["pt"] == r2["yt"] == 500 * W
    v.ledger.check_conservation()
    assert v.ledger.pt_supply == v.ledger.yt_supply == 1_500 * W


def test_fee_12bps_on_deposit_to_treasury():
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    assert v.fee_owed_wei == 1_000 * W * FEE_BPS // 10_000
    receipt = v.claim_fees()
    assert receipt["treasury"] == catalog_mod.TREASURY
    assert receipt["fee_bps"] == 12
    assert v.fee_owed_wei == 0


def test_fee_12bps_on_harvest():
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    v.claim_fees()
    v.harvest("note-1", "morpho", 100 * W)
    assert v.fee_owed_wei == 100 * W * FEE_BPS // 10_000
    assert v.harvests["note-1"] == 100 * W - 100 * W * FEE_BPS // 10_000


# -- principal protection ----------------------------------------------------------------
def test_floor_holds_under_rate_shocks():
    # AC(p18-structured-products-principal-protection-sleeve): PT
    # redemption never returns less than the principal floor, including
    # rate-shock scenarios.
    for rate_bps in (100, 500, 1_500):  # 1%, 5%, 15% discount rates
        v = listed_vault("note-r", discount_rate_bps=rate_bps)
        v.deposit("note-r", "alice", 1_000 * W, discount_rate_bps=rate_bps,
                  now=NOW)
        v.feed = PriceFeed(price=0.5, updated_at=NOW + 31_536_000)  # -50%
        s = v.settle("note-r", initial_price=1.0,
                     now=NOW + 31_536_000 + 1)
        assert s["pt_payout_total"] >= 1_000 * W
        got = v.redeem_pt("note-r", "alice", 1_000 * W)
        assert got >= 1_000 * W


def test_partial_floor_product():
    v = listed_vault("note-90", floor_bps=9_000)
    v.deposit("note-90", "alice", 1_000 * W, now=NOW)
    v.feed = PriceFeed(price=0.4, updated_at=NOW + 31_536_000)
    s = v.settle("note-90", initial_price=1.0, now=NOW + 31_536_000 + 1)
    assert s["pt_payout_total"] >= 900 * W
    got = v.redeem_pt("note-90", "alice", 1_000 * W)
    assert got >= 900 * W


# -- cap enforcement on-chain --------------------------------------------------------------
def test_cap_clamped_with_event():
    # AC(p18-structured-products-cap-rate-enforcement): a test with an
    # underlying return above the cap receives exactly the capped payout.
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    v.feed = PriceFeed(price=1.35, updated_at=NOW + 31_536_000)
    s = v.settle("note-1", initial_price=1.0, now=NOW + 31_536_000 + 1)
    assert s["clamped"] is True
    assert s["pt_payout_total"] == 1_200 * W  # exactly the cap
    assert any(e["kind"] == "CapClamped" for e in v.events)
    got = v.redeem_pt("note-1", "alice", 1_000 * W)
    assert got == 1_200 * W


# -- full lifecycle --------------------------------------------------------------------------
def test_full_lifecycle_deposit_to_maturity():
    # AC(p18-structured-products-integration-tests): agent lists a
    # product, user deposits, split mints PT/YT, yield accrues, maturity
    # settles, treasury receives 12 bps.
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    v.harvest("note-1", "morpho", 100 * W)
    v.feed = PriceFeed(price=1.10, updated_at=NOW + 31_536_000)  # +10%
    s = v.settle("note-1", initial_price=1.0, now=NOW + 31_536_000 + 1)
    assert s["clamped"] is False
    assert s["pt_payout_total"] == 1_100 * W  # floor + 10% upside
    pt_got = v.redeem_pt("note-1", "alice", 1_000 * W)
    assert pt_got == 1_100 * W
    yt_got = v.claim_yt("note-1", "alice", 1_000 * W)
    assert yt_got == s["yt_residual_total"] > 0
    v.ledger.check_conservation()  # burns keep books balanced
    receipt = v.claim_fees()
    assert receipt["treasury"] == catalog_mod.TREASURY
    assert receipt["amount_wei"] == (
        1_000 * W * 12 // 10_000 + 100 * W * 12 // 10_000)


# -- yield-source failure: non-bricking ----------------------------------------------------------
def test_yield_source_failure_never_bricks_pt():
    # AC(p18-structured-products-yield-sleeve): a yield-source failure
    # never bricks PT redemption; only the modeled upside is forfeited.
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    r = v.harvest("note-1", "morpho", 100 * W, source_ok=False)
    assert r["failed"] is True
    v.feed = PriceFeed(price=1.35, updated_at=NOW + 31_536_000)
    s = v.settle("note-1", initial_price=1.0, now=NOW + 31_536_000 + 1)
    assert s["hedge_ok"] is False
    assert s["pt_payout_total"] == 1_000 * W  # floor intact, upside gone
    got = v.redeem_pt("note-1", "alice", 1_000 * W)
    assert got == 1_000 * W


def test_unallowlisted_yield_source_rejected():
    v = listed_vault()
    with pytest.raises(YieldSourceNotAllowlisted):
        v.harvest("note-1", "evil_source", 10 * W)


# -- access control / edge cases -------------------------------------------------------------------
def test_stale_oracle_reverts_settlement():
    # AC(p18-structured-products-access-control-edge-cases): a
    # stale-oracle settlement attempt reverts rather than settling wrong.
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    v.feed = PriceFeed(price=1.35, updated_at=NOW)  # stale by maturity
    with pytest.raises(StaleOracle):
        v.settle("note-1", initial_price=1.0, now=NOW + 31_536_000 + 1)
    assert "note-1" not in v.settled


def test_unauthorized_actions_revert():
    v = make_vault()
    with pytest.raises(Unauthorized):
        v.list_product(make_terms(), caller="mallory", now=NOW)
    v.list_product(make_terms(), caller="lister", now=NOW)
    with pytest.raises(Unauthorized):
        v.allow_yield_source("note-1", "x", caller="mallory")
    with pytest.raises(Unauthorized):
        v.pause(caller="mallory")


def test_double_settle_reverts():
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    v.feed = PriceFeed(price=1.1, updated_at=NOW + 31_536_000)
    v.settle("note-1", initial_price=1.0, now=NOW + 31_536_000 + 1)
    with pytest.raises(StructuredError):
        v.settle("note-1", initial_price=1.0, now=NOW + 31_536_000 + 2)


def test_settle_before_maturity_reverts():
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    with pytest.raises(StructuredError):
        v.settle("note-1", initial_price=1.0, now=NOW + 100)


def test_emergency_early_maturity():
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    v.feed = PriceFeed(price=1.05, updated_at=NOW + 1_000)
    s = v.settle("note-1", initial_price=1.0, now=NOW + 1_000,
                 emergency=True)
    assert s["pt_payout_total"] == 1_050 * W


def test_param_timelock():
    v = listed_vault()
    v.propose_params("note-1", cap_bps=3_000, caller="lister", now=NOW)
    with pytest.raises(TimelockPending):
        v.execute_params("note-1", caller="lister", now=NOW + 1_000)
    v.execute_params("note-1", caller="lister",
                     now=NOW + TIMELOCK_SECONDS + 1)
    assert v.products["note-1"].cap_bps == 3_000


def test_listing_validation():
    v = make_vault()
    with pytest.raises(StructuredError):
        v.list_product(make_terms("bad", participation_bps=10_001),
                       caller="lister", now=NOW)
    with pytest.raises(StructuredError):
        v.list_product(make_terms("bad", floor_bps=10_001),
                       caller="lister", now=NOW)


# -- fuzz invariants -------------------------------------------------------------------------------
def test_fuzz_invariants():
    # AC(p18-structured-products-invariant-fuzz-tests): PT redemption >=
    # principal floor; payout <= cap rate; PT+YT supply conservation
    # across splits/redeems.
    rng = random.Random(20260929)
    for i in range(200):
        pid = f"fz-{i}"
        floor_bps = rng.choice([9_000, 9_500, 10_000])
        cap_bps = rng.choice([1_000, 2_000, 3_000])
        part_bps = rng.choice([5_000, 7_500, 10_000])
        v = listed_vault(pid, floor_bps=floor_bps, cap_bps=cap_bps,
                         participation_bps=part_bps)
        deposit = rng.randint(100, 10_000) * W
        v.deposit(pid, "alice", deposit, now=NOW)
        v.ledger.check_conservation()
        ret = rng.uniform(-0.6, 0.9)
        v.feed = PriceFeed(price=1.0 + ret, updated_at=NOW + 31_536_000)
        s = v.settle(pid, initial_price=1.0, now=NOW + 31_536_000 + 1)
        floor_total = deposit * floor_bps // 10_000
        cap_total = (deposit * floor_bps // 10_000
                     + deposit * part_bps // 10_000 * cap_bps // 10_000)
        assert s["pt_payout_total"] >= floor_total, "floor invariant"
        assert s["pt_payout_total"] <= cap_total, "cap invariant"
        got = v.redeem_pt(pid, "alice", deposit)
        assert got == s["pt_payout_total"]
        v.ledger.check_conservation()


# -- monitoring --------------------------------------------------------------------------------------
def test_metrics_payload_unknown_on_stale():
    # AC(p18-structured-products-monitoring-hooks): hooks report unknown
    # rather than healthy on stale data.
    v = listed_vault()
    v.deposit("note-1", "alice", 1_000 * W, now=NOW)
    ok = metrics_payload(v, "note-1", data_fresh=True)
    assert ok["status"] == "ok"
    assert 0.0 < ok["pt_coverage_ratio"]
    stale = metrics_payload(v, "note-1", data_fresh=False)
    assert stale["status"] == "unknown"
    missing = metrics_payload(v, "nope", data_fresh=True)
    assert missing["status"] == "unknown"
