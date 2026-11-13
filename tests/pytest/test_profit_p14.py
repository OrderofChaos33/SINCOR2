"""Tests for P14 profitability fork-sim (fork_sim/profit_p14.py).

All tests use synthetic fixtures — no live API calls. The live data path
(fetch_episodes) is tested via recorded fixtures, not the network.
"""
import dataclasses

import pytest

from sincor2.defi.prediction_markets import (
    MarketSnapshot,
    check_edge,
)


def _snap(price=0.55, volume_cents=2_000_000, now=1_700_000_000):
    return MarketSnapshot(
        market_id="test-mkt-1",
        question="Test?",
        yes_price=price,
        volume_24h_cents=volume_cents,
        oracle="uma",
        resolves_at=now + 30 * 86400,
        as_of=now,
        category="general",
    )


class TestMomentumSignal:
    def test_zero_when_no_history(self):
        from fork_sim.profit_p14 import momentum_signal
        assert momentum_signal(0.55, 0.0) == 0.0
        assert momentum_signal(0.55, -1.0) == 0.0

    def test_positive_momentum_positive_signal(self):
        from fork_sim.profit_p14 import momentum_signal
        s = momentum_signal(0.60, 0.50)
        assert s > 0

    def test_negative_momentum_negative_signal(self):
        from fork_sim.profit_p14 import momentum_signal
        s = momentum_signal(0.50, 0.60)
        assert s < 0

    def test_capped(self):
        from fork_sim.profit_p14 import momentum_signal
        # 100% jump should not exceed the cap
        assert abs(momentum_signal(1.0, 0.01)) <= 0.12
        assert abs(momentum_signal(0.01, 1.0)) <= 0.12

    def test_flat_is_zero(self):
        from fork_sim.profit_p14 import momentum_signal
        assert momentum_signal(0.55, 0.55) == 0.0


class TestPerfectSignal:
    def test_documents_lookahead(self):
        from fork_sim import profit_p14
        # The perfect signal MUST be documented as lookahead-by-design.
        assert "lookahead" in profit_p14.perfect_signal.__doc__.lower()

    def test_direction(self):
        from fork_sim.profit_p14 import perfect_signal
        assert perfect_signal(1) > 0
        assert perfect_signal(0) < 0


class TestVerdictThreshold:
    def _stats(self, **kw):
        base = dict(net_pnl_cents=1000, win_rate=0.5, max_dd_cents=500,
                    bankroll_cents=100_000, trades=20)
        base.update(kw)
        return base

    def test_go_when_all_bars_clear(self):
        from fork_sim.profit_p14 import profitability_verdict
        v, reason = profitability_verdict(self._stats())
        assert v == "GO", reason

    def test_no_go_on_nonpositive_pnl(self):
        from fork_sim.profit_p14 import profitability_verdict
        v, _ = profitability_verdict(self._stats(net_pnl_cents=0))
        assert v == "NO-GO"
        v, _ = profitability_verdict(self._stats(net_pnl_cents=-100))
        assert v == "NO-GO"

    def test_no_go_on_low_win_rate(self):
        from fork_sim.profit_p14 import profitability_verdict
        v, _ = profitability_verdict(self._stats(win_rate=0.39))
        assert v == "NO-GO"

    def test_no_go_on_deep_drawdown(self):
        from fork_sim.profit_p14 import profitability_verdict
        v, _ = profitability_verdict(self._stats(max_dd_cents=20_000))
        assert v == "NO-GO"

    def test_no_go_on_too_few_trades(self):
        # Degenerate pass guard: refusing to trade is not profitability.
        from fork_sim.profit_p14 import profitability_verdict
        v, reason = profitability_verdict(
            self._stats(net_pnl_cents=5000, trades=9))
        assert v == "NO-GO"
        assert "trade" in reason.lower()


class TestFeeMath:
    def test_platform_fee_only_on_profit(self):
        from fork_sim.profit_p14 import apply_fees
        # gross 10_000, product fee 150 (15bps of positive pnl portions),
        # platform 5% of (gross - product_fee)
        net, plat = apply_fees(gross_cents=10_000, product_fee_cents=150)
        assert plat == int((10_000 - 150) * 0.05)
        assert net == 10_000 - 150 - plat

    def test_no_platform_fee_on_loss(self):
        from fork_sim.profit_p14 import apply_fees
        net, plat = apply_fees(gross_cents=-5_000, product_fee_cents=0)
        assert plat == 0
        assert net == -5_000

    def test_five_percent_is_locked(self):
        from fork_sim import profit_p14
        assert profit_p14.PLATFORM_FEE_BPS == 500


class TestEpisodeConstruction:
    def test_snapshot_rejects_low_volume(self):
        # The product's own feed must veto below-bar markets.
        from sincor2.defi.prediction_markets import MarketDataFeed, EdgeVeto
        feed = MarketDataFeed()
        snap = _snap(volume_cents=999_999)  # just under $10k
        with pytest.raises(EdgeVeto):
            feed.ingest(snap, now=1_700_000_000)

    def test_episode_dataclass_fields(self):
        from fork_sim.profit_p14 import Episode
        ep = Episode(market_id="m1", question="Q?", price_at_t=0.55,
                     price_24h_before=0.50, outcome=1, decision_ts=1_700_000_000,
                     volume_24h_cents=2_000_000, resolves_at=1_700_000_000 + 10 * 86400)
        assert ep.outcome in (0, 1)
        assert 0.0 < ep.price_at_t < 1.0


class TestRunProfitSim:
    def test_uses_real_backtest_machinery(self):
        # The sim must route through prediction_markets.run_backtest,
        # not a reimplementation.
        import inspect
        from fork_sim import profit_p14
        src = inspect.getsource(profit_p14.run_profit_sim)
        assert "run_backtest" in src

    def test_deterministic_on_fixtures(self):
        from fork_sim.profit_p14 import Episode, run_profit_sim, episode_perfect
        eps = [
            Episode(market_id=f"m{i}", question="Q?", price_at_t=0.55,
                    price_24h_before=0.50, outcome=i % 2,
                    decision_ts=1_700_000_000,
                    volume_24h_cents=2_000_000,
                    resolves_at=1_700_000_000 + 30 * 86400)
            for i in range(12)
        ]
        r1 = run_profit_sim(eps, episode_perfect, bankroll_cents=100_000)
        r2 = run_profit_sim(eps, episode_perfect, bankroll_cents=100_000)
        assert r1["net_pnl_cents"] == r2["net_pnl_cents"]
        assert r1["trades"] == r2["trades"]

    def test_perfect_signal_is_profitable_machinery_check(self):
        # Upper bound: with perfect foresight the machinery MUST make money.
        # If this fails, the product's Kelly/edge/risk machinery is broken,
        # not the signal.
        from fork_sim.profit_p14 import Episode, run_profit_sim, episode_perfect
        eps = [
            Episode(market_id=f"m{i}", question="Q?", price_at_t=0.55,
                    price_24h_before=0.50, outcome=1,
                    decision_ts=1_700_000_000,
                    volume_24h_cents=5_000_000,
                    resolves_at=1_700_000_000 + 30 * 86400)
            for i in range(15)
        ]
        r = run_profit_sim(eps, episode_perfect, bankroll_cents=100_000)
        assert r["trades"] >= 10, "machinery took no trades on perfect signal"
        assert r["net_pnl_cents"] > 0, (
            f"machinery lost money with perfect foresight: {r}")
