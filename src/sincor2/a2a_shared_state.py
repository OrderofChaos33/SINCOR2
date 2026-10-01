"""Durable shared state for the A2A fabric (P3 wave 15, backlog item 21 / G2.8).

Problem: rate-limit counters, idempotency keys, quota usage, and heartbeat
state lived in per-process memory. Under multi-worker gunicorn two workers
disagree about the same client; on restart everything resets to zero.

This module provides ONE backend interface with three drivers:

* ``MemorySharedState`` — pure in-memory. Used when no store is configured
  in a dev/test environment, so existing suites keep full isolation and
  dev behavior is unchanged unless the operator opts in.
* ``SqliteSharedState`` — SQLite under ``SINCOR_DATA_DIR``
  (``a2a_fabric_state.db``). Durable across restarts and shared between
  processes on one host. This is the AUTOMATIC fallback when Redis is not
  configured in a production-like environment (mirrors
  ``a2a_task_store.get_task_store()``: ``A2A_TASK_STORE`` unset ->
  sqlite in prod, memory otherwise).
* ``RedisSharedState`` — cross-host, via ``REDIS_URL``/``REDIS_PRIVATE_URL``
  (same variables ``a2a_task_store.RedisTaskStore`` honors). Selected with
  ``A2A_STATE_STORE=redis``.

Selection: ``A2A_STATE_STORE=memory|sqlite|redis``. Unset -> ``sqlite`` in
production, ``memory`` in dev/test (same rule as ``A2A_TASK_STORE``).
``A2A_STATE_SQLITE_PATH`` overrides the SQLite file (tests).

Fail-closed rule (the point of this wave): the driver is resolved ONCE.
An explicitly configured driver that cannot be reached raises
``StateStoreUnavailable`` at resolution time — the app must not boot into
a silently-degraded limiter. Any operational failure AFTER resolution
(Redis down mid-request, disk full, lock contention exhausted) also
raises ``StateStoreUnavailable``, and callers MUST deny the request
rather than silently resetting counters. There is deliberately NO
mid-request fallback: falling back would zero every bucket and hand
attackers unlimited quota.

Key namespacing / tenant isolation: every key is built by
``SharedState.namespaced(*parts)`` as
``<prefix>:fabric:<quoted-part>:...`` where each part is URL-quoted, so
the mapping from logical key -> storage key is INJECTIVE. Two distinct
tenant inputs can never alias to the same storage key, and a caller
cannot smuggle ``:`` separators to hop into another domain's namespace.
Keys longer than 512 chars after quoting are rejected (fail closed)
rather than truncated into collisions.

Redis failover note: the window claim is atomic (WATCH/MULTI with
bounded retries). A failover mid-request surfaces as a connection
error -> ``StateStoreUnavailable`` -> deny (safe direction). A client
that retries a denied request consumes one more hit — over-counting,
never under-counting. Idempotency claims use SET NX EX, which is atomic
on a single primary; with async replicas a failover may lose a fresh
claim, so strict exactly-once needs single-primary Redis (or the SQLite
driver, which is single-host by construction).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

logger = logging.getLogger("sincor.a2a.shared_state")

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class StateStoreUnavailable(RuntimeError):
    """The configured shared-state store cannot be reached or has failed.

    Callers MUST treat this as fail-closed: deny the request (HTTP 503 /
    429), never silently reset counters or replay a write.
    """


# ---------------------------------------------------------------------------
# Key namespacing
# ---------------------------------------------------------------------------

_MAX_KEY_LEN = 512


def _quote_part(part: str) -> str:
    # safe="" quotes everything except [A-Za-z0-9_.-~]; injective mapping.
    return quote(str(part), safe="")


def build_namespaced_key(prefix: str, *parts: str) -> str:
    """Build an injective, tenant-isolated storage key.

    Raises ValueError (fail closed) on empty parts or over-long keys
    instead of truncating two distinct keys into one.
    """
    if not parts or any(str(p) == "" for p in parts):
        raise ValueError("shared-state key parts must be non-empty")
    key = ":".join([prefix, "fabric"] + [_quote_part(p) for p in parts])
    if len(key) > _MAX_KEY_LEN:
        raise ValueError(f"shared-state key too long ({len(key)} > {_MAX_KEY_LEN})")
    return key


# ---------------------------------------------------------------------------
# Driver interface
# ---------------------------------------------------------------------------


class SharedState(ABC):
    """Backend interface for durable A2A fabric state."""

    def __init__(self, key_prefix: str = "a2a") -> None:
        self._key_prefix = key_prefix
        # Longest window ever claimed through this driver; bounds the
        # opportunistic stale-row sweep so one policy's cleanup can never
        # delete another policy's still-live hits.
        self._longest_window = 0.0

    def _note_windows(self, windows: Sequence[Tuple[float, float]]) -> float:
        longest = max(float(s) for s, _ in windows)
        if longest > self._longest_window:
            self._longest_window = longest
        return longest

    def namespaced(self, *parts: str) -> str:
        return build_namespaced_key(self._key_prefix, *parts)

    @abstractmethod
    def ping(self) -> None:
        """Raise StateStoreUnavailable if the store is unreachable."""

    @abstractmethod
    def get(self, key: str) -> Optional[str]:
        ...

    @abstractmethod
    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def incr(self, key: str, ttl_seconds: Optional[int] = None) -> int:
        """Atomically increment an integer key; returns the new value."""

    @abstractmethod
    def add_if_absent(self, key: str, value: str,
                      ttl_seconds: Optional[int] = None) -> bool:
        """Atomically set only if the key does not exist (or is expired).

        Returns True when this caller created the key.
        """

    @abstractmethod
    def claim_window_hit(self, domain: str, bucket: str, now: float,
                         windows: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
        """Atomically record one hit in a sliding-window bucket.

        ``windows``: ``[(window_seconds, max_hits), ...]``. On allow, the
        hit is recorded; on deny, nothing is written. Returns
        ``{"allowed": bool, "retry_after": float, "window_seconds": float,
        "window_max": float}`` — the same shape as
        ``SlidingWindowLimiter.check()`` in ``a2a_rate_limits.py``.
        """

    @abstractmethod
    def clear(self) -> None:
        """Delete everything under this driver's fabric namespace.

        Test/ops hook only. On Redis this SCANs the namespace — do not
        call it in a request path.
        """

    def close(self) -> None:
        """Release driver resources (connections). Default: no-op."""


# ---------------------------------------------------------------------------
# In-memory driver — dev/test default, identical semantics to before
# ---------------------------------------------------------------------------


class MemorySharedState(SharedState):
    """Pure in-memory driver. Same semantics as the old per-process dicts."""

    def __init__(self, key_prefix: str = "a2a",
                 clock: Any = None) -> None:
        super().__init__(key_prefix)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._kv: Dict[str, Tuple[str, Optional[float]]] = {}
        self._hits: Dict[str, List[Tuple[str, float]]] = {}

    def ping(self) -> None:
        return None

    def _kv_get_locked(self, key: str) -> Optional[str]:
        entry = self._kv.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and expires_at <= self._clock():
            del self._kv[key]
            return None
        return value

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._kv_get_locked(self.namespaced("kv", key))

    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        skey = self.namespaced("kv", key)
        expires_at = (self._clock() + ttl_seconds) if ttl_seconds else None
        with self._lock:
            self._kv[skey] = (value, expires_at)

    def delete(self, key: str) -> None:
        with self._lock:
            self._kv.pop(self.namespaced("kv", key), None)

    def incr(self, key: str, ttl_seconds: Optional[int] = None) -> int:
        skey = self.namespaced("kv", key)
        with self._lock:
            raw = self._kv_get_locked(skey)
            current = int(raw) if raw is not None else 0
            new_value = current + 1
            expires_at = (self._clock() + ttl_seconds) if ttl_seconds else None
            self._kv[skey] = (str(new_value), expires_at)
            return new_value

    def add_if_absent(self, key: str, value: str,
                      ttl_seconds: Optional[int] = None) -> bool:
        skey = self.namespaced("kv", key)
        with self._lock:
            if self._kv_get_locked(skey) is not None:
                return False
            expires_at = (self._clock() + ttl_seconds) if ttl_seconds else None
            self._kv[skey] = (value, expires_at)
            return True

    def claim_window_hit(self, domain: str, bucket: str, now: float,
                         windows: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
        skey = self.namespaced("rl", domain, bucket)
        wins = sorted((float(s), float(m)) for s, m in windows)
        longest = self._note_windows(wins)
        with self._lock:
            events = [(m, t) for m, t in self._hits.get(skey, []) if t > now - longest]
            for seconds, max_hits in wins:
                in_window = [t for _, t in events if t > now - seconds]
                if len(in_window) >= max_hits:
                    retry_after = (min(in_window) + seconds) - now
                    self._hits[skey] = events  # persist the eviction only
                    return {
                        "allowed": False,
                        "retry_after": max(0.0, retry_after),
                        "window_seconds": float(seconds),
                        "window_max": float(max_hits),
                    }
            member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
            events.append((member, now))
            self._hits[skey] = events
            return {"allowed": True, "retry_after": 0.0}

    def clear(self) -> None:
        with self._lock:
            self._kv.clear()
            self._hits.clear()


# ---------------------------------------------------------------------------
# SQLite driver — automatic durable fallback (single host)
# ---------------------------------------------------------------------------


class SqliteSharedState(SharedState):
    """SQLite-backed driver: durable across restarts, shared across
    processes on one host. File: ``SINCOR_DATA_DIR/a2a_fabric_state.db``
    (override with ``A2A_STATE_SQLITE_PATH``). WAL mode + busy timeout
    for multi-process writers."""

    def __init__(self, path: Optional[str] = None,
                 key_prefix: str = "a2a") -> None:
        super().__init__(key_prefix)
        raw = path or os.environ.get("A2A_STATE_SQLITE_PATH", "").strip()
        if raw:
            self._path = Path(raw)
        else:
            from sincor2.data_paths import data_dir
            self._path = data_dir() / "a2a_fabric_state.db"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._tls = threading.local()
        self._write_lock = threading.Lock()
        try:
            con = self._connect()
            (journal_mode,) = con.execute("PRAGMA journal_mode=WAL;").fetchone()
            if journal_mode.lower() != "wal":
                logger.warning(
                    "SqliteSharedState: WAL unavailable at %s (mode=%s); "
                    "multi-process writers will serialize via locks",
                    self._path, journal_mode)
            con.execute("PRAGMA busy_timeout=5000;")
            con.execute("PRAGMA synchronous=NORMAL;")
            con.execute(
                "CREATE TABLE IF NOT EXISTS fabric_kv ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL,"
                " expires_at REAL)"
            )
            con.execute(
                "CREATE TABLE IF NOT EXISTS fabric_hits ("
                " bucket TEXT NOT NULL, member TEXT NOT NULL,"
                " ts REAL NOT NULL, PRIMARY KEY (bucket, member))"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_fabric_hits "
                "ON fabric_hits(bucket, ts)"
            )
            con.commit()
        except sqlite3.Error as err:
            raise StateStoreUnavailable(
                f"sqlite shared-state init failed at {self._path}: {err}"
            ) from err
        logger.info("SqliteSharedState at %s", self._path)

    def _connect(self) -> sqlite3.Connection:
        con = getattr(self._tls, "con", None)
        if con is None:
            con = sqlite3.connect(str(self._path), timeout=5.0,
                                  check_same_thread=False)
            con.execute("PRAGMA busy_timeout=5000;")
            self._tls.con = con
        return con

    def _op(self, fn: Any) -> Any:
        """Run fn(con) with fail-closed error mapping."""
        try:
            return fn(self._connect())
        except StateStoreUnavailable:
            raise
        except sqlite3.Error as err:
            raise StateStoreUnavailable(f"sqlite shared-state op failed: {err}") from err

    def ping(self) -> None:
        def _ping(con: sqlite3.Connection) -> None:
            con.execute("SELECT 1").fetchone()
        self._op(_ping)

    def get(self, key: str) -> Optional[str]:
        skey = self.namespaced("kv", key)

        def _get(con: sqlite3.Connection) -> Optional[str]:
            row = con.execute(
                "SELECT value, expires_at FROM fabric_kv WHERE key=?", (skey,)
            ).fetchone()
            if row is None:
                return None
            value, expires_at = row
            if expires_at is not None and expires_at <= time.time():
                with self._write_lock:
                    c2 = self._connect()
                    c2.execute("DELETE FROM fabric_kv WHERE key=?", (skey,))
                    c2.commit()
                return None
            return value

        return self._op(_get)

    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        skey = self.namespaced("kv", key)
        expires_at = (time.time() + ttl_seconds) if ttl_seconds else None

        def _set(con: sqlite3.Connection) -> None:
            with self._write_lock:
                con.execute(
                    "INSERT OR REPLACE INTO fabric_kv(key, value, expires_at)"
                    " VALUES(?, ?, ?)",
                    (skey, value, expires_at),
                )
                con.commit()

        self._op(_set)

    def delete(self, key: str) -> None:
        skey = self.namespaced("kv", key)

        def _delete(con: sqlite3.Connection) -> None:
            with self._write_lock:
                con.execute("DELETE FROM fabric_kv WHERE key=?", (skey,))
                con.commit()

        self._op(_delete)

    def incr(self, key: str, ttl_seconds: Optional[int] = None) -> int:
        skey = self.namespaced("kv", key)
        expires_at = (time.time() + ttl_seconds) if ttl_seconds else None

        def _incr(con: sqlite3.Connection) -> int:
            with self._write_lock:
                con.execute("BEGIN IMMEDIATE")
                try:
                    row = con.execute(
                        "SELECT value, expires_at FROM fabric_kv WHERE key=?",
                        (skey,),
                    ).fetchone()
                    if row is None or (row[1] is not None and row[1] <= time.time()):
                        new_value = 1
                    else:
                        new_value = int(row[0]) + 1
                    con.execute(
                        "INSERT OR REPLACE INTO fabric_kv(key, value, expires_at)"
                        " VALUES(?, ?, ?)",
                        (skey, str(new_value), expires_at),
                    )
                    con.execute("COMMIT")
                    return new_value
                except Exception:
                    con.execute("ROLLBACK")
                    raise

        return self._op(_incr)

    def add_if_absent(self, key: str, value: str,
                      ttl_seconds: Optional[int] = None) -> bool:
        skey = self.namespaced("kv", key)
        expires_at = (time.time() + ttl_seconds) if ttl_seconds else None

        def _add(con: sqlite3.Connection) -> bool:
            with self._write_lock:
                con.execute("BEGIN IMMEDIATE")
                try:
                    # An expired row must not block a fresh claim.
                    con.execute(
                        "DELETE FROM fabric_kv WHERE key=? AND expires_at IS NOT NULL"
                        " AND expires_at <= ?",
                        (skey, time.time()),
                    )
                    cur = con.execute(
                        "INSERT OR IGNORE INTO fabric_kv(key, value, expires_at)"
                        " VALUES(?, ?, ?)",
                        (skey, value, expires_at),
                    )
                    con.execute("COMMIT")
                    return cur.rowcount == 1
                except Exception:
                    con.execute("ROLLBACK")
                    raise

        return self._op(_add)

    def claim_window_hit(self, domain: str, bucket: str, now: float,
                         windows: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
        skey = self.namespaced("rl", domain, bucket)
        wins = sorted((float(s), float(m)) for s, m in windows)
        longest = self._note_windows(wins)
        cutoff = now - longest

        def _claim(con: sqlite3.Connection) -> Dict[str, Any]:
            with self._write_lock:
                con.execute("BEGIN IMMEDIATE")
                try:
                    con.execute(
                        "DELETE FROM fabric_hits WHERE bucket=? AND ts <= ?",
                        (skey, cutoff),
                    )
                    # Opportunistic global sweep: buckets abandoned by IP
                    # rotation would otherwise grow without bound (each
                    # bucket only cleans itself on claim). 1% of claims pay
                    # for one pass over rows older than the longest window
                    # ANY policy uses — never a live hit.
                    if self._longest_window > 0 and uuid.uuid4().int % 100 == 0:
                        con.execute(
                            "DELETE FROM fabric_hits WHERE ts <= ?",
                            (now - self._longest_window,),
                        )
                    for seconds, max_hits in wins:
                        wcut = now - seconds
                        (count,) = con.execute(
                            "SELECT COUNT(*) FROM fabric_hits"
                            " WHERE bucket=? AND ts > ?",
                            (skey, wcut),
                        ).fetchone()
                        if count >= max_hits:
                            (oldest,) = con.execute(
                                "SELECT MIN(ts) FROM fabric_hits"
                                " WHERE bucket=? AND ts > ?",
                                (skey, wcut),
                            ).fetchone()
                            con.execute("COMMIT")
                            return {
                                "allowed": False,
                                "retry_after": max(0.0, (oldest + seconds) - now),
                                "window_seconds": float(seconds),
                                "window_max": float(max_hits),
                            }
                    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
                    con.execute(
                        "INSERT INTO fabric_hits(bucket, member, ts)"
                        " VALUES(?, ?, ?)",
                        (skey, member, now),
                    )
                    con.execute("COMMIT")
                    return {"allowed": True, "retry_after": 0.0}
                except Exception:
                    con.execute("ROLLBACK")
                    raise

        return self._op(_claim)

    def clear(self) -> None:
        def _clear(con: sqlite3.Connection) -> None:
            with self._write_lock:
                con.execute("DELETE FROM fabric_kv")
                con.execute("DELETE FROM fabric_hits")
                con.commit()

        self._op(_clear)

    def close(self) -> None:
        con = getattr(self._tls, "con", None)
        if con is not None:
            try:
                con.close()
            except sqlite3.Error:
                pass
            self._tls.con = None


# ---------------------------------------------------------------------------
# Redis driver — cross-host (explicit opt-in, fail-closed)
# ---------------------------------------------------------------------------


class RedisSharedState(SharedState):
    """Redis-backed driver. Requires ``redis`` package and ``REDIS_URL``
    (or ``REDIS_PRIVATE_URL``) — the same variables
    ``a2a_task_store.RedisTaskStore`` honors. Key prefix from
    ``A2A_REDIS_PREFIX`` (default ``a2a``); fabric keys live under
    ``<prefix>:fabric:`` so they never collide with ``a2a:task:`` /
    ``a2a:push:`` keys.

    Every Redis error is mapped to ``StateStoreUnavailable``: callers
    deny the request rather than resetting counters.
    """

    def __init__(self, redis_url: Optional[str] = None,
                 key_prefix: Optional[str] = None,
                 _client: Any = None) -> None:
        super().__init__(key_prefix or os.environ.get("A2A_REDIS_PREFIX", "a2a"))
        if _client is not None:
            self._r = _client
        else:
            try:
                import redis
            except ImportError as err:
                raise StateStoreUnavailable(
                    "redis package required for A2A_STATE_STORE=redis"
                ) from err
            url = (redis_url or os.environ.get("REDIS_URL")
                   or os.environ.get("REDIS_PRIVATE_URL") or "").strip()
            if not url:
                raise StateStoreUnavailable(
                    "REDIS_URL (or REDIS_PRIVATE_URL) must be set for"
                    " A2A_STATE_STORE=redis"
                )
            self._r = redis.from_url(
                url, decode_responses=True,
                socket_connect_timeout=5, socket_timeout=5,
            )
            try:
                self._r.ping()
            except Exception as err:
                raise StateStoreUnavailable(
                    f"redis unreachable at {self._redact(url)}: {err}"
                ) from err
        logger.info("RedisSharedState connected prefix=%s", self._key_prefix)

    @staticmethod
    def _redact(url: str) -> str:
        # Never log credentials embedded in the URL.
        try:
            from urllib.parse import urlsplit, urlunsplit
            parts = urlsplit(url)
            netloc = parts.hostname or ""
            if parts.port:
                netloc += f":{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
        except Exception:
            return "<redis-url>"

    def _wrap(self, fn: Any) -> Any:
        try:
            return fn()
        except StateStoreUnavailable:
            raise
        except ValueError:
            # Key validation errors are caller bugs, not store outages.
            raise
        except Exception as err:
            # redis.exceptions.RedisError and friends -> fail closed.
            raise StateStoreUnavailable(f"redis op failed: {err}") from err

    def ping(self) -> None:
        self._wrap(self._r.ping)

    def get(self, key: str) -> Optional[str]:
        return self._wrap(lambda: self._r.get(self.namespaced("kv", key)))

    def set(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        def _set() -> None:
            self._r.set(self.namespaced("kv", key), value,
                        ex=ttl_seconds if ttl_seconds else None)
        self._wrap(_set)

    def delete(self, key: str) -> None:
        self._wrap(lambda: self._r.delete(self.namespaced("kv", key)))

    def incr(self, key: str, ttl_seconds: Optional[int] = None) -> int:
        def _incr() -> int:
            skey = self.namespaced("kv", key)
            pipe = self._r.pipeline(transaction=True)
            pipe.incr(skey)
            if ttl_seconds:
                pipe.expire(skey, ttl_seconds)
            results = pipe.execute()
            return int(results[0])
        return self._wrap(_incr)

    def add_if_absent(self, key: str, value: str,
                      ttl_seconds: Optional[int] = None) -> bool:
        def _add() -> bool:
            return bool(self._r.set(
                self.namespaced("kv", key), value,
                nx=True, ex=ttl_seconds if ttl_seconds else None,
            ))
        return self._wrap(_add)

    def claim_window_hit(self, domain: str, bucket: str, now: float,
                         windows: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
        skey = self.namespaced("rl", domain, bucket)
        wins = sorted((float(s), float(m)) for s, m in windows)
        longest = self._note_windows(wins)

        def _claim() -> Dict[str, Any]:
            # Atomic check-and-record via WATCH/MULTI with bounded
            # retries. Retry exhaustion -> StateStoreUnavailable (deny),
            # never a silent allow.
            for _attempt in range(5):
                pipe = self._r.pipeline(transaction=True)
                try:
                    pipe.watch(skey)
                    raw = pipe.zrangebyscore(skey, now - longest, "+inf",
                                             withscores=True)
                    stamps = sorted(float(score) for _m, score in raw)
                    for seconds, max_hits in wins:
                        in_window = [t for t in stamps if t > now - seconds]
                        if len(in_window) >= max_hits:
                            pipe.unwatch()
                            return {
                                "allowed": False,
                                "retry_after": max(
                                    0.0, (min(in_window) + seconds) - now),
                                "window_seconds": float(seconds),
                                "window_max": float(max_hits),
                            }
                    member = f"{now:.6f}:{uuid.uuid4().hex[:8]}"
                    pipe.multi()
                    pipe.zremrangebyscore(skey, "-inf", now - longest)
                    pipe.zadd(skey, {member: now})
                    pipe.expire(skey, int(longest) + 300)
                    pipe.execute()
                    return {"allowed": True, "retry_after": 0.0}
                except Exception as err:
                    # redis-py's WatchError and fakeredis' equivalent share
                    # the class NAME but may live in different modules;
                    # match by name so both drivers retry correctly.
                    # Anything else -> fail closed via _wrap.
                    if type(err).__name__ != "WatchError":
                        raise
                    continue
            raise StateStoreUnavailable(
                "redis window-claim retries exhausted (write contention)")

        return self._wrap(_claim)

    def clear(self) -> None:
        def _clear() -> None:
            pattern = f"{self._key_prefix}:fabric:*"
            cursor = 0
            while True:
                cursor, keys = self._r.scan(cursor=cursor, match=pattern,
                                            count=500)
                if keys:
                    self._r.delete(*keys)
                if cursor == 0:
                    break
        self._wrap(_clear)

    def close(self) -> None:
        try:
            pool = getattr(self._r, "connection_pool", None)
            if pool is not None:
                pool.disconnect()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Mode resolution + singletons
# ---------------------------------------------------------------------------

STATE_STORE_ENV = "A2A_STATE_STORE"
SQLITE_PATH_ENV = "A2A_STATE_SQLITE_PATH"

_DEV_ENVS = {"development", "dev", "test", "testing", "local"}


def resolve_state_mode() -> str:
    """Return ``memory`` | ``sqlite`` | ``redis``.

    Explicit ``A2A_STATE_STORE`` wins. Otherwise: ``sqlite`` in
    production-like environments (durable automatic fallback — the point
    of G2.8), ``memory`` in dev/test (identical behavior to before).
    """
    mode = (os.environ.get(STATE_STORE_ENV) or "").strip().lower()
    if mode in {"memory", "sqlite", "redis"}:
        return mode
    if mode:
        raise StateStoreUnavailable(
            f"unknown {STATE_STORE_ENV}={mode!r} (want memory|sqlite|redis)")
    env = (os.environ.get("FLASK_ENV") or os.environ.get("ENVIRONMENT")
           or "production").strip().lower()
    if env in _DEV_ENVS:
        return "memory"
    return "sqlite"


def build_shared_state(mode: Optional[str] = None) -> SharedState:
    """Build (not cache) a driver for ``mode``.

    Raises ``StateStoreUnavailable`` when an explicitly configured driver
    cannot be reached — fail fast, never silently degrade.
    """
    mode = mode or resolve_state_mode()
    if mode == "redis":
        return RedisSharedState()
    if mode == "sqlite":
        return SqliteSharedState()
    return MemorySharedState()


_state_singleton: Optional[SharedState] = None
# RLock: get_idempotency_store() holds this while calling
# get_shared_state(), which acquires it again (same thread).
_state_lock = threading.RLock()


def get_shared_state() -> SharedState:
    """Process-wide shared-state singleton. Resolved once (fail-closed)."""
    global _state_singleton
    with _state_lock:
        if _state_singleton is None:
            _state_singleton = build_shared_state()
        return _state_singleton


def reset_shared_state() -> None:
    """Drop the singleton. Test hook — the next get_shared_state()
    re-resolves from the environment."""
    global _state_singleton
    with _state_lock:
        if _state_singleton is not None:
            try:
                _state_singleton.close()
            except Exception:
                pass
        _state_singleton = None


# ---------------------------------------------------------------------------
# Shared sliding-window limiter (durable backend, spec-identical semantics)
# ---------------------------------------------------------------------------


class SharedSlidingWindowLimiter:
    """Sliding-window limiter backed by a ``SharedState`` driver.

    Same ``check(policy, client_key) -> (allowed, info)`` contract and
    same ``info`` shape as ``SlidingWindowLimiter`` in
    ``a2a_rate_limits.py`` (the executable spec); only the storage moves
    from a per-process deque to the shared backend, so N workers agree
    and restarts preserve counters.

    ``policies``: ``{name: [(window_seconds, max_hits), ...]}``.
    ``StateStoreUnavailable`` propagates to the caller, which MUST deny
    the request (fail closed).

    Clock note: defaults to ``time.time`` (wall clock), NOT monotonic.
    Monotonic resets on every boot and differs across hosts — with a
    durable backend that would make pre-restart hits look fresh forever
    (or misalign workers). Wall clock is the only sane base for shared
    buckets; tests inject a fake clock for determinism.
    """

    def __init__(self, store: SharedState,
                 policies: Dict[str, List[Tuple[float, float]]],
                 clock: Any = None) -> None:
        self._store = store
        self._policies = policies
        self._clock = clock or time.time

    def check(self, policy: str, client_key: str) -> Tuple[bool, Dict[str, float]]:
        windows = self._policies[policy]
        now = self._clock()
        result = self._store.claim_window_hit("a2a", f"{policy}:{client_key}",
                                              now, windows)
        info = {
            "retry_after": float(result.get("retry_after", 0.0)),
            "window_seconds": float(result.get("window_seconds", 0.0)),
            "window_max": float(result.get("window_max", 0.0)),
        }
        return bool(result["allowed"]), info

    def reset(self) -> None:
        """Clear all buckets. Test isolation hook."""
        self._store.clear()


# ---------------------------------------------------------------------------
# Idempotency-key store (adoption contract for the G2.7 write-idempotency
# wave — backlog item 20)
# ---------------------------------------------------------------------------


class IdempotencyStore:
    """Durable idempotency-key store for A2A writes.

    Adoption contract for the write-idempotency wave::

        store = get_idempotency_store()
        fresh, recorded = store.claim(idem_key, in_progress_marker)
        if not fresh:
            return recorded  # replay verbatim; do NOT re-execute
        try:
            result = execute_write(...)
        except ...:
            store.release(idem_key)  # allow a genuine retry
            raise
        store.complete(idem_key, result)

    Keys are namespaced per ``domain`` (e.g. the route name) so a key
    minted for bids can never collide with one minted for stake
    deposits, even if the client reuses the header value.
    """

    def __init__(self, state: SharedState, domain: str = "idemp",
                 default_ttl_seconds: int = 86400) -> None:
        if not domain or not str(domain).strip():
            raise ValueError("idempotency domain must be non-empty")
        self._state = state
        self._domain = str(domain)
        self._default_ttl = default_ttl_seconds

    @staticmethod
    def validate_key(key: str) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("idempotency key must be a non-empty string")
        if len(key) > 256:
            raise ValueError("idempotency key too long (>256)")
        if any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise ValueError("idempotency key contains control characters")
        return key

    def _skey(self, key: str) -> str:
        return self._state.namespaced("idemp", self._domain,
                                     self.validate_key(key))

    @staticmethod
    def _encode(result: Dict[str, Any]) -> str:
        return json.dumps({"v": 1, "result": result,
                           "stored_at": time.time()}, default=str)

    @staticmethod
    def _decode(raw: str) -> Dict[str, Any]:
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
            return payload["result"]
        return {}

    def claim(self, key: str, result: Dict[str, Any],
              ttl_seconds: Optional[int] = None) -> Tuple[bool, Dict[str, Any]]:
        """Atomically claim ``key``.

        Returns ``(True, result)`` when this caller won the key (proceed
        with the write), ``(False, recorded_result)`` when the key was
        already claimed (replay the recorded result verbatim).

        Fail-closed: if this raises ``StateStoreUnavailable`` the store
        is unreachable — the route MUST deny the request (HTTP 503),
        never execute the write without idempotency protection.
        """
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        if self._state.add_if_absent(self._skey(key), self._encode(result), ttl):
            return True, dict(result)
        return False, self.get(key) or {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        raw = self._state.get(self._skey(key))
        if raw is None:
            return None
        decoded = self._decode(raw)
        return decoded if decoded else None

    def complete(self, key: str, result: Dict[str, Any],
                 ttl_seconds: Optional[int] = None) -> None:
        """Overwrite the recorded result (owner finished the write)."""
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        self._state.set(self._skey(key), self._encode(result), ttl)

    def release(self, key: str) -> None:
        """Free a key so a genuine retry may proceed (write failed)."""
        self._state.delete(self._skey(key))


_idempotency_singleton: Optional[IdempotencyStore] = None


def get_idempotency_store(domain: str = "idemp") -> IdempotencyStore:
    """Process-wide idempotency store bound to the shared backend."""
    global _idempotency_singleton
    with _state_lock:
        if _idempotency_singleton is None or _idempotency_singleton._domain != domain:
            _idempotency_singleton = IdempotencyStore(get_shared_state(), domain)
        return _idempotency_singleton
