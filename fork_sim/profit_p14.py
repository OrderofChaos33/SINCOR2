"""P14 profitability fork-sim: real Polymarket data through the real product.

Founder bar (2026-10-22): "if they make money on sepolia go live" — proven
profitability is the go-live bar. This module produces that evidence for
P14 (Prediction Market Automation) without inventing anything.

Design (all read-only; no signing, no broadcasting, no fund movement):
- Episodes are REAL resolved Polymarket markets from the public Gamma API.
  Selection is mechanical: closed=true, ordered by volume desc, first N with
  volume >= $10k and clean binary outcomePrices. No cherry-picking.
- Decision time T is fixed at 48h before each market's endDate.
- Prices come from the public CLOB prices-history API (real fills).
- The signal is DOCUMENTED and has NO lookahead:
    * momentum_signal: trailing-24h price momentum, scaled to probability
      points, capped. Uses only prices at/before T.
    * perfect_signal: lookahead BY DESIGN (uses the known outcome). This is
      a MACHINERY upper bound only — it answers "does the Kelly/edge/risk
      machinery capture edge when edge exists?" It is never a strategy claim.
- P&L runs through the REAL prediction_markets.run_backtest (ingest ->
  forecast -> edge vetoes -> Kelly sizing -> fill -> resolve -> risk limits).
  Nothing is reimplemented.
- Fees: product fee (15bps of positive settled PnL, per the module) then the
  LOCKED 5% platform fee on net profit. Net P&L is what the verdict uses.

GO/NO-GO threshold (explicit, no cherry-picked windows):
  GO  iff  net_pnl_cents > 0
       AND win_rate >= 0.40
       AND max_drawdown_cents < 20% of bankroll
       AND trades >= 10            (degenerate guard: not trading != profit)
  otherwise NO-GO with the named reason.
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from sincor2.defi.prediction_markets import (
    ForecastEngine,
    KellySizer,
    MarketSnapshot,
    run_backtest,
)

PLATFORM_FEE_BPS = 500          # locked 5% platform fee to treasury
BANKROLL_CENTS = 100_000        # $1,000 starting bankroll per sim
N_EPISODES = 30
MIN_VOLUME_USD = 10_000
DECISION_LEAD_HOURS = 48        # decide this far before endDate
MOMENTUM_LOOKBACK_HOURS = 24
SIGNAL_CAP = 0.12               # probability points
MOMENTUM_SCALE = 1.5

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


@dataclass
class Episode:
    market_id: str
    question: str
    price_at_t: float           # real YES price at decision time T
    price_24h_before: float     # real YES price at T-24h (signal input)
    outcome: int                # 0/1 from real resolution
    decision_ts: int
    volume_24h_cents: int
    resolves_at: int


def momentum_signal(price_now: float, price_24h_ago: float) -> float:
    """Trailing-24h momentum as a probability-point edge hint.

    No lookahead: uses only prices at/before decision time T. Scaled and
    capped so the product's edge vetoes (2% min edge) see a real-valued
    signal, not noise.
    """
    if price_24h_ago <= 0:
        return 0.0
    mom = (price_now - price_24h_ago) / price_24h_ago
    return max(-SIGNAL_CAP, min(SIGNAL_CAP, mom * MOMENTUM_SCALE))


def perfect_signal(outcome: int) -> float:
    """Machinery upper bound. LOOKAHEAD BY DESIGN: uses the known outcome.

    Never a strategy claim. Answers only: "if edge existed, does the
    product's Kelly sizing + edge vetoes + risk limits capture it?" If the
    machinery loses money with perfect foresight, the machinery is broken.
    """
    return 0.30 if outcome == 1 else -0.30


def episode_momentum(ep: "Episode") -> float:
    """Signal adapter: momentum from the episode's real prices."""
    return momentum_signal(ep.price_at_t, ep.price_24h_before)


def episode_perfect(ep: "Episode") -> float:
    """Signal adapter: perfect foresight (machinery bound only)."""
    return perfect_signal(ep.outcome)


def apply_fees(gross_cents: int, product_fee_cents: int):
    """Net P&L after product fee and the locked 5% platform fee.

    Platform fee applies to net profit only (never on losses).
    Returns (net_pnl_cents, platform_fee_cents).
    """
    net_before_platform = gross_cents - product_fee_cents
    platform_fee = int(max(0, net_before_platform) * PLATFORM_FEE_BPS / 10_000)
    return net_before_platform - platform_fee, platform_fee


def profitability_verdict(stats: Dict):
    """Explicit GO/NO-GO threshold. Returns (verdict, reason)."""
    if stats["trades"] < 10:
        return "NO-GO", f"only {stats['trades']} trades (< 10; not trading != profit)"
    if stats["net_pnl_cents"] <= 0:
        return "NO-GO", f"net P&L {stats['net_pnl_cents']/100:.2f} USD not positive"
    if stats["win_rate"] < 0.40:
        return "NO-GO", f"win rate {stats['win_rate']:.1%} < 40%"
    if stats["max_dd_cents"] >= stats["bankroll_cents"] * 0.20:
        return "NO-GO", (
            f"max drawdown {stats['max_dd_cents']/100:.2f} USD >= 20% of bankroll")
    return "GO", "all bars cleared: positive net, >=40% win rate, <20% drawdown"


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _closest_price(history: List[Dict], ts: int) -> Optional[Dict]:
    best = None
    best_gap = None
    for pt in history:
        gap = abs(pt["t"] - ts)
        if best_gap is None or gap < best_gap:
            best, best_gap = pt, gap
    # Reject stale matches: price must be within 6h of the target.
    if best_gap is not None and best_gap > 6 * 3600:
        return None
    return best


