"""Added coverage for core paths the platform test files exercise thinly:

- protected-order routing behaviour (ProtectionPolicy boundary and
  precedence rules — the module's user-protection surface);
- submission behaviour (Bidder.submit_live past the gate, signer
  re-check at submission time, bid-intent shape for both flag kinds);
- configuration behaviour (per-instance detector thresholds overriding
  the module defaults, threshold-gate exact boundaries, ledger and
  access-control edges).

All behaviour pinned here is the extracted module's existing behaviour —
no semantics were changed during packaging.
"""

from __future__ import annotations

import pytest

from p24_mev_protection.mev_capture import (
    AccessControl,
    BackrunFlag,
    Bidder,
    CaptureLedger,
    FlowMeter,
    GasMeter,
    LiveGate,
    MEVDetector,
    MEVError,
    ProtectionPolicy,
    ProtectedOrder,
    ProtectionRevert,
    SandwichFlag,
    SwapEvent,
    TreasurySignerError,
    commitment_id,
)

BH = "0xblockX"


def sw(pool, tx, sender, direction, notional, impact, funded_by=""):
    return SwapEvent(pool, BH, 1, tx, sender, direction, notional, impact, funded_by)


# -- protected-order routing: boundaries & precedence -------------------------

def test_protection_exactly_at_guardrail_passes():
    """Slippage == guardrail (100 bps) is allowed; the rule reverts only above it."""
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=101.0,
                           opted_in=True)
    out = pol.evaluate(order)
    assert out["allowed"] and out["slippage_bps"] == pytest.approx(100.0)


def test_protection_custom_guardrail_is_respected():
    """A tighter configured guardrail reverts flow the default would allow."""
    pol = ProtectionPolicy(guardrail_bps=50)
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=100.75,
                           opted_in=True)
    with pytest.raises(ProtectionRevert):
        pol.evaluate(order)
    # and the default policy allows the same order
    assert ProtectionPolicy().evaluate(order)["allowed"]


def test_protection_toxic_flag_takes_precedence_over_slippage():
    """Toxic-block revert fires even when slippage is inside the guardrail."""
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=100.1,
                           opted_in=True, block_flagged_toxic_before_swap=True)
    with pytest.raises(ProtectionRevert, match="toxic"):
        pol.evaluate(order)


def test_protection_downward_slippage_uses_absolute_deviation():
    pol = ProtectionPolicy()
    order = ProtectedOrder("o1", quoted_price=100.0, executed_price=98.0,
                           opted_in=True)
    with pytest.raises(ProtectionRevert):
        pol.evaluate(order)


def test_loss_reduction_edge_cases():
    assert ProtectionPolicy.loss_reduction_pct(0.0, 0.0) == 0.0
    assert ProtectionPolicy.loss_reduction_pct(100.0, 150.0) == 0.0  # never negative
    assert ProtectionPolicy.loss_reduction_pct(100.0, 0.0) == 100.0


# -- submission behaviour -------------------------------------------------------

def _released_bidder():
    gate = LiveGate()
    gate.release("founder-sig-marker")
    return Bidder(bidder_eoa="0xBidderEOA", float_usd=500.0,
                  gas_ceiling_usd=10.0, live_gate=gate)


def test_submit_live_after_release_has_no_execution_path():
    """Past the gate, submission still cannot execute: the reference build
    deliberately ships no execution path (NotImplementedError)."""
    bidder = _released_bidder()
    intent, reason = bidder.evaluate(
        SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95), 5.0, 25.0)
    assert reason == "bid"
    with pytest.raises(NotImplementedError):
        bidder.submit_live(intent)


def test_submit_live_rechecks_signer_deny_list():
    """The deny-list is enforced again at submission time, not only at
    construction: swapping the bidder EOA to the treasury address after
    construction fails closed at submit."""
    from p24_mev_protection.mev_capture import TREASURY

    bidder = _released_bidder()
    intent, _ = bidder.evaluate(
        SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95), 5.0, 25.0)
    bidder.bidder_eoa = TREASURY
    with pytest.raises(TreasurySignerError):
        bidder.submit_live(intent)


