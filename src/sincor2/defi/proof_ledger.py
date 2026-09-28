"""Append-only proof ledger for the speculative DeFi arm.

Every piece of testing/audit/deployment evidence for a product SKU is
recorded here: test runs, invariant/fuzz results, fork simulations, audit
reports, deployment receipts. Entries are never edited or deleted — a
correction is a new entry. Stage gates read this ledger as evidence.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

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

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"entries": self._entries}, fh, indent=2)
        os.replace(tmp, self.path)

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
        self._entries.append(entry)
        self._save()
        return dict(entry)

    def read(self, sku: Optional[str] = None,
            kind: Optional[str] = None) -> List[Dict[str, Any]]:
        out = self._entries
        if sku is not None:
            out = [e for e in out if e.get("sku") == sku]
        if kind is not None:
            out = [e for e in out if e.get("kind") == kind]
        return [dict(e) for e in out]

    def count(self, sku: Optional[str] = None) -> int:
        return len(self.read(sku=sku))
