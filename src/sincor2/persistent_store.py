"""Persistent store — SQLite backing for state that currently lives in RAM.

Today a Railway restart wipes A2A tasks, settlement quotes, treasury journals,
forecasts, and trade state, and multi-worker gunicorn makes them inconsistent.
This module is the single durable layer the rest of the platform migrates to.

Tables
------
kv                 generic key/value (feature flags, cursors)
a2a_tasks          A2A task records (replaces a2a_integration._tasks dict)
settlements        settlement records (replaces SettlementCoordinator dicts)
treasury_journal   treasury routing events
chain_events       unified on-chain event log (curve buys, hook fills, burns)
predictions        forecasting-engine predictions + resolved outcomes
trades             handled by bankroll.py (same DB file by default)

Usage
-----
    from sincor2.persistent_store import get_store
    store = get_store()
    store.upsert_task({"id": ..., "status": ...})
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.store")

_DEFAULT_DB = os.getenv("SINCOR_STORE_DB_PATH", os.getenv("POLYCLAW_DB_PATH", "/data/polyclaw.db"))


def _db_path() -> Path:
    path = Path(_DEFAULT_DB)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        probe = path.parent / ".write_probe"
        probe.touch()
        probe.unlink()
        return path
    except OSError:
        local = Path("sincor_store.db")
        logger.warning("/data not writable, using local %s", local)
        return local


class PersistentStore:
    """Thread-safe SQLite store. One instance per process (see get_store)."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _db_path()
        self._lock = threading.Lock()
        self._init_db()

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS a2a_tasks (
                    id TEXT PRIMARY KEY,
                    context_id TEXT,
                    skill_id TEXT,
                    caller_id TEXT,
                    state TEXT,
                    payload TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS settlements (
                    settlement_id TEXT PRIMARY KEY,
                    quote_id TEXT,
                    task_reference TEXT,
                    tx_hash TEXT,
                    token_symbol TEXT,
                    amount TEXT,
                    platform_fee TEXT,
                    payee_amount TEXT,
                    treasury_address TEXT,
                    status TEXT,
                    recorded_at TEXT
                );
                CREATE TABLE IF NOT EXISTS treasury_journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,
                    token_symbol TEXT,
                    amount TEXT,
                    treasury_address TEXT,
                    meta TEXT,
                    recorded_at TEXT
                );
                CREATE TABLE IF NOT EXISTS chain_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id INTEGER,
                    contract_address TEXT,
                    event_name TEXT,
                    tx_hash TEXT,
                    block_number INTEGER,
                    data TEXT,
                    recorded_at TEXT,
                    UNIQUE(tx_hash, event_name)
                );
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT,
                    token_id TEXT,
                    model_probability REAL,
                    market_price REAL,
                    confidence REAL,
                    outcome REAL,
                    brier REAL,
                    created_at TEXT,
                    resolved_at TEXT
                );
                """
            )

    # ------------------------------------------------------------------
    # KV
    # ------------------------------------------------------------------

    def kv_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kv(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, self._now()),
            )

    # ------------------------------------------------------------------
    # A2A tasks
    # ------------------------------------------------------------------

    def upsert_task(self, task: Dict[str, Any]) -> None:
        payload = json.dumps(task, default=str)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO a2a_tasks(id, context_id, skill_id, caller_id, state, "
                "payload, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET state=excluded.state, "
                "payload=excluded.payload, updated_at=excluded.updated_at",
                (task.get("id"), task.get("contextId") or task.get("context_id"),
                 task.get("skill_id") or (task.get("metadata") or {}).get("skill_id"),
                 task.get("caller_id") or (task.get("metadata") or {}).get("caller_id"),
                 (task.get("status") or {}).get("state") if isinstance(task.get("status"), dict)
                 else task.get("state"),
                 payload, task.get("created_at") or self._now(), self._now()),
            )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM a2a_tasks WHERE id=?", (task_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_tasks(self, state: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        query = "SELECT payload FROM a2a_tasks"
        params: tuple = ()
        if state:
            query += " WHERE state=?"
            params = (state,)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ------------------------------------------------------------------
    # Settlements + treasury journal
    # ------------------------------------------------------------------

    def record_settlement(self, s: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settlements(settlement_id, quote_id, "
                "task_reference, tx_hash, token_symbol, amount, platform_fee, "
                "payee_amount, treasury_address, status, recorded_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (s.get("settlement_id"), s.get("quote_id"), s.get("task_reference"),
                 s.get("tx_hash"), s.get("token_symbol"), s.get("amount"),
                 s.get("platform_fee"), s.get("payee_amount"),
                 s.get("treasury_address"), s.get("status"),
                 s.get("recorded_at") or self._now()),
            )

    def journal_event(self, event_type: str, token_symbol: str, amount: str,
                      treasury_address: str, meta: Optional[Dict[str, Any]] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO treasury_journal(event_type, token_symbol, amount, "
                "treasury_address, meta, recorded_at) VALUES(?,?,?,?,?,?)",
                (event_type, token_symbol, amount, treasury_address,
                 json.dumps(meta or {}, default=str), self._now()),
            )

    # ------------------------------------------------------------------
    # Chain events (unified watcher target)
    # ------------------------------------------------------------------

    def record_chain_event(self, chain_id: int, contract_address: str,
                           event_name: str, tx_hash: str,
                           block_number: Optional[int] = None,
                           data: Optional[Dict[str, Any]] = None) -> bool:
        """Idempotent insert — returns False if the event was already recorded."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO chain_events(chain_id, contract_address, "
                "event_name, tx_hash, block_number, data, recorded_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (chain_id, contract_address.lower(), event_name, tx_hash,
                 block_number, json.dumps(data or {}, default=str), self._now()),
            )
            return cur.rowcount > 0

    def chain_events_since(self, event_name: Optional[str] = None,
                           limit: int = 200) -> List[Dict[str, Any]]:
        query = "SELECT * FROM chain_events"
        params: tuple = ()
        if event_name:
            query += " WHERE event_name=?"
            params = (event_name,)
        query += " ORDER BY id DESC LIMIT ?"
        params += (limit,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Predictions (forecasting calibration)
    # ------------------------------------------------------------------

    def record_prediction(self, market_id: str, token_id: str,
                          model_probability: float, market_price: float,
                          confidence: float) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO predictions(market_id, token_id, model_probability, "
                "market_price, confidence, created_at) VALUES(?,?,?,?,?,?)",
                (market_id, token_id, model_probability, market_price,
                 confidence, self._now()),
            )
            return int(cur.lastrowid)

    def pending_predictions(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE outcome IS NULL "
                "ORDER BY id DESC LIMIT 500"
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_prediction(self, prediction_id: int, outcome_yes: float) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT model_probability FROM predictions WHERE id=?",
                (prediction_id,),
            ).fetchone()
            if not row:
                return
            brier = (row["model_probability"] - outcome_yes) ** 2
            conn.execute(
                "UPDATE predictions SET outcome=?, brier=?, resolved_at=? WHERE id=?",
                (outcome_yes, brier, self._now(), prediction_id),
            )

    def calibration_stats(self) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n, AVG(brier) AS mean_brier FROM predictions "
                "WHERE outcome IS NOT NULL"
            ).fetchone()
        return {"resolved": row["n"],
                "mean_brier": round(row["mean_brier"], 4) if row["mean_brier"] else None}


_instance: Optional[PersistentStore] = None
_instance_lock = threading.Lock()


def get_store() -> PersistentStore:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = PersistentStore()
    return _instance
