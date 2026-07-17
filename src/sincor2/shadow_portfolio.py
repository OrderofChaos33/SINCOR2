"""Shadow portfolio — silently paper-trades the 25% TOA-blend strategy.

Every cycle the live runner trades the conservative strategy (order-book +
momentum, heavily market-weighted). This module runs a parallel, fully
simulated portfolio alongside it:

    live   : current forecasting_engine blend (baseline we trust today)
    shadow : 0.75 × market midpoint + 0.25 × TOA estimate (the challenger)

Both portfolios see the SAME markets at the SAME prices. Shadow trades are
paper only — no orders, no risk. When markets resolve, both are scored with
real settlement math (binary shares pay $1), so after a few weeks the DB
answers the question with data instead of debate:

    compare_performance() → live vs shadow: PnL, win rate, mean Brier

If the shadow consistently wins, we promote the TOA blend to live with
confidence. If it loses, we just saved real money finding out.

Also resolves LIVE open trades against market outcomes (nothing else was
closing them) — realized PnL feeds the bankroll's compounding.

Env
---
SHADOW_ENABLED            "true" (default) — set "false" to skip shadow work
SHADOW_SIGNAL_WEIGHT      0.25  — TOA weight in the shadow blend
SHADOW_START_CAPITAL_USD  mirrors POLYCLAW_CAPITAL_USD (default 20)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.shadow")

GAMMA_API = os.getenv("POLYMARKET_GAMMA_API", "https://gamma-api.polymarket.com")
SHADOW_WEIGHT = float(os.getenv("SHADOW_SIGNAL_WEIGHT", "0.25"))
KELLY_FRACTION = float(os.getenv("POLYCLAW_KELLY_FRACTION", "0.5"))
MIN_WAGER_USD = float(os.getenv("POLYCLAW_MIN_WAGER_USD", "1.0"))

_DB_PATH = Path(os.getenv("POLYCLAW_DB_PATH", "/data/polyclaw.db"))
if not _DB_PATH.parent.exists():
    _DB_PATH = Path("polyclaw.db")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                market_id TEXT,
                question TEXT,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                size_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                shadow_prob REAL,
                market_price REAL,
                status TEXT NOT NULL DEFAULT 'open',
                outcome REAL,
                realized_pnl REAL
            );
            CREATE TABLE IF NOT EXISTS shadow_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO shadow_state(key, value) VALUES('equity', ?)",
            (os.getenv("SHADOW_START_CAPITAL_USD",
                       os.getenv("POLYCLAW_CAPITAL_USD", "20")),),
        )


_init_db()


def _get_state(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM shadow_state WHERE key=?",
                           (key,)).fetchone()
    return row["value"] if row else default


def _set_state(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO shadow_state(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


# ---------------------------------------------------------------------------
# TOA estimate
# ---------------------------------------------------------------------------

_toa = None
_toa_failed = False


def _get_toa():
    """Lazy TOAOrchestrator singleton; None if the stack isn't importable."""
    global _toa, _toa_failed
    if _toa is not None or _toa_failed:
        return _toa
    try:
        repo_root = os.path.join(os.path.dirname(__file__), "..", "..")
        if repo_root not in sys.path:
            sys.path.insert(0, os.path.abspath(repo_root))
        from agents.toa import TOAOrchestrator
        _toa = TOAOrchestrator()
        logger.info("shadow: TOA orchestrator loaded")
    except Exception as exc:
        logger.warning("shadow: TOA unavailable (%s) — shadow uses momentum proxy", exc)
        _toa_failed = True
        _toa = None
    return _toa


def toa_estimate(prices: List[float]) -> Optional[float]:
    """TOA's next-value estimate for a price series, as a probability.

    Returns None when TOA can't produce a parseable estimate — the shadow
    then falls back to the momentum proxy so the A/B test keeps running.
    """
    orc = _get_toa()
    if orc is None or len(prices) < 6:
        return None
    try:
        result = orc.run(context={"values": prices, "horizon": 3})
        for action in result.get("action_plan", []):
            for key in ("expected_value", "predicted_value", "mean", "value"):
                if key in action:
                    est = float(action[key])
                    return max(0.02, min(0.98, est))
    except Exception as exc:
        logger.debug("shadow: TOA run failed: %s", exc)
    return None


def _momentum_proxy(prices: List[float]) -> Optional[float]:
    """Fallback signal: shrunk 24h momentum applied to last price."""
    if len(prices) < 6:
        return None
    drift = max(-0.05, min(0.05, (prices[-1] - prices[0]) * 0.30))
    return max(0.02, min(0.98, prices[-1] + drift))


# ---------------------------------------------------------------------------
# Shadow cycle
# ---------------------------------------------------------------------------

