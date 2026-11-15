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
records carry the deciding actor + role (the attestation).

**Phase 2 signing (directive pillar 2.1).** When an
:class:`AuditSigner` is configured on the store, every newly appended
audit record also gains a ``signature`` block — an Ed25519 signature,
by the interceptor/signing node, over the record's canonical bytes
*including* its ``record_hash`` (sign-the-hash-chain-head semantics:
the signature binds the content and its position in the chain).
Records also carry ``policy_id`` + ``policy_version`` from the
audited package. Signing is opt-in: with no signer configured the
store's behavior is byte-for-byte the Phase 1 behavior, unsigned
history keeps verifying on hash links alone, and a mixed chain
(unsigned history + signed tail) verifies. Private keys are loaded
only from a file path named by the ``SINCOR_ESCALATION_SIGNING_KEY_FILE``
env var — never from code, config JSON, or the store directory.
Merkle epoch anchoring of these records (pillar 2.4) lives in
:mod:`sincor2.merkle_anchor`.

A corrupt state file raises :class:`StoreCorruptError` (fail closed)
instead of silently starting empty over someone's pending decisions.

v1 scope notes (per spec): NO production wiring — no live surface is
re-routed by this module; wiring lands surface-by-surface in follow-ups,
each founder-gated. The schema carries a ``tenant`` field defaulting to
``"sincor"`` so Phase 1's multi-tenant middleware has its seam.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

# POSIX file locking serializes EscalationStore writes across store
# instances and processes (the sanctions tap builds a fresh store per
# emission; two request threads must not interleave load→append→save).
# Guarded: on a platform without fcntl the store degrades to its
# per-instance threading lock rather than failing to import.
try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

# Ed25519 signing (Phase 2, pillar 2.1) uses the `cryptography`
# package. The import is guarded so the Phase 1 unsigned path never
# depends on it: without the package, AuditSigner construction raises
# and signed records fail verification (fail closed) — the unsigned
# store keeps working exactly as before.
try:
    from cryptography.exceptions import InvalidSignature as _InvalidSignature
    from cryptography.hazmat.primitives import serialization as _serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey as _Ed25519PrivateKey,
        Ed25519PublicKey as _Ed25519PublicKey,
    )

    _CRYPTOGRAPHY_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the dep
    _InvalidSignature = Exception  # type: ignore[assignment]
    _serialization = None
    _Ed25519PrivateKey = None
    _Ed25519PublicKey = None
    _CRYPTOGRAPHY_AVAILABLE = False

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
    "SIGNATURE_ALG",
    "SIGNING_KEY_ENV_VAR",
    "OPERATOR_KEYS_ENV_VAR",
    "DEFAULT_SIGNER_ID",
    "verify_chain",
    "AuditSigner",
    "generate_signing_key",
    "load_signer_from_env",
    "attestation_payload_bytes",
    "load_operator_keys_from_env",
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

#: Signature algorithm stamped into audit-record ``signature`` blocks.
SIGNATURE_ALG = "Ed25519"

#: Env var naming the FILE PATH of the signer's PEM private key. The
#: key itself is only ever read from that file — never from a literal
#: in code, config JSON, or chat (Phase 2 spec, hard rule).
SIGNING_KEY_ENV_VAR = "SINCOR_ESCALATION_SIGNING_KEY_FILE"

#: Env var naming the operator-registry JSON file for W-122 identity
#: binding (identity -> {"pubkey": hex, "roles": [...]}). Like the
#: signing-key var, it carries a file path, never key material.
OPERATOR_KEYS_ENV_VAR = "SINCOR_ESCALATION_OPERATOR_KEYS_FILE"

#: Default identity string for the signing (interceptor) node when an
#: operator does not choose one.
DEFAULT_SIGNER_ID = "sincor-interceptor"

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


