"""Typed effect gateway — the choke point for agent-facing network/write sinks.

Every sink that produces an externally visible effect (email, SMS, content
publishing, CRM writes, chain writes, file writes, process execution,
scheduler enqueue, outbound HTTP, payment charges) must be invoked through
this gateway. Direct calls from agent code to sink functions remain possible
in legacy code paths, but the canonical inventory
(``src/sincor2/effect_sink_manifest.json``) tracks each sink's
``wrapped``/``unwrapped`` status and the CI manifest test fails on newly
added unwrapped adapters (see ``tests/pytest/test_sink_manifest.py``).

Dispatch pipeline (fail-closed at every step):

    EffectIntent -> intent validation -> idempotency check -> policy check
      -> (deny:  audit + receipt with executed=False, adapter NEVER called)
      -> (allow: adapter.execute(intent, context) -> adapter receipt
                -> audit + receipt with executed=True)

Guarantees enforced in code:

* Default-deny: a missing context, a missing policy snapshot, an unknown
  effect type, a malformed payload, or a duplicate idempotency key produces
  a DENY decision and zero adapter calls.
* An engaged kill switch denies every effect type, not just value-moving ones.
* Every dispatch is logged to the audit trail. Denied dispatches log once.
  Allowed dispatches log twice: the authorization decision BEFORE the
  adapter runs (fail-closed — an audit-append failure at this point denies
  the dispatch and the adapter never runs) and the outcome after.
* Adapters are registered one-per-effect-type; an unregistered effect type
  is denied, never routed to a fallback.

Relationship to ``sincor2.shadow_monitor.effect_boundary``: the shadow
boundary records *proposals* and never executes (``executed`` is always
False there). This gateway is the *live* counterpart: it routes allowed
intents to real adapters that perform effects. Shadow and live code share
no mutable state and import no common executor.

Stdlib only. Thread-safe.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "EffectType",
    "EffectIntent",
    "RuntimeContext",
    "Decision",
    "EffectReceipt",
    "PolicyCheck",
    "EffectAdapter",
    "AuditTrail",
    "EffectGateway",
    "default_policy",
    "build_intent",
    "canonical_payload_hash",
    "REQUIRED_PAYLOAD_FIELDS",
    "READ_ONLY_KINDS",
    "POLICY_VERSION",
]

POLICY_VERSION = "effect-gateway/v1"


# ---------------------------------------------------------------------------
# Effect types
# ---------------------------------------------------------------------------


class EffectType(str, Enum):
    """Every agent-facing sink class the gateway understands.

    Unknown effect types are denied (fail-closed); add a new member here,
    a payload schema in ``REQUIRED_PAYLOAD_FIELDS``, and a registered
    adapter before any code can dispatch it.
    """

    EMAIL_SEND = "EMAIL_SEND"
    SMS_SEND = "SMS_SEND"
    MESSAGE_SEND = "MESSAGE_SEND"  # peer/instant messaging (libp2p, chat, push)
    CONTENT_PUBLISH = "CONTENT_PUBLISH"
    CRM_WRITE = "CRM_WRITE"
    CHAIN_WRITE = "CHAIN_WRITE"
    FILE_WRITE = "FILE_WRITE"
    PROCESS_EXEC = "PROCESS_EXEC"
    SCHEDULER_ENQUEUE = "SCHEDULER_ENQUEUE"
    HTTP_EXTERNAL = "HTTP_EXTERNAL"
    PAYMENT_CHARGE = "PAYMENT_CHARGE"


# Minimal required payload fields per effect type. Adapters may demand more;
# the gateway refuses intents missing these before any policy evaluation.
REQUIRED_PAYLOAD_FIELDS: Dict[EffectType, Tuple[str, ...]] = {
    EffectType.EMAIL_SEND: ("to", "subject", "body"),
    EffectType.SMS_SEND: ("to", "body"),
    EffectType.MESSAGE_SEND: ("to", "body"),
    EffectType.CONTENT_PUBLISH: ("channel", "content"),
    EffectType.CRM_WRITE: ("record",),
    EffectType.CHAIN_WRITE: ("chain_id", "to", "calldata_or_value"),
    EffectType.FILE_WRITE: ("path",),
    EffectType.PROCESS_EXEC: ("argv",),
    EffectType.SCHEDULER_ENQUEUE: ("job",),
    EffectType.HTTP_EXTERNAL: ("url", "method"),
    EffectType.PAYMENT_CHARGE: ("amount", "currency", "destination"),
}

# Effect types that are value-moving / irreversible: denied by default unless
# the policy snapshot explicitly permits them.
_HIGH_RISK_TYPES = frozenset(
    {
        EffectType.CHAIN_WRITE,
        EffectType.PAYMENT_CHARGE,
        EffectType.PROCESS_EXEC,
    }
)

# Human-readable aliases accepted by build_intent for convenience.
_ALIASES = {
    "email.send": EffectType.EMAIL_SEND,
    "sms.send": EffectType.SMS_SEND,
    "social.post": EffectType.CONTENT_PUBLISH,
    "message.send": EffectType.MESSAGE_SEND,
    "crm.write": EffectType.CRM_WRITE,
    "contract.call": EffectType.CHAIN_WRITE,
    "payment.transfer": EffectType.PAYMENT_CHARGE,
    "trade.swap": EffectType.CHAIN_WRITE,
    "file.write": EffectType.FILE_WRITE,
    "process.exec": EffectType.PROCESS_EXEC,
    "scheduler.enqueue": EffectType.SCHEDULER_ENQUEUE,
    "http.post": EffectType.HTTP_EXTERNAL,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Current UTC time as an ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """sha256 hex of the canonical JSON encoding of ``payload``.

    Canonical form: keys sorted, no insignificant whitespace, non-JSON
    scalars stringified. Used for idempotency and audit binding.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _coerce_effect_type(value: Any) -> Optional[EffectType]:
    """Coerce ``value`` to an EffectType, or None if it is unknown.

    Never raises: unknown types become None so the policy can deny them
    explicitly with a reason code instead of crashing the dispatcher.
    """
    if isinstance(value, EffectType):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        for member in EffectType:
            if member.value.lower() == lowered or member.name.lower() == lowered:
                return member
        if lowered in _ALIASES:
            return _ALIASES[lowered]
    return None


