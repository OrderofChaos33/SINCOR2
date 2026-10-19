"""Whale-flow strategy — follow wallets with demonstrated resolved-market edge.

Thesis (the strongest empirical leg in the current Polymarket meta):
informed flow shows up on-chain before prices fully adjust. Rather than
modeling fundamentals, we score wallets by their *realized* P&L on
*resolved* markets and mirror fresh entries from the profitable ones.

Pipeline:
  1. ``discover_candidates()`` — seeds: Polymarket's public all-time-PnL
     leaderboard (wallets with demonstrated profit) plus recent
     high-volume flow wallets.
  2. ``score_wallet(wallet)`` — pull the wallet's trade history (long
     lookback, WHALE_MAX_TRADES), join each market to its resolution via
     the SQLite resolution cache (slug-based gamma lookup), compute
     realized P&L with real settlement math. Only resolved markets count;
     unresolved PnL is mark-to-model fiction and is excluded.
  3. ``discover_whales()`` — score candidates, persist qualifiers.
  4. ``fresh_entries()`` — for tracked whales, find trades in the last N
     minutes on markets where the whale has no prior position. A fresh
     whale entry is the signal.

A whale qualifies only with >= ``MIN_RESOLVED_TRADES`` resolved trades and
positive realized P&L. Win rate alone is not enough (it hides sizing).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.whaleflow")

GAMMA_API = os.getenv("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com")
DATA_API = os.getenv("POLYMARKET_DATA_API", "https://data-api.polymarket.com")

MIN_RESOLVED_TRADES = int(os.getenv("WHALE_MIN_RESOLVED_TRADES", "20"))
MIN_WHALE_PNL_USD = float(os.getenv("WHALE_MIN_PNL_USD", "100.0"))
FRESH_WINDOW_MIN = int(os.getenv("WHALE_FRESH_WINDOW_MIN", "60"))
WHALE_REGISTRY_PATH = Path(os.getenv("WHALE_REGISTRY_PATH", "/data/whale_registry.json"))
LEADERBOARD_URL = os.getenv("POLYMARKET_LEADERBOARD_URL",
                            "https://data-api.polymarket.com/v1/leaderboard")


def _cache_path() -> str:
    return os.getenv("WHALE_CACHE_PATH", "/data/whale_cache.db")


def _unresolved_ttl_sec() -> int:
    try:
        return int(os.getenv("WHALE_UNRESOLVED_TTL_SEC", "21600"))
    except ValueError:
        return 21600


def _max_trades_default() -> int:
    try:
        return int(os.getenv("WHALE_MAX_TRADES", "2000"))
    except ValueError:
        return 2000


def _cache_db() -> sqlite3.Connection:
    """SQLite resolution cache, keyed by condition_id.

    Resolved markets are immutable once closed, so they are cached
    indefinitely. Unresolved/unknown markets are cached for
    WHALE_UNRESOLVED_TTL_SEC (default 6h) to avoid re-hammering gamma
    while a sweep scores dozens of wallets sharing markets.
    """
    path = _cache_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS markets("
        " condition_id TEXT PRIMARY KEY,"
        " payload TEXT NOT NULL,"
        " is_resolved INTEGER NOT NULL,"
        " fetched_at REAL NOT NULL)")
    return conn


def _cache_get(condition_id: str) -> Optional[tuple]:
    try:
        conn = _cache_db()
        try:
            row = conn.execute(
                "SELECT payload, is_resolved, fetched_at FROM markets"
                " WHERE condition_id=?", (condition_id,)).fetchone()
            return row
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        logger.debug("resolution cache read failed: %s", exc)
        return None


def _cache_put(condition_id: str, payload: Dict[str, Any], is_resolved: bool) -> None:
    try:
        conn = _cache_db()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO markets(condition_id, payload,"
                " is_resolved, fetched_at) VALUES(?,?,?,?)",
                (condition_id, json.dumps(payload), 1 if is_resolved else 0,
                 time.time()))
            conn.commit()
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as exc:
        logger.debug("resolution cache write failed: %s", exc)


@dataclass
class WhaleStats:
    wallet: str
    resolved_trades: int
    resolved_markets: int
    realized_pnl_usd: float
    win_rate: float            # fraction of profitable resolved markets
    total_volume_usd: float
    qualifies: bool = False


@dataclass
class WhaleSignal:
    wallet: str
    market_id: str
    question: str
    token_id: str
    side: str                  # BUY_YES / BUY_NO (whale's direction)
    whale_pnl_usd: float
    whale_win_rate: float
    whale_trade_usdc: float    # size of the whale's entry
    detected_at: float = field(default_factory=time.time)


def _get_json(url: str, timeout: int = 15) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sincor-whaleflow/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("GET %s failed: %s", url[:80], exc)
        return None


def _wallet_trades(wallet: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Full trade history for one wallet (paginated)."""
    out: List[Dict[str, Any]] = []
    offset = 0
    while len(out) < limit:
        batch = _get_json(
            f"{DATA_API}/trades?limit=500&offset={offset}&user={wallet}")
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    return out[:limit]


