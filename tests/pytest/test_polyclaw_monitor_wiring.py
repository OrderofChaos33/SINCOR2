"""Item #4 monitor-feed wiring for Polyclaw (paper only).

The price-monitors service (~/workspace/price-monitors) keeps an
append-only SQLite store of Polymarket market_state observations keyed
``polymarket:<slug>`` with the first-outcome token id in meta. These
tests pin the consumer contract and the veto-only cross-check gate in
the live runner:

- fresh + agreeing monitor data  -> trade proceeds (confirm)
- fresh + diverging monitor data -> trade vetoed (the ONLY effect)
- stale / missing / corrupt data -> behavior EXACTLY as before the wiring

Fixture DBs are built in tmp_path; no network, no keys, POLYCLAW_LIVE
is never enabled.
"""
from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from sincor2.monitor_feed import MonitorFeed, get_monitor_feed
from sincor2 import polyclaw_mega_aggressive_live as live
from sincor2.forecasting_engine import Forecast
from sincor2.whale_flow import WhaleSignal

SLUG = "will-it-rain-tomorrow"
YES_TOK = "1111111111111111111111111111111111111111111111111111111111111111"
NO_TOK = "2222222222222222222222222222222222222222222222222222222222222222"


# ---------------------------------------------------------------------------
# Fixture DB helpers
# ---------------------------------------------------------------------------

def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE observations ("
        "id INTEGER PRIMARY KEY, ts REAL, source TEXT, key TEXT,"
        " kind TEXT, value REAL, meta TEXT)"
    )
    conn.executemany(
        "INSERT INTO observations (ts, source, key, kind, value, meta)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


def _gamma_row(slug, token_id, value, ts, source="polymarket_gamma"):
    meta = json.dumps({
        "slug": slug, "token_id": token_id, "condition_id": "0xcond",
        "outcome": "Yes", "outcomes": ["Yes", "No"],
        "outcome_prices": [str(value), str(round(1.0 - value, 4))],
    })
    return (ts, source, f"polymarket:{slug}", "market_state", value, meta)


@pytest.fixture()
def fresh_db(tmp_path):
    now = time.time()
    return _make_db(tmp_path / "monitors.db", [
        _gamma_row(SLUG, YES_TOK, 0.50, now - 5),
    ])


# ---------------------------------------------------------------------------
# Consumer: MonitorFeed
# ---------------------------------------------------------------------------

def test_fresh_slug_lookup_returns_value_and_age(fresh_db):
    obs = MonitorFeed(fresh_db).market_state_for_slug(SLUG)
    assert obs is not None
    assert obs["value"] == pytest.approx(0.50)
    assert obs["slug"] == SLUG
    assert obs["source"] == "polymarket_gamma"
    assert 0 <= obs["age_seconds"] < 60
    assert obs["fresh"] is True
    assert obs["meta"]["token_id"] == YES_TOK


def test_stale_slug_lookup_is_not_fresh(tmp_path):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.50, time.time() - 900),
    ])
    obs = MonitorFeed(db).market_state_for_slug(SLUG)
    assert obs is not None
    assert obs["age_seconds"] > 300
    assert obs["fresh"] is False


def test_missing_db_returns_none(tmp_path):
    feed = MonitorFeed(tmp_path / "does-not-exist.db")
    assert feed.market_state_for_slug(SLUG) is None
    assert feed.price_for_token(YES_TOK) is None


def test_missing_key_returns_none(fresh_db):
    feed = MonitorFeed(fresh_db)
    assert feed.market_state_for_slug("some-other-market") is None
    assert feed.price_for_token(NO_TOK) is None


def test_price_for_token_matches_meta_token(fresh_db):
    obs = MonitorFeed(fresh_db).price_for_token(YES_TOK)
    assert obs is not None
    assert obs["value"] == pytest.approx(0.50)
    assert obs["fresh"] is True


