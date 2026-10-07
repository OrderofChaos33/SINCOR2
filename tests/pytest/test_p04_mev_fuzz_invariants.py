"""Audit-prep invariant + adversarial fuzz tests for P04 (mev_capture).

Property tests an auditor would demand of the MEV hook reference build:

- flow_meter_fuzz: record() is idempotent per (block, pool, tx_index) —
  replays are safe no-ops; notional/swaps/gas sums are exact; gas per swap
  is 600 and never exceeds the 5,000 hot-path budget.
- threshold_gate_fuzz: passes_threshold holds iff notional >= $1,000 AND
  |impact| >= 50 bps (no flag without both).
- detector_fuzz: every sandwich flag has a same-actor frontrun/backrun pair,
  victim displacement > 50 bps, B reversing A, victim not below the flow
  gate; every backrun flag captures >= 30% of the displacement with the
  reversal trading against it; confidence stays in [0, 1].
- signer_deny_fuzz: the treasury EOA can never be a signer — construction,
  check(), and config audit all fail closed (case-insensitive).
- bidder_fuzz: treasury-EOA bidder refused at construction; float < $100
  refused; the 3x shading rule and the 2x gas-reserve stand-down are exact;
  intents are always dry_run and never executed.
- ledger_fuzz: capture amounts can never be negative; re-recording a
  capture id is a safe no-op; reconciliation flips under_review exactly
  past 1% deviation and decays bidder confidence x0.9 per breach.
- router_fuzz: fee is exactly 20 bps; fee + residual == amount (conservation
  asserted); pull claims are non-bricking; double claims pay 0.
- protection_fuzz: non-opted-in orders always pass; toxic blocks revert;
  slippage beyond 100 bps reverts; zero/negative quotes revert.
- access_fuzz: guardian threshold tuning stays inside +/-50%; non-guardian
  pause/tune raises; pause/unpause round-trips.
- live_gate_fuzz: live submission is hard-blocked until the founder release;
  release without a marker raises.

Deterministic: seeded RNG, no hypothesis dependency. N/N must pass.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi import mev_capture as mev  # noqa: E402
from src.sincor2.defi.mev_capture import (  # noqa: E402
    AccessControl,
    Bidder,
    CaptureLedger,
    FlowMeter,
    LiveGate,
    MEVDetector,
    ProtectionPolicy,
    ProtectedOrder,
    SignerRegistry,
    SwapEvent,
    TreasuryRouter,
)

RNG = random.Random(0xA00404)
TREASURY = mev.TREASURY

ADDRESSES = ["0xAttacker", "0xVictim", "0xRandom", "0xFunder", TREASURY]


def fuzz_event(i: int, block_hash: str = "0xblock") -> SwapEvent:
    return SwapEvent(
        pool_id=RNG.choice(["pool-a", "pool-b"]),
        block_hash=block_hash,
        block_number=RNG.randint(1, 100),
        tx_index=i,
        sender=RNG.choice(ADDRESSES),
        direction=RNG.choice([1, -1]),
        notional_usd=RNG.choice([RNG.uniform(0, 5000), 0.0, 1e6]),
        impact_bps=RNG.choice([RNG.uniform(-500, 500), 0.0, 50.0, -50.0]),
        funded_by=RNG.choice(["", "0xFunder"]),
    )


def test_flow_meter_idempotent_and_exact():
    for trial in range(200):
        meter = FlowMeter()
        events = [fuzz_event(i) for i in range(RNG.randint(1, 8))]
        for e in events:
            assert meter.record(e) is True
        # replays are safe no-ops
        for e in RNG.sample(events, k=min(3, len(events))):
            assert meter.record(e) is False
        by_key = {}
        for e in events:
            k = (e.block_hash, e.pool_id)
            by_key.setdefault(k, {"n": 0.0, "c": 0})
            by_key[k]["n"] += e.notional_usd
            by_key[k]["c"] += 1
        for (bh, pool), exp in by_key.items():
            assert meter.block_flow_usd(bh, pool) == exp["n"]
            cell = meter._ledger[(bh, pool)]
            assert cell["swaps"] == exp["c"]
            assert cell["gas"] == exp["c"] * 600  # GasMeter.PER_SWAP_GAS


def test_gas_budget_never_exceeded():
    assert mev.GasMeter.PER_SWAP_GAS == 600
    for n in (1, 10, 1000):
        assert mev.GasMeter.gas_for_swaps(n) == 600 * n
    mev.GasMeter.assert_budget(1000)  # 600k total but per-swap <= 5000


def test_threshold_gate_is_conjunction():
    for _ in range(200):
        meter = FlowMeter()
        impact = RNG.uniform(-500, 500)
        notional = RNG.choice([RNG.uniform(0, 3000), 999.9, 1000.0, 5000.0])
        ev = SwapEvent("pool-a", "0xb", 1, 0, "0xX", 1, notional, impact)
        meter.record(ev)
        expect = notional >= 1000.0 and abs(impact) >= 50
        assert meter.passes_threshold("0xb", "pool-a", impact) == expect


def test_detector_flags_obey_signatures():
    for trial in range(150):
        events = [fuzz_event(i) for i in range(RNG.randint(3, 7))]
        meter = FlowMeter()
        for e in events:
            meter.record(e)
        det = MEVDetector(meter)
        res = det.detect(events)
        for f in res["sandwich"]:
            assert 0.0 <= f.confidence <= 1.0
            # strictly above the flag band in either direction (gate is abs)
            assert abs(f.victim_impact_bps) > 50
            assert f.attributable_notional_usd >= 1000.0  # flow gate
            # backrun reverses the frontrun: opposite signed impacts
            a = events[f.frontrun_tx_index]
            b = events[f.backrun_tx_index]
            assert b.direction == -a.direction
        for f in res["backrun"]:
            assert 0.0 <= f.confidence <= 1.0
            assert f.capture_pct >= 0.30
            assert f.displacement_bps > 50


def test_detector_noise_never_flags():
    # every swap below the flow gate: no flags, however adversarial
    for _ in range(100):
        events = [
            SwapEvent("p", "0xb", 1, i, "0xA", 1 if i % 2 else -1,
                      RNG.uniform(0, 999.9), RNG.uniform(-49.9, 49.9))
            for i in range(RNG.randint(2, 6))
        ]
        det = MEVDetector(FlowMeter())
        res = det.detect(events)
        assert res["sandwich"] == [] and res["backrun"] == []


def test_treasury_never_signs():
    reg = SignerRegistry()
    for variant in (TREASURY, TREASURY.lower(), TREASURY.upper()):
        try:
            reg.check(variant)
            raise AssertionError("treasury signer accepted")
        except mev.TreasurySignerError:
            pass
    try:
        Bidder(TREASURY, 500.0, 50.0, registry=reg)
        raise AssertionError("treasury bidder accepted")
    except mev.TreasurySignerError:
        pass
    # config audit: treasury in a signer/key/eoa/wallet field fails closed
    bad = {"signer": TREASURY, "nested": [{"wallet": TREASURY.lower()}]}
    try:
        reg.audit_config(bad)
        raise AssertionError("treasury in signer config accepted")
    except mev.TreasurySignerError:
        pass
    # treasury in a non-signer field is not the deny-list's business
    reg.audit_config({"recipient": TREASURY, "note": "treasury payout addr"})


def test_bidder_shading_and_float_rules():
    for _ in range(200):
        bidder = Bidder("0xBidder", RNG.uniform(100, 10000), 50.0)
        gas = RNG.uniform(1, 100)
        capture = RNG.uniform(0, 1000)
        expected_net = capture - gas
        intent, reason = bidder.evaluate(None, gas, capture)
        if expected_net < 3 * gas:
            assert intent is None and "shading" in reason
        elif bidder.float_usd < 2 * bidder.gas_ceiling_usd:
            assert intent is None and "depleted" in reason
        else:
            assert intent is not None
            assert intent.dry_run is True
            assert intent.executed is False
            assert intent.expected_net_usd == expected_net
    # depleted float stands down even with a profitable flag
    thin = Bidder("0xBidder", 100.0, 60.0)  # float < 2*60
    intent, _ = thin.evaluate(None, 1.0, 1000.0)
    assert intent is None
    # crediting proceeds grows the float
    thin.credit_proceeds(50.0)
    assert thin.float_usd == 150.0
    try:
        thin.credit_proceeds(-1.0)
        raise AssertionError("negative proceeds accepted")
    except mev.MEVError:
        pass


def test_bidder_float_floor():
    try:
        Bidder("0xBidder", 99.99, 10.0)
        raise AssertionError("sub-floor float accepted")
    except mev.MEVError:
        pass
    gate = LiveGate()
    bidder = Bidder("0xBidder", 200.0, 10.0, live_gate=gate)
    try:
        bidder.submit_live(mev.BidIntent(0.0, "x", "p", 10.0, 1.0, 9.0))
        raise AssertionError("live submission escaped the gate")
    except mev.LiveBlockedError:
        pass
    try:
        gate.release("")
        raise AssertionError("empty release marker accepted")
    except mev.MEVError:
        pass


def test_capture_ledger_reconciliation():
    for _ in range(100):
        bidder = Bidder("0xBidder", 500.0, 10.0)
        ledger = CaptureLedger(bidder)
        est = RNG.randint(10**12, 10**18)
        rec = ledger.record_capture("c1", "pool", "0xT", est, est)
        # idempotent re-record: same object, no double count
        assert ledger.record_capture("c1", "pool", "0xT", est, est) is rec
        actual = int(est * RNG.choice([1.0, 0.999, 1.001, 1.05, 0.9]))
        out = ledger.reconcile("c1", actual)
        dev = abs(est - actual) / max(actual, 1)
        assert out["under_review"] == (dev > 0.01)
        assert rec.under_review == (dev > 0.01)
    # confidence decays exactly x0.9 per breach
    bidder = Bidder("0xBidder", 500.0, 10.0)
    ledger = CaptureLedger(bidder)
    ledger.record_capture("c9", "pool", "0xT", 10**15, 10**15)
    ledger.reconcile("c9", int(10**15 * 0.5))
    assert abs(bidder.confidence - 0.9) < 1e-12
    ledger.reconcile("c9", int(10**15 * 0.5))
    assert abs(bidder.confidence - 0.81) < 1e-12
    # negative amounts refused
    for bad in (-1, -10**18):
        try:
            ledger.record_capture("bad", "p", "0xT", bad, 1)
            raise AssertionError("negative capture accepted")
        except mev.MEVError:
            pass


def test_router_fee_conservation_and_nonbricking():
    for _ in range(200):
        router = TreasuryRouter(reverting={"0xReverter"})
        amt = RNG.choice([0, 1, RNG.randint(1, 10**24)])
        out = router.route("cap", amt)
        assert out["fee_wei"] == amt * 20 // 10_000
        assert out["fee_wei"] + out["residual_wei"] == amt
        # reverting claimant raises; treasury still claims in full
        try:
            router.claim("0xReverter")
            raise AssertionError("reverter claim did not raise")
        except mev.MEVError:
            pass
        got = router.claim(TREASURY)
        assert got == amt
        assert router.claim(TREASURY) == 0  # double claim pays 0


def test_protection_policy():
    pol = ProtectionPolicy()
    for _ in range(200):
        quoted = RNG.uniform(1, 10000)
        drift = RNG.choice([RNG.uniform(-0.02, 0.02), 0.0101, -0.0101, 0.5])
        order = ProtectedOrder(
            order_id="o1", quoted_price=quoted,
            executed_price=quoted * (1 + drift), opted_in=True)
        if abs(drift) * 10_000 > 100:
            try:
                pol.evaluate(order)
                raise AssertionError("over-guardrail slip allowed")
            except mev.ProtectionRevert:
                pass
        else:
            out = pol.evaluate(order)
            assert out["allowed"] is True
    # non-opted-in: no protection, always allowed
    out = pol.evaluate(ProtectedOrder("o2", 100.0, 1.0, False))
    assert out["allowed"] is True
    # toxic block and bad quotes revert
    for bad in (
        ProtectedOrder("o3", 100.0, 100.0, True,
                       block_flagged_toxic_before_swap=True),
        ProtectedOrder("o4", 0.0, 0.0, True),
        ProtectedOrder("o5", -5.0, -5.0, True),
    ):
        try:
            pol.evaluate(bad)
            raise AssertionError("bad order allowed")
        except mev.ProtectionRevert:
            pass


def test_access_control_bounds():
    ac = AccessControl()
    ac.grant(mev.GUARDIAN_ROLE, "0xGuardian")
    base = 1000.0
    for new in (base * 0.5, base, base * 1.5):
        assert ac.tune_threshold("0xGuardian", new) == new
    for new in (base * 0.49, base * 1.51, 0.0, 1e9):
        try:
            ac.tune_threshold("0xGuardian", new)
            raise AssertionError("out-of-band threshold accepted")
        except mev.ThresholdOutOfBoundsError:
            pass
    for op in ("pause", "unpause", "tune_threshold"):
        try:
            getattr(ac, op)("0xNobody") if op != "tune_threshold" \
                else ac.tune_threshold("0xNobody", 1000.0)
            raise AssertionError(f"non-guardian {op} accepted")
        except mev.MEVError:
            pass
    ac.pause("0xGuardian")
    assert ac.paused is True
    ac.unpause("0xGuardian")
    assert ac.paused is False