def _fetch_market(condition_id: str, slug: Optional[str] = None,
                event_slug: Optional[str] = None) -> tuple:
    """Fetch one market from gamma.

    Returns ``(payload, is_resolved, definitive)`` where payload carries
    condition_id/market_id/question/token maps plus ``yes_won``
    (None when unresolved). ``definitive`` is False when gamma gave no
    usable answer (network error, unknown market) — such results are NOT
    cached, so a later retry can still learn the resolution.

    Lookup chain (verified 2026-10-19):
      1. ``markets?slug=<market slug>`` — exact when the slug index has it.
      2. ``events?slug=<event slug>`` — the event's nested ``markets``
         array carries full per-market records; matched by conditionId.
    Gamma's ``condition_ids``/``conditionId`` market query params do NOT
    work (the former returns nothing, the latter is ignored and yields an
    arbitrary list), so slug-based lookup is the only reliable path. A
    conditionId mismatch at any step refuses the record — never resolve
    the wrong market.
    """
    m = None
    if slug:
        data = _get_json(
            f"{GAMMA_API}/markets?slug={urllib.parse.quote(slug)}")
        if data:
            m = next((x for x in data if x.get("slug") == slug), None)
            if m is not None and condition_id and \
                    m.get("conditionId") != condition_id:
                m = None  # slug/condition mismatch: refuse wrong market
    if m is None and event_slug:
        data = _get_json(
            f"{GAMMA_API}/events?slug={urllib.parse.quote(event_slug)}")
        if data:
            for ev in data:
                if ev.get("slug") != event_slug:
                    continue
                m = next((x for x in ev.get("markets") or []
                          if x.get("conditionId") == condition_id), None)
                if m is not None:
                    break
    if m is None:
        return None, False, False
    try:
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        token_ids = m.get("clobTokenIds")
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        yes_price = float(prices[0])
        no_price = float(prices[1]) if len(prices) > 1 else 0.0
        yes_tok = str(token_ids[0])
        no_tok = str(token_ids[1]) if len(token_ids) > 1 else ""
    except (TypeError, ValueError, KeyError, IndexError):
        return None, False, False
    payload = {
        "condition_id": condition_id or m.get("conditionId", ""),
        "market_id": str(m.get("id")),
        "question": m.get("question", ""),
        "token_id_yes": yes_tok,
        "token_id_no": no_tok,
    }
    if not m.get("closed"):
        payload["is_resolved"] = False
        payload["yes_won"] = None
        return payload, False, True
    # Resolved CLOB-era markets settle at exactly 1/0. Legacy markets
    # may show 0/0 (no winner info retained) -> unknown, not resolved.
    if yes_price >= 0.99:
        yes_won = True
    elif no_price >= 0.99:
        yes_won = False
    else:
        payload["is_resolved"] = False
        payload["yes_won"] = None
        return payload, False, True
    payload["is_resolved"] = True
    payload["yes_won"] = yes_won
    return payload, True, True


