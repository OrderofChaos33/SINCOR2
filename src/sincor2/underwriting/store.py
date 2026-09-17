"""Append-only JSONL store for underwriting runtime + /v1 engine."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .types import AuditEvent, IntentMandate, SpendEnvelope, iso, parse_iso, utcnow


def _default_dir() -> Path:
    raw = os.environ.get("SINCOR_UNDERWRITE_DIR") or os.environ.get("SINCOR_DATA_DIR")
    if raw:
        return Path(raw) / "underwriting"
    return Path("data") / "underwriting"


class UnderwriteStore:
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root) if root else _default_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.jsonl"

    def append(self, collection: str, record: Dict[str, Any]) -> Dict[str, Any]:
        line = json.dumps(record, separators=(",", ":"), sort_keys=True)
        with self._lock:
            with self._path(collection).open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def iter(self, collection: str) -> Iterable[Dict[str, Any]]:
        path = self._path(collection)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw)
                except json.JSONDecodeError:
                    continue

    def list(self, collection: str) -> List[Dict[str, Any]]:
        return list(self.iter(collection))

    def find_one(self, collection: str, key: str, value: Any) -> Optional[Dict[str, Any]]:
        last = None
        for rec in self.iter(collection):
            if rec.get(key) == value:
                last = rec
        return last

    # Runtime compatibility API (tap/ledger_sim services depend on these methods).
    def write_mandate(self, mandate: IntentMandate) -> IntentMandate:
        self.append("mandates", mandate.to_dict())
        return mandate

    def get_mandate(self, mandate_id: str) -> IntentMandate | None:
        rec = self.find_one("mandates", "mandate_id", mandate_id)
        if not rec:
            return None
        return IntentMandate.from_dict(rec)

    def active_mandate_for(self, agent_id: str) -> IntentMandate | None:
        now = utcnow()
        latest: IntentMandate | None = None
        for rec in self.iter("mandates"):
            if rec.get("agent_id") != agent_id:
                continue
            try:
                mandate = IntentMandate.from_dict(rec)
            except Exception:
                continue
            if mandate.killed:
                continue
            try:
                if parse_iso(mandate.expires_at) <= now:
                    continue
            except Exception:
                continue
            latest = mandate
        return latest

    def write_envelope(self, envelope: SpendEnvelope) -> SpendEnvelope:
        self.append("envelopes", envelope.to_dict())
        return envelope

    def get_envelope(self, envelope_id: str) -> SpendEnvelope | None:
        rec = self.find_one("envelopes", "envelope_id", envelope_id)
        if not rec:
            return None
        return SpendEnvelope.from_dict(rec)

    def audit(
        self,
        kind: str,
        agent_id: str,
        payload: Dict[str, Any],
        mandate_id: str | None = None,
        envelope_id: str | None = None,
    ) -> Dict[str, Any]:
        ev = AuditEvent(
            event_id=f"audit-{os.urandom(8).hex()}",
            ts=iso(utcnow()),
            kind=kind,
            agent_id=agent_id,
            payload=payload,
            mandate_id=mandate_id,
            envelope_id=envelope_id,
        ).to_dict()
        self.append("audit", ev)
        return ev

    def iter_audit(self) -> Iterable[Dict[str, Any]]:
        return self.iter("audit")
