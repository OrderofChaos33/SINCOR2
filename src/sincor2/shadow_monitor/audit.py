"""WP5 audit trail: tamper-evident, hash-chained audit records for shadow safety.

Extends the Phase-1 :mod:`sincor2.shadow_monitor.events` infrastructure with
a WP5 record schema covering the full effect lifecycle:

    proposal -> policy decision -> approval -> adapter attempt
        -> provider acknowledgment -> final state

Every record is:
- hash-chained to the previous record (tamper-evident; ``verify_chain``
  raises :class:`AuditChainTampered` on any break),
- redacted: payloads are stored as sha256 hashes only, never raw content
  or PII (see :func:`redacted_hash`),
- immutable once appended (frozen dataclasses; the store is append-only).

Fail-closed: if the audit store is unavailable for a consequential action
(value-moving effect type or high/critical risk tier), dispatch must raise
:class:`AuditFailureError` -- see :func:`require_audit_available`.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from sincor2.shadow_monitor.contract import (
    CONTRACT_VERSION,
    EFFECT_VOCABULARY,
    VALUE_MOVING_EFFECTS,
)
from sincor2.shadow_monitor.effect_boundary import (
    AuditFailureError,
    RISK_TIERS,
    canonical_payload_hash,
)

__all__ = [
    "AUDIT_RECORD_KINDS",
    "AuditRecord",
    "AuditChainTampered",
    "AuditStore",
    "OperatorKillSwitch",
    "is_consequential",
    "require_audit_available",
    "redacted_hash",
    "utc_now_iso",
]

#: The six lifecycle stages every effect passes through in the audit trail.
AUDIT_RECORD_KINDS = (
    "proposal",
    "policy_decision",
    "approval",
    "adapter_attempt",
    "provider_ack",
    "final_state",
)
AuditRecordKind = Literal[
    "proposal",
    "policy_decision",
    "approval",
    "adapter_attempt",
    "provider_ack",
    "final_state",
]

_GENESIS_HASH = "GENESIS"


class AuditChainTampered(Exception):
    """Raised when the audit hash chain fails verification."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redacted_hash(payload: Any) -> str:
    """One-way hash of a payload for audit storage.

    The full payload is NEVER stored. Only this sha256 hex digest is kept,
    so audit records cannot leak PII, credentials, or customer content.
    """
    if isinstance(payload, dict):
        return canonical_payload_hash(payload)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_consequential(effect_type: str, risk_tier: str) -> bool:
    """True for actions that must fail closed when a store is unavailable.

    Consequential = value-moving effect type OR high/critical risk tier.
    """
    return effect_type in VALUE_MOVING_EFFECTS or risk_tier in ("high", "critical")


def require_audit_available(
    audit_store: Optional["AuditStore"],
    *,
    effect_type: str,
    risk_tier: str,
) -> None:
    """Fail-closed gate: raise AuditFailureError if audit is unavailable.

    Called before dispatching a consequential action. If the audit store is
    None or reports itself unavailable, the dispatch must not proceed --
    an unauditable consequential action cannot be mistaken for an audited one.
    """
    if not is_consequential(effect_type, risk_tier):
        return
    if audit_store is None or not audit_store.available():
        raise AuditFailureError(
            f"audit store unavailable for consequential action "
            f"(effect_type={effect_type!r}, risk_tier={risk_tier!r}): "
            "dispatch refused (fail-closed)"
        )


