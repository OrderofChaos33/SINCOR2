"""Asynchronous dispute resolution with pluggable arbitration oracles.

CNP-7: the synchronous validator-vote path in ``dispute_resolution.py``
resolves a dispute in one step. This module adds the offchain async
lifecycle used before an oracle/adjudicator ruling lands:

    FILED -> EVIDENCE_OPEN -> RESOLVED            (oracle ruling applied)
                            -> AUTO_RESOLVED      (evidence-window timeout)

Security posture (fail-closed, no fiction):

* Every state transition is validated and appended to the dispute's event
  log; invalid transitions raise and change nothing.
* Only one open dispute may exist per (task_id, agent_id) — repeated
  filings (griefing) are rejected, not deduplicated silently.
* Oracle rulings are verified on the oracle path with secp256k1 EIP-191
  signatures before they can move a dispute. A ruling binds the dispute_id
  it was issued for; consumed rulings are single-use, so replays are
  rejected.
* Timeout auto-resolution defaults to "not upheld": without an
  adjudicator-signed ruling there is no slashing. Slashing is on the money
  path and must never be inferred from silence.
* All inputs are validated (lengths, hex formats, clocks, bounds); any
  invalid input raises without changing state.

The canonical EIP-191 message mirrors the authenticated
``POST /v1/a2a/disputes`` route in ``sincor2.a2a_inbound_market`` with an
additional dispute-id binding::

    SINCOR-DISPUTE|<dispute_id>|<task_id>|<agent_id>|<1|0>|<adjudicator>|<expires_at_ms>
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Evidence-window default: 24h in ms. Kept as a constructor default so tests
# can inject short windows; production passes the ratified policy value.
DEFAULT_EVIDENCE_WINDOW_MS = 24 * 60 * 60 * 1000
# Oracle ruling freshness bound, mirroring DISPUTE_MAX_SKEW_MS on the route:
# rulings may not expire more than 15 minutes in the future.
RULING_MAX_SKEW_MS = 15 * 60 * 1000
# Anti-griefing caps.
MAX_EVIDENCE_ITEMS = 16
MAX_REASON_LEN = 512
MAX_URI_LEN = 2048
MAX_ID_LEN = 128


class DisputeState(str, Enum):
    FILED = "filed"
    EVIDENCE_OPEN = "evidence_open"
    RESOLVED = "resolved"
    AUTO_RESOLVED = "auto_resolved"

    @property
    def terminal(self) -> bool:
        return self in (DisputeState.RESOLVED, DisputeState.AUTO_RESOLVED)


class DisputeError(Exception):
    """Base class for all async-dispute errors."""


class InvalidDisputeInput(DisputeError):
    """Fail-closed: malformed input; no state was changed."""


class InvalidTransition(DisputeError):
    """Fail-closed: transition not allowed from the current state."""


class DuplicateDispute(DisputeError):
    """An open dispute already exists for this (task_id, agent_id)."""

    def __init__(self, existing_dispute_id: str) -> None:
        super().__init__(
            f"open dispute already exists: {existing_dispute_id}")
        self.existing_dispute_id = existing_dispute_id


class InvalidRuling(DisputeError):
    """Oracle ruling rejected: signature, binding, freshness, or replay."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DisputeEvent:
    ts_ms: int
    kind: str            # "transition" or "note"
    from_state: str
    to_state: str
    detail: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "kind": self.kind,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EvidenceItem:
    evidence_id: str
    submitter_id: str
    uri: str
    content_hash: str  # "0x" + 64 hex chars (keccak/sha256 of evidence bytes)
    ts_ms: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "submitter_id": self.submitter_id,
            "uri": self.uri,
            "content_hash": self.content_hash,
            "ts_ms": self.ts_ms,
        }


@dataclass(frozen=True)
class OracleRuling:
    """An adjudicator-signed ruling submitted through an oracle adapter.

    ``dispute_id`` binds the ruling to exactly one dispute; ``signature``
    is the EIP-191 signature of the canonical message
    ``SINCOR-DISPUTE|<dispute_id>|<task_id>|<agent_id>|<1|0>|<adjudicator>|<expires_at_ms>``.
    """

    dispute_id: str
    task_id: str
    agent_id: str
    upheld: bool
    adjudicator_address: str
    expires_at_ms: int
    signature: str


# ---------------------------------------------------------------------------
# Oracle adapter interface
# ---------------------------------------------------------------------------

