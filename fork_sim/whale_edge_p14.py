"""Whale-flow edge wired into the P14 fork-sim (Green Team Mission 1A).

Hypothesis: entering the same side as a qualified whale's pre-decision flow
produces positive expectancy on resolved Polymarket markets.

COMMITTED STRATEGY PARAMETERS (fixed BEFORE the first sim run; never tuned
to results):
    N_EPISODES          = 60   (same mechanical selection as the momentum run:
                                fetch_episodes(60, min_volume_usd=10_000))
    MIN_VOLUME_USD      = 10_000
    DECISION_LEAD_HOURS = 48   (T = endDate - 48h, same as profit_p14)
    SIGNAL_CAP          = 0.12 (probability points, same cap as momentum)
    WHALE_BAR           = MIN_RESOLVED_TRADES=20, MIN_WHALE_PNL_USD=100.0
                          (reused via discover_whales; resolved-market-only
                          scoring, no change to the bar)
    WHALE_SEEDS         = discover_candidates_from_leaderboard(limit=50)
    WHALE_TRADE_PULL_LIMIT = 1000 trades/whale for pre-T flow matching
    DIRECTION_RULE      = BUY yes-token or SELL no-token  -> bullish YES (+1)
                          SELL yes-token or BUY no-token -> bearish  (-1)
                          anything else (unknown side/asset) -> 0
    WEIGHTING           = price * size notional per trade, summed across
                          trades AND whales (net_notional)
    SIGNAL_RULE         = sign(net_notional) * SIGNAL_CAP; 0.0 if net == 0
    PRE_DECISION_FILTER = trade timestamp < episode decision_ts (STRICT:
                          post-T trades excluded, no lookahead)
    TRADE_MATCH         = trade['asset'] must equal the episode market's
                          clobTokenIds yes/no token ID

GO bar (same as momentum): profitability_verdict from profit_p14 —
net_pnl > 0 AND win_rate >= 40% AND max_drawdown < 20% of bankroll
AND trades >= 10.

DISCLOSED LIMITATION (selection bias, not hidden): whale qualification
reuses the existing all-time resolved-P&L bar, so qualification itself
sees post-T resolutions. Only the *directional signal* is lookahead-free:
every trade counted in net_notional has timestamp < T. The bias is mild
(the signal is pre-decision flow, not the outcome) and is disclosed here.

PAPER ONLY: this module is read-only simulation wiring. It makes public
HTTP calls (gamma + data APIs), runs discovery scoring, and feeds the
real prediction_markets.run_backtest. No live capital, no signing, no
broadcasts, no fund movement, no private keys.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Persistent workspace paths: the whale registry/cache must survive VM
# restarts (the original /data paths were wiped by a restart).
GREEN_DIR = Path(os.getenv(
    "GREEN_MISSION1A_DIR", "/home/hatch/workspace/green-mission1a"))
os.environ.setdefault("WHALE_CACHE_PATH",
                      str(GREEN_DIR / "whale_cache.db"))
os.environ.setdefault("WHALE_REGISTRY_PATH",
                      str(GREEN_DIR / "whale_registry.json"))
GREEN_DIR.mkdir(parents=True, exist_ok=True)

from sincor2 import whale_flow  # noqa: E402  (env must be set first)

from fork_sim.profit_p14 import (  # noqa: E402
    SIGNAL_CAP as P14_SIGNAL_CAP,
    Episode,
    fetch_episodes,
    profitability_verdict,
    run_profit_sim,
    summarize,
)

# --- committed parameters -------------------------------------------------
N_EPISODES = 60
MIN_VOLUME_USD = 10_000
DECISION_LEAD_HOURS = 48
SIGNAL_CAP = 0.12
assert SIGNAL_CAP == P14_SIGNAL_CAP, "whale edge must share momentum's cap"
WHALE_SEED_LIMIT = 50
WHALE_TRADE_PULL_LIMIT = 1000

TOKEN_ID_CACHE = GREEN_DIR / "token_id_cache.json"
TRADES_CACHE = GREEN_DIR / "whale_trades_cache.json"
RESULTS_PATH = Path(__file__).resolve().parent / "whale_edge_results.json"
GAMMA = "https://gamma-api.polymarket.com"


def trade_direction(side: str, asset: str,
                    yes_token_id: str, no_token_id: str) -> int:
    """Map one trade to a YES-direction vote: +1 bullish, -1 bearish, 0 none.

    BUY yes-token or SELL no-token  -> +1 (whale leaning YES)
    SELL yes-token or BUY no-token  -> -1 (whale leaning NO)
    Any other side or asset (unrecognized side, foreign token) -> 0.
    """
    s = (side or "").upper()
    if s not in ("BUY", "SELL"):
        return 0
    if asset == yes_token_id:
        return 1 if s == "BUY" else -1
    if asset == no_token_id:
        return -1 if s == "BUY" else 1
    return 0


def aggregate_whale_flow(trades: List[Dict],
                         yes_token_id: str, no_token_id: str,
                         cutoff_ts: int) -> float:
    """Net pre-cutoff notional over trades on this market.

    Only trades with timestamp < cutoff_ts count (no lookahead). Each
    trade contributes trade_direction(...) * price * size (USDC notional).
    Malformed trades (missing/NaN price/size/timestamp) are skipped.
    """
    net = 0.0
    for t in trades:
        try:
            ts = float(t.get("timestamp") or 0)
        except (TypeError, ValueError):
            continue
        if ts >= cutoff_ts:
            continue  # post-decision: excluded, no lookahead
        try:
            price = float(t.get("price") or 0)
            size = float(t.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or size <= 0:
            continue
        d = trade_direction(t.get("side") or "", str(t.get("asset") or ""),
                            yes_token_id, no_token_id)
        net += d * price * size
    return net


def whale_signal_from_net(net_notional: float) -> float:
    """Direction-only signal at full cap (comparable with momentum).

    sign(net_notional) * SIGNAL_CAP; 0.0 when there is no whale flow.
    """
    if net_notional > 0:
        return SIGNAL_CAP
    if net_notional < 0:
        return -SIGNAL_CAP
    return 0.0


def make_whale_signal_fn(signals_by_market: Dict[str, float]
                         ) -> Callable[[Episode], float]:
    """Adapt precomputed per-market signals to run_profit_sim's adapter."""
    def _fn(ep: Episode) -> float:
        return signals_by_market.get(ep.market_id, 0.0)
    return _fn


