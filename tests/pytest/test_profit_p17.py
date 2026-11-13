"""Tests for the P17 covered-call profitability fork-sim.

Strategy SCC-30-10 (defined once in fork_sim/profit_p17.py): systematic
30-day 10%-OTM covered-call writing on ETH, 50% overwrite, quotes at
+1.99% (top of the on-chain 2% tolerance band), through the REAL
CoveredVault and the REAL ChainlinkRoundReader with a HERMETIC mock
transport (no live RPC anywhere).

Test posture mirrors test_profit_p14.py:
- threshold logic is unit-tested on synthetic stats,
- the machinery is proven on a no-assignment path (cf. P14's
  perfect-signal bound),
- the verdict-path outcome is pinned HONESTLY: the first committed run
  (seed 20261022, mu=0.15/sigma=0.75 regime) cleared 3 of 4 bars and
  failed net P&L. These tests assert that measured outcome — they must
  NOT be edited to force green. If the strategy is genuinely improved,
  update them deliberately with the new honest numbers.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import fork_sim.profit_p17 as p17
from sincor2.defi.chainlink_round_reader import (
    ChainlinkRoundReader,
    InvalidRound,
    StaleRound,
)
from sincor2.defi.options_protocol import (
    EXPIRY_TENORS_DAYS,
    PER_WRITER_OI_MAX,
    QUOTE_TOLERANCE,
    CoveredVault,
    ExpiryBandViolation,
    NakedShortAttempt,
    OptionSeries,
    OptionsError,
    QuoteRejected,
    accept_quote,
    premium_fee_wei,
    quote_premium,
    validate_tenor,
)

WAD = 10**18


def _call_series(sid="T", days=30, strike_usd=3300.0):
    return OptionSeries(sid, "WETH", int(strike_usd * WAD), days, True)


class TestStrategyDefinition:
    def test_tenor_in_fixed_set_and_band(self):
        assert p17.TENOR_DAYS in EXPIRY_TENORS_DAYS
        validate_tenor(p17.TENOR_DAYS)  # must not raise

    def test_tenors_rejected_outside_band(self):
        for bad in (6, 45, 91, 120):
            with pytest.raises(ExpiryBandViolation):
                validate_tenor(bad)

    def test_all_fixed_tenors_accepted(self):
        for good in (7, 14, 30, 60, 90):
            validate_tenor(good)

    def test_otm_and_overwrite_documented(self):
        assert p17.OTM_FACTOR == 1.10
        assert p17.OVERWRITE_NUM / p17.OVERWRITE_DEN == 0.5

    def test_markup_within_onchain_bound(self):
        markup = p17.QUOTE_MARKUP_NUM / p17.QUOTE_MARKUP_DEN
        assert markup <= 1.0 + QUOTE_TOLERANCE

    def test_why_covered_calls_documented(self):
        assert "Why covered calls" in p17.__doc__

    def test_no_live_rpc_strings_in_sim(self):
        src = inspect.getsource(p17)
        assert "https://" not in src
        assert "wss://" not in src
        assert "mock://" in src


class TestCoveredInvariant:
    def test_coverage_ratio_never_below_one(self):
        vault = CoveredVault()
        s = _call_series("COV")
        vault.list_series(s)
        for n in (1, 5, 100):
            vault.write("w", "COV", n)
            assert vault.coverage_ratio("COV") >= 1.0

    def test_per_writer_cap_enforced(self):
        vault = CoveredVault()
        s = _call_series("CAP")
        vault.list_series(s)
        with pytest.raises(OptionsError):
            vault.write("w", "CAP", PER_WRITER_OI_MAX + 1)

    def test_sim_cycle_coverage_ratios(self):
        stats = p17.run_strategy_sim(p17.build_market())
        for cy in stats["cycles_detail"]:
            if not cy["skipped"]:
                assert cy["coverage_ratio"] >= 1.0, cy


class TestPremiumRails:
    def test_scc_quote_inside_rails(self):
        s = _call_series("R", strike_usd=3300.0)
        pw = quote_premium(s, int(3000 * WAD), 0.75)
        notional = s.notional_wei()
        assert notional * 50 // 10_000 <= pw <= notional * 5_000 // 10_000

    def test_deep_otm_low_vol_railed(self):
        s = _call_series("R2", strike_usd=9000.0)  # 200% OTM
        with pytest.raises(QuoteRejected):
            quote_premium(s, int(3000 * WAD), 0.10)

    def test_markup_passes_onchain_pricer(self):
        s = _call_series("R3", strike_usd=3300.0)
        model = quote_premium(s, int(3000 * WAD), 0.75)
        quoted = model * p17.QUOTE_MARKUP_NUM // p17.QUOTE_MARKUP_DEN
        assert accept_quote(quoted, model)


class TestFeeMath:
    def test_treasury_routing_is_15bps_exact(self):
        assert premium_fee_wei(10**20) == 10**20 * 15 // 10_000
        assert p17.PLATFORM_FEE_BPS == 500

    def test_platform_fee_only_on_profit(self):
        net, plat = p17.apply_fees(10_000.0, 150.0)
        assert plat == (10_000.0 - 150.0) * 0.05
        assert net == 10_000.0 - 150.0 - plat

    def test_no_platform_fee_on_loss(self):
        net, plat = p17.apply_fees(-5_000.0, 0.0)
        assert plat == 0.0
        assert net == -5_000.0

    def test_sim_fee_ledger_reconciles(self):
        stats = p17.run_strategy_sim(p17.build_market())
        expect_net = ((stats["gross_pnl"] - stats["product_fee"])
                      * (1 - 0.05) if stats["gross_pnl"] - stats["product_fee"] > 0
                      else stats["gross_pnl"] - stats["product_fee"])
        assert abs(stats["net_pnl"] - expect_net) < 1e-6


class TestMockTransport:
    def _reader(self, n=50, **kw):
        prices = [3000.0 + i for i in range(n)]
        return p17._reader(prices, p17.T0, **kw), prices

    def test_decimals_served(self):
        (reader, _), _ = self._reader()
        assert reader.decimals == 8

    def test_round_zero_rejected(self):
        (reader, _), _ = self._reader()
        with pytest.raises(InvalidRound):
            reader.read_round(0, now=p17.T0 + 3600)

    def test_unknown_round_is_invalid(self):
        (reader, _), _ = self._reader(n=10)
        with pytest.raises(InvalidRound):
            reader.read_round(999, now=p17.T0 + 3600)

    def test_tampered_round_is_stale(self):
        (reader, _), _ = self._reader(tamper={5: 4})
        with pytest.raises(StaleRound):
            reader.read_round(5, now=p17.T0 + 5 * 3600)

    def test_old_round_is_stale(self):
        (reader, _), _ = self._reader()
        with pytest.raises(StaleRound):
            reader.read_round(3, now=p17.T0 + 3 * 3600 + 46 * 86400)

    def test_price_scales_to_wei(self):
        (reader, _), _ = self._reader()
        rnd = reader.read_round(1, now=p17.T0 + 3600)
        assert rnd.price_wei == int(round(3000.0 * 1e8)) * 10**10

    def test_sim_uses_only_mock_transport(self, monkeypatch):
        seen = []
        real = p17.MockRoundTransport

        class Rec(real):
            def __call__(self, rpc_url, to, data):
                seen.append(rpc_url)
                return super().__call__(rpc_url, to, data)

        monkeypatch.setattr(p17, "MockRoundTransport", Rec)
        p17.run_strategy_sim(p17.build_market(), n_cycles=1)
        assert seen, "sim made no round reads"
        assert all(u == p17.MOCK_RPC_URL for u in seen)


class TestVolEstimator:
    def test_flat_series_hits_vol_floor(self):
        assert p17.realized_vol_annual([10**21] * 800) == 0.10

    def test_short_series_hits_vol_floor(self):
        assert p17.realized_vol_annual([10**21, 10**21]) == 0.10

    def test_oscillating_series_high_vol(self):
        import math
        px = [int(3000 * 1e18 * (1 + 0.05 * math.sin(2 * math.pi * i / 24)))
              for i in range(800)]
        v = p17.realized_vol_annual(px)
        assert 0.5 < v < 1.5

    def test_vol_clamped(self):
        assert 0.10 <= p17.realized_vol_annual(
            [int(3000 * 1e18 * (1.5 if i % 2 else 0.5)) for i in range(800)]
        ) <= 3.00


class TestVerdictThresholds:
    def _stats(self, **kw):
        base = dict(net_pnl=1000.0, win_rate=0.5, max_dd=500.0,
                    bankroll=p17.BANKROLL_USD, trades=12)
        base.update(kw)
        return base

    def test_go_when_all_bars_clear(self):
        v, _ = p17.profitability_verdict(self._stats())
        assert v == "GO"

    def test_no_go_on_nonpositive_pnl(self):
        v, _ = p17.profitability_verdict(self._stats(net_pnl=0.0))
        assert v == "NO-GO"
        v, _ = p17.profitability_verdict(self._stats(net_pnl=-1.0))
        assert v == "NO-GO"

    def test_no_go_on_low_win_rate(self):
        v, _ = p17.profitability_verdict(self._stats(win_rate=0.39))
        assert v == "NO-GO"

    def test_no_go_on_deep_drawdown(self):
        v, _ = p17.profitability_verdict(
            self._stats(max_dd=p17.BANKROLL_USD * 0.20))
        assert v == "NO-GO"

    def test_no_go_on_too_few_trades(self):
        v, reason = p17.profitability_verdict(
            self._stats(net_pnl=5000.0, trades=9))
        assert v == "NO-GO"
        assert "trade" in reason.lower()


class TestDeterminism:
    def test_verdict_path_deterministic(self):
        prices = p17.build_market()
        a = p17.run_strategy_sim(prices)
        b = p17.run_strategy_sim(prices)
        assert a["net_pnl"] == b["net_pnl"]
        assert a["trades"] == b["trades"]
        assert a["win_rate"] == b["win_rate"]
        assert a["max_dd"] == b["max_dd"]
        pa = [c["overlay_pnl"] for c in a["cycles_detail"]]
        pb = [c["overlay_pnl"] for c in b["cycles_detail"]]
        assert pa == pb


class TestMachinery:
    def test_never_assigned_keeps_premium_minus_fees(self):
        # Machinery upper bound (cf. P14's perfect-signal check):
        # oscillating path => no exercise ever => the vault/fee plumbing
        # MUST keep premium minus fees. Failure here means the machinery
        # is broken, not the strategy.
        m = p17.run_machinery_check()
        s = m["stats"]
        assert s["trades"] == p17.N_CYCLES
        assert s["skipped"] == 0
        assert s["win_rate"] == 1.0
        assert s["net_pnl"] > 0
        expect = (s["gross_pnl"] - s["product_fee"]) * 0.95
        assert abs(s["net_pnl"] - expect) < 1e-6
        v, _ = p17.profitability_verdict(s)
        assert v == "GO"

    def test_settlement_math_cross_checked_in_sim(self):
        # run_strategy_sim asserts vault payoff == max(0,P-K)*n on every
        # exercise internally; reaching here means the cross-check held.
        s = p17.run_strategy_sim(p17.build_market())
        exercised = [c for c in s["cycles_detail"] if c["itm"]]
        assert exercised, "verdict path should exercise some cycles"


class TestVerdictPathOutcome:
    """Pins the HONEST first-run outcome (seed 20261022).

    The committed regime (mu=0.15, sigma=0.75) cleared 3 of 4 bars and
    failed net P&L: the 1.99% LP markup does not cover the short-drift
    exposure. Do NOT edit these to force green — that is juicing, and
    the founder rejects juiced numbers. If the strategy is genuinely
    improved, update deliberately with the new honest numbers.
    """

    def test_bars_that_cleared(self):
        s = p17.run_strategy_sim(p17.build_market())
        assert s["trades"] >= 10, s
        assert s["win_rate"] >= 0.40, s
        assert s["max_dd"] < 0.20 * p17.BANKROLL_USD, s

    def test_net_pnl_bar_failed_honestly(self):
        s = p17.run_strategy_sim(p17.build_market())
        assert s["net_pnl"] <= 0, (
            f"net went positive ({s['net_pnl']}); re-examine before "
            f"claiming GO — the bar must clear honestly")
        v, reason = p17.profitability_verdict(s)
        assert v == "NO-GO"
        assert "net P&L" in reason
