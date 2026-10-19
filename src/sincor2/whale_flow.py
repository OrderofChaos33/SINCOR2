"""Whale-flow strategy — follow wallets with demonstrated resolved-market edge.

Thesis (the strongest empirical leg in the current Polymarket meta):
informed flow shows up on-chain before prices fully adjust. Rather than
modeling fundamentals, we score wallets by their *realized* P&L on
*resolved* markets and mirror fresh entries from the profitable ones.

Pipeline:
  1. ``score_wallet(wallet)`` — pull the wallet's trade history, join each
     market to its resolution, compute realized P&L with real settlement
     math. Only resolved markets count; unresolved PnL is mark-to-model
     fiction and is excluded.
  2. ``discover_whales()`` — seed from recent high-volume wallets, score
     each, persist the profitable ones to the whale registry.
  3. ``fresh_entries()`` — for tracked whales, find trades in the last N
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


def _market_resolution(condition_id: str) -> Optional[Dict[str, Any]]:
    """Resolution info for one market, or None if unresolved/unknown."""
    data = _get_json(f"{GAMMA_API}/markets?condition_id={condition_id}")
    if not data:
        return None
    m = data[0]
    if not m.get("closed"):
        return None
    try:
        prices = m.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        token_ids = m.get("clobTokenIds")
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        yes_won = float(prices[0]) >= 0.99
        return {
            "yes_won": yes_won,
            "token_id_yes": str(token_ids[0]),
            "token_id_no": str(token_ids[1]) if len(token_ids) > 1 else "",
            "question": m.get("question", ""),
            "market_id": str(m.get("id")),
        }
    except (TypeError, ValueError, KeyError, IndexError):
        return None


def score_wallet(wallet: str, max_trades: int = 500) -> WhaleStats:
    """Realized P&L for a wallet across resolved markets only.

    Settlement math per wallet+market:
      net_cost   = Σ buy(price×size) − Σ sell(price×size)
      net_tokens = Σ buy(size) − Σ sell(size)
      payout     = net_tokens × resolution (1/0 for the held side)
      pnl        = payout − net_cost
    """
    trades = _wallet_trades(wallet, limit=max_trades)
    by_market: Dict[str, List[Dict[str, Any]]] = {}
    for t in trades:
        by_market.setdefault(t.get("conditionId", ""), []).append(t)

    resolved_trades = 0
    resolved_markets = 0
    pnl = 0.0
    wins = 0
    volume = 0.0
    for cid, ts in by_market.items():
        res = _market_resolution(cid)
        if res is None:
            continue  # unresolved: excluded, not marked
        # Determine which side's tokens the wallet mostly held
        yes_tok, no_tok = res["token_id_yes"], res["token_id_no"]
        net_cost = 0.0
        net_yes = 0.0
        net_no = 0.0
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
        market_pnl = (net_yes * (1.0 if res["yes_won"] else 0.0)
                      + net_no * (0.0 if res["yes_won"] else 1.0)
                      - net_cost)
        pnl += market_pnl
        resolved_trades += len(ts)
        resolved_markets += 1
        if market_pnl > 0:
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


def discover_whales(candidate_wallets: List[str]) -> List[WhaleStats]:
    """Score candidates, persist qualifiers. Returns all scored stats."""
    reg = _load_registry()
    scored = []
    for w in candidate_wallets:
        try:
            stats = score_wallet(w)
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
            # Token map + question from gamma (fresh entries are usually
            # unresolved, so the resolution lookup above missed).
            gm = _get_json(f"{GAMMA_API}/markets?condition_id={cid}")
            if not gm:
                continue
            try:
                tids = gm[0].get("clobTokenIds")
                if isinstance(tids, str):
                    tids = json.loads(tids)
                yes_tok, no_tok = str(tids[0]), str(tids[1])
            except (TypeError, ValueError, KeyError, IndexError):
                continue
            q, mid = gm[0].get("question", ""), str(gm[0].get("id"))
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
