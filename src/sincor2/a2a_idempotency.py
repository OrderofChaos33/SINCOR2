"""Idempotency keys for A2A write operations (G2.7 / backlog item 20).

Clients send ``Idempotency-Key: <key>`` on money-adjacent POST writes
(task create, bid submit, sealed-bid commit/reveal, settle). Semantics
(Stripe-style):

- Same key + byte-identical request (same endpoint scope) -> the ORIGINAL
  response (status + body) is returned; the write is NOT repeated.
- Same key + DIFFERENT request -> HTTP 422. A key is cryptographically
  bound to the first request seen with it, so a key can never be reused
  to replay a *different* write.
- Same key on a DIFFERENT endpoint scope -> HTTP 422. Keys never cross
  scopes, so a settle key cannot be replayed as a bid.
- Only 2xx responses are cached. Errors never consume a key: the in-flight
  marker is discarded and the client may fix the request and retry with
  the same key.
- Concurrent duplicate (key seen, response not yet stored) -> HTTP 409
  "request in progress". Exactly one execution per key, never two.
- Keys expire after ``SINCOR_IDEMPOTENCY_TTL_S`` (default 24h). Expired
  keys are treated as fresh. Abandoned in-flight markers (older than
  ``SINCOR_IDEMPOTENCY_INFLIGHT_S``, default 120s) are reclaimed so a
  crashed first attempt cannot wedge a key forever.

Storage: SQLite under ``SINCOR_DATA_DIR`` (same convention as the
reputation ledger), ``UNIQUE(key)`` for multi-process safety;
``SINCOR_IDEMPOTENCY_DB_PATH`` overrides the path for tests. Thread-safe
via a per-instance lock. Survives restarts, unlike the in-memory fabric.

Security notes:
- Keys are client-chosen; treat them like passwords (uuid4 recommended,
  never logged). Two clients that pick the SAME key AND send byte-identical
  requests share one execution — that is the definition of idempotent, but
  keys must still be unique per client in practice.
- The request fingerprint binds the canonical request body AND the scope;
  tampering with either yields 422, never another user's result.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from functools import wraps
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Header clients send. Also accepted: none (no key -> no idempotency,
# fully backward compatible with existing callers).
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"

# Client key format: 1..128 chars of unreserved token characters.
_KEY_RE = re.compile(r"^[A-Za-z0-9\-_.]{1,128}$")

ENV_DB_PATH = "SINCOR_IDEMPOTENCY_DB_PATH"
ENV_TTL_S = "SINCOR_IDEMPOTENCY_TTL_S"
ENV_INFLIGHT_S = "SINCOR_IDEMPOTENCY_INFLIGHT_S"

DEFAULT_TTL_S = 24 * 3600
DEFAULT_INFLIGHT_S = 120

_STATUS_INFLIGHT = "inflight"
_STATUS_COMPLETE = "complete"


def _ttl_s() -> float:
    try:
        return max(60.0, float(os.environ.get(ENV_TTL_S, "") or DEFAULT_TTL_S))
    except (TypeError, ValueError):
        return float(DEFAULT_TTL_S)


def _inflight_s() -> float:
    try:
        return max(10.0, float(os.environ.get(ENV_INFLIGHT_S, "") or DEFAULT_INFLIGHT_S))
    except (TypeError, ValueError):
        return float(DEFAULT_INFLIGHT_S)


def default_db_path() -> str:
    """SQLite file for the idempotency store. ``SINCOR_IDEMPOTENCY_DB_PATH``
    overrides for tests; otherwise the persistent data dir."""
    explicit = os.environ.get(ENV_DB_PATH, "").strip()
    if explicit:
        return explicit
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "a2a_idempotency.db")
    except Exception:
        return os.path.join("data", "a2a_idempotency.db")


def valid_key(key: str) -> bool:
    """Client key format check. Malformed keys are rejected with 400."""
    return bool(key) and bool(_KEY_RE.match(key))


def request_fingerprint(scope: str, body: Any) -> str:
    """Bind an idempotency key to the exact request.

    Canonical JSON (sorted keys, compact separators) of the request body,
    namespaced by endpoint scope. Any byte-level difference in the body —
    or a different scope — produces a different fingerprint, so a key can
    never silently authorize a different write.
    """
    try:
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                               default=str, ensure_ascii=True)
    except (TypeError, ValueError):
        canonical = repr(body)
    return hashlib.sha256(f"{scope}\n{canonical}".encode("utf-8")).hexdigest()


class IdempotencyStore:
    """Durable idempotency-key store. SQLite, UNIQUE(key), thread-safe."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._db_path = db_path or default_db_path()
        self._init_db()

    # -- plumbing ------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        parent = os.path.dirname(os.path.abspath(self._db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=30,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error:
                    pass  # some filesystems dislike WAL; rollback journal is fine
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS idempotency_keys (
                           key           TEXT PRIMARY KEY,
                           scope         TEXT NOT NULL,
                           request_hash  TEXT NOT NULL,
                           status        TEXT NOT NULL,
                           response_status INTEGER,
                           response_json TEXT,
                           created_at    REAL NOT NULL,
                           expires_at    REAL NOT NULL
                       )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_idem_expires "
                    "ON idempotency_keys (expires_at)"
                )
                conn.commit()
            finally:
                conn.close()

    def purge_expired(self) -> int:
        """Delete expired rows. Returns the number removed."""
        now = time.time()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "DELETE FROM idempotency_keys WHERE expires_at < ?", (now,))
                conn.commit()
                return cur.rowcount or 0
            finally:
                conn.close()

    # -- core protocol --------------------------------------------------
    def begin(self, key: str, scope: str, request_hash: str
              ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Attempt to claim ``key`` for this request.

        Returns ``(outcome, stored)`` where outcome is one of:
          - ``"fresh"``    — key claimed; caller must execute, then call
                             :meth:`complete` (2xx) or :meth:`discard` (error).
          - ``"replay"``   — key already completed this exact request;
                             ``stored`` is ``{"status": int, "body": dict}``.
          - ``"conflict"`` — key is bound to a different request or scope.
          - ``"inflight"`` — another request holds this key right now.
        """
        now = time.time()
        ttl = _ttl_s()
        inflight_ttl = _inflight_s()
        with self._lock:
            conn = self._connect()
            try:
                self._purge_locked(conn, now)
                row = conn.execute(
                    "SELECT scope, request_hash, status, response_status, "
                    "response_json, created_at, expires_at "
                    "FROM idempotency_keys WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    try:
                        conn.execute(
                            "INSERT INTO idempotency_keys "
                            "(key, scope, request_hash, status, created_at, expires_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (key, scope, request_hash, _STATUS_INFLIGHT,
                             now, now + ttl),
                        )
                        conn.commit()
                    except sqlite3.IntegrityError:
                        # Lost a cross-process race between SELECT and INSERT;
                        # re-read and adjudicate below.
                        row = conn.execute(
                            "SELECT scope, request_hash, status, response_status, "
                            "response_json, created_at, expires_at "
                            "FROM idempotency_keys WHERE key = ?", (key,)
                        ).fetchone()
                    else:
                        return "fresh", None
                # Adjudicate the existing row.
                if row["status"] == _STATUS_INFLIGHT:
                    if now - float(row["created_at"]) > inflight_ttl:
                        # Abandoned by a crashed first attempt: reclaim.
                        conn.execute("DELETE FROM idempotency_keys WHERE key = ?",
                                     (key,))
                        conn.execute(
                            "INSERT INTO idempotency_keys "
                            "(key, scope, request_hash, status, created_at, expires_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (key, scope, request_hash, _STATUS_INFLIGHT,
                             now, now + ttl),
                        )
                        conn.commit()
                        return "fresh", None
                    return "inflight", None
                if row["scope"] != scope or row["request_hash"] != request_hash:
                    return "conflict", None
                try:
                    body = json.loads(row["response_json"] or "null")
                except (TypeError, ValueError):
                    body = None
                return "replay", {
                    "status": int(row["response_status"] or 200),
                    "body": body,
                }
            finally:
                conn.close()

    def _purge_locked(self, conn: sqlite3.Connection, now: float) -> None:
        conn.execute("DELETE FROM idempotency_keys WHERE expires_at < ?", (now,))

    def complete(self, key: str, response_status: int,
                 response_body: Any) -> None:
        """Store the successful response for ``key``. Only 2xx call this."""
        try:
            payload = json.dumps(response_body, default=str)
        except (TypeError, ValueError):
            logger.warning("idempotency: unserializable response for key; "
                           "not caching")
            return
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE idempotency_keys SET status = ?, response_status = ?, "
                    "response_json = ? WHERE key = ? AND status = ?",
                    (_STATUS_COMPLETE, int(response_status), payload,
                     key, _STATUS_INFLIGHT),
                )
                conn.commit()
            finally:
                conn.close()

    def discard(self, key: str) -> None:
        """Release an in-flight key after an error response or exception.

        Errors never consume a key: the client may fix the request and
        retry with the same key.
        """
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM idempotency_keys WHERE key = ? AND status = ?",
                    (key, _STATUS_INFLIGHT),
                )
                conn.commit()
            finally:
                conn.close()


_STORE: Optional[IdempotencyStore] = None
_STORE_LOCK = threading.Lock()


def idempotency_store() -> IdempotencyStore:
    """Process-wide singleton (mirrors the reputation-ledger pattern)."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = IdempotencyStore()
    return _STORE


def reset_idempotency_store() -> None:
    """Test hook: drop the singleton so a new DB path takes effect."""
    global _STORE
    with _STORE_LOCK:
        _STORE = None


def _split_response(result: Any) -> Tuple[int, Any]:
    """Extract (status_code, json_body) from a Flask view return value."""
    status: Optional[int] = None
    resp = result
    if isinstance(result, tuple):
        resp = result[0]
        if len(result) > 1 and isinstance(result[1], int):
            status = result[1]
    body: Any = None
    if hasattr(resp, "get_json"):
        try:
            body = resp.get_json(silent=True)
        except Exception:
            body = None
        # Only read status_code off the Response object when the view did
        # NOT return an explicit (response, status) tuple: a bare Response
        # built for a tuple return keeps its default 200, which must not
        # overwrite the tuple's real status (e.g. 201).
        if status is None:
            try:
                status = int(getattr(resp, "status_code", 200) or 200)
            except (TypeError, ValueError):
                status = 200
    elif isinstance(resp, dict):
        body = resp
    if status is None:
        status = 200
    return status, body


def idempotent(scope: str,
               error: Optional[Callable[[str, int], Any]] = None):
    """Decorate a Flask POST view with idempotency-key handling.

    ``scope`` names the endpoint (e.g. ``"settle"``, ``"bids.commit"``);
    keys never cross scopes. ``error(message, http_status)`` builds error
    responses in the blueprint's own envelope style.
    """
    from flask import jsonify, make_response, request

    def _default_error(message: str, status: int):
        return jsonify({"error": message, "status": status}), status

    err = error or _default_error

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
            if not key:
                return view(*args, **kwargs)  # no key -> legacy behavior
            if not valid_key(key):
                return err(
                    "Invalid Idempotency-Key: 1-128 chars of "
                    "[A-Za-z0-9-_.]", 400,
                )
            try:
                body = request.get_json(silent=True)
            except Exception:
                body = None
            fingerprint = request_fingerprint(scope, body if body is not None else {})
            store = idempotency_store()
            outcome, stored = store.begin(key, scope, fingerprint)
            if outcome == "replay":
                assert stored is not None
                replayed = make_response(jsonify(stored["body"]),
                                         stored["status"])
                replayed.headers[REPLAYED_HEADER] = "true"
                return replayed
            if outcome == "conflict":
                return err(
                    "Idempotency-Key was already used with a different "
                    "request or endpoint", 422,
                )
            if outcome == "inflight":
                return err(
                    "A request with this Idempotency-Key is already in "
                    "progress", 409,
                )
            # fresh: execute exactly once
            try:
                result = view(*args, **kwargs)
            except Exception:
                store.discard(key)
                raise
            status, resp_body = _split_response(result)
            if 200 <= status < 300 and resp_body is not None:
                store.complete(key, status, resp_body)
            else:
                # Errors never consume the key: the client may fix the
                # request and retry with the same key.
                store.discard(key)
            return result

        return wrapper

    return decorator