def test_corrupt_meta_row_never_raises(tmp_path):
    db = _make_db(tmp_path / "m.db", [
        (time.time(), "polymarket_gamma", f"polymarket:{SLUG}",
         "market_state", 0.42, "not-json{{{"),
    ])
    feed = MonitorFeed(db)
    obs = feed.market_state_for_slug(SLUG)  # value survives, meta degrades
    assert obs is not None
    assert obs["value"] == pytest.approx(0.42)
    assert obs["meta"] == {}
    # Token join needs meta -> corrupt meta means "no monitor data".
    assert feed.price_for_token(YES_TOK) is None


def test_corrupt_value_row_returns_none(tmp_path):
    db = _make_db(tmp_path / "m.db", [
        (time.time(), "polymarket_gamma", f"polymarket:{SLUG}",
         "market_state", "garbage", json.dumps({"slug": SLUG})),
    ])
    assert MonitorFeed(db).market_state_for_slug(SLUG) is None


def test_non_database_file_returns_none(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not sqlite at all" * 10)
    feed = MonitorFeed(junk)
    assert feed.market_state_for_slug(SLUG) is None
    assert feed.price_for_token(YES_TOK) is None


def test_locked_db_returns_none(tmp_path):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.50, time.time()),
    ])
    locker = sqlite3.connect(db)
    try:
        locker.execute("BEGIN EXCLUSIVE")
        locker.execute(
            "INSERT INTO observations (ts, source, key, kind, value, meta)"
            " VALUES (0, 'x', 'x', 'x', 0, '{}')")
        feed = MonitorFeed(db)
        assert feed.market_state_for_slug(SLUG) is None
        assert feed.price_for_token(YES_TOK) is None
    finally:
        locker.rollback()
        locker.close()


def test_get_monitor_feed_uses_env_path(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(tmp_path / "env.db"))
    assert get_monitor_feed().db_path == tmp_path / "env.db"


# ---------------------------------------------------------------------------
# Gate: _monitor_crosscheck in the live runner (veto-only, fail-open)
# ---------------------------------------------------------------------------

def _gate(reference, token=YES_TOK):
    return live._monitor_crosscheck(reference, token)


def test_gate_confirms_when_fresh_and_agreeing(fresh_db, monkeypatch):
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(fresh_db))
    out = _gate(0.52)
    assert out["allow"] is True
    assert out["state"] == "confirm"
    assert out["monitor_price"] == pytest.approx(0.50)


def test_gate_vetoes_when_fresh_and_diverging(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.95, time.time() - 5),
    ])
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(db))
    out = _gate(0.50)
    assert out["allow"] is False
    assert out["state"] == "veto"


def test_gate_allows_when_stale_even_if_diverging(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.95, time.time() - 900),
    ])
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(db))
    out = _gate(0.50)
    assert out["allow"] is True
    assert out["state"] == "stale"


def test_gate_allows_when_db_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(tmp_path / "nope.db"))
    out = _gate(0.50)
    assert out["allow"] is True
    assert out["state"] == "no_data"


def test_gate_allows_when_token_not_watched(fresh_db, monkeypatch):
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(fresh_db))
    out = _gate(0.50, token=NO_TOK)
    assert out["allow"] is True
    assert out["state"] == "no_data"


# ---------------------------------------------------------------------------
# Loop wiring: run_cycle with stubbed collaborators
# ---------------------------------------------------------------------------

class _Bankroll:
    def snapshot(self):
        return {"equity": 1000.0, "exposure": 0.0, "available": 1000.0,
                "max_position": 100.0, "realized_today": 0.0}

    def max_position_size(self):
        return 100.0

    def record_trade(self, **kwargs):
        return 1


class _OrderResult:
    success = True
    order_id = "ord-1"
    simulated = True
    error = None


class _Adapter:
    def __init__(self):
        self.calls = []

    def place_market_buy(self, token_id, size):
        self.calls.append((token_id, size))
        return _OrderResult()

    def is_live(self):
        return False