def _coerce_threshold_number(value: Any) -> Optional[float]:
    """Coerce a context amount/threshold to a finite float, or None.

    Accepts ints/floats (never bools) and numeric strings — JSON
    payloads routinely carry amounts as strings, and a string amount
    must not silently skip the spend check (W-127). NaN and the
    infinities are NOT numbers for policy purposes: ``NaN > x`` is
    False, which used to let a NaN amount sail past the threshold, so
    they coerce to None and the caller fails closed.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


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
    2. Spend threshold — when ``context["threshold"]`` is present, the
       amount is coerced to a finite number (numeric strings accepted;
       NaN / non-numeric values fail closed as unverifiable) and an
       amount strictly greater than the threshold ESCALATEs under
       policy ``spend-threshold``.
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

        raw_amount = ctx.get("amount")
        raw_threshold = ctx.get("threshold")
        if raw_threshold is not None:
            # A spend threshold is present, so the spend check MUST
            # resolve: amounts/thresholds arrive from JSON and may be
            # numeric strings (coerced), and a value that cannot be
            # coerced to a finite number (NaN, non-numeric string,
            # bool) fails closed instead of skipping the check (W-127).
            amount = _coerce_threshold_number(raw_amount)
            threshold = _coerce_threshold_number(raw_threshold)
            if threshold is None:
                return PolicyVerdict(
                    ESCALATE,
                    "spend_threshold_unverifiable",
                    category,
                    policy_id="spend-threshold",
                    rule="threshold_not_numeric",
                    detail=f"threshold={raw_threshold!r}",
                )
            if raw_amount is not None and amount is None:
                return PolicyVerdict(
                    ESCALATE,
                    "spend_threshold_unverifiable",
                    category,
                    policy_id="spend-threshold",
                    rule="amount_not_numeric",
                    detail=f"amount={raw_amount!r} threshold={raw_threshold!r}",
                )
            if amount is not None and amount > threshold:
                return PolicyVerdict(
                    ESCALATE,
                    "spend_threshold_exceeded",
                    category,
                    policy_id="spend-threshold",
                    rule="amount_exceeds_threshold",
                    detail=f"amount={raw_amount} threshold={raw_threshold}",
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

    Carries the deciding actor + role — the envelope-level attestation.
    (Cryptographic key signing is an audit-log property, added in
    Phase 2: see :class:`AuditSigner`. The attestation here is not
    dressed up as more than it is.)
    """

    by: str
    at: str
    kind: str
    note: Optional[str] = None
    role: str = "operator"
    #: Ed25519 attestation signature (hex) over
    #: :func:`attestation_payload_bytes` — present only when the store
    #: enforces the operator registry (W-122); None in Phase 1 stores.
    signature: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "by": self.by,
            "at": self.at,
            "kind": self.kind,
            "note": self.note,
            "role": self.role,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        return cls(
            by=data["by"],
            at=data["at"],
            kind=data["kind"],
            note=data.get("note"),
            role=data.get("role", "operator"),
            signature=data.get("signature"),
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


def _normalize_required_signers(signers: Optional[Iterable]) -> list:
    """Canonical form of a required-signers role list.

    Roles are stripped + lowercased so the stored list and the
    decision path always agree on role identity: ``decide()`` has
    always normalized the deciding role, so a package created with
    ``["Founder", "CFO"]`` used to store the raw mixed-case list and
    its quorum could never be satisfied — the package sat PENDING
    forever (W-125). Non-string entries pass through untouched so
    :func:`_validate_risk_inputs` rejects them with its precise error
    instead of an AttributeError here.
    """
    normalized = []
    for signer in signers or []:
        if isinstance(signer, str):
            normalized.append(signer.strip().lower())
        else:
            normalized.append(signer)
    return normalized


def _normalize_risk(risk: Optional[dict]) -> dict:
    """Project any risk input onto the canonical risk-block shape."""
    risk = risk or {}
    return {
        "violations": [dict(v) for v in risk.get("violations") or []],
        "exposure": (
            dict(risk["exposure"]) if risk.get("exposure") is not None else None
        ),
        "recommended_action": risk.get("recommended_action", "escalate"),
        "required_signers": _normalize_required_signers(
            risk.get("required_signers")
        ),
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
    seen_signers = set()
    for signer in _normalize_required_signers(required_signers):
        if not isinstance(signer, str) or not signer:
            raise ValueError("required_signers must be non-empty role strings")
        if signer in seen_signers:
            raise ValueError(
                "duplicate required_signer after normalization "
                f"(strip + lower): {signer!r}"
            )
        seen_signers.add(signer)


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


# ---------------------------------------------------------------------------
# Phase 2 audit-record signing (Ed25519, directive pillar 2.1)
# ---------------------------------------------------------------------------


def _require_cryptography() -> None:
    if not _CRYPTOGRAPHY_AVAILABLE:
        raise EscalationError(
            "the `cryptography` package is required for Ed25519 audit "
            "signing and is not importable in this environment"
        )


class AuditSigner:
    """An Ed25519 signing identity for audit records (an interceptor node).

    Wraps a private key that is generated in-process (tests, ephemeral
    nodes) or loaded from a PEM key file (operators — see
    :func:`generate_signing_key` and :func:`load_signer_from_env`).
    The key material never leaves this object: signing exposes only
    the public key and the signature.
    """

    def __init__(self, private_key: Any, signer_id: str):
        _require_cryptography()
        if not isinstance(private_key, _Ed25519PrivateKey):
            raise TypeError("AuditSigner requires an Ed25519 private key")
        if not isinstance(signer_id, str) or not signer_id.strip():
            raise ValueError("signer_id must be a non-empty identity string")
        self._private_key = private_key
        self.signer_id = signer_id
        self._signer_pubkey = (
            private_key.public_key()
            .public_bytes(
                _serialization.Encoding.Raw,
                _serialization.PublicFormat.Raw,
            )
            .hex()
        )

    @property
    def signer_pubkey(self) -> str:
        """Hex-encoded Ed25519 public key for this signer."""
        return self._signer_pubkey

    @classmethod
    def generate(cls, signer_id: str = DEFAULT_SIGNER_ID) -> "AuditSigner":
        """A fresh in-process keypair (tests / ephemeral nodes)."""
        _require_cryptography()
        return cls(_Ed25519PrivateKey.generate(), signer_id)

    @classmethod
    def from_key_file(cls, path: str, signer_id: str = DEFAULT_SIGNER_ID) -> "AuditSigner":
        """Load a PEM (PKCS8, unencrypted) Ed25519 private key from a file."""
        _require_cryptography()
        with open(path, "rb") as fh:
            pem_data = fh.read()
        private_key = _serialization.load_pem_private_key(pem_data, password=None)
        return cls(private_key, signer_id)

    def sign_record(self, record: dict) -> dict:
        """Signature block for an audit record that already has its hash.

        Signs the record's canonical bytes *including* ``record_hash``
        and excluding any ``signature`` block, so the signature binds
        the content and the record's position in the hash chain.
        """
        payload = {k: v for k, v in record.items() if k != "signature"}
        signature = self._private_key.sign(_canonical_record_bytes(payload))
        return {
            "alg": SIGNATURE_ALG,
            "signer_id": self.signer_id,
            "signer_pubkey": self._signer_pubkey,
            "signature": signature.hex(),
        }


def generate_signing_key(path: str) -> str:
    """Generate an Ed25519 signing key file for operator setup.

    Writes a PEM (PKCS8, unencrypted — file permissions are the
    operator's control) private key to ``path`` with owner-only
    permissions, creating parent directories as needed. Refuses to
    overwrite an existing file (FileExistsError) so a live identity
    can never be silently replaced. Returns the hex public key.
    """
    _require_cryptography()
    if os.path.exists(path):
        raise FileExistsError(
            f"signing key already exists at {path}; refusing to overwrite"
        )
    private_key = _Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        _serialization.Encoding.PEM,
        _serialization.PrivateFormat.PKCS8,
        _serialization.NoEncryption(),
    )
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    # O_EXCL closes the check-then-write race: if the file appears
    # between the exists() check and here, we fail instead of
    # overwriting someone's key.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, pem)
        os.fsync(fd)
    finally:
        os.close(fd)
    return (
        private_key.public_key()
        .public_bytes(
            _serialization.Encoding.Raw,
            _serialization.PublicFormat.Raw,
        )
        .hex()
    )


def load_signer_from_env(
    signer_id: str = DEFAULT_SIGNER_ID,
    env_var: str = SIGNING_KEY_ENV_VAR,
) -> Optional["AuditSigner"]:
    """Build an AuditSigner from the key file named by the env var.

    Returns None when the env var is unset (signing stays off — the
    Phase 1 default). This is the only env-driven key path: the
    variable carries a file path, never key material.
    """
    path = os.environ.get(env_var)
    if not path:
        return None
    return AuditSigner.from_key_file(path, signer_id)


# ---------------------------------------------------------------------------
# Operator identity binding for decisions (W-122)
# ---------------------------------------------------------------------------
#
# Phase 1 ``decide()`` took ``by``/``role`` as caller-supplied strings:
# the co-sign quorum's "distinct actors" check compared strings, so one
# unauthenticated caller could attest every required role under a
# different string per role and complete the quorum alone. The registry
# below is the identity binding: when a store is configured with
# operator keys, a decision counts only if (1) the claimed ``by`` is a
# registered identity, (2) the claimed ``role`` is authorized for that
# identity, and (3) the attestation carries a valid Ed25519 signature
# by that identity's key over the canonical attestation payload.
# ``require_decision`` re-verifies the signatures from the stored
# attestations, so records forged straight into the state file do not
# count either. With no registry configured, behavior is byte-for-byte
# the Phase 1 string behavior (the same opt-in shape as the Phase 2
# audit signer).


def attestation_payload_bytes(
    package_id: str,
    kind: str,
    role: str,
    by: str,
    note: Optional[str],
) -> bytes:
    """Canonical bytes an operator signs to attest a decision.

    Binds the decision to exactly one package, kind, role, identity,
    and note — a signature captured for one attestation cannot be
    replayed for another package, kind, or role.
    """
    return _canonical_record_bytes(
        {
            "attestation": "escalation-decision/1.0",
            "package_id": str(package_id),
            "kind": str(kind),
            "role": str(role),
            "by": str(by),
            "note": note,
        }
    )


def _normalize_operator_keys(operator_keys: Optional[dict]) -> Optional[dict]:
    """Validate/normalize an operator registry mapping.

    Shape: ``{identity: {"pubkey": <hex>, "roles": [<role>, ...]}}``.
    Returns None for None (binding off). Raises ValueError on a
    malformed registry — a half-configured registry must fail closed
    at setup time, never silently degrade to string identity.
    """
    if operator_keys is None:
        return None
    normalized: dict = {}
    for identity, spec in dict(operator_keys).items():
        ident = str(identity).strip()
        if not ident:
            raise ValueError("operator registry: empty identity")
        spec = dict(spec or {})
        pubkey = str(spec.get("pubkey") or "").strip().lower()
        roles = [
            str(r).strip().lower() for r in (spec.get("roles") or []) if str(r).strip()
        ]
        if not pubkey or not roles:
            raise ValueError(
                f"operator registry: identity {ident!r} needs a pubkey "
                "and at least one authorized role"
            )
        normalized[ident] = {"pubkey": pubkey, "roles": roles}
    return normalized


def load_operator_keys_from_env(
    env_var: str = OPERATOR_KEYS_ENV_VAR,
) -> Optional[dict]:
    """Load the operator registry JSON from the file the env var names.

    Returns None when the env var is unset (identity binding stays
    off). The variable carries a file path, never key material; the
    file holds public keys and role authorizations only.
    """
    path = os.environ.get(env_var)
    if not path:
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return _normalize_operator_keys(data)


def _verify_record_signature(record: dict) -> bool:
    """True iff the record's ``signature`` block verifies.

    The signed payload is the record's canonical bytes including its
    ``record_hash`` and excluding the ``signature`` block itself (the
    exact bytes :meth:`AuditSigner.sign_record` signed). Any malformed
    block, wrong algorithm, or bad signature returns False — this
    never raises.
    """
    sig = record.get("signature")
    if not isinstance(sig, dict) or sig.get("alg") != SIGNATURE_ALG:
        return False
    if not _CRYPTOGRAPHY_AVAILABLE:
        return False
    try:
        public_key = _Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(str(sig["signer_pubkey"]))
        )
        payload = {k: v for k, v in record.items() if k != "signature"}
        public_key.verify(
            bytes.fromhex(str(sig["signature"])),
            _canonical_record_bytes(payload),
        )
        return True
    except Exception:
        return False


