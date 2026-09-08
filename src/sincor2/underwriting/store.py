from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .types import AuditEvent, IntentMandate, SpendEnvelope, iso, new_id, utcnow


class UnderwriteStore:
    def __init__(self, root: str | Path = "data/underwriting") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._mandates = self.root / "mandates.jsonl"
        self._envelopes = self.root / "envelopes.jsonl"

    def _audit_path(self, ts: datetime | None = None) -> Path:
        day = (ts or utcnow()).strftime("%Y-%m-%d")
        return self.root / f"audit-{day}.jsonl"

    def _append(self, path: Path, obj: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def write_mandate(self, m: IntentMandate) -> None:
        self._append(self._mandates, m.to_dict())

    def write_envelope(self, e: SpendEnvelope) -> None:
        self._append(self._envelopes, e.to_dict())

    def audit(self, kind: str, agent_id: str, payload: dict[str, Any],
              mandate_id: str | None = None, envelope_id: str | None = None) -> AuditEvent:
        ev = AuditEvent(
            event_id=new_id(),
            ts=iso(utcnow()),
            kind=kind,
            agent_id=agent_id,
            payload=payload,
            mandate_id=mandate_id,
            envelope_id=envelope_id,
        )
        self._append(self._audit_path(), ev.to_dict())
        return ev

    def latest_mandates(self) -> dict[str, IntentMandate]:
        latest: dict[str, IntentMandate] = {}
        for row in self._read_jsonl(self._mandates):
            m = IntentMandate.from_dict(row)
            latest[m.mandate_id] = m
        return latest

    def latest_envelopes(self) -> dict[str, SpendEnvelope]:
        latest: dict[str, SpendEnvelope] = {}
        for row in self._read_jsonl(self._envelopes):
            e = SpendEnvelope.from_dict(row)
            latest[e.envelope_id] = e
        return latest

    def get_mandate(self, mandate_id: str) -> IntentMandate | None:
        return self.latest_mandates().get(mandate_id)

    def get_envelope(self, envelope_id: str) -> SpendEnvelope | None:
        return self.latest_envelopes().get(envelope_id)

    def active_mandate_for(self, agent_id: str) -> IntentMandate | None:
        now = utcnow()
        found: IntentMandate | None = None
        for m in self.latest_mandates().values():
            if m.agent_id != agent_id or m.killed:
                continue
            exp = datetime.fromisoformat(m.expires_at.replace("Z", "+00:00"))
            if exp <= now:
                continue
            found = m
        return found

    def iter_audit(self) -> Iterable[dict[str, Any]]:
        for path in sorted(self.root.glob("audit-*.jsonl")):
            yield from self._read_jsonl(path)
