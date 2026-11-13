"""Tests for whale-flow discovery v2: resolution cache, leaderboard seeds,
longer lookback. All network access is mocked; the SQLite cache runs in a
tmp dir via WHALE_CACHE_PATH.
"""
import json
import sqlite3
from unittest.mock import patch

import pytest

from sincor2 import whale_flow


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WHALE_CACHE_PATH", str(tmp_path / "whale_cache.db"))
    monkeypatch.setenv("WHALE_UNRESOLVED_TTL_SEC", "3600")
    monkeypatch.delenv("WHALE_MAX_TRADES", raising=False)
    yield tmp_path


def _gamma_mock(calls, resolved=True, closed=True):
    """Fake _get_json: records calls, serves one market slug-cid1/CID1."""
    def fake(url, timeout=15):
        calls.append(url)
        if "markets?slug=slug-cid1" in url:
            return [{"slug": "slug-cid1", "conditionId": "CID1", "id": 111,
                     "closed": closed, "question": "Q?",
                     "outcomePrices": ["1", "0"] if resolved else ["0.4", "0.6"],
                     "clobTokenIds": ["YES_TOK", "NO_TOK"]}]
        return None
    return fake


def test_resolution_cache_hit_avoids_refetch(cache_env):
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=_gamma_mock(calls)):
        r1 = whale_flow._market_resolution("CID1", "slug-cid1")
        n1 = len(calls)
        assert n1 == 1  # single slug lookup
        r2 = whale_flow._market_resolution("CID1", "slug-cid1")
    assert r1 == r2
    assert r1["yes_won"] is True
    assert len(calls) == n1, "second lookup must be served from cache"


def test_slug_condition_mismatch_refused(cache_env):
    """A slug resolving to a different conditionId must not be trusted."""
    def fake(url, timeout=15):
        if "markets?slug=slug-cid1" in url:
            return [{"slug": "slug-cid1", "conditionId": "OTHER_CID",
                     "id": 111, "closed": True, "question": "Q?",
                     "outcomePrices": ["1", "0"],
                     "clobTokenIds": ["YES_TOK", "NO_TOK"]}]
        return None

    with patch.object(whale_flow, "_get_json", side_effect=fake):
        assert whale_flow._market_resolution("CID1", "slug-cid1") is None


def test_no_slug_no_fetch(cache_env):
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=lambda u, timeout=15: calls.append(u) or None):
        assert whale_flow._market_resolution("CID1") is None
    assert calls == [], "without a slug there is no working lookup; must not guess"


def test_unresolved_cached_within_ttl(cache_env):
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=_gamma_mock(calls, closed=False)):
        assert whale_flow._market_resolution("CID1", "slug-cid1") is None
        n1 = len(calls)
        assert whale_flow._market_resolution("CID1", "slug-cid1") is None
    assert len(calls) == n1, "unresolved market must not be refetched within TTL"


def test_unresolved_refetched_after_ttl_expiry(cache_env, monkeypatch):
    monkeypatch.setenv("WHALE_UNRESOLVED_TTL_SEC", "0")
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=_gamma_mock(calls, closed=False)):
        whale_flow._market_resolution("CID1", "slug-cid1")
        n1 = len(calls)
        whale_flow._market_resolution("CID1", "slug-cid1")
    assert len(calls) == n1 * 2, "expired negative cache must refetch"


def test_fetch_failure_not_cached(cache_env):
    calls = []
    with patch.object(whale_flow, "_get_json", side_effect=lambda u, timeout=15: calls.append(u) or None):
        assert whale_flow._market_resolution("CID1", "slug-cid1") is None
        n1 = len(calls)
        assert whale_flow._market_resolution("CID1", "slug-cid1") is None
    assert len(calls) == n1 * 2, "API failures must not be cached"
    conn = sqlite3.connect(str(cache_env / "whale_cache.db"))
    try:
        n = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    finally:
        conn.close()
    assert n == 0


def test_cache_file_created_at_env_path(cache_env):
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=_gamma_mock(calls)):
        whale_flow._market_resolution("CID1", "slug-cid1")
    db = cache_env / "whale_cache.db"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT is_resolved FROM markets WHERE condition_id='CID1'").fetchone()
    finally:
        conn.close()
    assert row[0] == 1


def test_cache_creates_missing_parent_dir(tmp_path, monkeypatch):
    nested = tmp_path / "deep" / "nested" / "whale_cache.db"
    monkeypatch.setenv("WHALE_CACHE_PATH", str(nested))
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=_gamma_mock(calls)):
        r = whale_flow._market_resolution("CID1", "slug-cid1")
    assert r["yes_won"] is True
    assert nested.is_file(), "cache must create missing parent directories"


