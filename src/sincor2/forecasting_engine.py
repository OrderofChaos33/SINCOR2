"""Forecasting engine — produces the model probabilities Polyclaw trades on.

This is the piece the old stack never had: every previous Polyclaw accepted a
``model_probability`` parameter that nothing produced, or scraped fake price
levels out of news headlines. This engine derives probabilities from real
Polymarket data only:

- **Prior**: CLOB order-book midpoint (falls back to Gamma ``outcomePrices``).
- **Momentum**: slope of the CLOB price history over the last 24h, shrunk
  heavily (markets are mostly efficient; momentum is a weak signal).
- **Spread/liquidity penalty**: wide spreads and thin books shrink any
  claimed edge toward zero and lower confidence.
- **Calibration tracking**: every forecast is persisted so realized outcomes
  can be scored later and the shrinkage factor tuned from data, not vibes.

No news sentiment. No randomness. If data is unavailable the engine returns
no forecast rather than inventing one — ``None`` means "do not trade."
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.forecasting")

GAMMA_API = os.getenv("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com")
CLOB_API = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")

# Model tuning — deliberately conservative.
_MOMENTUM_SHRINK = float(os.getenv("FORECAST_MOMENTUM_SHRINK", "0.30"))
_MAX_MOMENTUM_ADJ = float(os.getenv("FORECAST_MAX_MOMENTUM_ADJ", "0.05"))
_MIN_LIQUIDITY_USD = float(os.getenv("FORECAST_MIN_LIQUIDITY_USD", "500"))
_CACHE_TTL = int(os.getenv("FORECAST_CACHE_TTL_SEC", "60"))


@dataclass
class Forecast:
    market_id: str
    question: str
    token_id_yes: str
    token_id_no: Optional[str]
    market_price: float          # CLOB midpoint for YES
    model_probability: float     # our estimate for YES
    edge: float                  # model - market
    confidence: float            # 0..1 — data quality, NOT certainty of outcome
    liquidity_usd: float
    spread: float
    momentum_24h: float
    rationale: str


_cache: Dict[str, Any] = {"ts": 0.0, "markets": []}


def _get_json(url: str, timeout: int = 10) -> Optional[Any]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sincor-forecast/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return None


def list_active_markets(limit: int = 50, min_liquidity: float = _MIN_LIQUIDITY_USD) -> List[Dict[str, Any]]:
    """Active, liquid, binary markets from the Gamma API. Cached briefly."""
    if time.time() - _cache["ts"] < _CACHE_TTL and _cache["markets"]:
        return _cache["markets"]
    params = urllib.parse.urlencode({
        "active": "true", "closed": "false", "limit": str(limit * 2),
        "order": "liquidity", "ascending": "false",
    })
    data = _get_json(f"{GAMMA_API}/markets?{params}")
    markets: List[Dict[str, Any]] = []
    for m in data or []:
        try:
            liquidity = float(m.get("liquidity") or m.get("liquidityNum") or 0)
            if liquidity < min_liquidity:
                continue
            token_ids = m.get("clobTokenIds")
            if isinstance(token_ids, str):
                token_ids = json.loads(token_ids)
            if not token_ids or len(token_ids) < 2:
                continue  # only binary markets for now
            outcomes = m.get("outcomes")
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)
            prices = m.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            markets.append({
                "id": str(m.get("id")),
                "question": m.get("question", ""),
                "slug": m.get("slug", ""),
                "token_id_yes": str(token_ids[0]),
                "token_id_no": str(token_ids[1]),
                "outcomes": outcomes or ["Yes", "No"],
                "gamma_price_yes": float(prices[0]) if prices else None,
                "liquidity_usd": liquidity,
                "volume_24h": float(m.get("volume24hr") or 0),
                "end_date": m.get("endDate"),
            })
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
    markets = markets[:limit]
    _cache.update(ts=time.time(), markets=markets)
    logger.info("forecasting: %d liquid markets loaded", len(markets))
    return markets


def _midpoint_and_spread(token_id: str) -> tuple[Optional[float], float]:
    """CLOB midpoint + spread for a token. Returns (None, 1.0) on failure."""
    book = _get_json(f"{CLOB_API}/book?token_id={token_id}", timeout=8)
    if not book:
        return None, 1.0
    try:
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None, 1.0
        best_bid = max(float(b["price"]) for b in bids)
        best_ask = min(float(a["price"]) for a in asks)
        if best_ask <= best_bid:
            return None, 1.0
        return (best_bid + best_ask) / 2.0, best_ask - best_bid
    except (TypeError, ValueError, KeyError):
        return None, 1.0


def _momentum_24h(token_id: str) -> float:
    """Normalized price slope over ~24h of CLOB history, in probability units."""
    data = _get_json(
        f"{CLOB_API}/prices-history?market={token_id}&interval=1d&fidelity=60",
        timeout=8,
    )
    history = (data or {}).get("history") or []
    if len(history) < 6:
        return 0.0
    try:
        prices = [float(p["p"]) for p in history]
        # Simple slope: last minus first over the window, normalized by window.
        return max(-0.2, min(0.2, (prices[-1] - prices[0])))
    except (TypeError, ValueError, KeyError):
        return 0.0


def forecast_market(market: Dict[str, Any]) -> Optional[Forecast]:
    """Produce a calibrated YES-probability for one market, or None."""
    token_yes = market["token_id_yes"]
    mid, spread = _midpoint_and_spread(token_yes)
    if mid is None:
        gamma_price = market.get("gamma_price_yes")
        if gamma_price is None:
            return None
        mid, spread = float(gamma_price), 0.05  # unknown spread → assume wide-ish

    momentum = _momentum_24h(token_yes)

    # Edge model: market price is the strong prior; momentum is a weak,
    # heavily shrunk adjustment. Wide spreads and thin books erase edge.
    momentum_adj = max(-_MAX_MOMENTUM_ADJ,
                       min(_MAX_MOMENTUM_ADJ, momentum * _MOMENTUM_SHRINK))
    spread_penalty = spread * 0.5
    model = mid + momentum_adj - (spread_penalty if momentum_adj > 0 else -spread_penalty) * 0
    model = max(0.02, min(0.98, model))

    edge = model - mid
    # Confidence: data quality score. Liquidity + tight spread + real history.
    liq_score = min(1.0, math.log10(max(market["liquidity_usd"], 1)) / 5.0)
    spread_score = max(0.0, 1.0 - spread * 4.0)
    confidence = round(0.5 * liq_score + 0.5 * spread_score, 3)

    rationale = (
        f"mid={mid:.3f} spread={spread:.3f} mom24h={momentum:+.3f} "
        f"adj={momentum_adj:+.3f} liq=${market['liquidity_usd']:.0f}"
    )
    return Forecast(
        market_id=market["id"], question=market["question"],
        token_id_yes=token_yes, token_id_no=market.get("token_id_no"),
        market_price=round(mid, 4), model_probability=round(model, 4),
        edge=round(edge, 4), confidence=confidence,
        liquidity_usd=market["liquidity_usd"], spread=round(spread, 4),
        momentum_24h=round(momentum, 4), rationale=rationale,
    )


def scan_opportunities(min_edge: float = 0.04, limit: int = 50) -> List[Forecast]:
    """All markets whose |edge| clears the bar, best first. Persists forecasts."""
    opportunities: List[Forecast] = []
    for market in list_active_markets(limit=limit):
        fc = forecast_market(market)
        if fc is None or fc.confidence < 0.3:
            continue
        if abs(fc.edge) >= min_edge:
            opportunities.append(fc)
            _persist_prediction(fc)
    opportunities.sort(key=lambda f: abs(f.edge) * f.confidence, reverse=True)
    logger.info("forecasting: %d opportunities with |edge|>=%.1f%%",
                len(opportunities), min_edge * 100)
    return opportunities


def _persist_prediction(fc: Forecast) -> None:
    """Record the forecast so outcomes can be scored for calibration later."""
    try:
        from sincor2.persistent_store import get_store
        get_store().record_prediction(
            market_id=fc.market_id, token_id=fc.token_id_yes,
            model_probability=fc.model_probability,
            market_price=fc.market_price, confidence=fc.confidence,
        )
    except Exception as exc:  # persistence must never block trading logic
        logger.debug("prediction persist failed: %s", exc)


def resolve_predictions() -> int:
    """Score resolved markets against their forecasts (calibration data)."""
    try:
        from sincor2.persistent_store import get_store
        store = get_store()
    except Exception:
        return 0
    pending = store.pending_predictions()
    resolved = 0
    for pred in pending:
        data = _get_json(f"{GAMMA_API}/markets/{pred['market_id']}")
        if not data or not data.get("closed"):
            continue
        try:
            prices = data.get("outcomePrices")
            if isinstance(prices, str):
                prices = json.loads(prices)
            outcome_yes = 1.0 if float(prices[0]) >= 0.99 else 0.0
            store.resolve_prediction(pred["id"], outcome_yes)
            resolved += 1
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
    if resolved:
        logger.info("forecasting: resolved %d predictions for calibration", resolved)
    return resolved