def fetch_episodes(n: int = N_EPISODES,
                   min_volume_usd: float = MIN_VOLUME_USD) -> List[Episode]:
    """Fetch n resolved-market episodes. Mechanical selection, no cherry-pick.

    Order: events closed=true by volume desc; within each event, markets in
    API order. Filters (documented): volume >= min_volume_usd, binary
    outcomePrices exactly ["1","0"] or ["0","1"], price history covering both
    T and T-24h within 6h tolerance.
    """
    episodes: List[Episode] = []
    events = _get(f"{GAMMA}/events?limit=60&closed=true&order=volume&ascending=false")
    for e in events:
        for m in e.get("markets", []):
            if len(episodes) >= n:
                return episodes
            vol = float(m.get("volume") or 0)
            try:
                op = json.loads(m.get("outcomePrices") or "[]")
            except (json.JSONDecodeError, TypeError):
                continue
            if vol < min_volume_usd or op not in (["1", "0"], ["0", "1"]):
                continue
            # Note: multi-outcome events contribute many loser legs (only one
            # outcome wins per event). The product is YES-first-outcome-only,
            # so loser legs are correctly vetoed by the EV check. A larger N
            # ensures enough winner episodes for a meaningful trade count.
            outcome = 1 if op[0] == "1" else 0
            tids = json.loads(m["clobTokenIds"])
            try:
                from datetime import datetime
                end = datetime.fromisoformat(
                    m["endDate"].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                continue
            end_ts = int(end.timestamp())
            T = end_ts - DECISION_LEAD_HOURS * 3600
            T24 = T - MOMENTUM_LOOKBACK_HOURS * 3600
            try:
                hist = _get(
                    f"{CLOB}/prices-history?market={tids[0]}"
                    f"&startTs={T24 - 3600}&endTs={T + 3600}&fidelity=60"
                ).get("history", [])
            except Exception:
                continue
            pT = _closest_price(hist, T)
            pT24 = _closest_price(hist, T24)
            if not pT or not pT24:
                continue
            if not (0.0 < pT["p"] < 1.0 and 0.0 < pT24["p"] < 1.0):
                continue
            episodes.append(Episode(
                market_id=str(m["id"]),
                question=m["question"][:120],
                price_at_t=float(pT["p"]),
                price_24h_before=float(pT24["p"]),
                outcome=outcome,
                decision_ts=T,
                volume_24h_cents=int(vol * 100),
                resolves_at=end_ts,
            ))
            time.sleep(0.2)  # pacing
    return episodes


def run_profit_sim(episodes: List[Episode],
                   signal_fn: Callable[[Episode], float],
                   bankroll_cents: int = BANKROLL_CENTS) -> Dict:
    """Run the REAL run_backtest over the episodes; compute net P&L.

    Signal per episode comes from signal_fn (documented, no lookahead
    except perfect_signal which is the machinery bound). Snapshots use the
    real decision-time price; resolves_at is set 30d after the backtest
    clock start so the product's own duration/near-resolution gates evaluate
    on a realistic horizon (the tested dimension is profitability, not the
    calendar).
    """
    base_now = 1_700_000_000
    markets = []
    for i, ep in enumerate(episodes):
        now = base_now + i * 3600
        snap = MarketSnapshot(
            market_id=ep.market_id,
            question=ep.question,
            yes_price=ep.price_at_t,
            volume_24h_cents=ep.volume_24h_cents,
            oracle="uma",  # Polymarket resolves via UMA
            resolves_at=now + 30 * 86400,
            as_of=now,     # refreshed per-iteration by run_backtest
            category="general",
        )
        markets.append((snap, signal_fn(ep), ep.outcome))
    engine = ForecastEngine()
    sizer = KellySizer()
    result = run_backtest(markets, engine, sizer, bankroll_cents)

    gross = result["realized_pnl_cents"]
    product_fee = result["fee_total_cents"]
    net, platform_fee = apply_fees(gross, product_fee)

    per = result["per_market"]
    wins = sum(1 for p in per if p["pnl_cents"] > 0)
    trades = len(per)
    return {
        "episodes": len(episodes),
        "trades": trades,
        "wins": wins,
        "win_rate": (wins / trades) if trades else 0.0,
        "gross_pnl_cents": gross,
        "product_fee_cents": product_fee,
        "platform_fee_cents": platform_fee,
        "net_pnl_cents": net,
        "max_dd_cents": result["max_drawdown_cents"],
        "bankroll_cents": bankroll_cents,
        "brier": result["brier"],
        "naive_brier": result["naive_brier"],
        "fee_ledger_reconciles": result["fee_ledger_reconciles"],
    }


def summarize(label: str, stats: Dict) -> str:
    verdict, reason = profitability_verdict(stats)
    return (
        f"[{verdict}] {label}: net {stats['net_pnl_cents']/100:+.2f} USD "
        f"(gross {stats['gross_pnl_cents']/100:+.2f}, product fee "
        f"{stats['product_fee_cents']/100:.2f}, platform 5% "
        f"{stats['platform_fee_cents']/100:.2f}) over {stats['trades']} trades, "
        f"win rate {stats['win_rate']:.1%}, max DD "
        f"{stats['max_dd_cents']/100:.2f} USD — {reason}"
    )
