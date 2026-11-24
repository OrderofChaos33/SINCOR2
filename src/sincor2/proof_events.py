"""Proof wall — append-only verified settlement event store.

Backs the "Live activity" section of GET /x402-live (see
``docs``/spec: proof-wall-live-upgrade-spec.md, 2026-11-15). This is a
read-model only: it records events that have *already* happened and
been verified by their producers (in-app settlement finalization, or
the HMAC-signed ingest route which re-verifies the Base receipt
server-side before recording). It moves no funds, settles nothing, and
mutates no balances.

Storage: one JSON object per line (JSONL) in the app's data directory
(``sincor2.data_paths.data_dir()`` — SINCOR_DATA_DIR / Railway /data),
the same neighbourhood as the neighbouring JSON stores. Appends are a
single line write + fsync under a lock. The path can be overridden
with ``SINCOR_PROOF_EVENTS_PATH`` (ops/tests).

Fail-closed: a store file that exists but cannot be parsed raises
:class:`ProofStoreError` on read instead of silently degrading to an
empty wall — callers (lane3.proof_wall_data) catch it and render the
spec's empty-state sentence over the snapshot-only page. A partially
written final line (crash mid-append) is the one tolerated defect:
complete lines before it still load, and the torn tail is ignored.

Load-time integrity repair (SH-1): per-record *structural* problems
— a record that parses as JSON but fails :func:`validate_event`, or
a duplicate ``event_id`` — no longer brick the wall. Each such record
is quarantined to the sidecar ``<store>.quarantine.jsonl`` (appended,
with its reason) and in-memory state is rebuilt from the valid
remainder; the store file itself is never rewritten on load, so a
clean store loads byte-identically. BOUNDARY: corrupt JSON — a
complete line that does not parse as JSON at all, or a file that is
not decodable text — still fails closed with
:class:`ProofStoreError`; quarantine-and-rebuild applies to
per-record structural problems only, never to unparseable bytes.

Event shape (one shape for every source — spec §3.1):

    event_id    dedupe key everywhere ("moebiusx:trade-107", "task:tsk_…")
    source      "moebiusx_v3.1" | "marketplace"
    origin      "sincor_agent" | "marketplace_task" | "external_agent"
    label       human label ("MoebiusX trade-107", "Task tsk_…")
    detail      human detail ("SELL settled on Base — $8.24 USDC")
    amount      display string ("8.24")
    asset       "USDC" | "AXM"
    tx_hash     0x + 64 hex — the on-chain record for the event
    occurred_at ISO-8601 timestamp

Origin is part of the data model, not presentation garnish: SINCOR's
own-agent activity is never blended into an unlabeled total, and
:func:`totals` always splits by origin.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

try:  # POSIX advisory locks for cross-instance mutual exclusion (W-212)
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

STORE_FILENAME = "proof_events.jsonl"
PATH_ENV = "SINCOR_PROOF_EVENTS_PATH"

SOURCES = frozenset({"moebiusx_v3.1", "marketplace"})
ORIGINS = frozenset({"sincor_agent", "marketplace_task", "external_agent"})
ORIGIN_LABELS = {
    "sincor_agent": "SINCOR agent",
    "marketplace_task": "Marketplace task",
    "external_agent": "External agent",
}

_TX_HASH_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_REQUIRED_FIELDS = (
    "event_id", "source", "origin", "label", "detail",
    "amount", "asset", "tx_hash", "occurred_at",
)


class ProofStoreError(RuntimeError):
    """The event store exists but cannot be read (corrupt/unreadable).

    Raised instead of returning partial or invented data — the wall
    degrades to its snapshot + empty-state sentence, never to fiction.
    """


def store_path() -> Path:
    override = os.environ.get(PATH_ENV, "").strip()
    if override:
        return Path(override)
    from sincor2.data_paths import data_dir

    return data_dir() / STORE_FILENAME


def validate_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Validate + normalize one event dict. Raises ValueError.

    Only the spec §3.1 shape is accepted: known source/origin, a real
    tx-hash shape (simulated hashes can never match), non-empty text
    fields. Unknown extra fields are dropped so the stored record is
    exactly the public shape.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    out: Dict[str, Any] = {}
    for field_name in _REQUIRED_FIELDS:
        value = event.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"event missing required field: {field_name}")
        out[field_name] = str(value)
    if out["source"] not in SOURCES:
        raise ValueError(f"unknown event source: {out['source']!r}")
    if out["origin"] not in ORIGINS:
        raise ValueError(f"unknown event origin: {out['origin']!r}")
    if not _TX_HASH_RE.match(out["tx_hash"]):
        raise ValueError("tx_hash must be 0x followed by 64 hex chars")
    return out


class ProofEventStore:
    """Append-only JSONL event store with event_id dedupe."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path is not None else store_path()
        self._lock = threading.RLock()
        self._events: Optional[List[Dict[str, Any]]] = None
        self._ids: set = set()
        # W-212 locked-transaction state (mirrors BountyPool W-193 /
        # TokenBudget W-215 _locked_txn): depth > 0 means this instance
        # already holds the flock; nested entry joins the outer txn.
        self._txn_depth = 0

    @property
    def path(self) -> Path:
        return self._path

    # ── loading ──────────────────────────────────────────────────────

    @property
    def quarantine_path(self) -> Path:
        """Sidecar receiving quarantined records (SH-1)."""
        return Path(str(self._path) + ".quarantine.jsonl")

    def _quarantine(self, pending: List[Dict[str, Any]]) -> None:
        """Append quarantined records to the sidecar (SH-1).

        Each entry is one JSON line: {line, reason, raw, record}.
        Append-only and idempotent per (line, raw, reason): reloads
        re-validate the same on-disk records, and an entry already in
        the sidecar is not written again, so repeated loads cannot
        grow the quarantine file with copies of the same defect.
        """
        if not pending:
            return
        qpath = self.quarantine_path
        seen = set()
        if qpath.exists():
            try:
                for qline in qpath.read_text(encoding="utf-8").splitlines():
                    if not qline.strip():
                        continue
                    try:
                        entry = json.loads(qline)
                    except ValueError:
                        continue
                    seen.add((entry.get("line"), entry.get("raw"),
                              entry.get("reason")))
            except OSError:
                seen = set()
        fresh = [e for e in pending
                 if (e["line"], e["raw"], e["reason"]) not in seen]
        if not fresh:
            return
        try:
            qpath.parent.mkdir(parents=True, exist_ok=True)
            with open(qpath, "a", encoding="utf-8") as fh:
                for entry in fresh:
                    fh.write(json.dumps(entry, separators=(",", ":"),
                                        sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise ProofStoreError(
                f"proof event quarantine write failed: {exc}") from exc
        for entry in fresh:
            logger.warning(
                "proof_events: quarantined line %s from %s: %s",
                entry["line"], self._path, entry["reason"])

    def _read_disk(self):
        """Parse the on-disk file fresh. Returns (events, ids).

        Missing file = empty store. A torn final line (crash during
        append, no trailing newline, unparseable) is ignored so the
        events that were durably written still count.

        SH-1 boundary (see module docstring): a complete line that
        does not parse as JSON at all is corrupt-JSON and raises
        ProofStoreError (fail closed). A line that parses but is
        structurally invalid, or whose event_id duplicates an earlier
        record, is quarantined to the sidecar and skipped — the valid
        remainder loads. The store file is never rewritten here.
        """
        events: List[Dict[str, Any]] = []
        ids: set = set()
        if self._path.exists():
            try:
                raw = self._path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise ProofStoreError(
                    f"proof event store unreadable: {exc}") from exc
            lines = raw.split("\n")
            pending_quarantine: List[Dict[str, Any]] = []
            for idx, line in enumerate(lines):
                text = line.strip()
                if not text:
                    continue
                # Step 1 — JSON syntax. Unparseable = corrupt-JSON:
                # fail closed (torn final line excepted, as before).
                try:
                    parsed = json.loads(text)
                except ValueError as exc:
                    is_torn_tail = idx == len(lines) - 1 and not raw.endswith("\n")
                    if is_torn_tail:
                        logger.warning(
                            "proof_events: ignoring torn final line in %s",
                            self._path)
                        break
                    raise ProofStoreError(
                        f"proof event store corrupt at line {idx + 1}: {exc}"
                    ) from exc
                # Step 2 — structure. A parseable but invalid record is
                # a per-record problem: quarantine it, keep loading.
                try:
                    record = validate_event(parsed)
                except (ValueError, TypeError) as exc:
                    pending_quarantine.append({
                        "line": idx + 1,
                        "reason": f"invalid record: {exc}",
                        "raw": text,
                        "record": parsed if isinstance(parsed, dict) else None,
                    })
                    continue
                if record["event_id"] in ids:
                    # The file is append-only and record_event dedupes,
                    # so a duplicate on disk means tampering/corruption.
                    # SH-1: keep the first occurrence, quarantine the
                    # duplicate instead of bricking the whole wall.
                    pending_quarantine.append({
                        "line": idx + 1,
                        "reason": f"duplicate event_id: {record['event_id']!r}",
                        "raw": text,
                        "record": parsed if isinstance(parsed, dict) else None,
                    })
                    continue
                ids.add(record["event_id"])
                events.append(record)
            self._quarantine(pending_quarantine)
        return events, ids

    def _load(self) -> None:
        """Populate the in-memory index from disk (once, under lock)."""
        if self._events is not None:
            return
        self._events, self._ids = self._read_disk()

    def _reload(self) -> None:
        """Re-read the file fresh, discarding any stale snapshot.

        Call inside ``_locked_txn`` (W-212).
        """
        self._events, self._ids = self._read_disk()

    @contextmanager
    def _locked_txn(self):
        """Locked transaction (W-212), mirroring BountyPool's W-193
        ``_locked_txn``: instance RLock + exclusive flock on the
        sidecar ``.lock`` file (graceful fallback when fcntl is
        unavailable) + reload-under-lock, then run the body against
        the fresh state. Nested entry joins the outer transaction:
        no re-flock, no reload.
        """
        with self._lock:
            if self._txn_depth > 0:
                self._txn_depth += 1
                try:
                    yield
                finally:
                    self._txn_depth -= 1
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            lock_fh = open(Path(str(self._path) + ".lock"), "a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                self._txn_depth = 1
                try:
                    self._reload()
                    yield
                finally:
                    self._txn_depth = 0
            finally:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_fh.close()

    # ── writes ───────────────────────────────────────────────────────

    def record_event(self, event: Dict[str, Any]) -> bool:
        """Append ``event`` unless its event_id is already recorded.

        Returns True when the event was recorded, False when it was a
        duplicate (replays are no-ops). Raises ValueError for a
        malformed event and ProofStoreError when the store is corrupt
        or the append fails — a producer must never believe an event
        was recorded when it was not.
        """
        record = validate_event(event)
        # W-212: dedupe + append inside the flock with a fresh reload,
        # so a second instance's already-appended id is seen and the
        # duplicate is refused instead of bricking the file.
        with self._locked_txn():
            assert self._events is not None
            if record["event_id"] in self._ids:
                return False
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            try:
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError as exc:
                raise ProofStoreError(
                    f"proof event append failed: {exc}") from exc
            self._events.append(record)
            self._ids.add(record["event_id"])
            return True

    # ── reads ────────────────────────────────────────────────────────

    def list_events(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Newest-first events (at most ``limit``), each carrying its
        ``origin_label`` so no render path can drop the origin."""
        # W-212: reload fresh under the flock so appends by another
        # instance are never hidden by a stale cached snapshot.
        with self._locked_txn():
            assert self._events is not None
            events = list(self._events)
        events.reverse()
        if limit is not None and limit >= 0:
            events = events[:limit]
        return [self._with_origin_label(e) for e in events]

    def totals(self) -> Dict[str, Any]:
        """Event counts: grand total plus the by-origin split. Counts
        only — amounts are never summed across assets/origins into a
        single money figure."""
        with self._locked_txn():
            assert self._events is not None
            events = list(self._events)
        by_origin = {origin: 0 for origin in sorted(ORIGINS)}
        for event in events:
            by_origin[event["origin"]] = by_origin.get(event["origin"], 0) + 1
        return {"total": len(events), "by_origin": by_origin}

    @staticmethod
    def _with_origin_label(event: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(event)
        out["origin_label"] = ORIGIN_LABELS.get(event["origin"], event["origin"])
        return out


# ── module-level default store (the API the spec names) ──────────────

_default_store: Optional[ProofEventStore] = None
_default_lock = threading.Lock()


def default_store() -> ProofEventStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = ProofEventStore()
        return _default_store


def reset_default_store() -> None:
    """Drop the cached default store (tests / path reconfiguration)."""
    global _default_store
    with _default_lock:
        _default_store = None


def record_event(event: Dict[str, Any]) -> bool:
    return default_store().record_event(event)


def list_events(limit: int = 25) -> List[Dict[str, Any]]:
    return default_store().list_events(limit)


def totals() -> Dict[str, Any]:
    return default_store().totals()