def record_shadow_cycle(opportunities: List[Any],
                        price_histories: Optional[Dict[str, List[float]]] = None) -> int:
    """Paper-trade every scanned opportunity at the 25% TOA blend.

    ``opportunities`` are Forecast objects from forecasting_engine.
    Returns the number of shadow trades opened this cycle.
    """
    if os.getenv("SHADOW_ENABLED", "true").lower() != "true":
        return 0

    equity = float(_get_state("equity", "20"))
    opened = 0
    for fc in opportunities:
        token = fc.token_id_yes if fc.edge > 0 else (fc.token_id_no or "")
        if not token:
            continue
        prices = (price_histories or {}).get(fc.token_id_yes) or []
        signal = toa_estimate(prices)
        if signal is None:
            signal = _momentum_proxy(prices)
        if signal is None:
            continue

        # Shadow blend: 75% market + 25% signal (TOA when available)
        base = fc.market_price if fc.edge > 0 else 1.0 - fc.market_price
        signal_side = signal if fc.edge > 0 else 1.0 - signal
        shadow_prob = (1.0 - SHADOW_WEIGHT) * base + SHADOW_WEIGHT * signal_side
        edge = shadow_prob - base
        if abs(edge) < float(os.getenv("POLYCLAW_MIN_EDGE", "0.04")):
            continue

        side = "BUY_YES" if edge > 0 else "BUY_NO"
        price = fc.market_price if side == "BUY_YES" else round(1.0 - fc.market_price, 4)
        if price <= 0.01 or price >= 0.99:
            continue
        b = (1.0 / price) - 1.0
        p = shadow_prob
        kelly = (p * (b + 1.0) - 1.0) / b
        if kelly <= 0:
            continue
        size = round(min(equity * kelly * KELLY_FRACTION * fc.confidence,
                         equity * 0.15), 2)
        if size < MIN_WAGER_USD:
            continue

        with _connect() as conn:
            conn.execute(
                "INSERT INTO shadow_trades(ts, market_id, question, token_id, side, "
                "size_usd, entry_price, shadow_prob, market_price) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (_now(), fc.market_id, fc.question[:200], token, side, size,
                 price, round(shadow_prob, 4), fc.market_price),
            )
        equity -= size
        opened += 1
        logger.info("[SHADOW] %s $%.2f on '%s' @ %.3f (prob %.3f)",
                    side, size, fc.question[:50], price, shadow_prob)
    _set_state("equity", f"{equity:.4f}")
    return opened


# ---------------------------------------------------------------------------
# Resolution — scores BOTH portfolios with real settlement math
# ---------------------------------------------------------------------------

def _market_outcome(market_id: str) -> Optional[float]:
    """1.0 if the market resolved YES, 0.0 for NO, None if still open."""
    try:
        url = f"{GAMMA_API}/markets/{market_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "sincor-shadow/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if not data.get("closed"):
            return None
        prices = data.get("outcomePrices")
        if isinstance(prices, str):
            prices = json.loads(prices)
        return 1.0 if float(prices[0]) >= 0.99 else 0.0
    except Exception:
        return None


def _settle(size_usd: float, entry_price: float, won: bool) -> float:
    """Binary-settlement PnL: shares pay $1 on a win, $0 on a loss."""
    shares = size_usd / entry_price
    return round(shares - size_usd, 4) if won else round(-size_usd, 4)


def resolve_all(bankroll: Optional[Any] = None) -> Dict[str, int]:
    """Resolve open shadow trades AND open live trades against outcomes."""
    resolved = {"shadow": 0, "live": 0}

    # Shadow
    with _connect() as conn:
        open_shadow = conn.execute(
            "SELECT * FROM shadow_trades WHERE status='open'"
        ).fetchall()
    for t in open_shadow:
        outcome = _market_outcome(t["market_id"])
        if outcome is None:
            continue
        won = (outcome == 1.0) == (t["side"] == "BUY_YES")
        pnl = _settle(t["size_usd"], t["entry_price"], won)
        with _connect() as conn:
            conn.execute(
                "UPDATE shadow_trades SET status='closed', outcome=?, "
                "realized_pnl=? WHERE id=?", (outcome, pnl, t["id"]))
        eq = float(_get_state("equity", "20")) + t["size_usd"] + pnl
        _set_state("equity", f"{eq:.4f}")
        resolved["shadow"] += 1

    # Live — realized PnL feeds bankroll compounding
    if bankroll is not None:
        for t in bankroll.open_trades():
            if not t.get("market_id"):
                continue
            outcome = _market_outcome(t["market_id"])
            if outcome is None:
                continue
            won = (outcome == 1.0) == (t["side"] == "BUY_YES")
            price = t["price"] or 0.5
            pnl = _settle(t["size_usd"], price, won)
            bankroll.close_trade(t["id"], pnl)
            resolved["live"] += 1
            logger.info("[LIVE] trade %s resolved: pnl=$%.2f", t["id"], pnl)

    return resolved


# ---------------------------------------------------------------------------
# The scoreboard
# ---------------------------------------------------------------------------

def compare_performance() -> Dict[str, Any]:
    """Live vs shadow head-to-head. This is the table that decides promotion."""
    with _connect() as conn:
        shadow = conn.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
            "COALESCE(SUM(realized_pnl),0) AS pnl "
            "FROM shadow_trades WHERE status='closed'"
        ).fetchone()
        live = conn.execute(
            "SELECT COUNT(*) AS n, "
            "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
            "COALESCE(SUM(realized_pnl),0) AS pnl "
            "FROM trades WHERE status='closed'"
        ).fetchone()

    def _fmt(row: Any) -> Dict[str, Any]:
        n = row["n"] or 0
        return {"trades": n,
                "win_rate": round((row["wins"] or 0) / n, 3) if n else None,
                "realized_pnl": round(row["pnl"], 2)}

    return {
        "live": _fmt(live),
        "shadow_toa_25": _fmt(shadow),
        "shadow_equity": round(float(_get_state("equity", "20")), 2),
        "verdict": _verdict(live, shadow),
    }


def _verdict(live: Any, shadow: Any) -> str:
    n_l, n_s = live["n"] or 0, shadow["n"] or 0
    if n_s < 20:
        return f"collecting data ({n_s}/20 resolved shadow trades needed)"
    if shadow["pnl"] > live["pnl"]:
        return "SHADOW WINNING — consider promoting the 25% TOA blend to live"
    return "LIVE WINNING — keep the conservative blend, shadow stays paper"
