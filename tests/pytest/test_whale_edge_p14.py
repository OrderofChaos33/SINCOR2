"""Tests for the whale-flow edge wiring (fork_sim/whale_edge_p14.py).

All tests use synthetic fixtures — no live API calls. The live data path
(whale discovery + real trade pulls) is evidence, not a test.
"""
import pytest

YES_TOK = "yes-token-aaa"
NO_TOK = "no-token-bbb"
T = 1_700_000_000  # decision time used in fixtures


def _trade(side, asset, price, size, ts):
    return {"side": side, "asset": asset, "price": price, "size": size,
            "timestamp": ts, "conditionId": "cid-1"}


class TestTradeDirection:
    def test_buy_yes_is_bullish(self):
        from fork_sim.whale_edge_p14 import trade_direction
        assert trade_direction("BUY", YES_TOK, YES_TOK, NO_TOK) == 1

    def test_sell_yes_is_bearish(self):
        from fork_sim.whale_edge_p14 import trade_direction
        assert trade_direction("SELL", YES_TOK, YES_TOK, NO_TOK) == -1

    def test_buy_no_is_bearish(self):
        from fork_sim.whale_edge_p14 import trade_direction
        assert trade_direction("BUY", NO_TOK, YES_TOK, NO_TOK) == -1

    def test_sell_no_is_bullish(self):
        from fork_sim.whale_edge_p14 import trade_direction
        assert trade_direction("SELL", NO_TOK, YES_TOK, NO_TOK) == 1

    def test_unknown_side_is_neutral(self):
        from fork_sim.whale_edge_p14 import trade_direction
        # e.g. REDEEM settlements are not directional entries
        assert trade_direction("REDEEM", YES_TOK, YES_TOK, NO_TOK) == 0
        assert trade_direction("", YES_TOK, YES_TOK, NO_TOK) == 0

    def test_foreign_asset_is_neutral(self):
        from fork_sim.whale_edge_p14 import trade_direction
        assert trade_direction("BUY", "other-market-token",
                               YES_TOK, NO_TOK) == 0


class TestAggregateWhaleFlow:
    def test_post_t_trades_excluded_no_lookahead(self):
        from fork_sim.whale_edge_p14 import aggregate_whale_flow
        trades = [
            _trade("BUY", YES_TOK, 0.5, 100, T - 100),   # pre-T: +50
            _trade("BUY", YES_TOK, 0.5, 100, T + 100),   # post-T: excluded
        ]
        net = aggregate_whale_flow(trades, YES_TOK, NO_TOK, T)
        assert net == pytest.approx(50.0)

    def test_cutoff_is_strict(self):
        from fork_sim.whale_edge_p14 import aggregate_whale_flow
        # A trade exactly at T is decision-time knowledge at earliest —
        # the filter keeps only strictly pre-T flow.
        trades = [_trade("BUY", YES_TOK, 0.5, 100, T)]
        assert aggregate_whale_flow(trades, YES_TOK, NO_TOK, T) == 0.0

    def test_weighted_by_notional(self):
        from fork_sim.whale_edge_p14 import aggregate_whale_flow
        trades = [
            _trade("BUY", YES_TOK, 0.6, 200, T - 10),    # +120
            _trade("SELL", YES_TOK, 0.5, 100, T - 10),   # -50
        ]
        assert aggregate_whale_flow(trades, YES_TOK, NO_TOK, T) == \
            pytest.approx(70.0)

    def test_malformed_trades_skipped(self):
        from fork_sim.whale_edge_p14 import aggregate_whale_flow
        trades = [
            {"side": "BUY", "asset": YES_TOK, "timestamp": T - 1},  # no price/size
            {"side": "BUY", "asset": YES_TOK, "price": "bad",
             "size": 10, "timestamp": T - 1},
            _trade("BUY", YES_TOK, 0.5, 100, T - 1),                # +50
        ]
        assert aggregate_whale_flow(trades, YES_TOK, NO_TOK, T) == \
            pytest.approx(50.0)


class TestWhaleSignal:
    def test_positive_net_full_cap_bullish(self):
        from fork_sim.whale_edge_p14 import whale_signal_from_net
        assert whale_signal_from_net(1234.5) == 0.12

    def test_negative_net_full_cap_bearish(self):
        from fork_sim.whale_edge_p14 import whale_signal_from_net
        assert whale_signal_from_net(-0.01) == -0.12

    def test_zero_net_zero_signal(self):
        from fork_sim.whale_edge_p14 import whale_signal_from_net
        assert whale_signal_from_net(0.0) == 0.0

    def test_cap_respected(self):
        from fork_sim.whale_edge_p14 import whale_signal_from_net
        # Direction-only: huge nets still map to exactly the cap.
        assert abs(whale_signal_from_net(10_000_000)) <= 0.12
        assert abs(whale_signal_from_net(-10_000_000)) <= 0.12

    def test_matches_momentum_cap(self):
        from fork_sim import whale_edge_p14
        from fork_sim import profit_p14
        assert whale_edge_p14.SIGNAL_CAP == profit_p14.SIGNAL_CAP == 0.12


class TestSignalAdapter:
    def test_adapter_routes_by_market_id(self):
        from fork_sim.whale_edge_p14 import make_whale_signal_fn, Episode
        fn = make_whale_signal_fn({"m1": 0.12, "m2": -0.12})
        ep = Episode(market_id="m1", question="Q?", price_at_t=0.55,
                     price_24h_before=0.50, outcome=1, decision_ts=T,
                     volume_24h_cents=2_000_000, resolves_at=T + 30 * 86400)
        assert fn(ep) == 0.12

    def test_unknown_market_zero_signal(self):
        from fork_sim.whale_edge_p14 import make_whale_signal_fn, Episode
        fn = make_whale_signal_fn({})
        ep = Episode(market_id="mX", question="Q?", price_at_t=0.55,
                     price_24h_before=0.50, outcome=1, decision_ts=T,
                     volume_24h_cents=2_000_000, resolves_at=T + 30 * 86400)
        assert fn(ep) == 0.0