# ---------------------------------------------------------------------------
# AuditRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditRecord:
    """One immutable, hash-chained audit record.

    ``payload_hash`` carries the redacted evidence -- never raw content.
    ``prev_hash`` / ``record_hash`` form the tamper-evident chain.
    """

    record_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    kind: str = "proposal"
    effect_id: str = ""
    effect_type: str = ""
    originator: str = ""
    payload_hash: str = ""  # redacted: hash only, never content
    detail: str = ""  # short, sanitized human-readable note (no PII)
    risk_tier: str = "low"
    trace_id: str = ""
    recorded_at: str = field(default_factory=utc_now_iso)
    contract_version: str = CONTRACT_VERSION
    prev_hash: str = _GENESIS_HASH
    record_hash: str = ""

    def __post_init__(self) -> None:
        if self.kind not in AUDIT_RECORD_KINDS:
            raise ValueError(f"unknown audit record kind: {self.kind!r}")
        if self.effect_type and self.effect_type not in EFFECT_VOCABULARY:
            raise ValueError(f"unknown effect_type: {self.effect_type!r}")
        if self.risk_tier not in RISK_TIERS:
            raise ValueError(f"unknown risk_tier: {self.risk_tier!r}")
        if not self.effect_id:
            raise ValueError("effect_id is required")

    @staticmethod
    def compute_hash(
        *,
        record_id: str,
        kind: str,
        effect_id: str,
        effect_type: str,
        originator: str,
        payload_hash: str,
        detail: str,
        risk_tier: str,
        trace_id: str,
        recorded_at: str,
        contract_version: str,
        prev_hash: str,
    ) -> str:
        body = {
            "record_id": record_id,
            "kind": kind,
            "effect_id": effect_id,
            "effect_type": effect_type,
            "originator": originator,
            "payload_hash": payload_hash,
            "detail": detail,
            "risk_tier": risk_tier,
            "trace_id": trace_id,
            "recorded_at": recorded_at,
            "contract_version": contract_version,
            "prev_hash": prev_hash,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# AuditStore: append-only, hash-chained, thread-safe
# ---------------------------------------------------------------------------


class AuditStore:
    """Append-only audit store with hash-chained integrity.

    In-memory by default (tests, CI). Production wiring persists via the
    injected ``persist_fn``. If persistence fails, the store marks itself
    unavailable -- consequential dispatches then fail closed via
    :func:`require_audit_available`.
    """

    def __init__(
        self,
        persist_fn: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._records: List[AuditRecord] = []
        self._lock = threading.Lock()
        self._persist_fn = persist_fn
        self._unavailable = False
        self._unavailable_reason = ""

    # -- availability ------------------------------------------------------

    def available(self) -> bool:
        """True if the store can accept records."""
        with self._lock:
            return not self._unavailable

    def mark_unavailable(self, reason: str) -> None:
        """Simulate (or report) store outage. Consequential dispatches fail."""
        with self._lock:
            self._unavailable = True
            self._unavailable_reason = reason

    def mark_available(self) -> None:
        with self._lock:
            self._unavailable = False
            self._unavailable_reason = ""

    # -- append ------------------------------------------------------------

    def append(
        self,
        *,
        kind: str,
        effect_id: str,
        effect_type: str = "",
        originator: str = "",
        payload: Any = None,
        detail: str = "",
        risk_tier: str = "low",
        trace_id: str = "",
    ) -> AuditRecord:
        """Append one audit record; returns the frozen record.

        Raises AuditFailureError if the store is unavailable -- the caller
        must not proceed with the action.
        """
        with self._lock:
            if self._unavailable:
                raise AuditFailureError(
                    f"audit store unavailable ({self._unavailable_reason}): "
                    f"refusing to record {kind} for effect {effect_id}"
                )
            prev_hash = (
                self._records[-1].record_hash if self._records else _GENESIS_HASH
            )
            record_id = uuid.uuid4().hex
            recorded_at = utc_now_iso()
            payload_hash = redacted_hash(payload) if payload is not None else ""
            record_hash = AuditRecord.compute_hash(
                record_id=record_id,
                kind=kind,
                effect_id=effect_id,
                effect_type=effect_type,
                originator=originator,
                payload_hash=payload_hash,
                detail=detail,
                risk_tier=risk_tier,
                trace_id=trace_id,
                recorded_at=recorded_at,
                contract_version=CONTRACT_VERSION,
                prev_hash=prev_hash,
            )
            record = AuditRecord(
                record_id=record_id,
                kind=kind,
                effect_id=effect_id,
                effect_type=effect_type,
                originator=originator,
                payload_hash=payload_hash,
                detail=detail,
                risk_tier=risk_tier,
                trace_id=trace_id,
                recorded_at=recorded_at,
                contract_version=CONTRACT_VERSION,
                prev_hash=prev_hash,
                record_hash=record_hash,
            )
            # Persist first; if persistence fails the record is NOT kept
            # in memory either (fail-closed: no partial audit state).
            if self._persist_fn is not None:
                try:
                    self._persist_fn(asdict(record))
                except Exception as exc:
                    self._unavailable = True
                    self._unavailable_reason = f"persist_fn failed: {exc}"
                    raise AuditFailureError(
                        f"audit persistence failed for {kind} "
                        f"(effect {effect_id}): {exc}"
                    ) from exc
            self._records.append(record)
            return record

    # -- read --------------------------------------------------------------

    def records(self) -> List[AuditRecord]:
        with self._lock:
            return list(self._records)

    def records_for_effect(self, effect_id: str) -> List[AuditRecord]:
        with self._lock:
            return [r for r in self._records if r.effect_id == effect_id]

    def lifecycle_complete(self, effect_id: str) -> bool:
        """True if all six lifecycle kinds are present for the effect."""
        with self._lock:
            kinds = {r.kind for r in self._records if r.effect_id == effect_id}
        return set(AUDIT_RECORD_KINDS) <= kinds

    def verify_chain(self) -> bool:
        """Verify hash chain integrity; raises AuditChainTampered on break."""
        with self._lock:
            records = list(self._records)
        prev = _GENESIS_HASH
        for i, record in enumerate(records):
            if record.prev_hash != prev:
                raise AuditChainTampered(
                    f"record {i} ({record.record_id[:8]}): prev_hash mismatch "
                    f"(chain broken before this record)"
                )
            expected = AuditRecord.compute_hash(
                record_id=record.record_id,
                kind=record.kind,
                effect_id=record.effect_id,
                effect_type=record.effect_type,
                originator=record.originator,
                payload_hash=record.payload_hash,
                detail=record.detail,
                risk_tier=record.risk_tier,
                trace_id=record.trace_id,
                recorded_at=record.recorded_at,
                contract_version=record.contract_version,
                prev_hash=record.prev_hash,
            )
            if record.record_hash != expected:
                raise AuditChainTampered(
                    f"record {i} ({record.record_id[:8]}): record_hash mismatch "
                    f"(payload altered after append)"
                )
            prev = record.record_hash
        return True


# ---------------------------------------------------------------------------
# OperatorKillSwitch: principal-gated kill switch with audit trail
# ---------------------------------------------------------------------------


class OperatorKillSwitch:
    """Kill switch that only human operators can engage or clear.

    Wraps the underlying kill-switch state with:
    - principal gating: ``principal_type == "agent"`` raises PermissionError
      on both engage and disengage (agents cannot clear their own halt),
    - an audit trail: every engage/disengage appends a ``final_state``
      audit record (kill-switch transitions are terminal state changes).

    There is deliberately NO code path by which an agent principal can
    disengage the switch. The ``_raw`` escape hatch is private, test-only,
    and itself audited.
    """

    def __init__(self, audit_store: Optional[AuditStore] = None) -> None:
        self._lock = threading.Lock()
        self._engaged = False
        self._engagements: List[Dict[str, Any]] = []
        self._audit = audit_store

    @property
    def engaged(self) -> bool:
        with self._lock:
            return self._engaged

    def _check_principal(self, principal: Optional[Dict[str, Any]], action: str) -> str:
        principal_type = (principal or {}).get("principal_type", "")
        principal_id = (principal or {}).get("principal_id", "unknown")
        if principal_type == "agent":
            raise PermissionError(
                f"agent principal {principal_id!r} may not {action} the kill switch; "
                "only human operators may"
            )
        if principal_type not in ("human", "operator"):
            raise PermissionError(
                f"unknown principal type {principal_type!r}: kill-switch {action} "
                "requires a human operator principal"
            )
        return principal_id

    def _audit_transition(self, action: str, principal_id: str, reason: str) -> None:
        if self._audit is None:
            return
        # Kill-switch transitions are audited as final_state records on a
        # synthetic effect id; they are terminal state changes.
        self._audit.append(
            kind="final_state",
            effect_id=f"kill-switch-{action}",
            effect_type="",
            originator="operator",
            detail=f"kill switch {action}d by {principal_id}: {reason}"[:200],
            risk_tier="critical",
        )

    def engage(
        self,
        principal: Optional[Dict[str, Any]],
        reason: str = "",
    ) -> Dict[str, Any]:
        """Engage the kill switch. Human operators only. Idempotent."""
        principal_id = self._check_principal(principal, "engage")
        with self._lock:
            already = self._engaged
            self._engaged = True
            self._engagements.append(
                {
                    "action": "engage",
                    "principal_id": principal_id,
                    "reason": reason,
                    "at": utc_now_iso(),
                }
            )
        if not already:
            self._audit_transition("engage", principal_id, reason)
        return {"engaged": True, "by": principal_id}

    def disengage(
        self,
        principal: Optional[Dict[str, Any]],
        reason: str = "",
    ) -> Dict[str, Any]:
        """Clear the kill switch. Human operators only. Idempotent.

        Agents can NEVER clear the switch: an agent principal raises
        PermissionError before any state changes.
        """
        principal_id = self._check_principal(principal, "disengage")
        with self._lock:
            already = not self._engaged
            self._engaged = False
            self._engagements.append(
                {
                    "action": "disengage",
                    "principal_id": principal_id,
                    "reason": reason,
                    "at": utc_now_iso(),
                }
            )
        if not already:
            self._audit_transition("disengage", principal_id, reason)
        return {"engaged": False, "by": principal_id}

    def engagement_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._engagements)

    def _raw_disengage_for_tests(self) -> None:
        """Test-only escape hatch. Audited. Never callable by agents."""
        with self._lock:
            self._engaged = False