def _forecast(market_price=0.50, token_yes=YES_TOK):
    return Forecast(
        market_id="123", question="Will it rain tomorrow?",
        token_id_yes=token_yes, token_id_no=NO_TOK,
        market_price=market_price, model_probability=0.70,
        edge=0.20, confidence=0.90, liquidity_usd=5000.0,
        spread=0.02, momentum_24h=0.01, rationale="test",
    )


def _stub_cycle(monkeypatch, opportunities, whale_signals=None):
    monkeypatch.setattr(live, "get_bankroll", lambda: _Bankroll())
    monkeypatch.setattr(live, "kill_switch_tripped", lambda: False)
    monkeypatch.setattr(live, "resolve_predictions", lambda: None)
    monkeypatch.setattr(live, "scan_opportunities", lambda min_edge: opportunities)
    monkeypatch.setattr(live, "shadow_portfolio", SimpleNamespace(
        resolve_all=lambda bankroll: {},
        record_shadow_cycle=lambda opps: 0,
        compare_performance=lambda: {},
    ))
    if whale_signals is not None:
        monkeypatch.setattr(live, "WHALE_FLOW_ENABLED", True)
        monkeypatch.setattr(
            "sincor2.whale_flow.fresh_entries", lambda: whale_signals)
        monkeypatch.setattr(
            "sincor2.execution_adapter._book_ask", lambda token_id: 0.50)
    else:
        monkeypatch.setattr(live, "WHALE_FLOW_ENABLED", False)


def test_cycle_veto_blocks_momentum_trade(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.95, time.time() - 5),
    ])
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(db))
    _stub_cycle(monkeypatch, [_forecast(0.50)])
    adapter = _Adapter()
    result = live.run_cycle(adapter=adapter)
    assert adapter.calls == []
    assert result["trades_taken"] == []
    assert result["monitor_vetoed"] == 1


def test_cycle_confirm_lets_momentum_trade_through(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.52, time.time() - 5),
    ])
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(db))
    _stub_cycle(monkeypatch, [_forecast(0.50)])
    adapter = _Adapter()
    result = live.run_cycle(adapter=adapter)
    assert len(adapter.calls) == 1
    assert len(result["trades_taken"]) == 1
    assert result["monitor_confirmed"] == 1
    assert result["monitor_vetoed"] == 0


def test_cycle_without_monitor_db_trades_as_before(tmp_path, monkeypatch):
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(tmp_path / "absent.db"))
    _stub_cycle(monkeypatch, [_forecast(0.50)])
    adapter = _Adapter()
    result = live.run_cycle(adapter=adapter)
    assert len(adapter.calls) == 1
    assert len(result["trades_taken"]) == 1
    assert result["monitor_vetoed"] == 0
    assert result["monitor_confirmed"] == 0


def _whale_signal(token_id=YES_TOK):
    return WhaleSignal(
        wallet="0x" + "a" * 40, market_id="123",
        question="Will it rain tomorrow?", token_id=token_id,
        side="BUY_YES", whale_pnl_usd=500.0, whale_win_rate=0.80,
        whale_trade_usdc=100.0,
    )


def test_cycle_veto_blocks_whale_trade(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.95, time.time() - 5),
    ])
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(db))
    _stub_cycle(monkeypatch, [], whale_signals=[_whale_signal()])
    adapter = _Adapter()
    result = live.run_cycle(adapter=adapter)
    assert adapter.calls == []
    assert result["whale_trades"] == 0
    assert result["monitor_vetoed"] == 1


def test_cycle_confirm_lets_whale_trade_through(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "m.db", [
        _gamma_row(SLUG, YES_TOK, 0.52, time.time() - 5),
    ])
    monkeypatch.setenv("POLYCLAW_MONITOR_DB", str(db))
    _stub_cycle(monkeypatch, [], whale_signals=[_whale_signal()])
    adapter = _Adapter()
    result = live.run_cycle(adapter=adapter)
    assert len(adapter.calls) == 1
    assert result["whale_trades"] == 1
    assert result["monitor_confirmed"] == 1