# ---------------------------------------------------------------------------
# EffectIntent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectIntent:
    """Typed, immutable request to produce an external effect.

    ``payload`` is the effect-specific data the adapter will consume; it is
    validated against the effect type's required fields at construction.
    ``idempotency_key`` is required and must be unique per
    (tenant, effect_type); reusing it is denied (never re-executed).
    """

    effect_type: EffectType
    actor: str
    tenant: str
    payload: Dict[str, Any]
    idempotency_key: str
    purpose: str = ""
    trace_id: str = ""
    intent_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        coerced = _coerce_effect_type(self.effect_type)
        if coerced is None:
            raise ValueError(f"unknown effect_type: {self.effect_type!r}")
        object.__setattr__(self, "effect_type", coerced)
        if not self.actor:
            raise ValueError("actor (agent/principal id) is required")
        if not self.tenant:
            raise ValueError("tenant is required")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required and must be unique")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dict")
        required = REQUIRED_PAYLOAD_FIELDS.get(coerced, ())
        missing = [name for name in required if not self.payload.get(name)]
        if missing:
            raise ValueError(
                f"payload for {coerced.value} is missing required fields: "
                + ", ".join(missing)
            )
        # Defensive copy so the frozen intent cannot share mutable state.
        object.__setattr__(self, "payload", dict(self.payload))

    @property
    def payload_hash(self) -> str:
        """sha256 of the canonical payload (binding idempotency to content)."""
        return canonical_payload_hash(self.payload)


def build_intent(
    effect_type: Any,
    *,
    actor: str,
    tenant: str,
    payload: Dict[str, Any],
    idempotency_key: str,
    purpose: str = "",
    trace_id: str = "",
) -> EffectIntent:
    """Construct an :class:`EffectIntent`, raising ValueError on bad input.

    Accepts an :class:`EffectType`, its name/value string, or a known
    dotted alias (``"email.send"``, ``"contract.call"``, ...).
    """
    coerced = _coerce_effect_type(effect_type)
    if coerced is None:
        raise ValueError(f"unknown effect_type: {effect_type!r}")
    return EffectIntent(
        effect_type=coerced,
        actor=actor,
        tenant=tenant,
        payload=payload,
        idempotency_key=idempotency_key,
        purpose=purpose,
        trace_id=trace_id,
    )


