"""Append-only proof ledger for the speculative DeFi arm.

Every piece of testing/audit/deployment evidence for a product SKU is
recorded here: test runs, invariant/fuzz results, fork simulations, audit
reports, deployment receipts. Entries are never edited or deleted — a
correction is a new entry. Stage gates read this ledger as evidence.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

try:  # POSIX advisory locks for cross-instance mutual exclusion (W-204)
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

# Evidence kinds the stage gates understand.
KIND_TEST_RUN = "test_run"
KIND_INVARIANT_TEST = "invariant_test"
KIND_FORK_SIM = "fork_sim"
KIND_AUDIT_REPORT = "audit_report"
KIND_DEPLOY_RECEIPT = "deploy_receipt"
KIND_NOTE = "note"

KINDS = (
    KIND_TEST_RUN,
    KIND_INVARIANT_TEST,
    KIND_FORK_SIM,
    KIND_AUDIT_REPORT,
    KIND_DEPLOY_RECEIPT,
    KIND_NOTE,
)

ENV_DATA_DIR = "SINCOR_DEFI_ARM_DATA_DIR"


def default_data_dir() -> str:
    explicit = os.environ.get(ENV_DATA_DIR, "").strip()
    if explicit:
        return explicit
    # Volume-aware: Railway /data when mounted, else the repo-local data dir.
    # The old relative default lived on the ephemeral filesystem, so redeploys
    # wiped product-arm state.
    try:
        from sincor2.data_paths import data_dir

        return str(data_dir() / "defi_product_arm")
    except Exception:
        return os.path.join("data", "defi_product_arm")


def default_ledger_path() -> str:
    return os.path.join(default_data_dir(), "proof_ledger.json")


class ProofLedger:
    """JSON-persisted, append-only evidence ledger. Atomic tmp+replace writes."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_ledger_path()
        self._entries: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._txn_depth = 0
        self._txn_dirty = False
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                entries = data.get("entries", [])
                if isinstance(entries, list):
                    self._entries = entries
            except (json.JSONDecodeError, OSError):
                self._entries = []

    def _reload_under_lock(self) -> None:
        """Re-read the ledger file. Call inside ``_locked_txn`` (W-204).

        A missing or corrupt file keeps the in-memory state rather
        than wiping entries — ``_load()`` already handled those cases
        at construction.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        entries = data.get("entries", [])
        if isinstance(entries, list):
            self._entries = entries

    @contextmanager
    def _locked_txn(self):
        """Locked transaction (W-204), mirroring PersistentErrorTracker's
        W-213 / FileSealRegistry pattern: instance RLock + exclusive
        flock on the sidecar ``.lock`` file (graceful fallback when
        fcntl is unavailable) + reload-under-lock, then run the body;
        on exit, persist exactly once if any nested ``_save()`` marked
        the transaction dirty. Nested entry joins the outer transaction.
        """
        with self._lock:
            if self._txn_depth > 0:
                self._txn_depth += 1
                try:
                    yield
                finally:
                    self._txn_depth -= 1
                return
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            lock_fh = open(self.path + ".lock", "a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                self._txn_depth = 1
                self._txn_dirty = False
                try:
                    self._reload_under_lock()
                    try:
                        yield
                    except BaseException:
                        if self._txn_dirty:
                            self._save_locked()
                        raise
                    if self._txn_dirty:
                        self._save_locked()
                finally:
                    self._txn_depth = 0
                    self._txn_dirty = False
            finally:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_fh.close()

    def _save(self) -> None:
        """Persist, or — inside a locked transaction — defer to its exit.

        Outside a transaction, persist the current in-memory state
        under the flock WITHOUT reloading: subclasses such as
        ObsProofLedger mutate ``_entries`` then call ``_save()``
        directly, and a reload here would discard their pending entry.
        Cross-instance safety for ProofLedger's own API comes from
        ``append``/``read`` reloading under ``_locked_txn``.
        """
        if self._txn_depth > 0:
            self._txn_dirty = True
            return
        with self._lock:
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            lock_fh = open(self.path + ".lock", "a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    self._save_locked()
                finally:
                    if fcntl is not None:
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
            finally:
                lock_fh.close()

    def _save_locked(self) -> None:
        tmp = f"{self.path}.tmp-{os.getpid()}-{threading.get_ident()}"
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"entries": self._entries}, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        if parent:
            try:
                dir_fd = os.open(parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass

    # -- API --------------------------------------------------------------
    def append(self, sku: str, kind: str, details: Dict[str, Any],
               commit: Optional[str] = None,
               recorded_by: str = "operator") -> Dict[str, Any]:
        """Record one evidence entry. There is no update or delete."""
        if kind not in KINDS:
            raise ValueError(f"unknown evidence kind: {kind}")
        entry = {
            "entry_id": "ev_" + uuid.uuid4().hex[:12],
            "sku": str(sku),
            "kind": kind,
            "timestamp": time.time(),
            "commit": commit,
            "recorded_by": recorded_by,
            "details": dict(details or {}),
        }
        # W-204: reload-mutate-save under the flock, so a stale
        # instance never erases another instance's entries.
        with self._locked_txn():
            self._entries.append(entry)
            self._save()
        return dict(entry)

    def read(self, sku: Optional[str] = None,
            kind: Optional[str] = None) -> List[Dict[str, Any]]:
        # W-204: reload fresh under the lock so reads never serve a
        # stale snapshot missing another instance's appends.
        with self._locked_txn():
            out = self._entries
            if sku is not None:
                out = [e for e in out if e.get("sku") == sku]
            if kind is not None:
                out = [e for e in out if e.get("kind") == kind]
            return [dict(e) for e in out]

    def count(self, sku: Optional[str] = None) -> int:
        return len(self.read(sku=sku))
