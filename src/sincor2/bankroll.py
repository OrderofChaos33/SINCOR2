"""Bankroll and risk accounting for Polyclaw trading.

Everything is PROPORTIONAL to current equity — there is no fixed dollar
ceiling. The account starts at ``POLYCLAW_CAPITAL_USD`` (default $20) and
every limit scales as realized profit compounds:

    equity = starting capital + realized PnL   (open positions at cost)

- Position size:   up to POLYCLAW_MAX_POSITION_PCT (15%) of equity
                   → $20 bankroll ≈ $0.30–$3 wagers, $3,000 ≈ $30–$300
- Total exposure:  up to TRADING_MAX_LEVERAGE (2x) of equity
- Daily loss stop: POLYCLAW_DAILY_LOSS_PCT (25%) of equity → kill switch
- Drawdown stop:   TRADING_MAX_DRAWDOWN (15%) off peak equity → kill switch

So the system is always growing: bigger equity → bigger wagers → bigger
absolute limits, with the same relative discipline at every level.

Kill switch persists in the DB; ``execution_adapter`` also mirrors it to a
halt file so a crashed process cannot keep trading.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.bankroll")

_DEFAULT_DB = os.getenv("POLYCLAW_DB_PATH", "/data/polyclaw.db")


def _db_path() -> Path:
    path = Path(_DEFAULT_DB)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".write_probe"
        probe.touch()
        probe.unlink()
        return path
    except OSError:
        local = Path("polyclaw.db")
        logger.warning("/data not writable, using local %s", local)
        return local


class Bankroll:
    """Thread-safe SQLite-backed bankroll and risk gate. Equity-proportional."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _db_path()
        self._lock = threading.Lock()
        # Starting bankroll — a floor, not a cap. Equity grows from here.
        self.start_capital = float(os.getenv("POLYCLAW_CAPITAL_USD", "20"))
        # Proportional limits (fractions of current equity)
        self.max_position_pct = float(os.getenv("POLYCLAW_MAX_POSITION_PCT", "0.15"))
        self.daily_loss_pct = float(os.getenv("POLYCLAW_DAILY_LOSS_PCT", "0.25"))
        self.max_drawdown = float(os.getenv("TRADING_MAX_DRAWDOWN", "0.15"))
        self.max_leverage = float(os.getenv("TRADING_MAX_LEVERAGE", "2.0"))
        self._init_db()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    market_id TEXT,
                    token_id TEXT NOT NULL,
                    side TEXT NOT NULL,
                    size_usd REAL NOT NULL,
                    price REAL,
                    order_id TEXT,
                    simulated INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'open',
                    realized_pnl REAL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS risk_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    equity REAL NOT NULL,
                    exposure REAL NOT NULL
                );
                """
            )
            conn.execute(
                "INSERT OR IGNORE INTO risk_state(key, value) VALUES('kill_switch', '')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO risk_state(key, value) VALUES('peak_equity', ?)",
                (str(self.start_capital),),
            )

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @staticmethod
    def _today() -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def _get_state(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM risk_state WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def _set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO risk_state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ------------------------------------------------------------------
    # Kill switch
    # ------------------------------------------------------------------

    def kill_switch_active(self) -> bool:
        return bool(self._get_state("kill_switch"))

    def trip_kill_switch(self, reason: str) -> None:
        self._set_state("kill_switch", f"{self._now()} {reason}")

    def clear_kill_switch(self) -> None:
        self._set_state("kill_switch", "")

    # ------------------------------------------------------------------
    # Equity / exposure / PnL
    # ------------------------------------------------------------------

    def realized_pnl_total(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) AS pnl FROM trades WHERE status='closed'"
            ).fetchone()
        return float(row["pnl"])

    def realized_pnl_today(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) AS pnl FROM trades "
                "WHERE status='closed' AND ts LIKE ?",
                (self._today() + "%",),
            ).fetchone()
        return float(row["pnl"])

    def open_exposure(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(size_usd),0) AS exp FROM trades WHERE status='open'"
            ).fetchone()
        return float(row["exp"])

    def equity(self) -> float:
        """Starting capital + realized PnL. Grows as we win."""
        return self.start_capital + self.realized_pnl_total()

    # -- Dynamic (equity-proportional) limits ---------------------------

    def max_position_size(self) -> float:
        """Max notional for ONE position right now (15% of equity default)."""
        return self.equity() * self.max_position_pct

    def daily_loss_limit_usd(self) -> float:
        """Daily realized-loss stop in USD (25% of equity default)."""
        return max(1.0, self.equity() * self.daily_loss_pct)

    def max_exposure(self) -> float:
        """Total open notional ceiling (leverage × equity)."""
        return self.equity() * self.max_leverage

    def available_capital(self) -> float:
        return max(0.0, self.max_exposure() - self.open_exposure())

    def snapshot(self) -> Dict[str, Any]:
        eq, exp = self.equity(), self.open_exposure()
        peak = max(float(self._get_state("peak_equity", str(eq)) or eq), eq)
        self._set_state("peak_equity", str(peak))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO equity_snapshots(ts, equity, exposure) VALUES(?,?,?)",
                (self._now(), eq, exp),
            )
        return {"equity": round(eq, 2), "exposure": round(exp, 2),
                "available": round(self.available_capital(), 2),
                "max_position": round(self.max_position_size(), 2),
                "daily_loss_limit": round(self.daily_loss_limit_usd(), 2),
                "peak_equity": round(peak, 2),
                "realized_today": round(self.realized_pnl_today(), 2),
                "kill_switch": self.kill_switch_active()}

    # ------------------------------------------------------------------
    # Risk gates
    # ------------------------------------------------------------------

    def can_open(self, size_usd: float) -> bool:
        """True only if the position fits every (equity-scaled) risk limit."""
        with self._lock:
            if self.kill_switch_active():
                return False
            if size_usd <= 0 or size_usd > self.max_position_size():
                return False
            if size_usd > self.available_capital():
                return False
            # Daily loss limit (proportional)
            if self.realized_pnl_today() <= -abs(self.daily_loss_limit_usd()):
                self.trip_kill_switch(
                    f"daily loss limit reached "
                    f"(-${self.daily_loss_limit_usd():.2f} of ${self.equity():.2f} equity)"
                )
                return False
            # Drawdown limit vs peak equity
            peak = float(self._get_state("peak_equity", str(self.start_capital)) or self.start_capital)
            if peak > 0 and (peak - self.equity()) / peak >= self.max_drawdown:
                self.trip_kill_switch(
                    f"max drawdown {self.max_drawdown:.0%} breached (peak {peak:.2f})"
                )
                return False
            return True

    def reserve_exposure(self, size_usd: float) -> None:
        """Record open exposure for a simulated/dry-run order (no order id)."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO trades(ts, token_id, side, size_usd, simulated, status) "
                "VALUES(?,?,?,?,1,'open')",
                (self._now(), "dry-run", "BUY", size_usd),
            )

    # ------------------------------------------------------------------
    # Trade ledger
    # ------------------------------------------------------------------

    def record_trade(self, token_id: str, side: str, size_usd: float,
                     price: Optional[float], order_id: Optional[str],
                     simulated: bool, market_id: Optional[str] = None) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO trades(ts, market_id, token_id, side, size_usd, price, "
                "order_id, simulated, status) VALUES(?,?,?,?,?,?,?,?,'open')",
                (self._now(), market_id, token_id, side, size_usd, price,
                 order_id, 1 if simulated else 0),
            )
            return int(cur.lastrowid)

    def close_trade(self, trade_id: int, realized_pnl: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE trades SET status='closed', realized_pnl=? WHERE id=?",
                (realized_pnl, trade_id),
            )
        # Immediately check whether this loss trips a limit.
        self.can_open(0.01)

    def open_trades(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status='open' ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]


_instance: Optional[Bankroll] = None
_instance_lock = threading.Lock()


def get_bankroll() -> Bankroll:
    """Process-wide singleton."""
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = Bankroll()
    return _instance