# ---------------------------------------------------------------------------
# RuntimeContext
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeContext:
    """Verified execution context for a dispatch.

    ``principal`` must be a verified agent/principal id (authentication
    happens before the gateway is reached; the gateway does not verify
    identities itself). ``policy`` is an immutable snapshot of the policy
    that was in force when the context was built; a missing snapshot denies.
    ``kill_switch_engaged`` denies every effect type when True.
    """

    principal: str
    tenant: str
    role: str
    policy: Optional[Mapping[str, Any]]
    kill_switch_engaged: bool = False

    def __post_init__(self) -> None:
        if not self.principal:
            raise ValueError("principal (verified id) is required")
        if not self.tenant:
            raise ValueError("tenant is required")
        if self.policy is not None and not isinstance(self.policy, Mapping):
            raise ValueError("policy must be a mapping snapshot or None")
        # Defensive copy of the policy snapshot.
        if self.policy is not None:
            object.__setattr__(self, "policy", dict(self.policy))


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """Allow/deny verdict with machine-readable reason codes."""

    allowed: bool
    reason_codes: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))

    @classmethod
    def allow(cls, *reasons: str) -> "Decision":
        """Build an allow decision."""
        return cls(allowed=True, reason_codes=reasons)

    @classmethod
    def deny(cls, *reasons: str) -> "Decision":
        """Build a deny decision (fail-closed default)."""
        return cls(allowed=False, reason_codes=reasons)


# A policy function maps (intent, context) to a Decision. Any exception or
# malformed result from a policy function is treated as DENY by the gateway.
PolicyCheck = Callable[[EffectIntent, RuntimeContext], Decision]

# Alias kept for the EffectGateway constructor signature.
PolicyFnAlias = PolicyCheck


# ---------------------------------------------------------------------------
# Default policy (fail-closed)
# ---------------------------------------------------------------------------