def _cached_market(condition_id: str,
                   slug: Optional[str] = None,
                   event_slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Market payload via the resolution cache (fetch + store on miss)."""
    row = _cache_get(condition_id)
    if row is not None:
        payload_s, is_resolved, fetched_at = row
        if is_resolved or time.time() - fetched_at < _unresolved_ttl_sec():
            try:
                return json.loads(payload_s)
            except ValueError:
                pass
    payload, is_resolved, definitive = _fetch_market(
        condition_id, slug, event_slug)
    if definitive:
        _cache_put(condition_id, payload, is_resolved)
        return payload
    if row is not None:
        # Fetch failed but we have a stale row: token maps are immutable,
        # so the stale payload is still useful (resolution unknown).
        try:
            return json.loads(row[0])
        except ValueError:
            pass
    return None


def _market_resolution(condition_id: str,
                       slug: Optional[str] = None,
                       event_slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Resolution info for one market, or None if unresolved/unknown.

    Results are served from the SQLite resolution cache when available.
    """
    rec = _cached_market(condition_id, slug, event_slug)
    if rec is None or not rec.get("is_resolved"):
        return None
    return {
        "yes_won": rec["yes_won"],
        "token_id_yes": rec["token_id_yes"],
        "token_id_no": rec["token_id_no"],
        "question": rec["question"],
        "market_id": rec["market_id"],
    }


def _market_tokens(condition_id: str,
                   slug: Optional[str] = None,
                   event_slug: Optional[str] = None) -> Optional[tuple]:
    """``(yes_tok, no_tok, question, market_id)`` via cache, or None.

    Used for direction mapping on unresolved markets (fresh entries):
    the token map is immutable, so even a stale/unresolved cache row is
    authoritative here.
    """
    rec = _cached_market(condition_id, slug, event_slug)
    if not rec:
        return None
    try:
        return (rec["token_id_yes"], rec["token_id_no"],
                rec["question"], rec["market_id"])
    except KeyError:
        return None


def _market_pnl(ts: List[Dict[str, Any]], res: Dict[str, Any]) -> tuple:
    """``(pnl_usd, won, volume_usd)`` for one wallet+market given resolution.

    Settlement math:
      net_cost   = Σ buy(price×size) − Σ sell(price×size)
      net_tokens = Σ buy(size) − Σ sell(size)
      payout     = net_tokens × resolution (1/0 for the held side)
      pnl        = payout − net_cost
    Shared by ``score_wallet`` and the resolved-market seed discovery.
    """
    yes_tok, no_tok = res["token_id_yes"], res["token_id_no"]
    net_cost = 0.0
    net_yes = 0.0
    net_no = 0.0
    volume = 0.0
    for t in ts:
        try:
            price = float(t.get("price") or 0)
            size = float(t.get("size") or 0)
        except (TypeError, ValueError):
            continue
        asset = str(t.get("asset") or "")
        side = str(t.get("side") or "").upper()
        usdc = price * size
        volume += usdc
        if side == "BUY":
            net_cost += usdc
            if asset == yes_tok:
                net_yes += size
            elif asset == no_tok:
                net_no += size
        else:  # SELL
            net_cost -= usdc
            if asset == yes_tok:
                net_yes -= size
            elif asset == no_tok:
                net_no -= size
    pnl = (net_yes * (1.0 if res["yes_won"] else 0.0)
           + net_no * (0.0 if res["yes_won"] else 1.0)
           - net_cost)
    return pnl, pnl > 0, volume


def score_wallet(wallet: str, max_trades: Optional[int] = None) -> WhaleStats:
    """Realized P&L for a wallet across resolved markets only.

    Settlement math per wallet+market:
      net_cost   = Σ buy(price×size) − Σ sell(price×size)
      net_tokens = Σ buy(size) − Σ sell(size)
      payout     = net_tokens × resolution (1/0 for the held side)
      pnl        = payout − net_cost

    ``max_trades`` defaults to WHALE_MAX_TRADES (2000): the deeper the
    pull, the more likely we reach the wallet's resolved-market history
    instead of only its recent (usually unresolved) flow.
    """
    if max_trades is None:
        max_trades = _max_trades_default()
    trades = _wallet_trades(wallet, limit=max_trades)
    by_market: Dict[str, List[Dict[str, Any]]] = {}
    slugs: Dict[str, tuple] = {}
    for t in trades:
        cid = t.get("conditionId", "")
        by_market.setdefault(cid, []).append(t)
        # Gamma has no working condition-ID lookup; the trade's market
        # slug (+ event slug fallback) is the only reliable market key.
        if cid and cid not in slugs and (t.get("slug") or t.get("eventSlug")):
            slugs[cid] = (t.get("slug") or None,
                          t.get("eventSlug") or None)

    resolved_trades = 0
    resolved_markets = 0
    pnl = 0.0
    wins = 0
    volume = 0.0
    for cid, ts in by_market.items():
        slug, event_slug = slugs.get(cid, (None, None))
        res = _market_resolution(cid, slug, event_slug)
        if res is None:
            continue  # unresolved: excluded, not marked
        market_pnl, won, vol = _market_pnl(ts, res)
        pnl += market_pnl
        volume += vol
        resolved_trades += len(ts)
        resolved_markets += 1
        if won:
            wins += 1

    win_rate = (wins / resolved_markets) if resolved_markets else 0.0
    qualifies = (resolved_trades >= MIN_RESOLVED_TRADES
                 and pnl >= MIN_WHALE_PNL_USD)
    return WhaleStats(wallet=wallet, resolved_trades=resolved_trades,
                      resolved_markets=resolved_markets,
                      realized_pnl_usd=round(pnl, 2),
                      win_rate=round(win_rate, 3),
                      total_volume_usd=round(volume, 2),
                      qualifies=qualifies)


# --- whale registry (persisted) -------------------------------------------
def _load_registry() -> Dict[str, Dict[str, Any]]:
    try:
        return json.loads(WHALE_REGISTRY_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _save_registry(reg: Dict[str, Dict[str, Any]]) -> None:
    try:
        WHALE_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        WHALE_REGISTRY_PATH.write_text(json.dumps(reg, indent=2))
    except OSError as exc:
        logger.warning("whale registry save failed: %s", exc)


def tracked_whales() -> Dict[str, Dict[str, Any]]:
    """Wallets that qualified, with their last-known stats."""
    return _load_registry()


def discover_whales(candidate_wallets: List[str],
                    max_trades: Optional[int] = None) -> List[WhaleStats]:
    """Score candidates, persist qualifiers. Returns all scored stats."""
    reg = _load_registry()
    scored = []
    for w in candidate_wallets:
        try:
            stats = score_wallet(w, max_trades=max_trades)
        except Exception as exc:
            logger.debug("score_wallet(%s) failed: %s", w[:12], exc)
            continue
        scored.append(stats)
        if stats.qualifies:
            reg[w] = {
                "realized_pnl_usd": stats.realized_pnl_usd,
                "win_rate": stats.win_rate,
                "resolved_trades": stats.resolved_trades,
                "resolved_markets": stats.resolved_markets,
                "scored_at": time.time(),
            }
            logger.info("whale qualified %s pnl=$%.0f wr=%.0f%% n=%d",
                        w[:12], stats.realized_pnl_usd,
                        stats.win_rate * 100, stats.resolved_trades)
    _save_registry(reg)
    return scored


def discover_candidates_from_flow(limit: int = 5000) -> List[str]:
    """High-volume wallets from recent public trade flow (discovery seeds)."""
    from collections import Counter
    counts: Counter = Counter()
    offset = 0
    pulled = 0
    while pulled < limit:
        batch = _get_json(f"{DATA_API}/trades?limit=500&offset={offset}")
        if not batch:
            break
        for t in batch:
            counts[t.get("proxyWallet", "")] += 1
        pulled += len(batch)
        if len(batch) < 500:
            break
        offset += 500
    # Wallets with 25+ trades in the window are worth scoring
    return [w for w, n in counts.most_common(60) if w and n >= 25]


def discover_candidates_from_leaderboard(limit: int = 50) -> List[str]:
    """Top all-time-PnL wallets from Polymarket's public leaderboard.

    Seed only — qualification still runs through ``score_wallet``'s
    resolved-market-only math, since leaderboard PnL may include
    unrealized/mark-to-market on open positions. These seeds are far more
    likely than recent-flow volume to have deep resolved history.
    """
    data = _get_json(f"{LEADERBOARD_URL}?limit=100")
    if not data:
        return []
    out: List[str] = []
    for entry in data[:limit]:
        w = (entry or {}).get("proxyWallet")
        if w and w not in out:
            out.append(str(w))
    logger.info("leaderboard seeds: %d wallets", len(out))
    return out


def discover_candidates(flow_limit: int = 5000,
                        leaderboard_limit: int = 50) -> List[str]:
    """Combined seeds: leaderboard (proven all-time PnL) first, then
    recent-flow volume. Deduped.

    NOTE: a resolved-market-first seed was tried and removed — the Data
    API's ``trades?conditionId=`` filter is ignored (returns arbitrary
    trades), so per-market trade pulls are impossible. Leaderboard seeds
    scored through ``score_wallet``'s slug-based resolution are the
    working path.
    """
    seeds = discover_candidates_from_leaderboard(leaderboard_limit)
    for w in discover_candidates_from_flow(flow_limit):
        if w not in seeds:
            seeds.append(w)
    return seeds


def fresh_entries(window_min: int = FRESH_WINDOW_MIN) -> List[WhaleSignal]:
    """New whale entries in the last ``window_min`` minutes.

    A fresh entry = a tracked whale traded a market where it has no prior
    trades in our pull window. Direction follows the whale's side.
    """
    reg = _load_registry()
    if not reg:
        return []
    cutoff = time.time() - window_min * 60
    signals: List[WhaleSignal] = []
    for wallet, meta in reg.items():
        try:
            trades = _wallet_trades(wallet, limit=200)
        except Exception:
            continue
        seen_markets = set()
        for t in trades:
            try:
                ts = float(t.get("timestamp") or 0)
            except (TypeError, ValueError):
                continue
            cid = t.get("conditionId", "")
            if ts < cutoff:
                seen_markets.add(cid)  # prior position, not fresh
                continue
            if cid in seen_markets:
                continue
            # Fresh: first trade in window on this market. Check it's truly
            # new by confirming no older trades on it in this pull.
            older = [x for x in trades
                     if x.get("conditionId") == cid and float(x.get("timestamp") or 0) < cutoff]
            if older:
                seen_markets.add(cid)
                continue
            side = str(t.get("side") or "").upper()
            try:
                price = float(t.get("price") or 0)
                size = float(t.get("size") or 0)
            except (TypeError, ValueError):
                continue
            # Token map + question via the cached market record (fresh entries
            # are usually unresolved, so the resolution lookup misses — but
            # the token map is immutable and safe to reuse from cache).
            # The trade's slugs are the lookup key (see _fetch_market).
            tok = _market_tokens(cid, t.get("slug"), t.get("eventSlug"))
            if not tok:
                continue
            yes_tok, no_tok, q, mid = tok
            asset = str(t.get("asset") or "")
            # Direction follows the whale; token_id is always the token WE buy.
            if side == "BUY":
                direction = "BUY_YES" if asset == yes_tok else "BUY_NO"
                buy_token = asset
            else:  # SELL: mirror by buying the opposite side
                direction = "BUY_NO" if asset == yes_tok else "BUY_YES"
                buy_token = no_tok if asset == yes_tok else yes_tok
            signals.append(WhaleSignal(
                wallet=wallet, market_id=mid, question=q,
                token_id=buy_token, side=direction,
                whale_pnl_usd=meta.get("realized_pnl_usd", 0),
                whale_win_rate=meta.get("win_rate", 0),
                whale_trade_usdc=round(price * size, 2),
            ))
            seen_markets.add(cid)
    signals.sort(key=lambda s: s.whale_pnl_usd, reverse=True)
    logger.info("whaleflow: %d fresh entries in last %dm", len(signals), window_min)
    return signals