def verify_chain(records: list, expected_signer_pubkey: Optional[str] = None) -> bool:
    """Verify a hash-chained audit log (a full log or a seq-ordered slice).

    Checks, for every record: ``prev_hash`` links to the previous
    record's ``record_hash`` (genesis = 64 zeros for a full log),
    ``record_hash`` recomputes from the canonical content (excluding
    ``record_hash`` itself and any ``signature`` block, which is added
    after hashing), and ``seq`` is consecutive. Any tampering — edit,
    reorder, deletion — fails.

    Slice semantics (W-128): a mid-log slice is anchored at its first
    record's ``prev_hash`` instead of GENESIS, so an auditor checking
    a window is not told a valid log is broken. Internal linkage,
    hash recomputation, signature checks, and ``seq`` consecutiveness
    are still fully enforced within the slice; the anchor itself is
    trusted input — a caller verifying a slice should confirm the
    anchor equals the ``record_hash`` of the preceding record from a
    trusted source. A slice may not masquerade as a full log: a first
    record claiming the genesis position (``seq`` 1) must anchor at
    GENESIS, and a non-genesis anchor without a ``seq`` proving the
    slice starts mid-log fails closed.

    Phase 2 signature rules: a record carrying a ``signature`` block
    must verify against its embedded public key, and — when
    ``expected_signer_pubkey`` is given — that embedded key must equal
    it (a record signed by the wrong key is invalid). A bad or
    malformed signature fails the chain; verification problems return
    False, never raise. Records without a signature block (all Phase 1
    history, and the unsigned head of a mixed chain) verify on hash
    links alone, so history is never retroactively invalidated.
    """
    if not records:
        return True
    first_seq = records[0].get("seq")
    first_prev_hash = records[0].get("prev_hash")
    if first_prev_hash == GENESIS_PREV_HASH:
        # Full log: anchored at genesis.
        prev_hash = GENESIS_PREV_HASH
    elif isinstance(first_seq, int) and first_seq > 1:
        # Mid-log slice (W-128): the slice's first prev_hash is the
        # anchor — the hash of the record just before the window,
        # which the caller supplies implicitly by slicing a real log.
        prev_hash = first_prev_hash
    else:
        # Claims the genesis position (or cannot prove it starts
        # mid-log) but does not anchor at GENESIS: forgery, not a
        # slice. Fail closed.
        return False
    for offset, record in enumerate(records):
        if record.get("prev_hash") != prev_hash:
            return False
        body = {
            k: v
            for k, v in record.items()
            if k not in ("record_hash", "signature")
        }
        if _compute_record_hash(body) != record.get("record_hash"):
            return False
        if first_seq is not None and record.get("seq") != first_seq + offset:
            return False
        if "signature" in record:
            sig = record["signature"]
            if expected_signer_pubkey is not None and (
                not isinstance(sig, dict)
                or sig.get("signer_pubkey") != expected_signer_pubkey
            ):
                return False
            if not _verify_record_signature(record):
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
    - ``.escalation_store.lock`` — advisory file lock serializing
      writes across store instances and processes (see
      :meth:`_write_transaction`): every mutation reloads state under
      the lock before appending, so concurrent emitters extend one
      chain instead of forking it.

    Pass ``signer`` (an :class:`AuditSigner`, e.g. from
    :func:`load_signer_from_env`) to sign every newly appended audit
    record. The default ``signer=None`` is byte-for-byte the Phase 1
    behavior: no signature blocks are written, and verification checks
    hash links (plus any signatures already present in the log,
    against their embedded keys).
    """

    STATE_FILENAME = "escalation_state.json"
    AUDIT_FILENAME = "escalation_audit.jsonl"
    LOCK_FILENAME = ".escalation_store.lock"

    def __init__(
        self,
        base_dir: str,
        signer: Optional[AuditSigner] = None,
        operator_keys: Optional[dict] = None,
    ):
        self._signer = signer
        #: W-122 identity binding: None = Phase 1 string identity;
        #: a registry dict = every decision must be a verified
        #: Ed25519 attestation by a registered operator identity.
        self._operator_keys = _normalize_operator_keys(operator_keys)
        self.base_dir = str(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.state_path = os.path.join(self.base_dir, self.STATE_FILENAME)
        self.audit_path = os.path.join(self.base_dir, self.AUDIT_FILENAME)
        self.lock_path = os.path.join(self.base_dir, self.LOCK_FILENAME)
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

    @contextlib.contextmanager
    def _write_transaction(self):
        """Serialize one read-modify-write cycle across store instances.

        The per-instance ``threading.Lock`` cannot coordinate the
        pattern the sanctions tap uses — a FRESH store per emission —
        let alone separate processes: two stores constructed over the
        same directory each snapshot ``_audit_seq`` / ``_last_hash`` /
        ``_packages`` at load time, and interleaved
        load→append→save cycles then fork the audit chain (duplicate
        ``seq``, two records claiming the same ``prev_hash``) and
        silently drop each other's packages from the state file.

        This transaction therefore (1) takes the instance lock, (2)
        takes an exclusive POSIX file lock on ``LOCK_FILENAME`` in the
        store directory, and (3) RELOADS packages + audit tail from
        disk under the lock, so the mutation is applied to the
        freshest state and the appended records extend the real chain
        head. Without ``fcntl`` (non-POSIX) it degrades to the instance
        lock alone. Never nested: the public mutators
        (``create_package`` / ``decide``) open exactly one transaction
        and the internals they call take no lock.
        """
        with self._lock:
            if fcntl is None:
                yield
                return
            fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._load()
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    def _save_state(self) -> None:
        payload = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "packages": {
                pid: pkg.to_dict() for pid, pkg in self._packages.items()
            },
        }
        # Unique tmp path per writer: even outside the write
        # transaction, two savers must never share one tmp file and
        # interleave their bytes before os.replace.
        tmp_path = "{}.{}.{}.{}.tmp".format(
            self.state_path, os.getpid(), threading.get_ident(), uuid.uuid4().hex
        )
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
        policy_id: Optional[str] = None,
        policy_version: Optional[str] = None,
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
        # Directive 2.1 field completion: when the audited event
        # carries them, the record names the exact policy + version
        # that governed it (from the package's triggered rule and the
        # envelope schema version).
        if policy_id is not None:
            record["policy_id"] = policy_id
        if policy_version is not None:
            record["policy_version"] = policy_version
        record["record_hash"] = _compute_record_hash(record)
        if self._signer is not None:
            # Phase 2 signing: the signature is computed over the
            # record including its record_hash, then attached; the
            # hash itself never covers the signature block.
            record["signature"] = self._signer.sign_record(record)
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

    def verify_audit_chain(
        self, expected_signer_pubkey: Optional[str] = None
    ) -> bool:
        """Verify this store's full audit log (hash chain + signatures).

        Signed records must verify against their embedded public keys.
        When this store has a signer configured, its public key is the
        expected signer unless ``expected_signer_pubkey`` overrides
        it — a log signed by a different key then fails verification.
        """
        expected = expected_signer_pubkey
        if expected is None and self._signer is not None:
            expected = self._signer.signer_pubkey
        return verify_chain(self.read_audit_log(), expected_signer_pubkey=expected)

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
        with self._write_transaction():
            self._packages[package.id] = package
            self._append_audit(
                package_id=package.id,
                event="created",
                at=created_at,
                by=actor,
                from_state=None,
                to_state=PENDING,
                detail=f"surface={source_surface}",
                policy_id=package.triggered_rule.get("policy_id") or None,
                policy_version=package.schema_version,
            )
            self._append_audit(
                package_id=package.id,
                event="classified",
                at=created_at,
                by="policy_engine",
                from_state=PENDING,
                to_state=PENDING,
                detail=classified_detail,
                policy_id=package.triggered_rule.get("policy_id") or None,
                policy_version=package.schema_version,
            )
            self._save_state()
        return self._copy(package)

    def _verify_decision_attestation(
        self,
        package_id: str,
        kind: str,
        role: str,
        by: str,
        note: Optional[str],
        signature: Optional[str],
    ) -> None:
        """W-122: verify one decision attestation against the registry.

        No-op when no operator registry is configured (Phase 1 string
        identity). Otherwise raises ValueError unless ``by`` is a
        registered identity, ``role`` is authorized for it, and
        ``signature`` is a valid Ed25519 signature by its key over
        :func:`attestation_payload_bytes`.
        """
        registry = self._operator_keys
        if registry is None:
            return
        _require_cryptography()
        spec = registry.get(str(by))
        if spec is None:
            raise ValueError(
                f"decide: identity {by!r} is not a registered operator "
                "(W-122 identity binding)"
            )
        if role not in spec["roles"]:
            raise ValueError(
                f"decide: identity {by!r} is not authorized for role "
                f"{role!r} (W-122 identity binding)"
            )
        if not signature:
            raise ValueError(
                "decide: an Ed25519 attestation signature is required "
                "when operator identity binding is configured (W-122)"
            )
        try:
            public_key = _Ed25519PublicKey.from_public_bytes(
                bytes.fromhex(spec["pubkey"])
            )
            public_key.verify(
                bytes.fromhex(str(signature)),
                attestation_payload_bytes(package_id, kind, role, by, note),
            )
        except _InvalidSignature as exc:
            raise ValueError(
                "decide: attestation signature does not verify for "
                f"identity {by!r} (W-122 identity binding)"
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "decide: malformed attestation signature or registry "
                f"key for identity {by!r} (W-122 identity binding)"
            ) from exc

    def decide(
        self,
        package_id: str,
        kind: str,
        by: str,
        note: Optional[str] = None,
        role: str = "operator",
        now: Optional[str] = None,
        signature: Optional[str] = None,
        tenant: Optional[str] = None,
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

        W-122: when the store carries an operator registry
        (``operator_keys``), the decision must arrive as a verified
        attestation — ``by`` a registered identity, ``role``
        authorized for it, and ``signature`` a valid Ed25519 signature
        over :func:`attestation_payload_bytes`; unverifiable
        attestations raise ValueError and are never recorded.
        W-129: when ``tenant`` is given it must match the package's
        tenant, else PackageNotFoundError (no cross-tenant leak).
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
        with self._write_transaction():
            package = self._packages.get(package_id)
            if package is None:
                raise PackageNotFoundError(f"no such package: {package_id}")
            if tenant is not None and package.tenant != tenant:
                # W-129: a tenant mismatch is indistinguishable from a
                # missing package — cross-tenant existence never leaks.
                raise PackageNotFoundError(f"no such package: {package_id}")
            if package.state != PENDING:
                raise IllegalTransitionError(
                    f"package {package_id} is {package.state} (terminal); "
                    f"cannot apply decision {kind_norm!r}"
                )
            self._verify_decision_attestation(
                package_id, kind_norm, role_norm, by, note, signature
            )
            required = list(_normalize_risk(package.risk)["required_signers"])
            record = Decision(
                by=by, at=decided_at, kind=kind_norm, note=note,
                role=role_norm, signature=signature,
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
                policy_id=package.triggered_rule.get("policy_id") or None,
                policy_version=package.schema_version,
            )
            self._save_state()
            return self._copy(package)

    @staticmethod
    def _transition(
        package: EscalationPackage, record: Decision, to_state: str
    ) -> None:
        package.state = to_state
        package.decision = record

    def get_package(
        self, package_id: str, tenant: Optional[str] = None
    ) -> EscalationPackage:
        """Return a copy of the package, or raise PackageNotFoundError.

        W-129: when ``tenant`` is given, a package belonging to another
        tenant raises the same PackageNotFoundError as a missing one.
        """
        package = self._packages.get(package_id)
        if package is None or (tenant is not None and package.tenant != tenant):
            raise PackageNotFoundError(f"no such package: {package_id}")
        return self._copy(package)

    def find_package(
        self, package_id: str, tenant: Optional[str] = None
    ) -> Optional[EscalationPackage]:
        """Return a copy of the package, or None if it does not exist.

        W-129: when ``tenant`` is given, another tenant's package
        returns None, exactly like a missing one.
        """
        package = self._packages.get(package_id)
        if package is None or (tenant is not None and package.tenant != tenant):
            return None
        return self._copy(package)

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
    if getattr(store, "_operator_keys", None):
        # W-122: in identity-bound mode the quorum is re-verified from
        # the attestation SIGNATURES, not just the stored by/role
        # strings — an attestation forged into the state file (right
        # strings, no valid signature) does not count.
        verified = []
        for attestation in package.attestations:
            try:
                store._verify_decision_attestation(
                    package.id,
                    attestation.kind,
                    attestation.role,
                    attestation.by,
                    attestation.note,
                    attestation.signature,
                )
                verified.append(attestation)
            except (ValueError, EscalationError):
                continue
        bound = EscalationPackage.from_dict(package.to_dict())
        bound.attestations = verified
        return _quorum_satisfied(bound)
    return _quorum_satisfied(package)