def test_evaluate_backrun_flag_kind_and_intent_shape():
    bidder = Bidder(bidder_eoa="0xBidderEOA", float_usd=500.0, gas_ceiling_usd=10.0)
    flag = BackrunFlag(displacer_tx_index=0, reversal_tx_index=1,
                       displacement_bps=120.0, captured_bps=80.0,
                       capture_pct=0.4, confidence=0.85)
    intent, reason = bidder.evaluate(flag, gas_cost_usd=5.0,
                                     estimated_capture_usd=30.0)
    assert reason == "bid" and intent is not None
    assert intent.flag_kind == "backrun"
    assert intent.dry_run is True and intent.executed is False
    as_dict = intent.to_dict()
    assert as_dict["expected_net_usd"] == 25.0
    assert as_dict["details"]["confidence"] == 0.85


def test_evaluate_shading_boundary_exactly_3x_bids():
    """expected_net == 3x gas_cost exactly satisfies the shading rule."""
    bidder = Bidder(bidder_eoa="0xBidderEOA", float_usd=500.0, gas_ceiling_usd=10.0)
    flag = SandwichFlag("atk", 1, 0, 2, 50_000, 80.0, 0.95)
    intent, reason = bidder.evaluate(flag, gas_cost_usd=5.0,
                                     estimated_capture_usd=20.0)  # net 15 == 3*5
    assert intent is not None and reason == "bid"


# -- configuration behaviour ----------------------------------------------------

def test_detector_per_instance_thresholds_override_defaults():
    """MEVDetector configuration: instance thresholds replace the module
    defaults for the flow gate (both limbs)."""
    meter = FlowMeter()
    strict = MEVDetector(meter)  # defaults: $1,000 / 50 bps
    lenient = MEVDetector(meter, flow_threshold_usd=100.0, impact_flag_bps=10)
    evs = [
        sw("p1", 0, "attacker", +1, 5_000, 5.0),
        sw("p1", 1, "victim", +1, 500, 20.0),
        sw("p1", 2, "attacker", -1, 5_000, -6.0),
    ]
    # victim impact 20 bps: above the lenient instance's 10 bps flag band
    # only if the detector's sandwich rule used it — the rule itself also
    # requires victim displacement > impact_flag_bps, so use a detector
    # whose band the 20 bps victim clears and whose $ gate the $500 clears.
    assert strict.detect_sandwich(evs) == []          # 20 bps <= 50, $500 < $1,000
    assert len(lenient.detect_sandwich(evs)) == 1     # both limbs configured down


def test_threshold_gate_exact_boundaries():
    meter = FlowMeter()
    meter.record(sw("p1", 0, "a", +1, 1_000.0, 50.0))
    assert meter.passes_threshold(BH, "p1", 50.0) is True    # both limbs at bound
    assert meter.passes_threshold(BH, "p1", -50.0) is True   # impact is absolute
    assert meter.passes_threshold(BH, "p1", 49.9) is False
    meter2 = FlowMeter()
    meter2.record(sw("p1", 0, "a", +1, 999.99, 80.0))
    assert meter2.passes_threshold(BH, "p1", 80.0) is False  # notional just under


def test_gas_meter_zero_swaps():
    assert GasMeter.gas_for_swaps(0) == 0
    assert GasMeter.PER_SWAP_GAS == 600


def test_reconcile_zero_actual_is_max_deviation_review():
    ledger = CaptureLedger()
    ledger.record_capture("c1", "p1", "WETH", 0, 100)
    out = ledger.reconcile("c1", 0)
    assert out["under_review"] is True
    assert out["deviation"] == pytest.approx(100.0)


def test_reconcile_unknown_capture_raises_keyerror():
    ledger = CaptureLedger()
    with pytest.raises(KeyError):
        ledger.reconcile("missing", 1)


def test_access_control_role_require_and_pause_guard():
    ac = AccessControl()
    with pytest.raises(MEVError):
        ac.require("BIDDER_ROLE", "0xnobody")
    ac.grant("BIDDER_ROLE", "0xbidder")
    ac.require("BIDDER_ROLE", "0xbidder")  # no raise
    with pytest.raises(MEVError):
        ac.pause("0xbidder")  # pause is a guardian power, not a bidder power


def test_commitment_id_deterministic_and_sensitive():
    assert commitment_id("a", "b") == commitment_id("a", "b")
    assert commitment_id("a", "b") != commitment_id("a", "c")
    assert len(commitment_id("x")) == 16
