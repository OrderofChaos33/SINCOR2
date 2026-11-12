"""Tests for the Data API v2 port of whale_flow's fetch layer.

Polymarket retired Data API v1 (HTTP 410, verified live 2026-11-11):
``/v1/leaderboard`` and the unversioned ``/trades`` are gone. V2 serves
an envelope (``data`` + ``pagination.next_cursor``), snake_case fields
(``proxy_wallet`` / ``condition_id`` / ``token_id`` / ``event_slug``),
cursor-only pagination (``offset`` is a 400), and the leaderboard needs
``time_period=all`` for the all-time board the seed logic assumes.

These tests pin the v2 wire contract at the fetch boundary. Downstream
consumers (score_wallet, fresh_entries, fork_sim) keep reading the
normalized internal keys (conditionId / asset / eventSlug) — the port
translates at the boundary so their math is untouched.
"""
from unittest.mock import patch

import pytest

from sincor2 import whale_flow


def _v2_trade(**over):
    row = {
        "proxy_wallet": "0xwhale",
        "side": "BUY",
        "token_id": "YES_TOK",
        "condition_id": "CID1",
        "size": 100.0,
        "price": 0.50,
        "timestamp": 1_700_000_000,
        "slug": "slug-cid1",
        "event_slug": "event-cid1",
        "outcome": "Yes",
        "outcome_index": 0,
    }
    row.update(over)
    return row


def _page(rows, next_cursor=None):
    return {"data": rows,
            "pagination": {"next_cursor": next_cursor, "has_more": bool(next_cursor)}}


class TestWalletTradesV2:
    def test_cursor_pagination_and_normalization(self):
        pages = {
            None: _page([_v2_trade()], next_cursor="cur2"),
            "cur2": _page([_v2_trade(price=0.60)], next_cursor=None),
        }
        calls = []

        def fake(url, timeout=15):
            calls.append(url)
            cursor = None
            if "cursor=cur2" in url:
                cursor = "cur2"
            return pages[cursor]

        with patch.object(whale_flow, "_get_json", side_effect=fake):
            trades = whale_flow._wallet_trades("0xwhale", limit=500)

        assert len(trades) == 2
        assert all("/v2/trades" in u for u in calls)
        assert all("user=0xwhale" in u for u in calls)
        assert all("offset" not in u for u in calls), "v2 rejects offset"
        # Normalized internal keys for downstream consumers.
        t = trades[0]
        assert t["conditionId"] == "CID1"
        assert t["asset"] == "YES_TOK"
        assert t["eventSlug"] == "event-cid1"
        assert t["slug"] == "slug-cid1"

    def test_stops_at_limit(self):
        page = _page([_v2_trade()] * 10, next_cursor="more")
        with patch.object(whale_flow, "_get_json", return_value=page):
            trades = whale_flow._wallet_trades("0xwhale", limit=5)
        assert len(trades) == 5

    def test_api_failure_returns_empty(self):
        with patch.object(whale_flow, "_get_json", return_value=None):
            assert whale_flow._wallet_trades("0xwhale") == []


class TestLeaderboardV2:
    def test_all_time_envelope_user_id(self):
        fake = _page([
            {"rank": 1, "user_id": "0xaaa", "pnl": 2500000.0},
            {"rank": 2, "user_id": "0xbbb", "pnl": 1000000.0},
            {"rank": 3, "pnl": 500.0},               # no wallet -> skipped
            {"rank": 4, "user_id": "0xaaa", "pnl": 10.0},  # dup -> skipped
        ])
        with patch.object(whale_flow, "_get_json", return_value=fake) as m:
            seeds = whale_flow.discover_candidates_from_leaderboard(limit=10)
        assert seeds == ["0xaaa", "0xbbb"]
        url = m.call_args[0][0]
        assert "/v2/leaderboard" in url
        assert "time_period=all" in url


class TestFlowCandidatesV2:
    def test_counts_proxy_wallet_over_pages(self):
        rows = ([_v2_trade(proxy_wallet="0xbusy")] * 30
                + [_v2_trade(proxy_wallet="0xquiet")] * 3)
        with patch.object(whale_flow, "_get_json",
                          return_value=_page(rows)) as m:
            seeds = whale_flow.discover_candidates_from_flow(limit=100)
        assert seeds == ["0xbusy"]  # 25+ trades in window qualifies as seed
        assert "/v2/trades" in m.call_args[0][0]


class TestScoreWalletOverV2:
    def test_settlement_math_unchanged_on_v2_rows(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WHALE_CACHE_PATH", str(tmp_path / "cache.db"))
        monkeypatch.setenv("WHALE_UNRESOLVED_TTL_SEC", "3600")

        def fake(url, timeout=15):
            if "/v2/trades" in url:
                return _page([_v2_trade()])  # BUY 100 YES @ 0.50
            if "markets?slug=" in url:
                return [{"slug": "slug-cid1", "conditionId": "CID1", "id": 111,
                         "question": "Q?", "closed": True,
                         "outcomePrices": "[\"1\", \"0\"]",
                         "clobTokenIds": "[\"YES_TOK\", \"NO_TOK\"]"}]
            return None

        with patch.object(whale_flow, "_get_json", side_effect=fake):
            stats = whale_flow.score_wallet("0xwhale")
        # YES won: payout 100 shares x $1 - $50 cost = +$50 realized.
        assert stats.resolved_trades == 1
        assert stats.resolved_markets == 1
        assert stats.realized_pnl_usd == pytest.approx(50.0)
        assert stats.win_rate == 1.0
