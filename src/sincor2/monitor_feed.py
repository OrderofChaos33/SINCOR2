"""Polyclaw consumer for the Item #4 price/state monitors (read-only).

The monitors service (~/workspace/price-monitors) polls Polymarket's
public Gamma/CLOB endpoints and appends ``market_state`` observations
to a local SQLite store. This module is Polyclaw's read-only window
into that store: it mirrors the monitors' own ``price_monitors.feed``
contract (freshest row + explicit ``age_seconds``) without importing
that package — the store file is the interface.

Monitor observation semantics (verified against the live store):

- ``key`` is ``polymarket:<slug>``, ``kind`` is ``market_state``.
- ``value`` is the price of the market's FIRST outcome token (the YES
  token on Yes/No markets): Gamma rows carry the Gamma outcome price,
  CLOB rows the CLOB midpoint for that same token.
- ``meta`` carries ``slug``, ``condition_id`` and that first token's
  ``token_id``.

Join keys: neither ``Forecast`` nor ``WhaleSignal`` carries a slug or
a condition id — both carry token ids — so the trading path joins on
``token_id`` (:meth:`MonitorFeed.price_for_token`). The slug lookup
(:meth:`MonitorFeed.market_state_for_slug`) is the monitors' native
key and is kept for completeness/debugging.

Safety contract — this consumer sits next to a trading path, so it
must NEVER raise and NEVER write:

- The DB is opened ``mode=ro`` with a short busy timeout, one
  short-lived connection per lookup (no long-lived handle to leak,
  go stale, or hold a lock against the monitors' writer).
- Missing DB, missing key, locked DB, corrupt row, non-SQLite file —
  every failure mode returns ``None`` ("no monitor data"), and the
  caller treats that exactly like the monitors not existing.

Freshness: a row is ``fresh`` when its age is at most
``POLYCLAW_MONITOR_MAX_AGE_SEC`` (default 300s). Staleness is reported,
never hidden — the caller decides what to do with an old row.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_MONITOR_DB = "~/workspace/price-monitors/state/price_monitors.db"
_KEY_PREFIX = "polymarket:"
_CONNECT_TIMEOUT_SEC = 0.25


def _max_age_sec() -> float:
    try:
        return float(os.getenv("POLYCLAW_MONITOR_MAX_AGE_SEC", "300"))
    except ValueError:
        return 300.0


def default_db_path() -> Path:
    raw = os.getenv("POLYCLAW_MONITOR_DB", "").strip()
    return Path(raw).expanduser() if raw else Path(DEFAULT_MONITOR_DB).expanduser()


class MonitorFeed:
    """Read-only lookups over the monitors' SQLite store. Never raises."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = (
            Path(db_path).expanduser() if db_path is not None else default_db_path()
        )

    # -- internals ---------------------------------------------------------

    def _query_one(self, sql: str, params: tuple) -> Optional[sqlite3.Row]:
        """Run one read query on a short-lived ro connection.

        Returns the row, or None on ANY failure (missing file, locked
        DB, malformed schema/JSON, ...). Callers never see an exception.
        """
        try:
            if not self.db_path.exists():
                return None
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_SEC)
            try:
                conn.row_factory = sqlite3.Row
                return conn.execute(sql, params).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("monitor feed query failed (%s): %s", self.db_path, exc)
            return None

    @staticmethod
    def _shape(row: sqlite3.Row, now: float) -> Optional[Dict[str, Any]]:
        """Normalise one observation row; None if the row is corrupt."""
        try:
            value = float(row["value"])
            ts = float(row["ts"])
        except (TypeError, ValueError):
            return None
        try:
            meta = json.loads(row["meta"]) if row["meta"] else {}
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        key = str(row["key"])
        age = round(now - ts, 3)
        return {
            "key": key,
            "slug": key[len(_KEY_PREFIX):] if key.startswith(_KEY_PREFIX) else key,
            "source": row["source"],
            "kind": row["kind"],
            "value": value,
            "ts": ts,
            "age_seconds": age,
            "fresh": age <= _max_age_sec(),
            "meta": meta,
        }

    # -- lookups -----------------------------------------------------------

    def market_state_for_slug(
        self, slug: str, now: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Freshest market_state observation for ``polymarket:<slug>``."""
        now = time.time() if now is None else now
        row = self._query_one(
            "SELECT * FROM observations WHERE key = ? AND kind = 'market_state'"
            " ORDER BY ts DESC, id DESC LIMIT 1",
            (f"{_KEY_PREFIX}{slug}",),
        )
        return self._shape(row, now) if row is not None else None

    def price_for_token(
        self, token_id: str, now: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """Freshest market_state observation whose meta token_id matches.

        The monitor value is the price of the market's first-outcome
        token, and meta.token_id IS that token — so a match means the
        returned value is a price for exactly this token. No match
        (market not watched, or the traded token is the second outcome)
        returns None: the monitor simply has no data for that token.
        """
        now = time.time() if now is None else now
        row = self._query_one(
            "SELECT * FROM observations WHERE kind = 'market_state'"
            " AND json_extract(meta, '$.token_id') = ?"
            " ORDER BY ts DESC, id DESC LIMIT 1",
            (str(token_id),),
        )
        return self._shape(row, now) if row is not None else None


def get_monitor_feed() -> MonitorFeed:
    """MonitorFeed at the env-configured (or default) store path."""
    return MonitorFeed()
