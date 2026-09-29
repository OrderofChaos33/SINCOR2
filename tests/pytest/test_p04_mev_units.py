"""Unit tests for the P04 MEV Protection & Capture reference build.

Covers src/sincor2/defi/mev_capture.py — flow metering, sandwich/backrun
detection, bid shading, capture reconciliation, treasury routing,
protection policy, signer deny-list, live gate, and access control behind
SKU SINCOR-DEFI-P04-MEV. Pure logic, no chain, no relay. 33/33 must pass.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.mev_capture import (
    BIDDER_ROLE,
    FLOW_THRESHOLD_USD,
    GAS_BUDGET_PER_SWAP,
    GUARDIAN_ROLE,
    TREASURY,
    AccessControl,
    BackrunFlag,
    Bidder,
    CaptureLedger,
    FlowMeter,
    GasMeter,
    LiveBlockedError,
    LiveGate,
    MEVDetector,
    MEVError,
    ProtectionPolicy,
    ProtectedOrder,
    ProtectionRevert,
    SandwichFlag,
    SignerRegistry,
    SwapEvent,
    ThresholdOutOfBoundsError,
    TreasuryRouter,
    TreasurySignerError,
)


def sw(pool, bh, n, tx, sender, direction, notional, impact, funded_by=""):
    return SwapEvent(pool, bh, n, tx, sender, direction, notional, impact, funded_by)


@pytest.fixture()
def meter():
    return FlowMeter()


@pytest.fixture()
def detector(meter):
    return MEVDetector(meter)


@pytest.fixture()
def bidder():
    return Bidder(bidder_eoa="0xBidderEOA", float_usd=500.0, gas_ceiling_usd=10.0)


BH = "0xblock1"


# -- sandwich detection ------------------------------------------------------
def test_sandwich_true_positive(detector):
    """Rule p04-mev-detection: A->V->B, shared sender, >50bps victim, B reverses A."""
    evs = [
        sw("p1", BH, 1, 0, "attacker", +1, 5_000, 5.0),
        sw("p1", BH, 1, 1, "victim", +1, 50_000, 80.0),
        sw("p1", BH, 1, 2, "attacker", -1, 5_000, -6.0),
    ]
    flags = detector.detect_sandwich(evs)
    assert len(flags) == 1
    assert flags[0].attacker == "attacker"
    assert flags[0].victim_tx_index == 1
    assert flags[0].attributable_notional_usd == 50_000


def test_sandwich_funded_by_relation_counts(detector):
    """Rule p04-mev-detection: funded-by relation attributes the sandwich."""
    evs = [
        sw("p1", BH, 1, 0, "attacker-a", +1, 5_000, 5.0, funded_by="0xfunder"),
        sw("p1", BH, 1, 1, "victim", +1, 50_000, 80.0),
        sw("p1", BH, 1, 2, "attacker-b", -1, 5_000, -6.0, funded_by="0xfunder"),
    ]
    assert len(detector.detect_sandwich(evs)) == 1


def test_sandwich_different_senders_not_flagged(detector):
    """Rule p04-mev-detection: unrelated A/B is not a sandwich."""
    evs = [
        sw("p1", BH, 1, 0, "trader-a", +1, 5_000, 5.0),
        sw("p1", BH, 1, 1, "victim", +1, 50_000, 80.0),
        sw("p1", BH, 1, 2, "trader-b", -1, 5_000, -6.0),
    ]
    assert detector.detect_sandwich(evs) == []


def test_sandwich_no_reversal_not_flagged(detector):
    """Rule p04-mev-detection: B must reverse A's direction."""
    evs = [
        sw("p1", BH, 1, 0, "attacker", +1, 5_000, 5.0),
        sw("p1", BH, 1, 1, "victim", +1, 50_000, 80.0),
        sw("p1", BH, 1, 2, "attacker", +1, 5_000, 6.0),
    ]
    assert detector.detect_sandwich(evs) == []