def test_market_tokens_served_from_cache(cache_env):
    calls = []
    with patch.object(whale_flow, "_get_json",
                       side_effect=_gamma_mock(calls, closed=False)):
        t1 = whale_flow._market_tokens("CID1", "slug-cid1")
        n1 = len(calls)
        t2 = whale_flow._market_tokens("CID1", "slug-cid1")
    assert t1 == ("YES_TOK", "NO_TOK", "Q?", "111")
    assert t2 == t1
    assert len(calls) == n1, "token map must come from cache on second call"


def test_leaderboard_seed_parsing_rank_order(cache_env):
    fake = [
        {"rank": "1", "proxyWallet": "0xaaa", "pnl": 2500000.0},
        {"rank": "2", "proxyWallet": "0xbbb", "pnl": 1000000.0},
        {"rank": "3", "pnl": 500.0},  # no wallet -> skipped
        {"rank": "4", "proxyWallet": "0xaaa", "pnl": 10.0},  # dup -> skipped
    ]
    with patch.object(whale_flow, "_get_json", return_value=fake) as m:
        seeds = whale_flow.discover_candidates_from_leaderboard(limit=10)
    assert seeds == ["0xaaa", "0xbbb"]
    assert "v1/leaderboard" in m.call_args[0][0]


def test_leaderboard_empty_on_api_failure(cache_env):
    with patch.object(whale_flow, "_get_json", return_value=None):
        assert whale_flow.discover_candidates_from_leaderboard() == []
    with patch.object(whale_flow, "_get_json", return_value=[]):
        assert whale_flow.discover_candidates_from_leaderboard() == []


def test_discover_candidates_leaderboard_first_deduped(cache_env):
    with patch.object(whale_flow, "discover_candidates_from_leaderboard",
                       return_value=["0xaaa"]), \
         patch.object(whale_flow, "discover_candidates_from_flow",
                       return_value=["0xaaa", "0xbbb"]):
        assert whale_flow.discover_candidates() == ["0xaaa", "0xbbb"]


def _res_yes():
    return {"yes_won": True, "token_id_yes": "YES_TOK",
            "token_id_no": "NO_TOK", "question": "Q", "market_id": "M1"}


def test_market_pnl_win_and_loss():
    # BUY 100 YES @ 0.60, YES wins: pnl = 100*1 - 60 = +40
    pnl, won, vol = whale_flow._market_pnl(
        [{"side": "BUY", "asset": "YES_TOK", "price": 0.60, "size": 100.0}],
        _res_yes())
    assert pnl == pytest.approx(40.0)
    assert won is True
    assert vol == pytest.approx(60.0)
    # BUY 100 NO @ 0.60, YES wins: pnl = 0 - 60 = -60
    pnl, won, _ = whale_flow._market_pnl(
        [{"side": "BUY", "asset": "NO_TOK", "price": 0.60, "size": 100.0}],
        _res_yes())
    assert pnl == pytest.approx(-60.0)
    assert won is False


def test_market_pnl_skips_bad_rows():
    pnl, won, vol = whale_flow._market_pnl(
        [{"side": "BUY", "asset": "YES_TOK", "price": "bad", "size": 100.0},
         {"side": "BUY", "asset": "YES_TOK", "price": 0.5, "size": 10.0}],
        _res_yes())
    assert pnl == pytest.approx(10.0 - 5.0)
    assert vol == pytest.approx(5.0)


def test_wallet_trades_paginates_to_limit(cache_env):
    pages = [[{"id": i}] * 500 for i in range(4)] + [[{"id": 9}] * 200]
    with patch.object(whale_flow, "_get_json", side_effect=pages):
        ts = whale_flow._wallet_trades("0xw", limit=1200)
    assert len(ts) == 1200, "longer lookback must pull multiple pages"


def test_score_wallet_default_lookback_is_2000(cache_env, monkeypatch):
    seen = {}

    def fake_trades(wallet, limit=500):
        seen["limit"] = limit
        return []

    monkeypatch.setenv("WHALE_MAX_TRADES", "3000")
    with patch.object(whale_flow, "_wallet_trades", side_effect=fake_trades):
        whale_flow.score_wallet("0xw")
    assert seen["limit"] == 3000


def test_score_wallet_default_lookback_env_default(cache_env):
    seen = {}

    def fake_trades(wallet, limit=500):
        seen["limit"] = limit
        return []

    with patch.object(whale_flow, "_wallet_trades", side_effect=fake_trades):
        whale_flow.score_wallet("0xw")
    assert seen["limit"] == 2000