def coverage_report(signals_by_market: Dict[str, float],
                    episodes: List[Episode]) -> Dict[str, int]:
    """How many episodes carried a nonzero whale signal."""
    nonzero = sum(1 for ep in episodes
                  if signals_by_market.get(ep.market_id, 0.0) != 0.0)
    return {"episodes": len(episodes), "nonzero_signal": nonzero,
            "zero_signal": len(episodes) - nonzero}


def _load_json_cache(path: Path) -> Dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _save_json_cache(path: Path, data: Dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass


def fetch_market_token_ids(market_id: str) -> Optional[Tuple[str, str]]:
    """(yes_token_id, no_token_id) for a gamma market id, cached to disk.

    Reuses the clobTokenIds convention shared with profit_p14 (index 0 is
    the YES token) and whale_flow._fetch_market.
    """
    cache = _load_json_cache(TOKEN_ID_CACHE)
    if market_id in cache:
        return tuple(cache[market_id])
    try:
        req = urllib.request.Request(f"{GAMMA}/markets/{market_id}",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            m = json.load(r)
        tids = m["clobTokenIds"]
        if isinstance(tids, str):
            tids = json.loads(tids)
        yes_tok, no_tok = str(tids[0]), str(tids[1])
    except Exception:
        return None
    cache[market_id] = [yes_tok, no_tok]
    _save_json_cache(TOKEN_ID_CACHE, cache)
    time.sleep(0.1)
    return yes_tok, no_tok


def pull_whale_trades(wallet: str,
                      limit: int = WHALE_TRADE_PULL_LIMIT) -> List[Dict]:
    """Bounded trade pull per whale, cached to disk for reproducibility."""
    cache = _load_json_cache(TRADES_CACHE)
    if wallet in cache and len(cache[wallet]) >= limit:
        return cache[wallet][:limit]
    trades = whale_flow._wallet_trades(wallet, limit=limit)
    cache[wallet] = trades
    _save_json_cache(TRADES_CACHE, cache)
    time.sleep(0.2)
    return trades


@dataclass
class WhaleEdgeRun:
    episodes: List[Episode]
    whales: List[str]              # qualified whale wallets used
    scored: List[Dict]            # all scored stats (qualifiers + rejects)
    signals_by_market: Dict[str, float]
    nets_by_market: Dict[str, float]


def build_whale_signals(episodes: List[Episode],
                        whale_wallets: List[str]) -> WhaleEdgeRun:
    """Wire qualified-whale pre-decision flow into per-episode signals.

    For each episode (decision time T) and each whale: pull the whale's
    trades, keep only those on the episode's market (asset match against
    clobTokenIds) with timestamp < T, and sum direction*price*size.
    """
    signals: Dict[str, float] = {}
    nets: Dict[str, float] = {}
    for ep in episodes:
        tids = fetch_market_token_ids(ep.market_id)
        if tids is None:
            signals[ep.market_id] = 0.0
            nets[ep.market_id] = 0.0
            continue
        yes_tok, no_tok = tids
        net = 0.0
        for wallet in whale_wallets:
            trades = pull_whale_trades(wallet)
            net += aggregate_whale_flow(trades, yes_tok, no_tok,
                                        ep.decision_ts)
        nets[ep.market_id] = net
        signals[ep.market_id] = whale_signal_from_net(net)
    return WhaleEdgeRun(episodes=episodes, whales=whale_wallets, scored=[],
                        signals_by_market=signals, nets_by_market=nets)


def discover_qualifiers(seed_limit: int = WHALE_SEED_LIMIT) -> Tuple[List[str], List[Dict]]:
    """Re-run whale discovery with the persistent registry paths.

    Seeds from the public all-time-PnL leaderboard; qualification runs
    through the existing bar (>=20 resolved trades, >=+$100 realized P&L
    on resolved markets only). Returns (qualifier wallets, scored stats).

    If the persistent registry already holds qualifiers scored within the
    last 7 days, they are reused (re-scoring is idempotent but costs
    hundreds of API calls); otherwise discovery re-runs from scratch.
    """
    reg = whale_flow.tracked_whales()
    fresh = [w for w, m in reg.items()
             if time.time() - float(m.get("scored_at", 0)) < 7 * 86400]
    if fresh:
        scored = [{"wallet": w, "resolved_trades": m.get("resolved_trades"),
                   "resolved_markets": m.get("resolved_markets"),
                   "realized_pnl_usd": m.get("realized_pnl_usd"),
                   "win_rate": m.get("win_rate"), "qualifies": True}
                  for w, m in reg.items() if w in fresh]
        return fresh, scored
    seeds = whale_flow.discover_candidates_from_leaderboard(seed_limit)
    if not seeds:
        return [], []
    scored = whale_flow.discover_whales(seeds)
    qualifiers = [s.wallet for s in scored if s.qualifies]
    return qualifiers, [
        {"wallet": s.wallet, "resolved_trades": s.resolved_trades,
         "resolved_markets": s.resolved_markets,
         "realized_pnl_usd": s.realized_pnl_usd,
         "win_rate": s.win_rate, "qualifies": s.qualifies}
        for s in scored
    ]


def run() -> Dict:
    """Full paper sim: episodes -> whale signals -> real backtest -> verdict."""
    print("fetching episodes (mechanical selection, n=60)...")
    episodes = fetch_episodes(N_EPISODES, MIN_VOLUME_USD)
    print(f"  {len(episodes)} episodes")

    print("discovering whales (leaderboard seeds, resolved-P&L bar)...")
    qualifiers, scored = discover_qualifiers()
    print(f"  {len(qualifiers)} qualified whales of {len(scored)} scored")

    print("building pre-decision whale signals...")
    edge = build_whale_signals(episodes, qualifiers)
    edge.scored = scored
    cov = coverage_report(edge.signals_by_market, episodes)
    print(f"  coverage: {cov['nonzero_signal']}/{cov['episodes']} "
          f"episodes with nonzero signal")

    stats = run_profit_sim(episodes, make_whale_signal_fn(
        edge.signals_by_market))
    verdict, reason = profitability_verdict(stats)
    stats["verdict"] = verdict
    stats["reason"] = reason
    stats["coverage"] = cov
    stats["qualifiers"] = qualifiers
    stats["scored"] = scored
    stats["net_notional_by_market"] = edge.nets_by_market

    print(summarize("whale-edge", stats))
    RESULTS_PATH.write_text(json.dumps(stats, indent=2))
    print(f"results -> {RESULTS_PATH}")
    return stats


if __name__ == "__main__":
    run()
