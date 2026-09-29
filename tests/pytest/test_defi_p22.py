"""P22 acceptance tests: stable_only gate, optimizer, fees, rebalance, adapters.

Maps to the numbered acceptance criteria in
~/workspace/sincor2-auction-tasks/specs/p22-stablecoin-yield.md.
Self-contained: no network, no fork (fork runs are a documented gap).
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.p22 import PARAMS, adapters, fees, gate, optimizer, scanner, twap
from src.sincor2.defi.p22.adapters import (
    SEL_DEPOSIT, SEL_REDEEM, SEL_SUPPLY, SEL_WITHDRAW,
    AaveV3Adapter, MorphoAdapter, UNRESOLVED, VenueConfig,
)
from src.sincor2.defi.p22.gate import StableGateViolation, assert_stable
from src.sincor2.defi.p22.interfaces import PoolQuote
from src.sincor2.defi.catalog import TREASURY

R = random.Random(20260929)


def _quote(venue="v", asset="USDC", chain=8453, apr=0.04, depth=10_000_000.0,
           ts=1_000_000.0, morpho=False):
    return PoolQuote(venue, asset, chain, apr, depth, ts, morpho)


def _twap(venue="v", apr=0.04, depth=10_000_000.0, morpho=False):
    return twap.TwapQuote(venue, "USDC", apr, depth, morpho, 12, 1_000_000.0)


# -- AC1: stable_only is absolute -------------------------------------------
def test_ac1_gate_rejects_non_stables_directly():
    gate.clear_events()
    for bad in ("DAI", "ETH", "cbBTC", "WETH", "", "usdc"):
        with pytest.raises(StableGateViolation):
            assert_stable(bad, 8453, "v", "allocation")
    with pytest.raises(StableGateViolation):
        assert_stable("USDC", 1, "v", "allocation")  # wrong chain
    assert_stable("USDC", 8453, "v", "allocation")
    assert_stable("USDT", 8453, "v", "allocation")
    assert len(gate.SECURITY_EVENTS) >= 7


def test_ac1_gate_rejects_at_all_three_entrypoints():
    gate.clear_events()
    # scan entrypoint
    ok, rejected = gate.filter_quotes(
        [_quote(asset="DAI", venue="evil"), _quote(asset="USDC", venue="good")],
        entrypoint="scan")
    assert [q.venue_id for q in ok] == ["good"]
    assert len(rejected) == 1 and rejected[0].entrypoint == "scan"
    # adapter entrypoint: construction with a bad asset fails
    with pytest.raises(StableGateViolation):
        MorphoAdapter(VenueConfig("x", "ETH", UNRESOLVED, 0, True, 1e6))
    # allocation entrypoint
    with pytest.raises(StableGateViolation):
        assert_stable("cbBTC", 8453, "v", "allocation")


def test_ac1_fuzzed_assets_1000_rejected():
    gate.clear_events()
    alphabet = "ABCDEabcde019_-$."
    rejected = 0
    for _ in range(1000):
        asset = "".join(R.choice(alphabet) for _ in range(R.randint(1, 8)))
        chain = R.choice([8453, 1, 8453, 137])
        try:
            assert_stable(asset, chain, "fuzz", "scan")
        except StableGateViolation:
            rejected += 1
        else:
            assert asset in PARAMS["allowed_assets"] and chain == 8453
    # every rejection logged a security event
    assert len(gate.SECURITY_EVENTS) >= rejected


# -- AC2: optimizer constraints hold exactly ---------------------------------
def test_ac2_optimizer_caps_and_sum():
    quotes = [
        _twap("morpho-gauntlet-usdc", 0.044, 50_000_000.0, True),
        _twap("aave-v3-usdc", 0.060, 80_000_000.0, False),
        _twap("morpho-steakhouse-usdc", 0.032, 20_000_000.0, True),
    ]
    plan = optimizer.optimize(quotes, 100_000.0, ts=1_000_000.0)
    assert abs(sum(plan.weights.values()) - 1.0) <= 1e-9
    for vid, w in plan.weights.items():
        assert w <= PARAMS["max_alloc_pct"] + 1e-9
    # depth: w*capital <= 10% of venue depth
    depths = {q.venue_id: q.depth_usd for q in quotes}
    for vid, w in plan.weights.items():
        if vid != optimizer.CASH_ID:
            assert w * 100_000.0 <= 0.10 * depths[vid] + 1e-6


def test_ac2_morpho_tiebreak_within_5bps():
    quotes = [
        _twap("aave-v3-usdc", 0.0440, 80_000_000.0, False),
        _twap("morpho-gauntlet-usdc", 0.0436, 50_000_000.0, True),  # 4 bps lower
    ]
    plan = optimizer.optimize(quotes, 10_000.0, ts=1_000_000.0)
    # Within 5 bps, Morpho fills first despite the slightly lower APR.
    assert plan.weights["morpho-gauntlet-usdc"] >= plan.weights["aave-v3-usdc"]


def test_ac2_fuzz_1000_quote_sets():
    for _ in range(1000):
        n = R.randint(1, 6)
        quotes = []
        for i in range(n):
            quotes.append(_twap(
                f"v{i}",
                apr=R.uniform(0.0, 0.12),
                depth=R.uniform(1_000.0, 200_000_000.0),
                morpho=R.random() < 0.5,
            ))
        capital = R.uniform(10.0, 5_000_000.0)
        plan = optimizer.optimize(quotes, capital, ts=1_000_000.0)
        # verify_plan raises on any violation; optimize calls it internally.
        assert abs(sum(plan.weights.values()) - 1.0) <= 1e-9


def test_ac2_min_capital_refuses():
    with pytest.raises(optimizer.OptimizerError):
        optimizer.optimize([_twap()], 9.99, ts=0.0)


# -- AC3: fee math is wei-exact ----------------------------------------------
def test_ac3_fee_wei_exact_worked_example():
    # docs/spec/P22_ECONOMICS.md worked example: $100k at 5.8% -> $5,800/yr
    # yield -> $5.80 fee at 10 bps, $5,794.20 to the user (net 5.7942%).
    ledger = fees.FeeLedger()
    ledger.set_baseline("u1", "morpho-gauntlet-usdc", 100_000_000_000)
    fee = ledger.accrue("u1", "morpho-gauntlet-usdc", 105_800_000_000)
    assert fee == 5_800_000  # floor(5_800_000_000 * 10 / 10000), USDC-wei
    assert ledger.accrued("u1") == 5_800_000


def test_ac3_fee_only_on_realized_yield():
    ledger = fees.FeeLedger()
    ledger.set_baseline("u1", "v", 1_000_000_000)
    assert ledger.accrue("u1", "v", 900_000_000) == 0  # loss: no fee
    assert ledger.accrue("u1", "v", 1_000_000_000) == 0  # flat: no fee
    assert ledger.accrue("u1", "v", 1_000_100_000) == 100  # floor(100000*10/1e4)
    # baseline ratcheted: same yield is never fee'd twice
    assert ledger.accrue("u1", "v", 1_000_100_000) == 0


def test_ac3_settlement_only_to_treasury_above_10():
    ledger = fees.FeeLedger()
    ledger.set_baseline("u1", "v", 0)
    ledger.accrue("u1", "v", 500_000_000)  # $500 yield -> $0.50 fee
    assert ledger.settle("u1") is None  # below $10 batch floor
    ledger.set_baseline("u1", "v", 500_000_000)
    ledger.accrue("u1", "v", 500_000_000 + 200_000_000_000)  # +$200k -> $200
    s = ledger.settle("u1")
    assert s is not None and s.to == TREASURY == "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
    assert ledger.accrued("u1") == 0


# -- AC4: rebalance discipline --------------------------------------------------
def _plan(apr, ts=0.0):
    return optimizer.AllocationPlan({"v": 1.0}, apr, ts)


def test_ac4_no_rotation_below_50bps():
    d = optimizer.rotation_decision(_plan(0.0400), _plan(0.0449), 0.0, 99_999.0, 1.0, 100_000.0)
    assert not d.rotate and "49.0 bps" in d.reason


def test_ac4_no_rotation_when_gas_too_high():
    # ΔAPR 100 bps on $100k = $1000/yr gain; gas $300 >= 25% -> no rotate
    d = optimizer.rotation_decision(_plan(0.04), _plan(0.05), 0.0, 99_999.0, 300.0, 100_000.0)
    assert not d.rotate and "gas" in d.reason


def test_ac4_no_rotation_inside_cooldown():
    d = optimizer.rotation_decision(_plan(0.04), _plan(0.06), 90_000.0, 99_999.0, 1.0, 100_000.0)
    assert not d.rotate and "cooldown" in d.reason


def test_ac4_rotation_when_all_gates_pass():
    d = optimizer.rotation_decision(_plan(0.04), _plan(0.06), 0.0, 99_999.0, 1.0, 100_000.0)
    assert d.rotate


def test_ac4_toa_forced_rotation_still_gated():
    d = optimizer.rotation_decision(_plan(0.04), _plan(0.0401), 0.0, 99_999.0, 0.01, 100_000.0,
                                    forced=True)
    assert not d.rotate  # 1 bp < 50 bps: force does not bypass


# -- AC5: graceful degradation ---------------------------------------------------
class _BoomAdapter:
    venue_id = "boom"

    def quote(self):
        raise RuntimeError("RPC down")


def test_ac5_venue_failure_degrades_to_skipped():
    gate.clear_events()
    good = MorphoAdapter(VenueConfig("morpho-gauntlet-usdc", "USDC", UNRESOLVED, 0, True, 5e7))
    good.set_supply_apr(0.044)
    sc = scanner.Scanner([good, _BoomAdapter()])
    result = sc.scan(now=1_000_000.0)
    assert result.skipped["boom"].startswith("quote failed")
    assert "venue_skipped: boom" in result.alerts
    assert len(result.accepted) == 1
    # optimizer works on the TWAP of the surviving venue
    plan = optimizer.optimize(sc.twap_quotes(1_000_000.0), 50_000.0, ts=1_000_000.0)
    assert abs(sum(plan.weights.values()) - 1.0) <= 1e-9


def test_ac5_stale_quotes_discarded():
    good = MorphoAdapter(VenueConfig("m", "USDC", UNRESOLVED, 0, True, 5e7))
    sc = scanner.Scanner([good])
    q = good.quote()
    stale = PoolQuote(q.venue_id, q.asset, q.chain_id, q.net_apr, q.depth_usd,
                      q.ts - 601.0, q.is_morpho)
    good.quote = lambda: stale  # type: ignore
    result = sc.scan(now=q.ts)
    assert result.skipped["m"] == "stale quote"


# -- adapters: real calldata ------------------------------------------------------
def test_adapter_selectors_are_canonical():
    assert SEL_DEPOSIT == bytes.fromhex("6e553f65")
    assert SEL_REDEEM == bytes.fromhex("ba087652")
    assert SEL_SUPPLY == bytes.fromhex("617ba037")
    assert SEL_WITHDRAW == bytes.fromhex("69328dec")


def test_adapter_curator_fee_priced_into_net_apr():
    steak = MorphoAdapter(VenueConfig("s", "USDC", UNRESOLVED, 2500, True, 1e7))
    steak.set_supply_apr(0.04)
    assert abs(steak.net_apr() - 0.04 * 0.75) < 1e-12  # 25% perf fee


def test_adapter_builds_real_calldata():
    vault = "0x" + "11" * 20
    receiver = "0x" + "22" * 20
    m = MorphoAdapter(VenueConfig("m", "USDC", vault, 0, True, 1e7))
    tx = m.build_supply_tx(1_000_000, receiver)
    assert tx["to"] == vault and tx["chain_id"] == 8453
    data = bytes.fromhex(tx["data"][2:])
    assert data[:4] == SEL_DEPOSIT
    assert int.from_bytes(data[4:36], "big") == 1_000_000
    assert data[36:68] == bytes(12) + bytes.fromhex("22" * 20)


def test_adapter_refuses_unresolved_venue_for_txs():
    m = MorphoAdapter(VenueConfig("m", "USDC", UNRESOLVED, 0, True, 1e7))
    with pytest.raises(ValueError, match="unresolved"):
        m.build_supply_tx(1_000_000, "0x" + "22" * 20)


def test_adapter_refuses_guessed_usdt_address():
    a = AaveV3Adapter(VenueConfig("a", "USDT", "0x" + "33" * 20, 0, False, 1e7))
    with pytest.raises(ValueError, match="no pinned onchain asset address"):
        a.build_supply_tx(1_000_000, "0x" + "22" * 20)


# -- integration: scan -> twap -> optimize -> fee -----------------------------------
def test_integration_scan_optimize_fee_pipeline():
    now = 2_000_000.0
    adapters_list = []
    for vid, apr, fee_bps, depth, morpho in [
        ("morpho-gauntlet-usdc", 0.044, 0, 50_000_000.0, True),
        ("morpho-steakhouse-usdc", 0.040, 2500, 20_000_000.0, True),
        ("aave-v3-usdc", 0.055, 0, 80_000_000.0, False),
    ]:
        a = (MorphoAdapter if morpho else AaveV3Adapter)(
            VenueConfig(vid, "USDC", UNRESOLVED, fee_bps, morpho, depth))
        a.set_supply_apr(apr)
        adapters_list.append(a)
    sc = scanner.Scanner(adapters_list)
    # several scans build the TWAP history
    for t in range(5):
        sc.scan(now=now + t * 300.0)
    quotes = sc.twap_quotes(now + 4 * 300.0)
    assert len(quotes) == 3
    plan = optimizer.optimize(quotes, 250_000.0, ts=now)
    assert abs(sum(plan.weights.values()) - 1.0) <= 1e-9
    assert plan.blended_net_apr > 0.03
    # fee accrual on the realized yield of the top venue
    top = max(plan.weights, key=plan.weights.get)
    ledger = fees.FeeLedger()
    ledger.set_baseline("user", top, 250_000_000_000)
    fee = ledger.accrue("user", top, 250_000_000_000 + 11_500_000_000)
    assert fee == 11_500_000_000 * 10 // 10_000