def test_score_wallet_shares_cache_across_wallets(cache_env):
    """Two wallets on the same market -> gamma fetched once for that market."""
    calls = []

    def fake_trades(wallet, limit=500):
        cids = [(("CID1", "slug-cid1", "ev-cid1"))] if wallet == "0xaaa" \
            else [("CID1", "slug-cid1", "ev-cid1"),
                  ("CID2", "slug-cid2", "ev-cid2")]
        return [{"conditionId": c, "slug": s, "eventSlug": e, "side": "BUY",
                 "asset": "YES_TOK", "price": 0.6, "size": 10.0,
                 "timestamp": 1700000000}
                for c, s, e in cids]

    def fake_get(url, timeout=15):
        calls.append(url)
        if "markets?slug=slug-cid1" in url:
            return [{"slug": "slug-cid1", "conditionId": "CID1", "id": 111,
                     "closed": True, "question": "Q1",
                     "outcomePrices": ["1", "0"],
                     "clobTokenIds": ["YES_TOK", "NO_TOK"]}]
        if "markets?slug=slug-cid2" in url:
            return [{"slug": "slug-cid2", "conditionId": "CID2", "id": 222,
                     "closed": False, "question": "Q2",
                     "outcomePrices": ["0.5", "0.5"],
                     "clobTokenIds": ["Y2", "N2"]}]
        return None

    with patch.object(whale_flow, "_wallet_trades", side_effect=fake_trades), \
         patch.object(whale_flow, "_get_json", side_effect=fake_get):
        s1 = whale_flow.score_wallet("0xaaa", max_trades=100)
        s2 = whale_flow.score_wallet("0xbbb", max_trades=100)
    slug_calls = [u for u in calls if "markets?slug=" in u]
    assert len([u for u in slug_calls if "slug-cid1" in u]) == 1, \
        "duplicate gamma slug fetches detected"
    assert s1.resolved_markets == 1
    assert s2.resolved_markets == 1  # CID2 open -> excluded, only CID1 counts
    assert s1.realized_pnl_usd == pytest.approx(10.0 - 6.0)


def test_score_wallet_builds_slug_map(cache_env):
    """score_wallet passes each market's slugs to the resolution lookup."""
    seen = {}

    def fake_trades(wallet, limit=500):
        return [
            {"conditionId": "CID1", "slug": "slug-one",
             "eventSlug": "event-one", "side": "BUY",
             "asset": "YES_TOK", "price": 0.6, "size": 10.0},
            {"conditionId": "CID1", "slug": "slug-one",
             "eventSlug": "event-one", "side": "BUY",
             "asset": "YES_TOK", "price": 0.7, "size": 10.0},
            {"conditionId": "CID9", "side": "BUY",  # no slugs -> Nones
             "asset": "YES_TOK", "price": 0.6, "size": 10.0},
        ]

    def fake_res(cid, slug=None, event_slug=None):
        seen[cid] = (slug, event_slug)
        return None

    with patch.object(whale_flow, "_wallet_trades", side_effect=fake_trades), \
         patch.object(whale_flow, "_market_resolution", side_effect=fake_res):
        whale_flow.score_wallet("0xw", max_trades=100)
    assert seen == {"CID1": ("slug-one", "event-one"),
                    "CID9": (None, None)}


def test_fetch_market_event_slug_fallback(cache_env):
    """When the market slug lookup misses, the event slug fallback finds
    the market by conditionId in the event's nested markets."""
    def fake(url, timeout=15):
        if "markets?slug=" in url:
            return []  # slug index misses
        if "events?slug=event-one" in url:
            return [{"slug": "event-one", "markets": [
                {"slug": "other", "conditionId": "CIDX", "id": 1,
                 "closed": True, "question": "Q?",
                 "outcomePrices": ["0", "1"],
                 "clobTokenIds": ["Y", "N"]},
                {"slug": "slug-one", "conditionId": "CID1", "id": 2,
                 "closed": True, "question": "Q?",
                 "outcomePrices": ["1", "0"],
                 "clobTokenIds": ["YES_TOK", "NO_TOK"]},
            ]}]
        return None

    with patch.object(whale_flow, "_get_json", side_effect=fake):
        r = whale_flow._market_resolution("CID1", "slug-one", "event-one")
    assert r is not None
    assert r["yes_won"] is True
    assert r["token_id_yes"] == "YES_TOK"


def test_fetch_market_event_slug_wrong_event_refused(cache_env):
    """An event slug resolving to a different event must not be trusted."""
    def fake(url, timeout=15):
        if "events?slug=" in url:
            return [{"slug": "different-event", "markets": []}]
        return None

    with patch.object(whale_flow, "_get_json", side_effect=fake):
        assert whale_flow._market_resolution(
            "CID1", "slug-one", "event-one") is None


def test_discover_whales_passes_max_trades(cache_env):
    seen = {}
    stats = whale_flow.WhaleStats(wallet="0xaaa", resolved_trades=0,
                                  resolved_markets=0, realized_pnl_usd=0.0,
                                  win_rate=0.0, total_volume_usd=0.0)

    def fake_score(wallet, max_trades=None):
        seen["max_trades"] = max_trades
        return stats

    with patch.object(whale_flow, "score_wallet", side_effect=fake_score):
        whale_flow.discover_whales(["0xaaa"], max_trades=1500)
    assert seen["max_trades"] == 1500