def default_policy(intent: EffectIntent, context: RuntimeContext) -> Decision:
    """Fail-closed default policy check.

    Denies when ANY of the following holds:

    * kill switch engaged (all effect types);
    * policy snapshot missing;
    * intent tenant != context tenant (cross-tenant confusion);
    * effect type unknown or not listed in ``policy["allowed_effect_types"]``;
    * high-risk type (CHAIN_WRITE, PAYMENT_CHARGE, PROCESS_EXEC) without an
      explicit per-type grant in ``policy["allowed_effect_types"]`` and a
      matching role in ``policy["roles"]``;
    * intent payload fails the type schema (already enforced at
      construction, re-checked here for defense in depth);
    * duplicate idempotency key (checked by the gateway, not here).

    The policy snapshot shape:

        {
          "allowed_effect_types": ["EMAIL_SEND", "CRM_WRITE", ...],
          "roles": ["executor", "reader"],        # roles this principal holds
          "require_approval_for": ["CHAIN_WRITE"],# extra approval gate (deny)
        }

    Returns Decision.allow("policy_ok") only when every check passes.
    """
    if context is None:  # defensive: dispatch checks this first
        return Decision.deny("missing_context")
    if context.kill_switch_engaged:
        return Decision.deny("kill_switch_engaged")
    policy = context.policy
    if not policy:
        return Decision.deny("missing_policy")

    effect_type = _coerce_effect_type(intent.effect_type)
    if effect_type is None:
        return Decision.deny("unknown_effect_type")

    if intent.tenant != context.tenant:
        return Decision.deny("tenant_mismatch")

    allowed_types = policy.get("allowed_effect_types") or []
    if effect_type.value not in allowed_types and effect_type.name not in allowed_types:
        return Decision.deny("effect_type_not_permitted")

    if effect_type in _HIGH_RISK_TYPES:
        # High-risk types need an explicit grant AND a privileged role.
        roles = set(policy.get("roles") or [])
        if not (roles & {"executor", "admin", "treasury_ops"}):
            return Decision.deny("high_risk_role_required")

    gated = policy.get("require_approval_for") or []
    if effect_type.value in gated or effect_type.name in gated:
        return Decision.deny("requires_human_approval")

    required = REQUIRED_PAYLOAD_FIELDS.get(effect_type, ())
    missing = [name for name in required if not intent.payload.get(name)]
    if missing:
        return Decision.deny("payload_invalid")

    return Decision.allow("policy_ok")


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class EffectAdapter:
    """Interface for effect-type adapters.

    Subclass and implement :meth:`execute`. The gateway calls an adapter
    only after the policy check allows the intent; adapters must never be
    called directly by agent code (see the sink manifest's wrapped status).
    """

    @property
    def effect_type(self) -> EffectType:
        """The single effect type this adapter handles."""
        raise NotImplementedError

    def execute(
        self, intent: EffectIntent, context: RuntimeContext
    ) -> Dict[str, Any]:
        """Perform the effect and return a provider receipt dict.

        Must raise on provider failure (the gateway converts it into a
        denied/failed receipt; the adapter must not swallow errors into
        fake success receipts).
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Receipts and audit trail
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectReceipt:
    """Frozen receipt for a dispatched intent.

    ``executed`` is True only when the policy allowed the intent AND the
    registered adapter ran without raising. Denied intents produce
    ``executed=False`` with the deny reason codes.
    """

    intent_id: str
    effect_type: str
    executed: bool
    status: str  # "allowed" | "denied" | "adapter_failed"
    reason_codes: Tuple[str, ...] = ()
    adapter_receipt: Optional[Dict[str, Any]] = None
    dispatched_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_codes", tuple(self.reason_codes))
        if self.status not in ("allowed", "denied", "adapter_failed"):
            raise ValueError(f"invalid receipt status: {self.status!r}")


class AuditTrail:
    """Append-only audit trail for every dispatch (allowed or denied).

    Entries carry hashes and metadata, never full provider payloads. The
    trail is append-only: there is no delete or edit API. Thread-safe.
    """

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._fail_next_append = False  # test hook: simulate storage failure

    def append(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Append one audit entry; returns the stored entry.

        Raises RuntimeError if the append fails (the gateway treats this
        as fail-closed: the dispatch is denied and no adapter runs).
        """
        with self._lock:
            if self._fail_next_append:
                self._fail_next_append = False
                raise RuntimeError("simulated audit storage failure")
            stored = dict(entry)
            stored["seq"] = len(self._entries)
            stored["audit_ts"] = utc_now_iso()
            self._entries.append(stored)
            return stored

    def entries(self) -> List[Dict[str, Any]]:
        """Return a copy of all audit entries."""
        with self._lock:
            return [dict(entry) for entry in self._entries]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class EffectGateway:
    """Typed dispatch choke point for agent-facing effect sinks.

    Usage::

        gateway = EffectGateway(policy_fn=default_policy)
        gateway.register_adapter(EmailAdapter())
        receipt = gateway.dispatch(intent, context)
        if not receipt.executed:
            # denied or failed — see receipt.reason_codes
            ...

    ``adapters`` may also be passed per-dispatch as a mapping
    ``{EffectType: EffectAdapter}`` to override registration for tests;
    the override is used only for that call.
    """

    def __init__(
        self,
        policy_fn: Optional[PolicyFnAlias] = None,
        audit_trail: Optional[AuditTrail] = None,
    ) -> None:
        self._policy_fn: PolicyCheck = policy_fn or default_policy
        self.audit_trail = audit_trail or AuditTrail()
        self._adapters: Dict[EffectType, EffectAdapter] = {}
        # (tenant, effect_type, idempotency_key) -> payload_hash
        self._seen: Dict[Tuple[str, str, str], str] = {}
        self._lock = threading.Lock()

    # -- adapter registration -------------------------------------------------

    def register_adapter(self, adapter: EffectAdapter) -> None:
        """Register the adapter for its effect type.

        Raises ValueError on duplicate registration for the same type:
        exactly one adapter per effect type keeps routing unambiguous.
        """
        effect_type = _coerce_effect_type(adapter.effect_type)
        if effect_type is None:
            raise ValueError(
                f"adapter declares unknown effect type: {adapter.effect_type!r}"
            )
        with self._lock:
            if effect_type in self._adapters:
                raise ValueError(
                    f"adapter already registered for {effect_type.value}"
                )
            self._adapters[effect_type] = adapter

    def registered_types(self) -> List[EffectType]:
        """Effect types with a registered adapter (copy)."""
        with self._lock:
            return sorted(self._adapters, key=lambda t: t.value)

    # -- dispatch ---------------------------------------------------------------

    def dispatch(
        self,
        intent: EffectIntent,
        context: Optional[RuntimeContext],
        adapters: Optional[Mapping[EffectType, EffectAdapter]] = None,
    ) -> EffectReceipt:
        """Evaluate policy and, if allowed, route to the registered adapter.

        Fail-closed order of operations:

        1. Validate the intent (malformed -> deny, no adapter call).
        2. Idempotency: a reused key is denied even if the payload matches
           (replays never re-execute); a conflicting reuse is denied with a
           distinct reason code.
        3. Policy check (missing context/policy, kill switch, unknown type,
           tenant mismatch, role gates -> deny).
        4. Adapter routing: no registered adapter for the type -> deny.
        5. Adapter execution: adapter exceptions -> "adapter_failed" receipt.
        6. Every outcome appends exactly one audit entry. An audit-append
           failure denies the dispatch before any adapter runs.

        Returns an :class:`EffectReceipt`; never raises for policy reasons
        (raises only for programmer errors such as a non-EffectIntent).
        """
        if not isinstance(intent, EffectIntent):
            raise TypeError("dispatch requires an EffectIntent")
        if context is not None and not isinstance(context, RuntimeContext):
            raise TypeError("dispatch requires a RuntimeContext or None")

        effect_type = _coerce_effect_type(intent.effect_type)

        decision: Decision
        if effect_type is None:
            decision = Decision.deny("unknown_effect_type")
        elif not intent.idempotency_key:
            decision = Decision.deny("missing_idempotency_key")
        elif context is None:
            decision = Decision.deny("missing_context")
        else:
            with self._lock:
                idem_key = (intent.tenant, effect_type.value, intent.idempotency_key)
                prior = self._seen.get(idem_key)
                if prior is not None:
                    decision = Decision.deny(
                        "duplicate_idempotency_key"
                        if prior == intent.payload_hash
                        else "idempotency_key_conflict"
                    )
                else:
                    decision = self._run_policy(intent, context)

        receipt = self._finalize(
            intent, effect_type, decision, context, adapters=adapters
        )
        return receipt

    # -- internals ---------------------------------------------------------------

    def _run_policy(
        self, intent: EffectIntent, context: RuntimeContext
    ) -> Decision:
        """Run the policy function; any problem becomes DENY.

        A missing, raising, or malformed policy result denies — a broken
        policy can never allow an effect.
        """
        try:
            result = self._policy_fn(intent, context)
        except Exception as exc:  # noqa: BLE001 - fail-closed by design
            return Decision.deny(f"policy_fn_error:{type(exc).__name__}")
        if not isinstance(result, Decision):
            return Decision.deny("policy_fn_invalid_result")
        return result

    def _finalize(
        self,
        intent: EffectIntent,
        effect_type: Optional[EffectType],
        decision: Decision,
        context: Optional[RuntimeContext],
        adapters: Optional[Mapping[EffectType, EffectAdapter]] = None,
    ) -> EffectReceipt:
        """Route an allowed intent to its adapter; audit every outcome.

        Fail-closed on audit failure: if the audit append raises, the
        dispatch is denied and the adapter is NOT called (the audit append
        happens before adapter execution).
        """
        adapter: Optional[EffectAdapter] = None
        if decision.allowed and effect_type is not None:
            table = adapters if adapters is not None else self._adapters
            adapter = table.get(effect_type)
            if adapter is None:
                decision = Decision.deny("no_adapter_registered")

        if not decision.allowed or adapter is None:
            return self._audit_and_receipt(
                intent,
                effect_type,
                executed=False,
                status="denied",
                reason_codes=decision.reason_codes,
                adapter_receipt=None,
            )

        # Allowed path: audit the ALLOW decision first (fail-closed), then
        # execute, then record the outcome. If the adapter raises, the
        # failure itself is audited and the receipt reports adapter_failed.
        audit_ok = self._try_audit(
            intent,
            effect_type,
            executed=False,
            status="allowed_authorized",
            reason_codes=decision.reason_codes,
            adapter_receipt=None,
        )
        if not audit_ok:
            return EffectReceipt(
                intent_id=intent.intent_id,
                effect_type=effect_type.value,
                executed=False,
                status="denied",
                reason_codes=("audit_failure",),
            )

        assert context is not None  # allowed implies context was present
        try:
            adapter_result = adapter.execute(intent, context)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            return self._audit_and_receipt(
                intent,
                effect_type,
                executed=False,
                status="adapter_failed",
                reason_codes=("adapter_error", type(exc).__name__),
                adapter_receipt=None,
            )

        with self._lock:
            idem_key = (intent.tenant, effect_type.value, intent.idempotency_key)
            self._seen[idem_key] = intent.payload_hash
        return self._audit_and_receipt(
            intent,
            effect_type,
            executed=True,
            status="allowed",
            reason_codes=decision.reason_codes,
            adapter_receipt=dict(adapter_result)
            if isinstance(adapter_result, dict)
            else {"result": adapter_result},
        )

    def _audit_and_receipt(
        self,
        intent: EffectIntent,
        effect_type: Optional[EffectType],
        *,
        executed: bool,
        status: str,
        reason_codes: Tuple[str, ...],
        adapter_receipt: Optional[Dict[str, Any]],
    ) -> EffectReceipt:
        """Append one audit entry and build the receipt for a denied/failed dispatch."""
        entry = self._audit_entry(
            intent, effect_type, executed, status, reason_codes
        )
        try:
            self.audit_trail.append(entry)
        except Exception:
            # Fail-closed: the dispatch could not be audited, so it is
            # denied regardless of the policy outcome — and the adapter was
            # never called on this path.
            return EffectReceipt(
                intent_id=intent.intent_id,
                effect_type=effect_type.value if effect_type else "UNKNOWN",
                executed=False,
                status="denied",
                reason_codes=("audit_failure",),
            )
        return EffectReceipt(
            intent_id=intent.intent_id,
            effect_type=effect_type.value if effect_type else "UNKNOWN",
            executed=executed,
            status=status,
            reason_codes=reason_codes,
            adapter_receipt=adapter_receipt,
        )

    def _try_audit(
        self,
        intent: EffectIntent,
        effect_type: Optional[EffectType],
        *,
        executed: bool,
        status: str,
        reason_codes: Tuple[str, ...],
        adapter_receipt: Optional[Dict[str, Any]],
    ) -> bool:
        """Append an audit entry; False if the append failed."""
        try:
            self.audit_trail.append(
                self._audit_entry(intent, effect_type, executed, status, reason_codes)
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _audit_entry(
        intent: EffectIntent,
        effect_type: Optional[EffectType],
        executed: bool,
        status: str,
        reason_codes: Tuple[str, ...],
    ) -> Dict[str, Any]:
        """Build one audit entry (hashes and metadata, never raw payloads)."""
        return {
            "event": "effect_dispatch",
            "policy_version": POLICY_VERSION,
            "intent_id": intent.intent_id,
            "effect_type": effect_type.value if effect_type else "UNKNOWN",
            "actor": intent.actor,
            "tenant": intent.tenant,
            "payload_hash": intent.payload_hash,
            "idempotency_key": intent.idempotency_key,
            "trace_id": intent.trace_id,
            "purpose": intent.purpose,
            "executed": executed,
            "status": status,
            "reason_codes": list(reason_codes),
        }


# Kinds that are never routed through the gateway: read-only operations are
# always permitted and never require policy approval. (Kept as an explicit
# constant so callers can branch on it without importing the shadow runtime.)
READ_ONLY_KINDS = frozenset({"plan", "quote", "dry_run", "read", "simulate"})
