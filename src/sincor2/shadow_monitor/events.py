"""Structured decision logging for SINCOR2 Phase 1 shadow-mode monitoring.

This module records exactly one structured event per task and per proposed
action while the shadow-mode monitor observes agent behavior.  Nothing here
executes, approves, or mutates live state -- it is the audit trail the
reviewers query before anything goes live.

Cardinality rule (metrics):
    High-cardinality identifiers -- trace_id, task_id, agent_id, tenant,
    human_owner, input_source_refs -- live ONLY in the JSONL log lines.
    They are NEVER used as metric label values.  The ``labels_safe_summary()``
    helper enforces this by returning only low-cardinality aggregates:
    counts by risk_tier, policy_result, and hour bucket.

Privacy rule:
    Logs hold references (``input_source_refs``), never content.  Full
    prompts and CRM records are never stored.  ``redact()`` tokenizes
    customer identifiers before they reach a string field, and
    ``validate_no_raw_pii()`` warns (does not raise) on append when obvious
    raw PII patterns are still present.

Integrity rule:
    The store is append-only.  Every line carries a chained SHA-256 hash
    (previous line hash + canonical payload); ``verify_chain()`` detects
    tampering and raises ``EventStoreTampered``.

Stdlib only: hashlib, json, uuid, datetime, threading, re, os, warnings.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Enumerated vocabularies
# ---------------------------------------------------------------------------

RISK_TIERS = ("low", "medium", "high", "critical")
DATA_CLASSIFICATIONS = ("public", "internal", "confidential", "restricted")
POLICY_RESULTS = ("allow", "deny", "needs_approval")
HUMAN_DISPOSITIONS = ("accepted", "edited", "rejected", "not_reviewed")

GENESIS_HASH = "GENESIS"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EventStoreTampered(Exception):
    """Raised by ``EventStore.verify_chain()`` when the hash chain breaks."""


class RawPIIWarning(UserWarning):
    """Emitted (not raised) when an event looks like it carries raw PII."""


# ---------------------------------------------------------------------------
# DecisionEvent
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DecisionEvent:
    """One structured record of a task decision or proposed action.

    Exactly one event is logged per task and per proposed action.  High-
    cardinality fields (trace_id, task_id, agent_id, tenant, ...) stay in the
    log lines; see the module docstring for the metrics cardinality rule.
    """

    task_id: str
    tenant: str
    agent_id: str
    human_owner: str
    model_version: str
    prompt_version: str
    config_version: str
    tool_version: str
    policy_version: str
    risk_tier: str
    data_classification: str
    input_source_refs: List[str]
    input_freshness: Dict[str, float]
    proposed_action: Dict[str, Any]
    policy_result: str
    policy_reason_codes: List[str]
    blocked_in_live_mode: bool

    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: str = field(default_factory=_utcnow_iso)
    human_disposition: str = "not_reviewed"
    outcome_evidence: Optional[Dict[str, Any]] = None
    latency_ms: Optional[float] = None
    seq: Optional[int] = None  # assigned by EventStore.append, per-stream

    def __post_init__(self) -> None:
        if self.risk_tier not in RISK_TIERS:
            raise ValueError(f"risk_tier must be one of {RISK_TIERS}, got {self.risk_tier!r}")
        if self.data_classification not in DATA_CLASSIFICATIONS:
            raise ValueError(
                f"data_classification must be one of {DATA_CLASSIFICATIONS}, "
                f"got {self.data_classification!r}"
            )
        if self.policy_result not in POLICY_RESULTS:
            raise ValueError(f"policy_result must be one of {POLICY_RESULTS}, got {self.policy_result!r}")
        if self.human_disposition not in HUMAN_DISPOSITIONS:
            raise ValueError(
                f"human_disposition must be one of {HUMAN_DISPOSITIONS}, "
                f"got {self.human_disposition!r}"
            )
        if not isinstance(self.blocked_in_live_mode, bool):
            raise ValueError("blocked_in_live_mode must be a bool")
        # Normalize proposed_action to the documented shape.
        pa = dict(self.proposed_action or {})
        self.proposed_action = {
            "kind": pa.get("kind"),
            "target": pa.get("target"),
            "estimated_cost": pa.get("estimated_cost"),
        }
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("latency_ms must be non-negative")

    # -- (de)serialization ---------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionEvent":
        return cls(**dict(data))

    def parsed_timestamp(self) -> datetime:
        ts = datetime.fromisoformat(self.timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_EMAIL_RE = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
# Phone-like: needs formatting evidence (separator, parens, or leading +) so
# bare digit runs fall through to the <id:...> branch instead.
_PHONE_RE = r"(?:\+\d{1,3}[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]\d{3}[-.\s]\d{4}|\+\d{10,14}"
_LONG_DIGITS_RE = r"\d{8,}"

_TOKENIZE_RE = re.compile(
    rf"(?P<email>{_EMAIL_RE})|(?P<phone>{_PHONE_RE})|(?P<idnum>{_LONG_DIGITS_RE})"
)


def _hash8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact(text: str) -> str:
    """Tokenize customer identifiers in free text.

    - emails            -> ``<email:hash8>``
    - phone-like values -> ``<phone:hash8>``
    - long digit runs   -> ``<id:hash8>`` (account/CRM/customer ids, ...)

    Single-pass substitution so inserted tokens are never re-matched.
    The 8-hex suffix is a one-way fingerprint for correlation, not a
    reversible encoding.
    """

    def _replace(match: re.Match) -> str:
        if match.group("email"):
            return f"<email:{_hash8(match.group('email'))}>"
        if match.group("phone"):
            return f"<phone:{_hash8(match.group('phone'))}>"
        return f"<id:{_hash8(match.group('idnum'))}>"

    return _TOKENIZE_RE.sub(_replace, text)


# Patterns consulted by the raw-PII gate (warn, never raise).
_PII_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("email", _EMAIL_RE),
    ("phone", _PHONE_RE),
    ("long_digit_string", r"\d{12,}"),  # card-ish / account-ish runs
    ("credit_card", r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"),
    ("ssn_like", r"\b\d{3}-\d{2}-\d{4}\b"),
)


def validate_no_raw_pii(event: DecisionEvent) -> List[str]:
    """Scan an event for obvious raw PII patterns.

    Returns a list of human-readable warnings (empty when clean).  Callers
    should surface these via :class:`RawPIIWarning`; the event is still
    appended -- the gate warns, it does not block.
    """
    findings: List[str] = []
    payload = event.to_dict()
    # trace_id is system-generated randomness (uuid4 hex), never customer data.
    payload.pop("trace_id", None)
    blob = json.dumps(payload, default=str)
    for name, pattern in _PII_PATTERNS:
        if re.search(pattern, blob):
            findings.append(
                f"possible raw {name} pattern in event trace_id={event.trace_id}; "
                "redact identifiers before logging"
            )
    return findings


# ---------------------------------------------------------------------------
# EventStore: append-only, hash-chained JSONL
# ---------------------------------------------------------------------------


def _canonical(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stream_key(tenant: str, agent_id: str) -> str:
    return f"{tenant}/{agent_id}"


class EventStore:
    """Append-only JSONL event store with a chained integrity hash.

    Each line: ``{"line_hash", "prev_hash", "stream", "seq", "event"}`` where
    ``seq`` is monotonically increasing *per (tenant, agent_id) stream* and
    ``line_hash = sha256(canonical(prev_hash, stream, seq, event))``.

    Thread-safe: all appends hold a single lock; writes are flushed and
    fsync'd before the lock is released.
    """

    def __init__(self, path: Union[str, os.PathLike]) -> None:
        self.path = os.fspath(path)
        self._lock = threading.Lock()
        self._last_hash = GENESIS_HASH
        self._stream_seqs: Dict[str, int] = {}
        self._replay()

    # -- internal ------------------------------------------------------------

    def _replay(self) -> None:
        """Rebuild chain head and per-stream sequence counters from disk."""
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                self._last_hash = record["line_hash"]
                key = record["stream"]
                self._stream_seqs[key] = max(self._stream_seqs.get(key, 0), record["seq"])

    @staticmethod
    def _line_hash(prev_hash: str, stream: str, seq: int, payload: Dict[str, Any]) -> str:
        body = {"prev_hash": prev_hash, "stream": stream, "seq": seq, "event": payload}
        return hashlib.sha256(_canonical(body)).hexdigest()

    def _write_record(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    # -- public API ----------------------------------------------------------

    def append(self, event: DecisionEvent) -> DecisionEvent:
        """Append an event; assigns its per-stream seq; returns the event.

        Emits :class:`RawPIIWarning` (never raises) if the payload looks like
        it carries raw PII.  Thread-safe and durable (flush + fsync).
        """
        for finding in validate_no_raw_pii(event):
            warnings.warn(finding, RawPIIWarning, stacklevel=3)
        key = _stream_key(event.tenant, event.agent_id)
        with self._lock:
            seq = self._stream_seqs.get(key, 0) + 1
            event.seq = seq
            payload = event.to_dict()
            prev = self._last_hash
            line_hash = self._line_hash(prev, key, seq, payload)
            self._write_record(
                {"line_hash": line_hash, "prev_hash": prev, "stream": key, "seq": seq, "event": payload}
            )
            self._last_hash = line_hash
            self._stream_seqs[key] = seq
        return event

    def get(self, trace_id: str) -> Optional[DecisionEvent]:
        """Return the event with this trace_id, or None."""
        for event in self.iter_events():
            if event.trace_id == trace_id:
                return event
        return None

    def iter_events(self) -> Iterator[DecisionEvent]:
        """Yield all events in append order."""
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                event = DecisionEvent.from_dict(record["event"])
                event.seq = record["seq"]  # store-assigned stream sequence
                yield event

    def count(self) -> int:
        """Number of events in the store."""
        return sum(1 for _ in self.iter_events())

    def verify_chain(self) -> bool:
        """Verify the hash chain; raises :class:`EventStoreTampered` on any break."""
        if not os.path.exists(self.path):
            return True
        prev = GENESIS_HASH
        with open(self.path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    body = {
                        "prev_hash": record["prev_hash"],
                        "stream": record["stream"],
                        "seq": record["seq"],
                        "event": record["event"],
                    }
                    expected = hashlib.sha256(_canonical(body)).hexdigest()
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise EventStoreTampered(f"line {lineno}: malformed record ({exc})") from exc
                if record["prev_hash"] != prev:
                    raise EventStoreTampered(
                        f"line {lineno}: prev_hash mismatch "
                        f"(expected {prev[:12]}..., got {record['prev_hash'][:12]}...)"
                    )
                if record["line_hash"] != expected:
                    raise EventStoreTampered(f"line {lineno}: line_hash mismatch (payload altered)")
                prev = record["line_hash"]
        return True

    def check_completeness(self) -> List[Dict[str, Any]]:
        """Audit-event completeness: find missing seq numbers per stream.

        Returns a list of ``{"tenant", "agent_id", "missing": [seqs]}``,
        one entry per stream with gaps.  Empty list means complete.
        """
        seen: Dict[str, set] = {}
        for event in self.iter_events():
            key = _stream_key(event.tenant, event.agent_id)
            seen.setdefault(key, set()).add(event.seq)
        gaps: List[Dict[str, Any]] = []
        for key, seqs in sorted(seen.items()):
            full = set(range(1, max(seqs) + 1))
            missing = sorted(full - seqs)
            if missing:
                tenant, agent_id = key.split("/", 1)
                gaps.append({"tenant": tenant, "agent_id": agent_id, "missing": missing})
        return gaps

    # -- queries -------------------------------------------------------------

    def by_agent(self, agent_id: str) -> List[DecisionEvent]:
        return [e for e in self.iter_events() if e.agent_id == agent_id]

    def by_policy_result(self, result: str) -> List[DecisionEvent]:
        if result not in POLICY_RESULTS:
            raise ValueError(f"policy_result must be one of {POLICY_RESULTS}, got {result!r}")
        return [e for e in self.iter_events() if e.policy_result == result]

    def by_time_range(
        self, start: Union[str, datetime], end: Union[str, datetime]
    ) -> List[DecisionEvent]:
        def _as_dt(value: Union[str, datetime]) -> datetime:
            dt = datetime.fromisoformat(value) if isinstance(value, str) else value
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        start_dt, end_dt = _as_dt(start), _as_dt(end)
        return [e for e in self.iter_events() if start_dt <= e.parsed_timestamp() <= end_dt]

    def pending_review(self) -> List[DecisionEvent]:
        """Events whose human_disposition is still ``not_reviewed``."""
        return [e for e in self.iter_events() if e.human_disposition == "not_reviewed"]

    # -- metrics-safe summary ------------------------------------------------

    def labels_safe_summary(self) -> Dict[str, Any]:
        """Low-cardinality aggregate only: safe to expose as metric labels.

        Returns counts by ``risk_tier``, ``policy_result``, and hour bucket.
        High-cardinality identifiers (trace_id, task_id, agent_id, tenant,
        human_owner, refs) never appear here -- they stay in the log lines.
        """
        by_risk: Dict[str, int] = {tier: 0 for tier in RISK_TIERS}
        by_result: Dict[str, int] = {res: 0 for res in POLICY_RESULTS}
        by_hour: Dict[str, int] = {}
        total = 0
        for event in self.iter_events():
            total += 1
            by_risk[event.risk_tier] = by_risk.get(event.risk_tier, 0) + 1
            by_result[event.policy_result] = by_result.get(event.policy_result, 0) + 1
            hour = event.parsed_timestamp().strftime("%Y-%m-%dT%H:00Z")
            by_hour[hour] = by_hour.get(hour, 0) + 1
        return {
            "total": total,
            "by_risk_tier": by_risk,
            "by_policy_result": by_result,
            "by_hour": by_hour,
        }
