"""Phase 1 escalation layer — deterministic policy enforcement + HITL routing.

Implements the founder's Strategic Roadmap Directive (Nov 2026), Phase 1,
in code, under the Pinned AI Guardrail Document v1.0 (2026-10-31). This
layer *implements* the guardrail document; it never weakens it. One
deterministic layer sits between agent reasoning and execution:

1. **PolicyEngine** classifies every consequential action. Hard-gate
   categories (treasury/funds, merges, deploys, private keys,
   legal/compliance, external sends, production actions, withdrawals)
   NEVER auto-allow — they escalate to a human operator. Detector rule
   hits (e.g. a sanctions-screen OFAC match) and spend-threshold
   breaches escalate with the specific rule stamped on the envelope.
   Unknown categories fail closed → escalate. Only known reversible
   internal categories auto-allow, and only when the context carries no
   irreversibility / external-effect signal.

2. **EscalationEnvelope** — the Canonical Escalation Envelope Schema
   (version ``escalation-envelope/1.0``) — is the one standard context
   package every surface raises: who (source agent + actor), which
   surface, the triggered rule that fired, a state snapshot (the
   proposed action's input parameters + the dry-run state it would
   produce — the proof payload the human reviews), evidence refs, and
   the decision record. The internal dataclass is named
   :class:`EscalationPackage`; the serialized schema and all docs use
   EscalationEnvelope. The envelope carries ``action_choices`` as data:
   exactly ``["approve", "override", "escalate"]`` — the route the
   operator picks. ``evidence_refs`` stays a top-level envelope field
   (not nested in ``state_snapshot``): refs identify *external* proof
   artifacts, while the snapshot is the action's own inputs/dry-run.

3. **Decision route** — the operator answers APPROVE / OVERRIDE /
   ESCALATE / DENY (override = proceed with a recorded modification or
   dissent note; the note is mandatory). States:
   ``PENDING → APPROVED | OVERRIDDEN | ESCALATED | DENIED | EXPIRED``.
   Every non-PENDING state is terminal for agents. ESCALATED is terminal
   here by design: the founder is the final authority and decides out of
   band; his decision re-enters as a *new* package/decision record, never
   by mutating an escalated package back open.

4. **Gate hook** — execution code calls :func:`require_decision`. Unless
   the package state is APPROVED or OVERRIDDEN, the action does not run.
   No package, no execution. The hook returns ``False`` (fail closed)
   rather than raising, matching the repo's boolean guard idiom.

**Posture: Default-Deny.** This is the layer's named, load-bearing
posture. An action executes ONLY if (a) it satisfies deterministic
policy parameters — a PolicyEngine ALLOW, which exists solely for
known reversible internal categories in a clean context — or (b) it
carries a HITL authorization decision (an envelope in state APPROVED
or OVERRIDDEN, co-sign quorum included where required). There is no
third path: no default-allow, no timeout-allow, no "probably fine".

**Co-sign quorum.** When the envelope's ``risk.required_signers`` is
non-empty, a single decision does NOT open the gate: every required
signer role must attest the same decision kind, from distinct actors,
before the package transitions. Decisions from roles not in the list
are recorded as advisory attestations and never count toward the
quorum; a DENY from any required signer is an immediate veto.

Persistence: :class:`EscalationStore` keeps envelopes in one JSON state
file (atomic writes: tmp file + ``os.replace``, JSON re-validated after
every write) plus an append-only JSONL audit log — one record per
event, in order, with a monotonic sequence number. The audit log is
**hash-chained**: every record carries ``prev_hash`` and
``record_hash`` (sha256 over the canonical record content, which
includes ``prev_hash``; the genesis record's ``prev_hash`` is 64
zeros), making the log a tamper-evident, append-only audit asset —
any edit to a historical record breaks :func:`verify_chain`. Decision
records carry the deciding actor + role (the attestation); real
cryptographic key signing is Phase 2, deliberately not faked here.
A corrupt state file raises :class:`StoreCorruptError` (fail closed)
instead of silently starting empty over someone's pending decisions.

v1 scope notes (per spec): NO production wiring — no live surface is
re-routed by this module; wiring lands surface-by-surface in follow-ups,
each founder-gated. The schema carries a ``tenant`` field defaulting to
``"sincor"`` so Phase 1's multi-tenant middleware has its seam.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

__all__ = [
    "ALLOW",
    "ESCALATE",
    "PENDING",
    "APPROVED",
    "OVERRIDDEN",
    "ESCALATED",
    "DENIED",
    "EXPIRED",
    "TERMINAL_STATES",
    "HARD_GATE_CATEGORIES",
    "INTERNAL_CATEGORIES",
    "SEVERITIES",
    "DECISION_KINDS",
    "PACKAGE_SCHEMA_VERSION",
    "SCHEMA_NAME",
    "SCHEMA_VERSION",
    "ACTION_CHOICES",
    "RECOMMENDED_ACTIONS",
    "GENESIS_PREV_HASH",
    "verify_chain",
    "EscalationError",
    "IllegalTransitionError",
    "PackageNotFoundError",
    "StoreCorruptError",
    "PolicyVerdict",
    "PolicyEngine",
    "Decision",
    "EscalationPackage",
    "EscalationStore",
    "require_decision",
]

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

ALLOW = "ALLOW"
ESCALATE = "ESCALATE"

PENDING = "PENDING"
APPROVED = "APPROVED"
OVERRIDDEN = "OVERRIDDEN"
ESCALATED = "ESCALATED"
DENIED = "DENIED"
EXPIRED = "EXPIRED"

#: States from which no further transition is legal. ESCALATED is terminal
#: for agents: the founder decides out of band and the decision re-enters
#: as a new record, never by reopening the escalated package.
TERMINAL_STATES = frozenset({APPROVED, OVERRIDDEN, ESCALATED, DENIED, EXPIRED})

#: Hard-gate categories — NEVER auto-allowed, always escalate to a human.
#: Mirrors the pinned guardrail document's founder-gated list.
HARD_GATE_CATEGORIES = frozenset(
    {
        "treasury",
        "funds",
        "merge",
        "deploy",
        "private_keys",
        "legal_compliance",
        "external_send",
        "production_action",
        "withdrawal",
    }
)

#: Known reversible, internal-only categories — the only auto-allow set.
#: Anything not listed here (and not a hard gate) is unknown → escalate.
INTERNAL_CATEGORIES = frozenset(
    {
        "read_only",
        "report",
        "draft",
        "internal_note",
        "test_run",
        "metrics",
    }
)

SEVERITIES = frozenset({"low", "medium", "high", "critical"})

#: Operator decision kinds → resulting package state.
DECISION_KINDS = {
    "approve": APPROVED,
    "override": OVERRIDDEN,
    "escalate": ESCALATED,
    "deny": DENIED,
    "expire": EXPIRED,
}

#: Canonical Escalation Envelope Schema identity. The serialized
#: envelope carries ``schema`` = SCHEMA_NAME and ``schema_version`` =
#: SCHEMA_VERSION. PACKAGE_SCHEMA_VERSION is kept as an alias.
SCHEMA_NAME = "EscalationEnvelope"
SCHEMA_VERSION = "escalation-envelope/1.0"
PACKAGE_SCHEMA_VERSION = SCHEMA_VERSION

#: The operator's route, carried as data on every envelope — exactly
#: these three, in this order (deny/expire are API-level outcomes, not
#: advertised envelope choices).
ACTION_CHOICES = ["approve", "override", "escalate"]

#: Legal values for the risk block's ``recommended_action``.
RECOMMENDED_ACTIONS = frozenset({"approve", "override", "escalate", "deny"})

#: prev_hash of the first (genesis) audit record: 64 zero hex chars.
GENESIS_PREV_HASH = "0" * 64

#: Context keys that force escalation even for an internal category.
_CONTEXT_ESCALATION_FLAGS = ("irreversible", "external_effect")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class EscalationError(Exception):
    """Base class for escalation-layer errors."""


class IllegalTransitionError(EscalationError):
    """A decision was attempted on a package that is not PENDING."""


class PackageNotFoundError(EscalationError):
    """The referenced package id does not exist in the store."""


class StoreCorruptError(EscalationError):
    """The on-disk state is unreadable/invalid — fail closed, never reset."""


# ---------------------------------------------------------------------------
# Policy engine (pure, deterministic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyVerdict:
    """Outcome of a policy classification.

    ``verdict`` is ``ALLOW`` or ``ESCALATE``; ``reason`` is a stable
    machine-readable token explaining which rule class fired;
    ``category`` is the normalized action category that was classified;
    ``policy_id`` / ``rule`` / ``detail`` are the triggered-rule triple
    stamped onto the envelope (e.g. policy_id ``sanctions-screen``,
    rule ``ofac-match`` for a detector hit supplied via context).
    """

    verdict: str
    reason: str
    category: str
    policy_id: str = ""
    rule: str = ""
    detail: str = ""

    def triggered_rule(self) -> dict:
        return {
            "policy_id": self.policy_id,
            "rule": self.rule,
            "detail": self.detail,
        }


class PolicyEngine:
    """Deterministic classification of (action_category, context).

    **Posture: Default-Deny.** An action may execute only if it
    satisfies deterministic policy parameters (an ALLOW verdict — known
    reversible internal category, clean context) or carries a HITL
    authorization decision on its envelope. Every other outcome is
    ESCALATE. There is no third path.

    Pure function: no I/O, no randomness, no clock. The same inputs
    always produce the same verdict, which is what makes the audit log
    replayable and the layer testable.

    Rule precedence (first match wins, all deterministic):

    1. ``context["rule_hit"]`` — a detector (sanctions screen, vuln
       score, …) already fired: ESCALATE, stamped with the detector's
       own ``{policy_id, rule, detail}``.
    2. Spend threshold — numeric ``context["amount"]`` strictly greater
       than numeric ``context["threshold"]``: ESCALATE under policy
       ``spend-threshold``.
    3. Hard-gate category: ESCALATE under policy ``hard-gate``.
    4. Internal category with an irreversibility / external-effect
       context flag: ESCALATE under policy ``context-guard``.
    5. Internal category, clean context: ALLOW.
    6. Anything else (unknown / empty / non-string): ESCALATE under
       policy ``fail-closed``.
    """

    @staticmethod
    def _normalize_category(action_category: Any) -> str:
        if not isinstance(action_category, str):
            return ""
        return action_category.strip().lower()

    @classmethod
    def classify(
        cls, action_category: Any, context: Optional[dict] = None
    ) -> PolicyVerdict:
        category = cls._normalize_category(action_category)
        ctx = context or {}

        rule_hit = ctx.get("rule_hit")
        if isinstance(rule_hit, dict) and rule_hit.get("policy_id") and rule_hit.get("rule"):
            return PolicyVerdict(
                ESCALATE,
                "rule_hit",
                category,
                policy_id=str(rule_hit["policy_id"]),
                rule=str(rule_hit["rule"]),
                detail=str(rule_hit.get("detail", "")),
            )

        amount = ctx.get("amount")
        threshold = ctx.get("threshold")
        if (
            isinstance(amount, (int, float))
            and not isinstance(amount, bool)
            and isinstance(threshold, (int, float))
            and not isinstance(threshold, bool)
            and amount > threshold
        ):
            return PolicyVerdict(
                ESCALATE,
                "spend_threshold_exceeded",
                category,
                policy_id="spend-threshold",
                rule="amount_exceeds_threshold",
                detail=f"amount={amount} threshold={threshold}",
            )

        if category in HARD_GATE_CATEGORIES:
            return PolicyVerdict(
                ESCALATE,
                "hard_gate_category",
                category,
                policy_id="hard-gate",
                rule="hard_gate_category",
                detail=f"category={category}",
            )

        if category in INTERNAL_CATEGORIES:
            for flag in _CONTEXT_ESCALATION_FLAGS:
                if ctx.get(flag):
                    return PolicyVerdict(
                        ESCALATE,
                        f"context_flag:{flag}",
                        category,
                        policy_id="context-guard",
                        rule=f"context_flag:{flag}",
                        detail=f"category={category} flag={flag}",
                    )
            return PolicyVerdict(
                ALLOW,
                "reversible_internal",
                category,
                policy_id="internal-allowlist",
                rule="reversible_internal",
                detail=f"category={category}",
            )

        # Unknown (or empty / non-string) category: fail closed.
        return PolicyVerdict(
            ESCALATE,
            "unknown_category",
            category,
            policy_id="fail-closed",
            rule="unknown_category",
            detail=f"category={category!r}",
        )


# ---------------------------------------------------------------------------
# EscalationEnvelope — Canonical Escalation Envelope Schema
# (serialized name: "EscalationEnvelope", version "escalation-envelope/1.0")
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    """A decision record (attestation), as stored on the envelope.

    Carries the deciding actor + role — the v1 attestation. (Real
    cryptographic key signing is Phase 2; actor+role+hash-chained
    audit is the v1 integrity story, and it is not dressed up as more.)
    """

    by: str
    at: str
    kind: str
    note: Optional[str] = None
    role: str = "operator"

    def to_dict(self) -> dict:
        return {
            "by": self.by,
            "at": self.at,
            "kind": self.kind,
            "note": self.note,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        return cls(
            by=data["by"],
            at=data["at"],
            kind=data["kind"],
            note=data.get("note"),
            role=data.get("role", "operator"),
        )


@dataclass
class EscalationPackage:
    """One EscalationEnvelope (internal class name: EscalationPackage).

    Serialized form is the Canonical Escalation Envelope Schema:
    ``schema`` = "EscalationEnvelope", ``schema_version`` =
    "escalation-envelope/1.0".

    Beyond the spec's core fields, the envelope carries:

    - ``source_agent_id`` + ``source_context`` {surface, session_id,
      trace_id} — traceability: which agent/surface raised it, and the
      session/trace it belongs to when present.
    - ``triggered_rule`` {policy_id, rule, detail} — the deterministic
      rule that fired (from the PolicyEngine, or a detector's rule hit).
    - ``state_snapshot`` {input_parameters, dry_run_state} — the
      proposed action's inputs and the dry-run/simulated state it would
      produce; the proof payload the human reviews before deciding.
    - ``action_choices`` — exactly ``["approve", "override",
      "escalate"]``, carried as data on the envelope.
    - ``evidence_refs`` — top-level (external proof artifacts; see the
      module docstring for why it is not nested in the snapshot).
    - ``risk`` — the risk block: ``violations`` [{policy_id, detail,
      confidence}] (confidence is a float 0..1 from the detector, or
      None when not quantifiable — never fabricated), ``exposure``
      {amount, currency} or None (the at-risk value, from the action's
      own parameters), ``recommended_action``, and
      ``required_signers`` [roles] — when non-empty, the gate needs a
      co-sign quorum (see the module docstring).
    - ``decision`` — the effective decision record (the one that moved
      the envelope out of PENDING), or None while pending;
      ``attestations`` — every decision record submitted, in order,
      including advisory and quorum-building ones.
    - ``history`` — the in-envelope event trail (created, classified,
      one entry per transition); the store's JSONL audit log is the
      durable, cross-envelope record of the same events.
    """

    id: str
    created_at: str
    source_surface: str
    actor: str
    action_category: str
    summary: str
    evidence_refs: list = field(default_factory=list)
    severity: str = "medium"
    state: str = PENDING
    decision: Optional[Decision] = None
    history: list = field(default_factory=list)
    tenant: str = "sincor"
    schema_version: str = PACKAGE_SCHEMA_VERSION
    source_agent_id: str = ""
    source_context: dict = field(default_factory=dict)
    triggered_rule: dict = field(default_factory=dict)
    state_snapshot: dict = field(default_factory=dict)
    action_choices: list = field(default_factory=lambda: list(ACTION_CHOICES))
    risk: dict = field(default_factory=dict)
    attestations: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_NAME,
            "schema_version": self.schema_version,
            "id": self.id,
            "created_at": self.created_at,
            "tenant": self.tenant,
            "source_surface": self.source_surface,
            "source_agent_id": self.source_agent_id,
            "source_context": dict(self.source_context),
            "actor": self.actor,
            "action_category": self.action_category,
            "summary": self.summary,
            "triggered_rule": dict(self.triggered_rule),
            "state_snapshot": {
                "input_parameters": dict(
                    (self.state_snapshot or {}).get("input_parameters") or {}
                ),
                "dry_run_state": (self.state_snapshot or {}).get("dry_run_state"),
            },
            "evidence_refs": list(self.evidence_refs),
            "severity": self.severity,
            "state": self.state,
            "action_choices": list(self.action_choices),
            "risk": _normalize_risk(self.risk),
            "decision": self.decision.to_dict() if self.decision else None,
            "attestations": [a.to_dict() for a in self.attestations],
            "history": [dict(h) for h in self.history],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EscalationPackage":
        decision = data.get("decision")
        snapshot = data.get("state_snapshot") or {}
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            source_surface=data["source_surface"],
            actor=data["actor"],
            action_category=data["action_category"],
            summary=data["summary"],
            evidence_refs=list(data.get("evidence_refs") or []),
            severity=data.get("severity", "medium"),
            state=data.get("state", PENDING),
            decision=Decision.from_dict(decision) if decision else None,
            history=[dict(h) for h in data.get("history") or []],
            tenant=data.get("tenant", "sincor"),
            schema_version=data.get("schema_version", PACKAGE_SCHEMA_VERSION),
            source_agent_id=data.get("source_agent_id", ""),
            source_context=dict(data.get("source_context") or {}),
            triggered_rule=dict(data.get("triggered_rule") or {}),
            state_snapshot={
                "input_parameters": dict(snapshot.get("input_parameters") or {}),
                "dry_run_state": snapshot.get("dry_run_state"),
            },
            action_choices=list(data.get("action_choices") or ACTION_CHOICES),
            risk=_normalize_risk(data.get("risk")),
            attestations=[
                Decision.from_dict(a) for a in data.get("attestations") or []
            ],
        )


# ---------------------------------------------------------------------------
# Risk block + audit hash chain helpers
# ---------------------------------------------------------------------------


def _normalize_risk(risk: Optional[dict]) -> dict:
    """Project any risk input onto the canonical risk-block shape."""
    risk = risk or {}
    return {
        "violations": [dict(v) for v in risk.get("violations") or []],
        "exposure": (
            dict(risk["exposure"]) if risk.get("exposure") is not None else None
        ),
        "recommended_action": risk.get("recommended_action", "escalate"),
        "required_signers": list(risk.get("required_signers") or []),
    }


def _validate_risk_inputs(
    *,
    violations: Optional[list],
    exposure: Optional[dict],
    recommended_action: Optional[str],
    required_signers: Optional[list],
) -> None:
    """Refuse malformed risk data at envelope creation (fail closed)."""
    if recommended_action is not None and recommended_action not in RECOMMENDED_ACTIONS:
        raise ValueError(
            f"unknown recommended_action {recommended_action!r}; "
            f"expected one of {sorted(RECOMMENDED_ACTIONS)}"
        )
    for violation in violations or []:
        if not isinstance(violation, dict) or "policy_id" not in violation:
            raise ValueError("each violation must be a dict with a policy_id")
        confidence = violation.get("confidence")
        if confidence is not None and (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0.0 <= float(confidence) <= 1.0
        ):
            raise ValueError(
                "violation confidence must be a float in [0, 1] or None "
                "(None when not quantifiable — never fabricate one)"
            )
    if exposure is not None:
        if (
            not isinstance(exposure, dict)
            or "amount" not in exposure
            or "currency" not in exposure
        ):
            raise ValueError("exposure must be {amount, currency} or None")
    for signer in required_signers or []:
        if not isinstance(signer, str) or not signer.strip():
            raise ValueError("required_signers must be non-empty role strings")


def _canonical_record_bytes(record_without_hash: dict) -> bytes:
    return json.dumps(
        record_without_hash, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _compute_record_hash(record_without_hash: dict) -> str:
    """sha256 over the canonical record content.

    The content includes ``prev_hash``, so each record's hash binds the
    entire history before it — editing any earlier record invalidates
    every later hash.
    """
    return hashlib.sha256(_canonical_record_bytes(record_without_hash)).hexdigest()


def verify_chain(records: list) -> bool:
    """Verify a hash-chained audit log (a full log or a seq-ordered slice).

    Checks, for every record: ``prev_hash`` links to the previous
    record's ``record_hash`` (genesis = 64 zeros for a full log),
    ``record_hash`` recomputes from the canonical content, and ``seq``
    is consecutive. Any tampering — edit, reorder, deletion — fails.
    """
    if not records:
        return True
    first_seq = records[0].get("seq")
    prev_hash = GENESIS_PREV_HASH
    for offset, record in enumerate(records):
        if record.get("prev_hash") != prev_hash:
            return False
        body = {k: v for k, v in record.items() if k != "record_hash"}
        if _compute_record_hash(body) != record.get("record_hash"):
            return False
        if first_seq is not None and record.get("seq") != first_seq + offset:
            return False
        prev_hash = record["record_hash"]
    return True


def _quorum_satisfied(
    package: "EscalationPackage", kind: Optional[str] = None
) -> bool:
    """True when the co-sign quorum behind a decision kind holds.

    Every required signer role must have attested the kind, from
    distinct actors (one actor cannot fill two roles). ``kind``
    defaults to the envelope's effective decision kind; ``decide``
    passes the candidate kind when evaluating a just-appended
    attestation whose decision is not yet effective. With no required
    signers, trivially satisfied.
    """
    required = list(_normalize_risk(package.risk)["required_signers"])
    if not required:
        return True
    effective_kind = kind or (package.decision.kind if package.decision else None)
    if effective_kind is None:
        return False
    role_actor: dict = {}
    for attestation in package.attestations:
        if (
            attestation.role in required
            and attestation.kind == effective_kind
            and attestation.role not in role_actor
        ):
            role_actor[attestation.role] = attestation.by
    return set(role_actor) == set(required) and len(set(role_actor.values())) == len(
        required
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EscalationStore:
    """JSON state file + append-only, hash-chained JSONL audit log.

    Layout inside ``base_dir``:

    - ``escalation_state.json`` — ``{"schema_version":
      "escalation-envelope/1.0", "packages": {id: envelope}}``,
      rewritten atomically (tmp + ``os.replace``) on every mutation and
      re-parsed after writing to prove validity.
    - ``escalation_audit.jsonl`` — one JSON record per event, append
      only, ``seq`` strictly increasing, hash-chained (``prev_hash`` /
      ``record_hash``; see :func:`verify_chain`). Never rewritten.
    """

    STATE_FILENAME = "escalation_state.json"
    AUDIT_FILENAME = "escalation_audit.jsonl"

    def __init__(self, base_dir: str):
        self.base_dir = str(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.state_path = os.path.join(self.base_dir, self.STATE_FILENAME)
        self.audit_path = os.path.join(self.base_dir, self.AUDIT_FILENAME)
        self._lock = threading.Lock()
        self._packages: dict[str, EscalationPackage] = {}
        self._audit_seq = 0
        self._last_hash = GENESIS_PREV_HASH
        self._load()

    # -- persistence internals -------------------------------------------

    def _load(self) -> None:
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                raw_packages = data["packages"]
                self._packages = {
                    pid: EscalationPackage.from_dict(pkg)
                    for pid, pkg in raw_packages.items()
                }
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise StoreCorruptError(
                    f"escalation state file is corrupt: {self.state_path}: {exc}"
                ) from exc
        if os.path.exists(self.audit_path):
            try:
                with open(self.audit_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            self._audit_seq = int(record["seq"])
                            self._last_hash = str(record["record_hash"])
            except (ValueError, KeyError, TypeError) as exc:
                raise StoreCorruptError(
                    f"escalation audit log is corrupt: {self.audit_path}: {exc}"
                ) from exc

    def _save_state(self) -> None:
        payload = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "packages": {
                pid: pkg.to_dict() for pid, pkg in self._packages.items()
            },
        }
        tmp_path = self.state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, self.state_path)
        # Validate-after-write: the file on disk must parse as the schema
        # we intended, or the store fails closed right here.
        with open(self.state_path, "r", encoding="utf-8") as fh:
            json.load(fh)

    def _append_audit(
        self,
        *,
        package_id: str,
        event: str,
        at: str,
        by: Optional[str] = None,
        role: Optional[str] = None,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        note: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> dict:
        self._audit_seq += 1
        record = {
            "seq": self._audit_seq,
            "at": at,
            "package_id": package_id,
            "event": event,
            "by": by,
            "role": role,
            "from_state": from_state,
            "to_state": to_state,
            "note": note,
            "detail": detail,
            "prev_hash": self._last_hash,
        }
        record["record_hash"] = _compute_record_hash(record)
        self._last_hash = record["record_hash"]
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return record

    def read_audit_log(self) -> list:
        """Return all audit records in append order (oldest first)."""
        if not os.path.exists(self.audit_path):
            return []
        records = []
        with open(self.audit_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def verify_audit_chain(self) -> bool:
        """Verify this store's full audit log hash chain."""
        return verify_chain(self.read_audit_log())

    # -- decision API ------------------------------------------------------

    def create_package(
        self,
        *,
        source_surface: str,
        actor: str,
        action_category: str,
        summary: str,
        evidence_refs: Optional[Iterable[str]] = None,
        severity: str = "medium",
        tenant: str = "sincor",
        context: Optional[dict] = None,
        source_agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        input_parameters: Optional[dict] = None,
        dry_run_state: Any = None,
        violations: Optional[list] = None,
        exposure: Optional[dict] = None,
        recommended_action: Optional[str] = None,
        required_signers: Optional[list] = None,
        now: Optional[str] = None,
    ) -> EscalationPackage:
        """Raise a new PENDING EscalationEnvelope.

        The PolicyEngine verdict is computed and stamped onto the
        envelope as ``triggered_rule`` {policy_id, rule, detail}, and
        written to the envelope history + audit log. A package is
        created even when the verdict is ALLOW (a surface explicitly
        asked for human review); the gate hook still requires an
        operator decision before execution. The category is stored
        normalized (lowercase) so the audit trail and the
        classification always agree.

        ``context`` is the PolicyEngine context: it may carry detector
        output (``rule_hit``), spend figures (``amount``/``threshold``),
        or effect flags (``irreversible``/``external_effect``).
        ``input_parameters`` + ``dry_run_state`` form the envelope's
        ``state_snapshot`` — the proof payload the human reviews.
        ``source_agent_id`` defaults to ``actor`` when not given.

        Risk block: explicit ``violations`` / ``exposure`` /
        ``recommended_action`` / ``required_signers`` win; otherwise
        the block is derived from the verdict — an ESCALATE verdict
        yields one violation from the triggered rule, with confidence
        taken from the detector's ``rule_hit`` when it supplied one and
        None otherwise (never fabricated), and a recommended action of
        "escalate" ("approve" for an ALLOW verdict).
        """
        severity_norm = str(severity).strip().lower()
        if severity_norm not in SEVERITIES:
            raise ValueError(
                f"unknown severity {severity!r}; expected one of {sorted(SEVERITIES)}"
            )
        _validate_risk_inputs(
            violations=violations,
            exposure=exposure,
            recommended_action=recommended_action,
            required_signers=required_signers,
        )
        created_at = now or _utcnow_iso()
        verdict = PolicyEngine.classify(action_category, context)
        if violations is None:
            if verdict.verdict == ESCALATE:
                confidence = None
                rule_hit = (context or {}).get("rule_hit")
                if isinstance(rule_hit, dict) and rule_hit.get("confidence") is not None:
                    confidence = float(rule_hit["confidence"])
                    _validate_risk_inputs(
                        violations=[
                            {
                                "policy_id": verdict.policy_id,
                                "confidence": confidence,
                            }
                        ],
                        exposure=None,
                        recommended_action=None,
                        required_signers=None,
                    )
                violations = [
                    {
                        "policy_id": verdict.policy_id,
                        "detail": verdict.detail,
                        "confidence": confidence,
                    }
                ]
            else:
                violations = []
        if recommended_action is None:
            recommended_action = "escalate" if verdict.verdict == ESCALATE else "approve"
        risk = _normalize_risk(
            {
                "violations": violations,
                "exposure": exposure,
                "recommended_action": recommended_action,
                "required_signers": required_signers or [],
            }
        )
        classified_detail = (
            f"verdict={verdict.verdict} reason={verdict.reason} "
            f"rule={verdict.policy_id}/{verdict.rule}"
        )
        package = EscalationPackage(
            id="esc_" + uuid.uuid4().hex,
            created_at=created_at,
            source_surface=source_surface,
            actor=actor,
            action_category=verdict.category,
            summary=summary,
            evidence_refs=list(evidence_refs or []),
            severity=severity_norm,
            state=PENDING,
            decision=None,
            history=[
                {
                    "event": "created",
                    "at": created_at,
                    "by": actor,
                    "detail": f"surface={source_surface}",
                },
                {
                    "event": "classified",
                    "at": created_at,
                    "by": "policy_engine",
                    "detail": classified_detail,
                },
            ],
            tenant=tenant or "sincor",
            source_agent_id=source_agent_id or actor,
            source_context={
                "surface": source_surface,
                "session_id": session_id,
                "trace_id": trace_id,
            },
            triggered_rule=verdict.triggered_rule(),
            state_snapshot={
                "input_parameters": dict(input_parameters or {}),
                "dry_run_state": dry_run_state,
            },
            risk=risk,
        )
        with self._lock:
            self._packages[package.id] = package
            self._append_audit(
                package_id=package.id,
                event="created",
                at=created_at,
                by=actor,
                from_state=None,
                to_state=PENDING,
                detail=f"surface={source_surface}",
            )
            self._append_audit(
                package_id=package.id,
                event="classified",
                at=created_at,
                by="policy_engine",
                from_state=PENDING,
                to_state=PENDING,
                detail=classified_detail,
            )
            self._save_state()
        return self._copy(package)

    def decide(
        self,
        package_id: str,
        kind: str,
        by: str,
        note: Optional[str] = None,
        role: str = "operator",
        now: Optional[str] = None,
    ) -> EscalationPackage:
        """Apply an operator decision (attestation) to a PENDING package.

        ``kind`` ∈ {approve, override, escalate, deny, expire}.
        OVERRIDE requires a non-empty ``note`` — the note is the recorded
        modification/dissent, and an override without one is refused.
        Deciding a non-PENDING package raises IllegalTransitionError;
        every terminal state (including ESCALATED) is final for agents.

        Co-sign: when the envelope's ``risk.required_signers`` is
        non-empty, decisions accumulate as attestations while the
        package stays PENDING, until every required role has attested
        the same kind from distinct actors — only then does it
        transition. A decision whose role is not in the required list
        is recorded as an advisory attestation and never counts toward
        the quorum (and never transitions the package). A DENY from a
        required signer is an immediate veto → DENIED. A required
        signer submitting a different non-deny kind than the kind
        already attested raises ValueError (conflict is surfaced, not
        silently resolved). EXPIRE is administrative and transitions
        immediately regardless of quorum.
        """
        kind_norm = str(kind).strip().lower()
        if kind_norm not in DECISION_KINDS:
            raise ValueError(
                f"unknown decision kind {kind!r}; expected one of {sorted(DECISION_KINDS)}"
            )
        if kind_norm == "override" and not (note and str(note).strip()):
            raise ValueError(
                "override requires a non-empty note (the recorded "
                "modification/dissent)"
            )
        role_norm = str(role).strip().lower() if role else "operator"
        decided_at = now or _utcnow_iso()
        with self._lock:
            package = self._packages.get(package_id)
            if package is None:
                raise PackageNotFoundError(f"no such package: {package_id}")
            if package.state != PENDING:
                raise IllegalTransitionError(
                    f"package {package_id} is {package.state} (terminal); "
                    f"cannot apply decision {kind_norm!r}"
                )
            required = list(_normalize_risk(package.risk)["required_signers"])
            record = Decision(
                by=by, at=decided_at, kind=kind_norm, note=note, role=role_norm
            )

            if required and kind_norm not in ("expire",):
                prior_required = [
                    a for a in package.attestations if a.role in required
                ]
                if role_norm in required and kind_norm != "deny":
                    conflicting = [
                        a for a in prior_required if a.kind != kind_norm
                    ]
                    if conflicting:
                        raise ValueError(
                            f"conflicting decision kind {kind_norm!r}: "
                            f"required signers already attested "
                            f"{conflicting[0].kind!r} on package {package_id}"
                        )

            package.attestations.append(record)
            outcome = "recorded"
            if kind_norm == "expire":
                self._transition(package, record, EXPIRED)
                outcome = "completed"
            elif not required:
                self._transition(package, record, DECISION_KINDS[kind_norm])
                outcome = "completed"
            elif role_norm not in required:
                outcome = "advisory_only"
            elif kind_norm == "deny":
                self._transition(package, record, DENIED)
                outcome = "veto"
            elif _quorum_satisfied(package, kind=kind_norm):
                self._transition(package, record, DECISION_KINDS[kind_norm])
                outcome = "quorum_completed"
            else:
                outcome = "quorum_pending"

            package.history.append(
                {
                    "event": f"decision:{kind_norm}",
                    "at": decided_at,
                    "by": by,
                    "detail": f"role={role_norm} outcome={outcome} "
                    f"state={package.state}",
                }
            )
            self._append_audit(
                package_id=package.id,
                event=f"decision:{kind_norm}",
                at=decided_at,
                by=by,
                role=role_norm,
                from_state=PENDING,
                to_state=package.state,
                note=note,
                detail=f"outcome={outcome}",
            )
            self._save_state()
            return self._copy(package)

    @staticmethod
    def _transition(
        package: EscalationPackage, record: Decision, to_state: str
    ) -> None:
        package.state = to_state
        package.decision = record

    def get_package(self, package_id: str) -> EscalationPackage:
        """Return a copy of the package, or raise PackageNotFoundError."""
        package = self._packages.get(package_id)
        if package is None:
            raise PackageNotFoundError(f"no such package: {package_id}")
        return self._copy(package)

    def find_package(self, package_id: str) -> Optional[EscalationPackage]:
        """Return a copy of the package, or None if it does not exist."""
        package = self._packages.get(package_id)
        return self._copy(package) if package is not None else None

    def list_pending(self, tenant: Optional[str] = None) -> list:
        """All PENDING packages, oldest first (created_at, then id)."""
        pending = [
            pkg
            for pkg in self._packages.values()
            if pkg.state == PENDING and (tenant is None or pkg.tenant == tenant)
        ]
        pending.sort(key=lambda p: (p.created_at, p.id))
        return [self._copy(pkg) for pkg in pending]

    @staticmethod
    def _copy(package: EscalationPackage) -> EscalationPackage:
        # Round-trip through the schema so callers can never mutate the
        # store's in-memory state behind its back.
        return EscalationPackage.from_dict(package.to_dict())


# ---------------------------------------------------------------------------
# Gate hook
# ---------------------------------------------------------------------------


def require_decision(store: EscalationStore, package_id: Optional[str]) -> bool:
    """Gate hook: True only when the package is APPROVED or OVERRIDDEN.

    Fail closed on everything else — PENDING (no decision yet, or a
    co-sign quorum still incomplete), DENIED, EXPIRED, ESCALATED
    (founder authority, out of band), a missing package, or no package
    id at all. When the envelope requires co-signers, the quorum is
    re-verified here from the attestation records (one decision per
    required role, distinct actors) — state alone is never trusted.
    Returns False rather than raising, matching the repo's boolean
    guard idiom: *no package, no execution*.
    """
    if not package_id:
        return False
    package = store.find_package(package_id)
    if package is None:
        return False
    if package.state not in (APPROVED, OVERRIDDEN):
        return False
    return _quorum_satisfied(package)