def test_sandwich_small_victim_impact_not_flagged(detector):
    """Rule p04-mev-detection: victim displacement <= 50 bps is noise."""
    evs = [
        sw("p1", BH, 1, 0, "attacker", +1, 5_000, 5.0),
        sw("p1", BH, 1, 1, "victim", +1, 50_000, 30.0),
        sw("p1", BH, 1, 2, "attacker", -1, 5_000, -6.0),
    ]
    assert detector.detect_sandwich(evs) == []


def test_sub_threshold_flow_is_noise(detector, meter):
    """Rule p04-mev-flow-threshold: below $1,000 attributable flow -> no flag."""
    evs = [
        sw("p1", BH, 1, 0, "attacker", +1, 100, 5.0),
        sw("p1", BH, 1, 1, "victim", +1, 500, 80.0),
        sw("p1", BH, 1, 2, "attacker", -1, 100, -6.0),
    ]
    for e in evs:
        meter.record(e)
    assert not meter.passes_threshold(BH, "p1", 80.0)
    assert detector.detect_sandwich(evs) == []


# -- backrun detection -------------------------------------------------------
def test_backrun_true_positive(detector):
    """Rule p04-mev-detection: reversal capturing >= 30% of displacement."""
    evs = [
        sw("p1", BH, 1, 0, "whale", +1, 100_000, 120.0),
        sw("p1", BH, 1, 1, "arber", -1, 60_000, -80.0),
    ]
    flags = detector.detect_backrun(evs)
    assert len(flags) == 1
    assert flags[0].capture_pct >= 0.30


def test_backrun_below_30pct_not_flagged(detector):
    """Rule p04-mev-detection: weak reversals are not backruns."""
    evs = [
        sw("p1", BH, 1, 0, "whale", +1, 100_000, 120.0),
        sw("p1", BH, 1, 1, "noise", -1, 1_000, -10.0),
    ]
    assert detector.detect_backrun(evs) == []


def test_organic_volatility_not_flagged(detector):
    """Rule p04-mev-detection: organic two-sided flow is not MEV."""
    evs = [
        sw("p1", BH, 1, 0, "mm-a", +1, 20_000, 20.0),
        sw("p1", BH, 1, 1, "mm-b", -1, 20_000, -18.0),
        sw("p1", BH, 1, 2, "retail", +1, 5_000, 8.0),
    ]
    out = detector.detect(evs)
    assert out["sandwich"] == [] and out["backrun"] == []


# -- detection battery: precision/recall -------------------------------------
def test_detection_battery_precision_recall(detector):
    """Acceptance #1: precision >= 90%, recall >= 80%, FP <= 5% on fixtures."""
    positives, negatives = [], []
    for i in range(10):  # 10 known sandwiches
        bh = f"0xb{i}"
        positives.append([
            sw("p1", bh, i, 0, f"atk{i}", +1, 5_000, 5.0),
            sw("p1", bh, i, 1, f"vic{i}", +1, 50_000, 60.0 + i),
            sw("p1", bh, i, 2, f"atk{i}", -1, 5_000, -6.0),
        ])
    for i in range(5):  # 5 known backruns
        bh = f"0xc{i}"
        positives.append([
            sw("p1", bh, i, 0, f"whale{i}", +1, 100_000, 120.0),
            sw("p1", bh, i, 1, f"arb{i}", -1, 60_000, -80.0),
        ])
    for i in range(40):  # 40 organic blocks
        bh = f"0xd{i}"
        negatives.append([
            sw("p1", bh, i, 0, f"mm{i}a", +1, 20_000, 15.0 + (i % 5)),
            sw("p1", bh, i, 1, f"mm{i}b", -1, 20_000, -14.0 - (i % 5)),
        ])
    tp = sum(1 for evs in positives
             for _ in [detector.detect(evs)] if _.get("sandwich") or _.get("backrun"))
    tp = sum(1 for evs in positives
             if detector.detect(evs)["sandwich"] or detector.detect(evs)["backrun"])
    fp = sum(1 for evs in negatives
             if detector.detect(evs)["sandwich"] or detector.detect(evs)["backrun"])
    precision = tp / max(tp + fp, 1)
    recall = tp / len(positives)
    assert precision >= 0.90, precision
    assert recall >= 0.80, recall
    assert fp / len(negatives) <= 0.05