class ArbitrationOracleAdapter(ABC):
    """Pluggable arbitration oracle.

    Implementations translate an external arbitration decision into a
    verified :class:`OracleRuling`. The registry calls ``verify_ruling``
    before a ruling can resolve anything; a ``False`` return keeps the
    dispute exactly where it was (fail-closed).
    """

    name: str = "base"

    @abstractmethod
    def verify_ruling(self, ruling: OracleRuling) -> bool:
        """Return True only if the ruling is authentic, fresh, and bound."""
        raise NotImplementedError


def oracle_message(ruling: OracleRuling) -> str:
    """Canonical EIP-191 message the adjudicator signs."""
    return "|".join([
        "SINCOR-DISPUTE",
        ruling.dispute_id,
        ruling.task_id,
        ruling.agent_id,
        "1" if ruling.upheld else "0",
        ruling.adjudicator_address.lower(),
        str(ruling.expires_at_ms),
    ])


def recover_message_signer(message: str, signature: str) -> str:
    """Recover the EIP-191 signer address. Raises on any failure."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except Exception as exc:
        raise InvalidRuling(
            "signature verification unavailable") from exc
    try:
        return str(Account.recover_message(
            encode_defunct(text=message), signature=signature))
    except Exception as exc:
        raise InvalidRuling("bad signature") from exc


class AdjudicatorOracleAdapter(ArbitrationOracleAdapter):
    """Default oracle: rulings signed by the configured adjudicator.

    Reuses the adjudicator-signed ruling scheme of the authenticated
    ``POST /v1/a2a/disputes`` route (secp256k1 EIP-191; HMAC is demo-only
    per the ratified auction-security decision and is not accepted here).
    The expected adjudicator address comes from ``expected_adjudicator``
    (or the ``SINCOR_ADJUDICATOR_ID`` env var when not given).
    """

    name = "adjudicator"

    def __init__(self, expected_adjudicator: Optional[str] = None,
                 *,
                 now_ms: Optional[Callable[[], int]] = None) -> None:
        import os
        adjudicator = (expected_adjudicator
                       or os.environ.get("SINCOR_ADJUDICATOR_ID", "")).strip()
        if not adjudicator:
            raise InvalidDisputeInput(
                "adjudicator not configured: pass expected_adjudicator or "
                "set SINCOR_ADJUDICATOR_ID")
        self.expected_adjudicator = adjudicator.lower()
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    def verify_ruling(self, ruling: OracleRuling) -> bool:
        try:
            self._verify(ruling)
        except DisputeError:
            return False
        return True

    def _verify(self, ruling: OracleRuling) -> None:
        now = self._now_ms()
        expires = int(ruling.expires_at_ms)
        if expires <= now:
            raise InvalidRuling("ruling expired")
        if expires - now > RULING_MAX_SKEW_MS:
            raise InvalidRuling("expires_at_ms too far in the future")
        if ruling.adjudicator_address.lower() != self.expected_adjudicator:
            raise InvalidRuling("not the adjudicator")
        message = oracle_message(ruling)
        signer = recover_message_signer(message, ruling.signature)
        if signer.lower() != self.expected_adjudicator:
            # Never reveal which check failed; both reject.
            raise InvalidRuling("not the adjudicator")


# ---------------------------------------------------------------------------
# Async dispute
# ---------------------------------------------------------------------------

def _validate_id(value: str, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise InvalidDisputeInput(f"{name} is required")
    if len(value) > MAX_ID_LEN:
        raise InvalidDisputeInput(f"{name} too long")
    return value


def _validate_hash(value: str) -> str:
    value = str(value or "").strip().lower()
    if len(value) != 66 or not value.startswith("0x"):
        raise InvalidDisputeInput(
            "content_hash must be 0x-prefixed 32-byte hex")
    try:
        int(value, 16)
    except ValueError as exc:
        raise InvalidDisputeInput(
            "content_hash must be 0x-prefixed 32-byte hex") from exc
    return value


class AsyncDispute:
    """One dispute moving through the async lifecycle.

    Created via :meth:`DisputeRegistry.file_dispute`; mutate only through
    the registry so every transition is validated and logged.
    """

    def __init__(self, *, dispute_id: str, task_id: str, agent_id: str,
                 filer_id: str, reason: str, now_ms: int) -> None:
        self.dispute_id = _validate_id(dispute_id, "dispute_id")
        self.task_id = _validate_id(task_id, "task_id")
        self.agent_id = _validate_id(agent_id, "agent_id")
        self.filer_id = _validate_id(filer_id, "filer_id")
        reason = str(reason or "").strip()
        if not reason:
            raise InvalidDisputeInput("reason is required")
        if len(reason) > MAX_REASON_LEN:
            raise InvalidDisputeInput("reason too long")
        self.reason = reason
        self.state = DisputeState.FILED
        self.evidence_deadline_ms: Optional[int] = None
        self.evidence: List[EvidenceItem] = []
        self.events: List[DisputeEvent] = []
        self.outcome_upheld: Optional[bool] = None
        self._log("transition", DisputeState.FILED, DisputeState.FILED,
                  f"filed by {self.filer_id}: {self.reason}", now_ms)

    # -- internals -------------------------------------------------------
    def _log(self, kind: str, from_state: DisputeState, to_state: DisputeState,
             detail: str, now_ms: int) -> None:
        event = DisputeEvent(ts_ms=now_ms, kind=kind,
                             from_state=from_state.value,
                             to_state=to_state.value, detail=detail)
        self.events.append(event)
        log.info("dispute %s: %s -> %s (%s)",
                 self.dispute_id, from_state.value, to_state.value, detail)

    def _transition(self, to_state: DisputeState, detail: str,
                    now_ms: int) -> None:
        from_state = self.state
        self.state = to_state
        self._log("transition", from_state, to_state, detail, now_ms)

    # -- read-only views --------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "dispute_id": self.dispute_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "filer_id": self.filer_id,
            "reason": self.reason,
            "state": self.state.value,
            "evidence_deadline_ms": self.evidence_deadline_ms,
            "evidence": [e.as_dict() for e in self.evidence],
            "outcome_upheld": self.outcome_upheld,
            "events": [e.as_dict() for e in self.events],
        }


# ---------------------------------------------------------------------------
# Registry: lifecycle engine + anti-griefing + timeouts
# ---------------------------------------------------------------------------

class DisputeRegistry:
    """Owns all async disputes and enforces the lifecycle.

    ``now_ms`` may be injected for deterministic tests; defaults to the
    wall clock.
    """

    def __init__(self, *,
                 evidence_window_ms: int = DEFAULT_EVIDENCE_WINDOW_MS,
                 auto_resolve_upheld: bool = False,
                 now_ms: Optional[Callable[[], int]] = None) -> None:
        if int(evidence_window_ms) <= 0:
            raise InvalidDisputeInput("evidence_window_ms must be positive")
        self.evidence_window_ms = int(evidence_window_ms)
        # Fail-closed default: timeout without an adjudicator ruling never
        # slashes (no silent "upheld").
        self.auto_resolve_upheld = bool(auto_resolve_upheld)
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.Lock()
        self._disputes: Dict[str, AsyncDispute] = {}
        self._open_by_pair: Dict[tuple, str] = {}
        self._consumed_rulings: set = set()  # EIP-191 signature digests

    # -- filing -----------------------------------------------------------
    def file_dispute(self, *, task_id: str, agent_id: str, filer_id: str,
                     reason: str) -> AsyncDispute:
        """Open a dispute; exactly one open dispute per (task, agent)."""
        task_id = _validate_id(task_id, "task_id")
        agent_id = _validate_id(agent_id, "agent_id")
        with self._lock:
            key = (task_id, agent_id)
            existing = self._open_by_pair.get(key)
            if existing is not None:
                raise DuplicateDispute(existing)
            dispute = AsyncDispute(
                dispute_id=uuid.uuid4().hex, task_id=task_id,
                agent_id=agent_id, filer_id=filer_id, reason=reason,
                now_ms=self._now_ms())
            self._disputes[dispute.dispute_id] = dispute
            self._open_by_pair[key] = dispute.dispute_id
            return dispute

    def open_evidence_window(self, dispute_id: str) -> AsyncDispute:
        """FILED -> EVIDENCE_OPEN; sets the evidence deadline."""
        with self._lock:
            dispute = self._get(dispute_id)
            if dispute.state is not DisputeState.FILED:
                raise InvalidTransition(
                    f"cannot open evidence window from {dispute.state.value}")
            now = self._now_ms()
            dispute.evidence_deadline_ms = now + self.evidence_window_ms
            dispute._transition(DisputeState.EVIDENCE_OPEN,
                                f"evidence window open for "
                                f"{self.evidence_window_ms}ms", now)
            return dispute

    # -- evidence ----------------------------------------------------------
    def submit_evidence(self, dispute_id: str, *, submitter_id: str,
                        uri: str, content_hash: str) -> EvidenceItem:
        """Attach evidence during the window; stalled windows reject."""
        with self._lock:
            dispute = self._get(dispute_id)
            now = self._now_ms()
            if dispute.state is not DisputeState.EVIDENCE_OPEN:
                raise InvalidTransition(
                    f"cannot submit evidence in {dispute.state.value}")
            if (dispute.evidence_deadline_ms is not None
                    and now > dispute.evidence_deadline_ms):
                raise InvalidTransition("evidence window closed")
            submitter_id = _validate_id(submitter_id, "submitter_id")
            uri = str(uri or "").strip()
            if not uri or len(uri) > MAX_URI_LEN:
                raise InvalidDisputeInput("uri required, max "
                                         f"{MAX_URI_LEN} chars")
            content_hash = _validate_hash(content_hash)
            if len(dispute.evidence) >= MAX_EVIDENCE_ITEMS:
                raise InvalidDisputeInput(
                    f"evidence cap reached ({MAX_EVIDENCE_ITEMS})")
            item = EvidenceItem(evidence_id=uuid.uuid4().hex[:16],
                                submitter_id=submitter_id, uri=uri,
                                content_hash=content_hash, ts_ms=now)
            dispute.evidence.append(item)
            dispute._log("note", dispute.state, dispute.state,
                         f"evidence {item.evidence_id} by {submitter_id}",
                         now)
            return item

    # -- oracle resolution ---------------------------------------------------
    def apply_oracle_ruling(self, ruling: OracleRuling,
                            adapter: ArbitrationOracleAdapter) -> AsyncDispute:
        """Verify the ruling via the adapter, then EVIDENCE_OPEN -> RESOLVED.

        Fail-closed: any verification failure raises InvalidRuling and the
        dispute state is untouched.
        """
        with self._lock:
            dispute = self._get(ruling.dispute_id)
            now = self._now_ms()
            if dispute.state is not DisputeState.EVIDENCE_OPEN:
                raise InvalidTransition(
                    f"cannot apply ruling in {dispute.state.value}")
            if (dispute.evidence_deadline_ms is not None
                    and now > dispute.evidence_deadline_ms):
                raise InvalidTransition(
                    "evidence window expired; run process_timeouts")
            self._validate_ruling_binding(dispute, ruling)
            digest = str(ruling.signature or "").strip().lower()
            if digest in self._consumed_rulings:
                raise InvalidRuling("ruling already consumed (replay)")
            if not adapter.verify_ruling(ruling):
                dispute._log("note", dispute.state, dispute.state,
                             f"ruling rejected by oracle {adapter.name}", now)
                raise InvalidRuling(
                    f"oracle {adapter.name} rejected the ruling")
            self._consumed_rulings.add(digest)
            dispute.outcome_upheld = bool(ruling.upheld)
            dispute._transition(
                DisputeState.RESOLVED,
                f"oracle {adapter.name} ruling applied: "
                f"upheld={dispute.outcome_upheld}", now)
            self._open_by_pair.pop((dispute.task_id, dispute.agent_id), None)
            return dispute

    @staticmethod
    def _validate_ruling_binding(dispute: AsyncDispute,
                                 ruling: OracleRuling) -> None:
        if ruling.task_id != dispute.task_id:
            raise InvalidRuling("ruling task_id does not match dispute")
        if ruling.agent_id != dispute.agent_id:
            raise InvalidRuling("ruling agent_id does not match dispute")
        if not str(ruling.signature or "").strip():
            raise InvalidRuling("ruling signature required")

    # -- timeouts ------------------------------------------------------------
    def process_timeouts(self, now_ms: Optional[int] = None) -> List[AsyncDispute]:
        """Auto-resolve every EVIDENCE_OPEN dispute past its deadline.

        Auto-resolution defaults to not-upheld (fail-closed: no ruling, no
        slash). Returns the disputes that were auto-resolved.
        """
        resolved: List[AsyncDispute] = []
        now = now_ms if now_ms is not None else self._now_ms()
        with self._lock:
            for dispute in self._disputes.values():
                if dispute.state is not DisputeState.EVIDENCE_OPEN:
                    continue
                if (dispute.evidence_deadline_ms is not None
                        and now > dispute.evidence_deadline_ms):
                    dispute.outcome_upheld = self.auto_resolve_upheld
                    dispute._transition(
                        DisputeState.AUTO_RESOLVED,
                        "evidence window expired; auto-resolved "
                        f"upheld={dispute.outcome_upheld}", now)
                    self._open_by_pair.pop(
                        (dispute.task_id, dispute.agent_id), None)
                    resolved.append(dispute)
        return resolved

    # -- reads ---------------------------------------------------------------
    def get(self, dispute_id: str) -> AsyncDispute:
        with self._lock:
            return self._get(dispute_id)

    def list_open(self) -> List[AsyncDispute]:
        with self._lock:
            return [d for d in self._disputes.values()
                    if not d.state.terminal]

    def _get(self, dispute_id: str) -> AsyncDispute:
        dispute_id = _validate_id(dispute_id, "dispute_id")
        try:
            return self._disputes[dispute_id]
        except KeyError as exc:
            raise InvalidDisputeInput("unknown dispute_id") from exc
