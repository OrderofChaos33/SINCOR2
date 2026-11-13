"""EffectIntent / EffectReceipt shadow boundary for SINCOR2 Phase 1.

Companion to :mod:`sincor2.shadow_monitor.boundary`. Where the adapter-level
``ShadowBoundary`` records *what an adapter would do*, this module records
*what an effect would be* as a first-class, auditable object -- and still
never performs it.

The pattern:

    EffectIntent            # frozen evidence of a desired effect; carries a
                          # sha256 of the canonical payload, NEVER the payload
        --> ShadowEffectBoundary.dispatch(intent)
                          # 1. validates the effect type
                          # 2. checks idempotency (tenant, effect_type, key)
                          # 3. evaluates policy (injectable; fail-closed default)
                          # 4. appends an audit event (hash only; failure is fatal)
                          # 5. appends the intent to the proposal queue (a list)
        --> EffectReceipt   # frozen receipt: executed is ALWAYS False in
                          # shadow; status is one of "would_execute",
                          # "blocked_policy", "pending_approval" -- never
                          # "sent", "settled" or "delivered".

Structural guarantees (enforced in code, documented as constraints):

    * The live executor lives in a SEPARATE DEPLOYMENT. It is never
      importable from agent worker code. ``build_effect_boundary("live")``
      refuses to construct a live boundary; ``assert_no_live_import()`` and
      ``guard_live_adapter_import()`` raise if a live adapter module ever
      appears in ``sys.modules``.
    * The proposal queue is a plain ``list``. No code path in this module
      moves items from it into anything named like a live destination
      (there is no live destination to move them to).
    * Dispatch is fail-closed: unknown effect types raise, a missing or
      malformed policy result becomes ``blocked_policy``, an audit-append
      failure raises ``AuditFailureError`` before any receipt is issued, and
      an engaged kill switch blocks would-pay effect kinds.
    * Idempotency is tracked per (tenant, effect_type, idempotency_key): an
      identical retry returns the original receipt (no duplicate queue
      entry); a conflicting retry raises ``IdempotencyConflict``.
    * ``verify_approval(intent, receipt)`` recomputes the approval hash over
      the canonical intent fields; any edited proposal fails verification.

Stdlib only. Thread-safe where state is shared.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import sys
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from sincor2.shadow_monitor.boundary import KillSwitch

__all__ = [
    "EFFECT_TYPES",
    "WOULD_PAY_EFFECT_TYPES",
    "RISK_TIERS",
    "RECEIPT_STATUSES",
    "POLICY_VERSION",
    "EffectIntent",
    "EffectReceipt",
    "ShadowEffectBoundary",
    "LogOnlyAdapter",
    "AuditFailureError",
    "IdempotencyConflict",
    "ImportBoundaryViolation",
    "build_effect_boundary",
    "assert_no_live_import",
    "guard_live_adapter_import",
    "verify_approval",
    "canonical_payload_hash",
    "canonical_intent_hash",
    "default_shadow_policy",
    "utc_now_iso",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLICY_VERSION = "effect-boundary/v1"

# Effect types the shadow boundary understands. Anything else is rejected.
EFFECT_TYPES = frozenset(
    {
        "email.send",
        "social.post",
        "crm.write",
        "crm.delete",
        "payment.transfer",
        "trade.swap",
        "contract.call",
        "message.send",
    }
)

# Effect types that move value / change on-chain state: the kill switch
# blocks these first.
WOULD_PAY_EFFECT_TYPES = frozenset(
    {"payment.transfer", "trade.swap", "contract.call"}
)

RISK_TIERS = frozenset({"low", "medium", "high", "critical"})

# The ONLY statuses a shadow receipt may carry. "sent", "settled" and
# "delivered" can never be produced by shadow code -- there is no code path
# here that performs an effect.
RECEIPT_STATUSES = ("would_execute", "blocked_policy", "pending_approval")
ReceiptStatus = Literal["would_execute", "blocked_policy", "pending_approval"]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AuditFailureError(RuntimeError):
    """Raised when the audit append fails during dispatch.

    Fail-closed: no receipt is issued and the intent is not queued, so a
    dispatch that cannot be audited cannot be mistaken for one that was.
    """


class IdempotencyConflict(ValueError):
    """Raised when an idempotency key is reused with a different payload."""


class ImportBoundaryViolation(ImportError):
    """Raised when a live executor/adapter module crosses the import boundary.

    Agent worker code must never be able to import the live executor; the
    live executor is deployed separately and is not on the worker's path.
    """


# ---------------------------------------------------------------------------
# Canonical hashing helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """Current UTC time as an ISO8601 string."""
    return datetime.now(timezone.utc).isoformat()


def canonical_payload_hash(payload: Dict[str, Any]) -> str:
    """sha256 hex of the canonical JSON encoding of ``payload``.

    Canonical form: keys sorted, no insignificant whitespace. The full
    payload is NEVER stored on intents, receipts, or audit events -- only
    this hash.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_intent_hash(intent: "EffectIntent") -> str:
    """sha256 hex over the canonical EffectIntent fields.

    Used as the receipt's ``approval_hash``: any edit to any intent field
    changes the hash, invalidating prior approvals.
    """
    fields = {
        "effect_id": intent.effect_id,
        "effect_type": intent.effect_type,
        "target": intent.target,
        "payload_hash": intent.payload_hash,
        "idempotency_key": intent.idempotency_key,
        "trace_id": intent.trace_id,
        "agent_id": intent.agent_id,
        "tenant": intent.tenant,
        "risk_tier": intent.risk_tier,
        "estimated_cost": intent.estimated_cost,
        "requested_at": intent.requested_at,
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# EffectIntent / EffectReceipt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EffectIntent:
    """Frozen evidence of a desired effect.

    Carries ``payload_hash`` (sha256 of canonical payload JSON) -- NEVER the
    full payload. Immutable once constructed: evidence cannot be edited, only
    superseded by a new intent (which gets a new effect_id / approval hash).
    """

    effect_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    effect_type: str = ""
    target: str = ""
    payload_hash: str = ""
    idempotency_key: str = ""
    trace_id: str = ""
    agent_id: str = ""
    tenant: str = ""
    risk_tier: str = "low"
    estimated_cost: float = 0.0
    requested_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if self.effect_type not in EFFECT_TYPES:
            raise ValueError(f"unknown effect_type: {self.effect_type!r}")
        if self.risk_tier not in RISK_TIERS:
            raise ValueError(f"unknown risk_tier: {self.risk_tier!r}")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")

    @classmethod
    def from_payload(
        cls,
        *,
        effect_type: str,
        target: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        trace_id: str = "",
        agent_id: str = "",
        tenant: str = "",
        risk_tier: str = "low",
        estimated_cost: float = 0.0,
    ) -> "EffectIntent":
        """Build an intent from a payload dict, hashing (never storing) it."""
        return cls(
            effect_type=effect_type,
            target=target,
            payload_hash=canonical_payload_hash(payload),
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            agent_id=agent_id,
            tenant=tenant,
            risk_tier=risk_tier,
            estimated_cost=estimated_cost,
        )


@dataclass(frozen=True)
class EffectReceipt:
    """Frozen receipt for a dispatched intent.

    ``executed`` is ALWAYS False in shadow: no receipt produced here may ever
    claim an effect was performed. ``status`` is restricted to the three
    shadow literals -- "sent"/"settled"/"delivered" are unrepresentable.
    """

    effect_id: str = ""
    status: ReceiptStatus = "blocked_policy"
    executed: bool = False
    policy_reason_codes: List[str] = field(default_factory=list)
    approval_hash: Optional[str] = None
    queued_at: str = field(default_factory=utc_now_iso)
    proposal_queue_ref: str = ""

    def __post_init__(self) -> None:
        if self.executed:
            raise ValueError(
                "shadow receipts must never claim executed=True "
                "(no effect is ever performed in shadow mode)"
            )
        if self.status not in RECEIPT_STATUSES:
            raise ValueError(f"invalid shadow receipt status: {self.status!r}")
        # Defensive copy so the frozen receipt cannot share mutable state
        # with its caller's list.
        object.__setattr__(self, "policy_reason_codes", list(self.policy_reason_codes))


# ---------------------------------------------------------------------------
# Default policy (fail-closed)
# ---------------------------------------------------------------------------

# A policy function maps an intent to (decision, reason_codes) where decision
# is one of the RECEIPT_STATUSES literals.
PolicyFn = Callable[[EffectIntent], Tuple[str, List[str]]]


def default_shadow_policy(intent: EffectIntent) -> Tuple[str, List[str]]:
    """Fail-closed default policy.

    * high / critical risk            -> blocked_policy (deny)
    * medium risk with nonzero cost   -> pending_approval
    * medium risk with zero cost      -> would_execute
    * low risk                        -> would_execute
    * unknown risk tier               -> blocked_policy (fail closed)
    """
    tier = intent.risk_tier
    if tier in ("high", "critical"):
        return ("blocked_policy", [f"risk_tier_{tier}_denied"])
    if tier == "medium":
        if intent.estimated_cost > 0:
            return ("pending_approval", ["medium_risk_cost_requires_approval"])
        return ("would_execute", [])
    if tier == "low":
        return ("would_execute", [])
    return ("blocked_policy", ["unknown_risk_tier"])


# ---------------------------------------------------------------------------
# Import-boundary guards
# ---------------------------------------------------------------------------

# Module-name fragments that must never appear in a worker process's imports.
# The live executor is deployed separately; worker code cannot reach it.
_LIVE_MODULE_FRAGMENTS = ("live_executor", "live_adapter")


def assert_no_live_import() -> None:
    """Raise ImportBoundaryViolation if a live module is in sys.modules.

    Import-boundary guard: scans loaded module names for live executor /
    live adapter fragments. Call at worker startup (and in tests) to prove
    the live deployment is not reachable from this process.
    """
    offenders = [
        name
        for name in sys.modules
        if any(fragment in name for fragment in _LIVE_MODULE_FRAGMENTS)
    ]
    if offenders:
        raise ImportBoundaryViolation(
            "live executor/adapter module loaded in a shadow worker process: "
            + ", ".join(sorted(offenders))
        )


def guard_live_adapter_import(module_name: str) -> None:
    """Raise ImportBoundaryViolation for live adapter module names.

    A narrow, explicit guard for code paths that resolve adapter modules by
    name: any name containing a live fragment is refused before import.
    """
    lowered = module_name.lower()
    if any(fragment in lowered for fragment in _LIVE_MODULE_FRAGMENTS):
        raise ImportBoundaryViolation(
            f"refusing to import live adapter module: {module_name!r} "
            "(live executor lives in a separate deployment)"
        )


# ---------------------------------------------------------------------------
# Shadow effect boundary
# ---------------------------------------------------------------------------


class ShadowEffectBoundary:
    """Choke point for effect intents in shadow mode.

    ``dispatch(intent)`` evaluates policy, records a hash-only audit event,
    appends the intent to the proposal queue (a plain ``list``), and returns
    a frozen receipt with ``executed=False``.

    Constraints, enforced by construction:

    * There is no live destination in this class. The proposal queue is a
      plain list and no method moves items from it into anything live --
      there is nothing live to move them to. (See the source-scan test.)
    * The kill switch blocks would-pay effect kinds (payment.transfer,
      trade.swap, contract.call) while engaged.
    * Idempotency is tracked per (tenant, effect_type, idempotency_key).
    """

    def __init__(
        self,
        policy_fn: Optional[PolicyFn] = None,
        kill_switch: Optional[KillSwitch] = None,
        audit_log: Optional[List[Dict[str, Any]]] = None,
    ):
        self._policy_fn: PolicyFn = policy_fn or default_shadow_policy
        self.kill_switch = kill_switch or KillSwitch()
        # Internal, plain-list stores. The audit log carries hashes, never
        # payloads; the proposal queue carries frozen intents, never effects.
        self._audit_log: List[Dict[str, Any]] = audit_log if audit_log is not None else []
        self._proposal_queue: List[EffectIntent] = []
        # (tenant, effect_type, idempotency_key) -> (payload_hash, receipt)
        self._seen: Dict[Tuple[str, str, str], Tuple[str, EffectReceipt]] = {}
        self._lock = threading.Lock()

    # -- inspection ----------------------------------------------------------

    @property
    def proposal_queue(self) -> List[EffectIntent]:
        """The proposal queue: a plain list of frozen intents (copy)."""
        with self._lock:
            return list(self._proposal_queue)

    @property
    def audit_log(self) -> List[Dict[str, Any]]:
        """Audit events, hash-only (copy)."""
        with self._lock:
            return list(self._audit_log)

    def seen_idempotency_keys(self) -> List[Tuple[str, str, str]]:
        with self._lock:
            return sorted(self._seen)

    # -- policy ----------------------------------------------------------------

    def _evaluate_policy(self, intent: EffectIntent) -> Tuple[str, List[str]]:
        """Run the policy function, fail-closed on any problem.

        A missing, raising, or malformed policy result becomes
        ``blocked_policy`` -- a broken policy denies, never allows.
        """
        try:
            result = self._policy_fn(intent)
        except Exception as exc:  # noqa: BLE001 - fail-closed by design
            return ("blocked_policy", [f"policy_fn_error:{type(exc).__name__}"])
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or result[0] not in RECEIPT_STATUSES
            or not isinstance(result[1], (list, tuple))
        ):
            return ("blocked_policy", ["policy_fn_invalid_result"])
        return (result[0], list(result[1]))

    # -- dispatch ----------------------------------------------------------------

    def dispatch(self, intent: EffectIntent) -> EffectReceipt:
        """Evaluate, audit, queue, and receipt an effect intent.

        Steps (in order):

        1. Validate the effect type (unknown -> ValueError).
        2. Idempotency: identical retry returns the original receipt without
           re-queueing; conflicting retry raises IdempotencyConflict.
        3. Policy evaluation (fail-closed); engaged kill switch forces
           ``blocked_policy`` for would-pay effect kinds.
        4. Audit append (hash-only event). Failure raises AuditFailureError
           BEFORE any receipt is issued -- nothing is queued on this path.
        5. Append the intent to the proposal queue.
        6. Return a frozen receipt with executed=False.

        Nothing here performs an effect. The receipt can never say otherwise.
        """
        if intent.effect_type not in EFFECT_TYPES:
            raise ValueError(f"unknown effect_type: {intent.effect_type!r}")

        idem_key = (intent.tenant, intent.effect_type, intent.idempotency_key)

        with self._lock:
            seen = self._seen.get(idem_key)
            if seen is not None:
                prior_hash, prior_receipt = seen
                if prior_hash != intent.payload_hash:
                    raise IdempotencyConflict(
                        f"idempotency key {intent.idempotency_key!r} reused with a "
                        f"different payload_hash for {intent.effect_type!r}"
                    )
                # Identical retry: return the original receipt, no duplicate
                # queue entry, no duplicate audit event.
                return prior_receipt

            decision, reason_codes = self._evaluate_policy(intent)

            # Kill switch: while engaged, would-pay effect kinds are blocked
            # even if policy would allow them.
            if (
                self.kill_switch.engaged
                and intent.effect_type in WOULD_PAY_EFFECT_TYPES
            ):
                decision = "blocked_policy"
                reason_codes = ["kill_switch_engaged_would_pay_blocked"]

            event: Dict[str, Any] = {
                "event": "effect_intent_dispatched",
                "policy_version": POLICY_VERSION,
                "effect_id": intent.effect_id,
                "effect_type": intent.effect_type,
                "target": intent.target,
                "payload_hash": intent.payload_hash,  # hash only -- never payload
                "idempotency_key": intent.idempotency_key,
                "trace_id": intent.trace_id,
                "agent_id": intent.agent_id,
                "tenant": intent.tenant,
                "risk_tier": intent.risk_tier,
                "estimated_cost": intent.estimated_cost,
                "requested_at": intent.requested_at,
                "decision": decision,
                "reason_codes": list(reason_codes),
                "audit_ts": utc_now_iso(),
            }
            try:
                self._audit_log.append(event)
            except Exception as exc:
                # Fail-closed: the dispatch is NOT auditable, so it must not
                # produce a receipt or a queue entry.
                raise AuditFailureError(
                    f"audit append failed for effect {intent.effect_id}: {exc}"
                ) from exc

            queue_ref = f"proposal-queue:{len(self._proposal_queue)}"
            self._proposal_queue.append(intent)

            receipt = EffectReceipt(
                effect_id=intent.effect_id,
                status=decision,  # type: ignore[arg-type] -- validated literal
                executed=False,  # invariant: shadow never executes
                policy_reason_codes=list(reason_codes),
                approval_hash=canonical_intent_hash(intent),
                proposal_queue_ref=queue_ref,
            )
            self._seen[idem_key] = (intent.payload_hash, receipt)
            return receipt

    # -- persistence (restart-safe) ----------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Serialize queue + receipts + audit log to JSON-safe data.

        Intents and receipts are frozen dataclasses; the snapshot is plain
        data that can be persisted and restored after a restart.
        """
        with self._lock:
            receipts = [self._seen[key][1] for key in sorted(self._seen)]
            return {
                "version": POLICY_VERSION,
                "intents": [asdict(intent) for intent in self._proposal_queue],
                "receipts": [asdict(receipt) for receipt in receipts],
                "audit_log": [dict(event) for event in self._audit_log],
            }

    @classmethod
    def restore(
        cls,
        data: Dict[str, Any],
        policy_fn: Optional[PolicyFn] = None,
        kill_switch: Optional[KillSwitch] = None,
    ) -> "ShadowEffectBoundary":
        """Rebuild a boundary from a persisted snapshot.

        Promotion-resistant: any receipt in the snapshot claiming
        ``executed=True`` (tampered or foreign data) is refused with
        ValueError -- a restart can never flip a shadow receipt to executed.
        """
        boundary = cls(policy_fn=policy_fn, kill_switch=kill_switch)
        for receipt_data in data.get("receipts", []):
            if receipt_data.get("executed") is not False:
                raise ValueError(
                    "refusing to restore receipt claiming executed=True "
                    f"(effect_id={receipt_data.get('effect_id')!r}): "
                    "shadow receipts can never be promoted to executed"
                )
        with boundary._lock:
            for intent_data in data.get("intents", []):
                boundary._proposal_queue.append(EffectIntent(**intent_data))
            for receipt_data in data.get("receipts", []):
                receipt = EffectReceipt(**receipt_data)
                intent_data = next(
                    (
                        item
                        for item in data.get("intents", [])
                        if item.get("effect_id") == receipt.effect_id
                    ),
                    None,
                )
                if intent_data is not None:
                    key = (
                        intent_data.get("tenant", ""),
                        intent_data.get("effect_type", ""),
                        intent_data.get("idempotency_key", ""),
                    )
                    boundary._seen[key] = (intent_data.get("payload_hash", ""), receipt)
            for event in data.get("audit_log", []):
                boundary._audit_log.append(dict(event))
        return boundary


# ---------------------------------------------------------------------------
# Approval verification
# ---------------------------------------------------------------------------


def verify_approval(intent: EffectIntent, receipt: EffectReceipt) -> bool:
    """True iff ``receipt`` approves exactly this ``intent``.

    Recomputes the approval hash over the canonical intent fields and
    compares it (constant-time) with the receipt's hash. Any edit to any
    intent field -- type, target, payload hash, risk tier, cost, ids --
    invalidates the approval and returns False.
    """
    if receipt.approval_hash is None:
        return False
    if receipt.effect_id != intent.effect_id:
        return False
    expected = canonical_intent_hash(intent)
    return hmac.compare_digest(expected, receipt.approval_hash)


# ---------------------------------------------------------------------------
# Boundary factory
# ---------------------------------------------------------------------------


def build_effect_boundary(profile: Optional[str] = None) -> ShadowEffectBoundary:
    """Build a ShadowEffectBoundary for the given deployment profile.

    * ``None``, ``""``, or ``"shadow"`` -> a shadow boundary.
    * ``"live"`` -> RuntimeError: live effects require a separately deployed
      executor. The live executor is NEVER importable from agent worker code;
      it is built, reviewed, and deployed as its own artifact with its own
      credentials, audit trail, and human-in-the-loop gates. There is no
      flag, profile value, or code path in this module that produces one.
    * anything else -> ValueError.
    """
    if profile in (None, "", "shadow"):
        return ShadowEffectBoundary()
    if profile == "live":
        raise RuntimeError(
            "live effects require separately deployed executor: the live "
            "executor is not importable from agent worker code and cannot "
            "be constructed from a shadow profile"
        )
    raise ValueError(f"unknown effect-boundary profile: {profile!r}")


# ---------------------------------------------------------------------------
# Log-only adapter (provider-disabled / delivery-incapable)
# ---------------------------------------------------------------------------


class LogOnlyAdapter:
    """Provider-disabled adapter: records the intent, never delivers.

    ``execute(intent)`` routes through the shadow effect boundary and returns
    the shadow receipt (status "would_execute" for an allowed low-risk
    intent). ``delivery_report()`` always reports ``{"status": "not sent",
    "reason": "log-only"}`` -- the literals "sent"/"delivered" are
    unrepresentable here by construction.
    """

    adapter_kind = "log_only"

    def __init__(self, boundary: ShadowEffectBoundary):
        self.boundary = boundary
        self._last_receipt: Optional[EffectReceipt] = None

    def execute(self, intent: EffectIntent) -> EffectReceipt:
        """Shadow-execute: dispatch through the boundary, return the receipt.

        Records what WOULD happen. Performs nothing.
        """
        receipt = self.boundary.dispatch(intent)
        self._last_receipt = receipt
        return receipt

    def delivery_report(self) -> Dict[str, str]:
        """Delivery report for a log-only adapter.

        Always ``{"status": "not sent", "reason": "log-only"}``: nothing was
        delivered because this adapter cannot deliver.
        """
        return {"status": "not sent", "reason": "log-only"}


# ---------------------------------------------------------------------------
# Structural self-check (import-time documentation of the constraint)
# ---------------------------------------------------------------------------

# The proposal queue must be a plain list and this module must contain no
# live destination for queue items to be moved into. The tokens below are
# the forbidden live-destination names; the test suite scans this module's
# ShadowEffectBoundary source for them.
_FORBIDDEN_LIVE_TOKENS = (
    "live_executor",
    "live_adapter",
    "live_queue",
    "live_dispatch",
)


def _assert_no_live_destinations() -> None:
    """Module self-check: ShadowEffectBoundary source has no live tokens."""
    source = inspect.getsource(ShadowEffectBoundary)
    hits = [token for token in _FORBIDDEN_LIVE_TOKENS if token in source]
    if hits:
        raise AssertionError(
            "ShadowEffectBoundary references live destinations: "
            + ", ".join(hits)
        )


_assert_no_live_destinations()
