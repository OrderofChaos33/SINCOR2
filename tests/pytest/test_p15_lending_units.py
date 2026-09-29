"""Unit tests for the P15 Lending Protocol Optimizer reference build.

Covers src/sincor2/defi/lending_optimizer.py — rehypothecation
accounting, utilization-band reverts, the morpho_only_live venue gate,
10 bps fee math, the ML risk model (train/monotonic/publish/drift),
the health-drop hedge router, and token-bound loan vault semantics
behind SKU SINCOR-DEFI-P15-LEND. Pure logic, no chain.

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

from src.sincor2.defi.lending_optimizer import (
    DRY_RUN,
    FEE_BPS,
    HEDGE_TRIGGER_HF,
    LIQUIDITY_BUFFER_PCT,
    MORPHO_GAUNTLET_USDC,
    REQUIREMENT_BASE,
    REQUIREMENT_SPREAD,
    UTIL_BAND_HI,
    HedgeRouter,
    InsufficientLiquidity,
    LendingError,
    LendingPool,
    LoanVault,
    ModelStale,
    Position,
    RehypothecationEngine,
    RiskModel,
    UtilizationBreach,
    VaultViolation,
    VenueGate,
    VenueNotAllowlisted,
    WalletFeatures,
    accrue,
    health_factor,
    status_payload,
    utilization,
)
from src.sincor2.defi import catalog as catalog_mod

W = 10**18


def make_pool(supply_wei: int = 1_000 * W) -> LendingPool:
    pool = LendingPool()
    pool.supply("lender1", supply_wei, now=1_000_000.0)
    return pool


# -- interest / utilization math ----------------------------------------------------
def test_accrue_simple_interest_exact():
    # AC(p15-economic-fee-model): interest/fee model with utilization table.
    i = accrue(1_000 * W, 0.08, 31_536_000)
    assert i == 80 * W  # 8% of 1000 over one year


def test_interest_accrues_to_borrower_accounts():
    # Regression: accrued interest must land on per-borrower debts, not
    # just the pool total — otherwise repay() underpays and dust debt
    # becomes unrepayable.
    pool = make_pool(2_000 * W)
    pool.borrow("b1", 500 * W, now=1_000_001.0)
    pool.borrow("b2", 300 * W, now=1_000_001.0)
    pool._accrue_interest(now=1_000_001.0 + 31_536_000)  # one year
    pool.check_invariants()
    assert pool.borrows["b1"] > 500 * W
    assert pool.borrows["b2"] > 300 * W
    # Full repayment clears everything.
    pool.repay("b1", pool.borrows["b1"], now=1_000_001.0 + 31_536_000)
    pool.repay("b2", pool.borrows["b2"], now=1_000_001.0 + 31_536_000)
    assert pool.total_borrows == 0
    pool.check_invariants()


def test_utilization_zero_supply():
    assert utilization(0, 0) == 0.0
    assert utilization(500, 1_000) == 0.5


# -- utilization band gate ----------------------------------------------------------
def test_borrow_inside_band_ok():
    # AC(p15-utilization-band-gate): out-of-band borrows revert.
    pool = make_pool()
    pool.borrow("b1", 800 * W, now=1_000_001.0)
    assert utilization(pool.total_borrows, pool.total_supply) <= UTIL_BAND_HI


def test_borrow_outside_band_reverts():
    pool = make_pool()
    with pytest.raises(UtilizationBreach):
        pool.borrow("b1", 900 * W, now=1_000_001.0)  # 0.90 > 0.85
    assert pool.total_borrows == 0


def test_band_update_boundary():
    # AC(p15-utilization-band-gate): band updates governable, validated.
    pool = make_pool()
    with pytest.raises(ValueError):
        pool.set_band(0.9, 0.5)
    pool.set_band(0.5, 0.9)
    pool.borrow("b1", 860 * W, now=1_000_001.0)
    assert pool.total_borrows == 860 * W


def test_supply_repay_withdraw_round_trip():
    pool = make_pool()
    pool.borrow("b1", 500 * W, now=1_000_001.0)
    # Repay everything owed, including dust interest (repay accrues
    # first, so a second pass clears the remainder).
    pool.repay("b1", pool.borrows["b1"], now=1_000_002.0)
    if pool.borrows["b1"]:
        pool.repay("b1", pool.borrows["b1"], now=1_000_002.0)
    assert pool.total_borrows == 0
    # Interest accrued to the lender: withdraw the full balance.
    pool.withdraw("lender1", pool.balances["lender1"], now=1_000_003.0)
    assert pool.total_supply == 0
    assert sum(pool.balances.values()) == 0


def test_withdraw_blocked_beyond_liquid_supply():
    # Lent-out funds are illiquid until repaid: withdrawing more than
    # supply - borrows reverts instead of breaking solvency.
    pool = make_pool(1_000 * W)
    pool.borrow("b1", 500 * W, now=1_000_001.0)
    with pytest.raises(InsufficientLiquidity):
        pool.withdraw("lender1", 600 * W, now=1_000_002.0)
    liquid = pool.total_supply - pool.total_borrows
    assert liquid > 0
    pool.withdraw("lender1", liquid, now=1_000_002.0)  # exact liquid ok
    assert pool.total_supply - pool.total_borrows == 0


# -- venue gate ---------------------------------------------------------------------
def test_venue_gate_live_rejects_non_morpho():
    # AC(p15-morpho-gauntlet-adapter): live rejects other venues.
    gate = VenueGate(allowlist=[MORPHO_GAUNTLET_USDC, "stub_venue"])
    gate.check(MORPHO_GAUNTLET_USDC, live=True)  # accepted
    with pytest.raises(VenueNotAllowlisted):
        gate.check("stub_venue", live=True)


def test_venue_gate_dry_run_permits_stubs():
    # AC(p15-morpho-gauntlet-adapter): dry-run permits stub venues.
    gate = VenueGate(allowlist=[MORPHO_GAUNTLET_USDC, "stub_venue"])
    gate.check("stub_venue", live=False)  # no raise
    with pytest.raises(VenueNotAllowlisted):
        gate.check("unknown_venue", live=False)


# -- rehypothecation engine ---------------------------------------------------------
def test_rehypothecation_round_trip_holds_invariants():
    # AC(p15-rehypothecation-engine): supply/redeem round-trips hold
    # accounting invariants with rehypothecation active.
    pool = make_pool(1_000 * W)
    gate = VenueGate()
    eng = RehypothecationEngine(pool, gate, dry_run=True)
    eng.deploy(MORPHO_GAUNTLET_USDC, 600 * W)
    eng.check_invariants()
    assert eng.cash() == 400 * W
    eng.recall(MORPHO_GAUNTLET_USDC, 600 * W)
    eng.check_invariants()
    assert eng.cash() == 1_000 * W


def test_rehyp_cap_and_buffer_enforced():
    pool = make_pool(1_000 * W)
    eng = RehypothecationEngine(pool, VenueGate(), dry_run=True)
    with pytest.raises(LendingError):  # > 70% cap
        eng.deploy(MORPHO_GAUNTLET_USDC, 800 * W)
    eng.deploy(MORPHO_GAUNTLET_USDC, 700 * W)  # exactly 70% ok
    with pytest.raises(LendingError):  # rehyp cap is hit first
        eng.deploy(MORPHO_GAUNTLET_USDC, 1)
    eng.check_invariants()


def test_withdraw_served_with_auto_recall():
    # Lenders never wait on locked yield: withdrawals route through the
    # engine, which recalls any shortfall from venues first.
    pool = make_pool(1_000 * W)
    eng = RehypothecationEngine(pool, VenueGate(), dry_run=True)
    eng.deploy(MORPHO_GAUNTLET_USDC, 700 * W)
    eng.withdraw("lender1", 300 * W, now=2_000_000.0)  # inside buffer
    eng.check_invariants()
    # A withdrawal larger than the buffer triggers recall, not a revert.
    eng.withdraw("lender1", 600 * W, now=2_000_001.0)
    eng.check_invariants()
    assert pool.balances["lender1"] == 100 * W
    assert sum(eng.deployed.values()) == 100 * W


# -- fee math -----------------------------------------------------------------------
def test_fee_10bps_on_interest_pull_based():
    # AC(p15-fee-routing-treasury): 10 bps on interest, pull-based claim.
    pool = LendingPool()
    pool.supply("lender1", 1_000 * W, now=0.0)
    pool.borrow("b1", 500 * W, now=0.0)
    pool._accrue_interest(now=float(31_536_000))  # one year at 8%
    interest = accrue(500 * W, 0.08, 31_536_000)
    assert pool.fee_owed_wei == interest * FEE_BPS // 10_000
    assert pool.fee_owed_wei > 0
    receipt = pool.claim_fees()
    assert receipt["treasury"] == catalog_mod.TREASURY
    assert receipt["fee_bps"] == 10
    assert receipt["amount_wei"] == interest * FEE_BPS // 10_000
    assert pool.fee_owed_wei == 0


def test_treasury_immutable_matches_catalog():
    import src.sincor2.defi.lending_optimizer as m

    assert m.TREASURY == catalog_mod.TREASURY


# -- ML risk model ------------------------------------------------------------------
def _trained_model() -> RiskModel:
    m = RiskModel()
    m.fit()
    return m


def test_model_trains_and_scores_monotonic():
    # AC(p15-ml-risk-model): trains on scripted histories; monotonic risk
    # scores on a held-out set.
    m = _trained_model()
    good = WalletFeatures(900, 1200, 0, 2.4, 0.05)
    mid = WalletFeatures(450, 400, 1, 1.5, 0.20)
    bad = WalletFeatures(45, 30, 3, 1.05, 1.20)
    s_good, s_mid, s_bad = m.score(good), m.score(mid), m.score(bad)
    assert s_good < s_mid < s_bad
    assert all(0.0 <= s <= 1.0 for s in (s_good, s_mid, s_bad))


def test_requirement_increases_with_risk():
    m = _trained_model()
    r_good = m.requirement(WalletFeatures(900, 1200, 0, 2.4, 0.05))
    r_bad = m.requirement(WalletFeatures(45, 30, 3, 1.05, 1.20))
    assert REQUIREMENT_BASE <= r_good < r_bad <= REQUIREMENT_BASE + REQUIREMENT_SPREAD


def test_publish_requirements_consumable():
    m = _trained_model()
    out = m.publish_requirements({"alice": WalletFeatures(700, 900, 0, 2.1, 0.08)})
    p = out["alice"]
    assert p["model_version"] == m.version
    assert "digest" in p and p["published_at"] > 0
    assert REQUIREMENT_BASE <= p["requirement"]


def test_drift_detection_freezes_requirements():
    # AC(p15-ml-risk-model): drift detection flags distribution shift.
    m = _trained_model()
    shifted = [WalletFeatures(5, 2, 8, 1.01, 5.0) for _ in range(20)]
    res = m.check_drift(shifted)
    assert res["drift"] is True
    with pytest.raises(ModelStale):
        m.requirement(WalletFeatures(700, 900, 0, 2.1, 0.08))
    # Retrain clears the flag.
    m.fit()
    assert m.check_drift([WalletFeatures(700, 900, 0, 2.1, 0.08)])["drift"] is False


# -- health-drop hedge router -------------------------------------------------------
def test_health_drop_router_recovers_position():
    # AC(p15-health-hedge-router): a simulated health drop routes
    # collateral into hedging and the position recovers above the
    # warning threshold in the dry-run scenario.
    router = HedgeRouter(dry_run=True)
    # Healthy pre-shock (1.45); a 14% drop pushes it under the 1.25
    # warning threshold, where the router intervenes.
    pos = Position(borrower="bob", collateral_wei=1_450 * W,
                   debt_wei=1_000 * W, collateral_factor=1.0)
    assert health_factor(pos) == pytest.approx(1.45)
    result = router.simulate_health_drop(pos, shock=0.86)
    assert result["plan"] is not None
    assert result["plan"]["hf_before"] == pytest.approx(1.45 * 0.86)
    assert result["plan"]["hf_before"] < HEDGE_TRIGGER_HF
    assert result["recovered"] is True
    assert result["plan"]["hf_after"] >= 1.30
    assert result["plan"]["hedge_pct"] <= 0.25
    assert result["would_liquidate"] is False  # never near liquidation


def test_no_plan_when_shock_stays_healthy():
    router = HedgeRouter(dry_run=True)
    pos = Position(borrower="carol", collateral_wei=2_000 * W,
                   debt_wei=1_000 * W, collateral_factor=1.0)
    assert router.plan_for(pos, shock=0.85) is None


def test_unrecoverable_drop_returns_no_plan():
    # A catastrophic drop the max hedge cannot fix: no phantom plan.
    router = HedgeRouter(dry_run=True)
    pos = Position(borrower="dave", collateral_wei=1_100 * W,
                   debt_wei=1_000 * W, collateral_factor=1.0)
    result = router.simulate_health_drop(pos, shock=0.50)
    assert result["plan"] is None
    assert result["would_liquidate"] is True


# -- token-bound loan vault ---------------------------------------------------------
def test_vault_strategy_execution_never_moves_principal():
    # AC(p15-erc6551-loan-vault): agent strategies run inside the TBA;
    # borrower ownership and withdrawal rights remain intact.
    v = LoanVault(token_id=7, owner="dave")
    v.deposit_collateral(500 * W)
    v.debt_wei = 200 * W
    v.allow_strategy("yield_loop_v1")
    rec = v.execute_strategy("yield_loop_v1", moves_principal=False)
    assert rec["owner"] == "dave"
    with pytest.raises(VaultViolation):
        v.execute_strategy("unlisted", moves_principal=False)
    with pytest.raises(VaultViolation):
        v.execute_strategy("yield_loop_v1", moves_principal=True)
    # Owner can withdraw only excess over required collateral.
    req = 1.25
    excess = 500 * W - int(200 * W * req)
    v.withdraw_excess(excess, req, caller="dave")
    assert v.collateral_wei == 500 * W - excess
    with pytest.raises(VaultViolation):
        v.withdraw_excess(1, req, caller="mallory")


# -- integration: score -> loan -> health drop -> hedge ------------------------------
def test_integration_score_open_drop_hedge():
    # AC(p15-integration-tests): score a borrower, open a loan, simulate a
    # health drop, verify the hedge router intervenes without liquidation.
    model = _trained_model()
    feats = WalletFeatures(450, 400, 1, 1.5, 0.20)
    req = model.requirement(feats)
    assert req > 1.0  # model prices this wallet as risky

    pool = make_pool(20_000 * W)
    collateral = 1_450 * W
    debt = int(collateral / req * 0.90)  # inside the model requirement
    pool.borrow("bob", debt, now=3_000_000.0)

    pos = Position(borrower="bob", collateral_wei=collateral,
                   debt_wei=debt, collateral_factor=1.0, requirement=req)
    hf_pre = health_factor(pos)
    assert hf_pre > HEDGE_TRIGGER_HF  # opens healthy

    # Size the shock so the drop lands just under the warning threshold.
    shock = 1.20 / hf_pre
    router = HedgeRouter(dry_run=True)
    result = router.simulate_health_drop(pos, shock=shock)
    assert result["plan"] is not None, "router must intervene on the drop"
    assert result["recovered"] is True, "position recovers above target"
    assert result["would_liquidate"] is False
    assert result["plan"]["hf_after"] >= 1.30
    # Fees still accrue and route correctly through the whole flow.
    receipt = pool.claim_fees()
    assert receipt["treasury"] == catalog_mod.TREASURY


# -- fuzz invariants ----------------------------------------------------------------
def _rand_pool(rng: random.Random) -> tuple[LendingPool, RehypothecationEngine]:
    pool = LendingPool()
    t = 10_000_000.0
    pool.supply("l", 1_000 * W, now=t)
    eng = RehypothecationEngine(pool, VenueGate(), dry_run=True)
    return pool, eng


def test_fuzz_invariants():
    # AC(p15-invariant-fuzz-tests): lender assets always cover
    # rehypothecated exposure plus buffer accounting; every borrow leaves
    # utilization inside the band (the band gates *borrows* — interest
    # accrual and withdrawals legitimately move the live ratio after);
    # dry-run venues always gated.
    rng = random.Random(20260929)
    for i in range(300):
        pool, eng = _rand_pool(rng)
        t = 10_000_000.0 + i
        for step in range(6):
            op = rng.choice(
                ["supply", "borrow", "repay", "deploy", "recall", "withdraw"])
            try:
                if op == "supply":
                    pool.supply("l", rng.randint(1, 200) * W, now=t)
                elif op == "borrow":
                    pool.borrow("b", rng.randint(1, 300) * W, now=t)
                    assert utilization(pool.total_borrows,
                                       pool.total_supply) <= pool.util_hi
                elif op == "repay":
                    pool.repay("b", rng.randint(1, 100) * W, now=t)
                elif op == "deploy":
                    eng.deploy(MORPHO_GAUNTLET_USDC, rng.randint(1, 100) * W)
                elif op == "withdraw":
                    bal = pool.balances.get("l", 0)
                    if bal:
                        eng.withdraw("l", rng.randint(1, bal // W or 1) * W,
                                     now=t)
                else:
                    pos = eng.deployed.get(MORPHO_GAUNTLET_USDC, 0)
                    if pos:
                        eng.recall(MORPHO_GAUNTLET_USDC,
                                   rng.randint(1, pos // W or 1) * W)
            except (LendingError, ValueError):
                pass
            t += 1.0
        eng.check_invariants()
        pool.check_invariants()
        assert pool.total_supply >= pool.total_borrows  # solvency
        assert pool.fee_owed_wei >= 0


def test_status_payload_schema():
    pool = make_pool()
    p = status_payload(pool)
    assert p["product"] == "SINCOR-DEFI-P15-LEND"
    assert 0.0 <= p["utilization"] <= 1.0