# -- bid shading -------------------------------------------------------------
def test_bid_shading_emits_bid(bidder):
    """Acceptance #7: expected_net >= 3x gas -> bid emitted."""
    flag = SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95)
    intent, reason = bidder.evaluate(flag, gas_cost_usd=5.0,
                                     estimated_capture_usd=25.0)
    assert intent is not None and reason == "bid"
    assert intent.expected_net_usd == 20.0
    assert intent.dry_run and not intent.executed


def test_bid_shading_stands_down(bidder):
    """Rule p04-mev-bid-shading: expected_net < 3x gas -> stand down."""
    flag = SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95)
    intent, reason = bidder.evaluate(flag, gas_cost_usd=5.0,
                                     estimated_capture_usd=15.0)
    assert intent is None
    assert "shading" in reason


def test_bidder_stands_down_when_depleted():
    """Tip: bidder stands down when float < 2x gas reserve, never fallback."""
    b = Bidder(bidder_eoa="0xBidderEOA", float_usd=100.0, gas_ceiling_usd=60.0)
    flag = SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95)
    intent, reason = b.evaluate(flag, gas_cost_usd=5.0, estimated_capture_usd=100.0)
    assert intent is None and "depleted" in reason


def test_sub_threshold_flow_produces_zero_bids(bidder, detector):
    """Acceptance #7: sub-threshold flow produces zero bids end-to-end."""
    evs = [sw("p1", BH, 1, 0, "a", +1, 100, 60.0),
           sw("p1", BH, 1, 1, "b", -1, 100, -40.0)]
    flags = detector.detect(evs)
    intents = [bidder.evaluate(f, 5.0, 25.0)
               for fl in flags.values() for f in fl]
    assert intents == [] or all(i[0] is None for i in intents)


# -- no_treasury_key ---------------------------------------------------------
def test_bidder_refuses_treasury_eoa():
    """Acceptance #3: treasury EOA as bidder signer fails closed."""
    with pytest.raises(TreasurySignerError):
        Bidder(bidder_eoa=TREASURY, float_usd=500.0, gas_ceiling_usd=10.0)


def test_signer_registry_check_case_insensitive():
    reg = SignerRegistry()
    with pytest.raises(TreasurySignerError):
        reg.check(TREASURY.upper())
    reg.check("0xSomeOtherEOA")  # no raise


def test_config_audit_catches_treasury_signer():
    """Acceptance #3: CI-grep layer — treasury in a signer field fails."""
    reg = SignerRegistry()
    with pytest.raises(TreasurySignerError):
        reg.audit_config({"bidder": {"signer_eoa": TREASURY}})
    reg.audit_config({"bidder": {"signer_eoa": "0xBidderEOA"}})  # clean


# -- capture reconciliation --------------------------------------------------
def test_reconcile_within_1pct_ok(bidder):
    """Acceptance #2: estimate within 1% of actual -> no review."""
    ledger = CaptureLedger(bidder)
    ledger.record_capture("c1", "p1", "WETH", 1_000_000, 1_005_000)
    out = ledger.reconcile("c1", 1_000_000)
    assert not out["under_review"]
    assert out["deviation"] <= 0.01


def test_reconcile_breach_flags_review_and_decays(bidder):
    """Rule p04-mev-reconciliation: >1% deviation -> review + decay."""
    ledger = CaptureLedger(bidder)
    before = bidder.confidence
    ledger.record_capture("c1", "p1", "WETH", 1_000_000, 1_100_000)
    out = ledger.reconcile("c1", 1_000_000)
    assert out["under_review"]
    assert bidder.confidence == pytest.approx(before * 0.9)


def test_reorg_replay_is_noop():
    """Acceptance #6: same capture_id replayed -> safe no-op."""
    ledger = CaptureLedger()
    r1 = ledger.record_capture("c1", "p1", "WETH", 1_000_000, 1_000_000)
    r2 = ledger.record_capture("c1", "p1", "WETH", 9_999_999, 9_999_999)
    assert r1 is r2
    assert r2.actual_wei == 1_000_000


