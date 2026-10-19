"""Tests for the Polyclaw execution adapter and whale-flow strategy.

Execution adapter: dry-run default, kill-switch behavior, no-key safety.
Whale flow: settlement-math correctness on synthetic ledgers (mocked APIs).
"""
import os
from unittest.mock import patch

import pytest

from sincor2.execution_adapter import (
    KillSwitchTripped,
    OrderResult,
    PolymarketAdapter,
    kill_switch_tripped,
)


@pytest.fixture
def dry_env(monkeypatch):
    monkeypatch.delenv("POLYCLAW_LIVE", raising=False)
    monkeypatch.delenv("POLYMARKET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYCLAW_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLYMARKET_PK", raising=False)
    monkeypatch.delenv("POLYCLAW_HALT", raising=False)


def test_adapter_defaults_to_dry_run(dry_env):
    a = PolymarketAdapter()
    assert not a.is_live()


def test_kill_switch_env_halts(dry_env, monkeypatch):
    monkeypatch.setenv("POLYCLAW_HALT", "1")
    assert kill_switch_tripped() is True


def test_kill_switch_blocks_orders(dry_env, monkeypatch):
    monkeypatch.setenv("POLYCLAW_HALT", "true")
    a = PolymarketAdapter()
    r = a.place_market_buy("0x" + "ab" * 32, 5.0)
    assert r.success is False
    assert r.error == "kill_switch_tripped"


def test_dry_run_order_simulates_at_ask(dry_env):
    a = PolymarketAdapter()
    with patch("sincor2.execution_adapter._book_ask", return_value=0.40):
        r = a.place_market_buy("0x" + "ab" * 32, 10.0)
    assert r.success is True
    assert r.simulated is True
    assert r.price == 0.40
    assert r.size_tokens == pytest.approx(25.0)
    assert r.order_id.startswith("dry_")


def test_no_book_means_no_order(dry_env):
    a = PolymarketAdapter()
    with patch("sincor2.execution_adapter._book_ask", return_value=None):
        r = a.place_market_buy("0x" + "ab" * 32, 10.0)
    assert r.success is False
    assert r.error == "no_book_available"


def test_key_never_logged(dry_env, monkeypatch, caplog):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0x" + "ff" * 32)
    monkeypatch.delenv("POLYCLAW_LIVE", raising=False)
    a = PolymarketAdapter()
    with patch("sincor2.execution_adapter._book_ask", return_value=0.5):
        a.place_market_buy("0x" + "ab" * 32, 5.0)
    assert ("ff" * 32) not in caplog.text


# --- whale-flow settlement math -------------------------------------------
from sincor2 import whale_flow


def _mock_trades_yes_win():
    # Wallet buys 100 YES @ 0.60, sells 40 @ 0.80, holds 60 through YES win.
    # pnl = 60*1.0 - (100*0.60 - 40*0.80) = 60 - 28 = +32
    return [
        {"proxyWallet": "0xw", "side": "BUY", "asset": "YES_TOK",
         "price": 0.60, "size": 100.0, "conditionId": "CID1",
         "timestamp": 1_700_000_000},
        {"proxyWallet": "0xw", "side": "SELL", "asset": "YES_TOK",
         "price": 0.80, "size": 40.0, "conditionId": "CID1",
         "timestamp": 1_700_000_100},
    ]


def _mock_resolution_yes_win(cid, slug=None, event_slug=None):
    return {"yes_won": True, "token_id_yes": "YES_TOK",
            "token_id_no": "NO_TOK", "question": "Q", "market_id": "M1"}


def test_score_wallet_yes_win():
    with patch.object(whale_flow, "_wallet_trades",
                       return_value=_mock_trades_yes_win()), \
         patch.object(whale_flow, "_market_resolution",
                       side_effect=_mock_resolution_yes_win):
        with patch.object(whale_flow, "MIN_RESOLVED_TRADES", 1), \
             patch.object(whale_flow, "MIN_WHALE_PNL_USD", 0):
            s = whale_flow.score_wallet("0xw")
    assert s.resolved_markets == 1
    assert s.realized_pnl_usd == pytest.approx(32.0)
    assert s.win_rate == 1.0
    assert s.qualifies is True


def test_score_wallet_yes_loss():
    # Same trades, but NO won: 60 YES tokens -> 0. pnl = 0 - 28 = -28
    def res_no(cid, slug=None, event_slug=None):
        r = _mock_resolution_yes_win(cid)
        r["yes_won"] = False
        return r
    with patch.object(whale_flow, "_wallet_trades",
                       return_value=_mock_trades_yes_win()), \
         patch.object(whale_flow, "_market_resolution", side_effect=res_no):
        with patch.object(whale_flow, "MIN_RESOLVED_TRADES", 1):
            s = whale_flow.score_wallet("0xw")
    assert s.realized_pnl_usd == pytest.approx(-28.0)
    assert s.win_rate == 0.0
    assert s.qualifies is False  # negative pnl < default MIN_WHALE_PNL_USD


def test_score_wallet_ignores_unresolved():
    with patch.object(whale_flow, "_wallet_trades",
                       return_value=_mock_trades_yes_win()), \
         patch.object(whale_flow, "_market_resolution", return_value=None):
        s = whale_flow.score_wallet("0xw")
    assert s.resolved_markets == 0
    assert s.realized_pnl_usd == 0.0
    assert s.qualifies is False


def test_fresh_entries_empty_registry():
    with patch.object(whale_flow, "_load_registry", return_value={}):
        assert whale_flow.fresh_entries() == []