class TestCoverageReport:
    def test_counts_nonzero_vs_total(self):
        from fork_sim.whale_edge_p14 import coverage_report, Episode

        def _ep(mid):
            return Episode(market_id=mid, question="Q?", price_at_t=0.55,
                           price_24h_before=0.50, outcome=1, decision_ts=T,
                           volume_24h_cents=2_000_000,
                           resolves_at=T + 30 * 86400)

        cov = coverage_report({"a": 0.12, "b": 0.0},
                              [_ep("a"), _ep("b"), _ep("c")])
        assert cov == {"episodes": 3, "nonzero_signal": 1, "zero_signal": 2}


class TestBuildWhaleSignals:
    def _ep(self, mid, decision_ts=T):
        from fork_sim.whale_edge_p14 import Episode
        return Episode(market_id=mid, question="Q?", price_at_t=0.55,
                       price_24h_before=0.50, outcome=1, decision_ts=decision_ts,
                       volume_24h_cents=2_000_000, resolves_at=decision_ts + 30 * 86400)

    def test_multi_whale_aggregation(self, monkeypatch):
        from fork_sim import whale_edge_p14
        # Two whales: whale1 bullish +100, whale2 bearish -40 => net +60
        monkeypatch.setattr(whale_edge_p14, "fetch_market_token_ids",
                            lambda mid: (YES_TOK, NO_TOK))

        def fake_pull(wallet, limit=1000):
            if wallet == "whale1":
                return [_trade("BUY", YES_TOK, 0.5, 200, T - 100)]
            return [_trade("SELL", YES_TOK, 0.4, 100, T - 100)]
        monkeypatch.setattr(whale_edge_p14, "pull_whale_trades", fake_pull)

        edge = whale_edge_p14.build_whale_signals(
            [self._ep("m1")], ["whale1", "whale2"])
        assert edge.nets_by_market["m1"] == pytest.approx(60.0)
        assert edge.signals_by_market["m1"] == 0.12

    def test_no_flow_zero_signal(self, monkeypatch):
        from fork_sim import whale_edge_p14
        monkeypatch.setattr(whale_edge_p14, "fetch_market_token_ids",
                            lambda mid: (YES_TOK, NO_TOK))
        # Whale traded a DIFFERENT market's token: no flow on this one.
        monkeypatch.setattr(whale_edge_p14, "pull_whale_trades",
                            lambda w, limit=1000: [
                                _trade("BUY", "foreign-tok", 0.5, 500, T - 1)])
        edge = whale_edge_p14.build_whale_signals([self._ep("m1")], ["w"])
        assert edge.nets_by_market["m1"] == 0.0
        assert edge.signals_by_market["m1"] == 0.0

    def test_post_t_whale_trades_do_not_leak(self, monkeypatch):
        from fork_sim import whale_edge_p14
        monkeypatch.setattr(whale_edge_p14, "fetch_market_token_ids",
                            lambda mid: (YES_TOK, NO_TOK))
        # All whale trades are AFTER decision time: signal must stay zero.
        monkeypatch.setattr(whale_edge_p14, "pull_whale_trades",
                            lambda w, limit=1000: [
                                _trade("BUY", YES_TOK, 0.99, 10_000, T + 3600)])
        edge = whale_edge_p14.build_whale_signals([self._ep("m1")], ["w"])
        assert edge.signals_by_market["m1"] == 0.0

    def test_missing_token_ids_zero_signal(self, monkeypatch):
        from fork_sim import whale_edge_p14
        monkeypatch.setattr(whale_edge_p14, "fetch_market_token_ids",
                            lambda mid: None)
        edge = whale_edge_p14.build_whale_signals([self._ep("m1")], ["w"])
        assert edge.signals_by_market["m1"] == 0.0


class TestDiscoverQualifiers:
    def test_reuses_fresh_registry_without_rescoring(self, monkeypatch):
        import time as _t
        from fork_sim import whale_edge_p14
        reg = {"0xwhale": {"realized_pnl_usd": 500.0, "win_rate": 0.6,
                           "resolved_trades": 25, "resolved_markets": 10,
                           "scored_at": _t.time() - 1000}}
        monkeypatch.setattr(whale_edge_p14.whale_flow, "tracked_whales",
                            lambda: reg)

        def _boom(*a, **k):
            raise AssertionError("re-scoring must not run on fresh registry")
        monkeypatch.setattr(whale_edge_p14.whale_flow, "discover_whales",
                            _boom)
        quals, scored = whale_edge_p14.discover_qualifiers()
        assert quals == ["0xwhale"]
        assert scored[0]["qualifies"] is True

    def test_stale_registry_triggers_rediscovery(self, monkeypatch):
        import time as _t
        from fork_sim import whale_edge_p14
        reg = {"0xwhale": {"realized_pnl_usd": 500.0, "win_rate": 0.6,
                           "resolved_trades": 25, "resolved_markets": 10,
                           "scored_at": _t.time() - 8 * 86400}}
        monkeypatch.setattr(whale_edge_p14.whale_flow, "tracked_whales",
                            lambda: reg)
        monkeypatch.setattr(whale_edge_p14.whale_flow,
                            "discover_candidates_from_leaderboard",
                            lambda limit: ["0xnew"])
        monkeypatch.setattr(whale_edge_p14.whale_flow, "discover_whales",
                            lambda seeds: [])
        quals, scored = whale_edge_p14.discover_qualifiers()
        assert quals == []
        assert scored == []