# -- treasury routing ----------------------------------------------------------
def test_fee_exact_20bps_on_1eth():
    """Acceptance #2: 20 bps fee exact to the wei on 1.0 ETH-equivalent."""
    assert TreasuryRouter.fee_wei(10**18) == 2 * 10**15


def test_route_conservation_and_pull_claim():
    """Acceptance #2: fee + residual == amount; treasury pull-claims."""
    router = TreasuryRouter()
    out = router.route("c1", 10**18)
    assert out["fee_wei"] + out["residual_wei"] == 10**18
    assert router.claim(TREASURY) == 10**18
    assert router.claim(TREASURY) == 0  # double claim -> 0


def test_reverting_claimant_does_not_block_others():
    """Rule p04-mev-non-bricking: one bad claimant never blocks others."""
    router = TreasuryRouter(reverting={"0xbad"})
    router.route("c1", 10**18)
    with pytest.raises(MEVError):
        router.claim("0xbad")
    assert router.claim(TREASURY) == 10**18


# -- protection ------------------------------------------------------------------
def test_protection_slippage_guardrail_reverts():
    """Rule p04-mev-protection: >100 bps deviation reverts opted-in orders."""
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=102.0,
                           opted_in=True)
    with pytest.raises(ProtectionRevert):
        pol.evaluate(order)


def test_protection_within_guardrail_passes():
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=100.5,
                           opted_in=True)
    out = pol.evaluate(order)
    assert out["allowed"] and out["slippage_bps"] == pytest.approx(50.0)


def test_protection_toxic_block_reverts():
    """Rule p04-mev-protection: toxic block before swap -> auto-revert."""
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=100.1,
                           opted_in=True, block_flagged_toxic_before_swap=True)
    with pytest.raises(ProtectionRevert):
        pol.evaluate(order)


def test_unprotected_order_ignores_guardrail():
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=110.0,
                           opted_in=False)
    assert pol.evaluate(order)["allowed"]


def test_loss_reduction_target():
    """Acceptance #4 model: protection must cut sandwich loss >= 80%."""
    reduction = ProtectionPolicy.loss_reduction_pct(100.0, 15.0)
    assert reduction >= 80.0


# -- gas / live gate / access ------------------------------------------------------
def test_gas_budget_per_swap():
    """Acceptance #5: hot-path metering <= 5,000 gas per swap."""
    assert GasMeter.PER_SWAP_GAS <= GAS_BUDGET_PER_SWAP
    assert GasMeter.assert_budget(1_000) == 1_000 * GasMeter.PER_SWAP_GAS


def test_live_gate_blocks_by_default(bidder):
    """Acceptance #6 gate: live submission blocked until release."""
    intent, _ = bidder.evaluate(
        SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95), 5.0, 25.0)
    with pytest.raises(LiveBlockedError):
        bidder.submit_live(intent)


def test_live_gate_release_path():
    gate = LiveGate()
    with pytest.raises(LiveBlockedError):
        gate.assert_live_allowed()
    gate.release("founder-sig-marker")
    gate.assert_live_allowed()  # no raise


def test_guardian_threshold_tuning_bounds():
    """Rule p04-mev-threshold: guardian tunes within +/-50% only."""
    ac = AccessControl()
    ac.grant(GUARDIAN_ROLE, "0xguardian")
    ac.tune_threshold("0xguardian", FLOW_THRESHOLD_USD * 1.5)
    with pytest.raises(ThresholdOutOfBoundsError):
        ac.tune_threshold("0xguardian", FLOW_THRESHOLD_USD * 1.51)
    with pytest.raises(MEVError):
        ac.tune_threshold("0xintruder", FLOW_THRESHOLD_USD)


def test_flow_meter_idempotent_record(meter):
    e = sw("p1", BH, 1, 0, "a", +1, 5_000, 10.0)
    assert meter.record(e) is True
    assert meter.record(e) is False  # duplicate: safe no-op
    assert meter.block_flow_usd(BH, "p1") == 5_000
