"""Application-facing, proposal-only shadow runtime (WP1).

This module deliberately constructs no provider client, signer, wallet, queue
producer, or live executor.  The single typed dispatch API
``ShadowRuntime.dispatch(intent, originator)`` performs validation, policy
evaluation, idempotency, audit emission, and default-deny handling, and can
only return hash-only shadow receipts.

W-40 fix: the kill switch is re-evaluated on EVERY dispatch, including
idempotency replays. A cached allow is never returned while the kill switch
is engaged -- the replay receives a fresh ``blocked_kill_switch`` decision.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional

from sincor2.shadow_monitor.contract import (
    CONTRACT_VERSION,
    PolicyDecision,
    PolicyVerdict,
    is_valid_originator,
)
from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    AuditFailureError,
    EffectIntent,
    EffectReceipt,
    IdempotencyConflict,
    ShadowEffectBoundary,
    build_effect_boundary,
    utc_now_iso,
)

# Per-type entrypoint: thin adapter over the single dispatch API.
# Takes (intent, originator="agent") and returns the receipt only.
EffectEntrypoint = Callable[..., EffectReceipt]


@dataclass(frozen=True, slots=True)
class ShadowRuntime:
    """Single typed dispatch API for proposal-only shadow effects.

    ``dispatch(intent, originator)`` is the only way to propose an effect
    through this runtime.  It validates the originator against the frozen
    WP0 contract, evaluates policy, re-checks the kill switch on every call
    (including replays), creates an immutable PolicyDecision, persists it to
    the decision audit log BEFORE any receipt is issued (fail-closed), and
    returns ``(decision, receipt)``.

    ``effect_entrypoints`` remains an immutable registry mapping each
    canonical effect type to a real adapter entrypoint; each entrypoint
    routes through :meth:`dispatch`.  This object intentionally offers no
    method that can execute a live effect.
    """

    mode: str
    effect_boundary: ShadowEffectBoundary
    effect_entrypoints: Mapping[str, EffectEntrypoint]
    live_executor: None = None
    signer: None = None
    write_credentials: tuple[str, ...] = ()
    # Runtime-level idempotency: (tenant, effect_type, idempotency_key) ->
    # (payload_hash, PolicyDecision, EffectReceipt)
    _decisions: Dict[tuple, tuple] = field(default_factory=dict, repr=False)
    # Decision audit log: every PolicyDecision is persisted here before its
    # receipt is issued.  Append failure -> AuditFailureError (fail closed).
    _decision_audit_log: List[Dict[str, Any]] = field(
        default_factory=list, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def entrypoint_for(self, effect_type: str) -> EffectEntrypoint:
        """Return a known proposal handler; reject unknown effects closed."""
        try:
            return self.effect_entrypoints[effect_type]
        except KeyError as exc:
            raise ValueError(f"unknown shadow effect type: {effect_type!r}") from exc

    # -- single typed dispatch API -------------------------------------------

    @staticmethod
    def _map_verdict(boundary_decision: str, kill_switched: bool) -> PolicyVerdict:
        """Map a boundary policy outcome to a contract PolicyVerdict."""
        if kill_switched:
            return "blocked_kill_switch"
        if boundary_decision == "would_execute":
            return "allow_proposal"
        if boundary_decision == "pending_approval":
            return "require_approval"
        return "deny"

    def _audit_decision(self, decision: PolicyDecision) -> None:
        """Persist a PolicyDecision to the decision audit log, fail-closed.

        Raises AuditFailureError if the append fails: the dispatch is then
        not auditable, so no receipt may be issued and nothing is cached.
        """
        record: Dict[str, Any] = {
            "event": "policy_decision",
            "contract_version": CONTRACT_VERSION,
            "effect_id": decision.effect_id,
            "effect_type": decision.effect_type,
            "originator": decision.originator,
            "verdict": decision.verdict,
            "reason": decision.reason,
            "risk_tier": decision.risk_tier,
            "decided_at": decision.decided_at,
            "kill_switch_engaged": decision.kill_switch_engaged,
            "idempotency_key": decision.idempotency_key,
        }
        try:
            self._decision_audit_log.append(record)
        except Exception as exc:
            raise AuditFailureError(
                f"decision audit append failed for effect "
                f"{decision.effect_id}: {exc}"
            ) from exc

    def _build_decision(
        self,
        intent: EffectIntent,
        originator: str,
        verdict: PolicyVerdict,
        reason: str,
        kill_switch_engaged: bool,
    ) -> PolicyDecision:
        """Construct an immutable PolicyDecision for this dispatch."""
        return PolicyDecision(
            effect_id=intent.effect_id,
            effect_type=intent.effect_type,
            originator=originator,
            verdict=verdict,
            reason=reason,
            risk_tier=intent.risk_tier,
            decided_at=utc_now_iso(),
            kill_switch_engaged=kill_switch_engaged,
            idempotency_key=intent.idempotency_key,
        )

    def dispatch(
        self, intent: EffectIntent, originator: str
    ) -> tuple[PolicyDecision, EffectReceipt]:
        """Validate, decide, audit (fail-closed), and receipt an effect intent.

        Steps (in order):

        1. Validate the intent is an EffectIntent (TypeError otherwise).
        2. Validate the originator against the frozen WP0 contract
           (ValueError on unknown or unauthorized originator).
        3. Prove registry mapping: the effect type must resolve to a real
           registered entrypoint (ValueError on unknown type).
        4. Idempotency: a replayed idempotency_key with an identical payload
           returns the ORIGINAL (decision, receipt) -- after re-evaluating
           the kill switch (W-40 fix).  If the kill switch has engaged since
           the original allow, the replay receives a fresh
           ``blocked_kill_switch`` decision, never the cached allow.  A
           replayed key with a different payload raises IdempotencyConflict.
        5. Policy evaluation (fail-closed via the boundary).
        6. Build the PolicyDecision and persist it to the decision audit log
           BEFORE any receipt is issued.  Audit failure raises
           AuditFailureError: no receipt, no queue entry, nothing cached.
        7. Commit through the boundary (receipt, proposal queue, event audit)
           and cache the (decision, receipt).

        Nothing here performs an effect.  The receipt can never say otherwise.
        """
        # 1. Intent validation.
        if not isinstance(intent, EffectIntent):
            raise TypeError("dispatch requires an EffectIntent")
        # 2. Originator validation (frozen WP0 contract).
        if not is_valid_originator(intent.effect_type, originator):
            raise ValueError(
                f"originator {originator!r} may not originate "
                f"{intent.effect_type!r}"
            )
        # 3. Registry mapping proof.
        self.entrypoint_for(intent.effect_type)

        idem_key = (intent.tenant, intent.effect_type, intent.idempotency_key)

        with self._lock:
            # 4. Idempotency.
            prior = self._decisions.get(idem_key)
            if prior is not None:
                prior_hash, prior_decision, prior_receipt = prior
                if prior_hash != intent.payload_hash:
                    raise IdempotencyConflict(
                        f"idempotency key {intent.idempotency_key!r} reused with "
                        f"a different payload_hash for {intent.effect_type!r}"
                    )
                # W-40 FIX: re-evaluate the kill switch on EVERY dispatch,
                # including idempotency replays.  A cached allow must never
                # bypass an engaged kill switch.
                if self.effect_boundary.kill_switch_blocks(intent):
                    decision = self._build_decision(
                        intent,
                        originator,
                        "blocked_kill_switch",
                        "kill_switch_engaged_on_replay: cached allow does not "
                        "bypass an engaged kill switch",
                        kill_switch_engaged=True,
                    )
                    # 6. Audit the override decision BEFORE the receipt.
                    self._audit_decision(decision)
                    # The boundary's W-40 fix returns a fresh blocked receipt
                    # (no duplicate queue entry, no duplicate event audit).
                    receipt = self.effect_boundary.dispatch(intent)
                    return (decision, receipt)
                # Clean replay: return the originals, no duplicate audit.
                return (prior_decision, prior_receipt)

            # 5. Policy evaluation (fail-closed via the boundary).
            boundary_decision, reason_codes = (
                self.effect_boundary._evaluate_policy(intent)
            )
            ks_blocks = self.effect_boundary.kill_switch_blocks(intent)
            verdict = self._map_verdict(boundary_decision, ks_blocks)
            reason = (
                ";".join(reason_codes) if reason_codes else boundary_decision
            )
            if ks_blocks and "kill_switch_engaged_would_pay_blocked" not in reason_codes:
                reason = (
                    "kill_switch_engaged_would_pay_blocked"
                    + (";" + reason if reason else "")
                )
            decision = self._build_decision(
                intent,
                originator,
                verdict,
                reason,
                kill_switch_engaged=self.effect_boundary.kill_switch.engaged,
            )

            # 6. Persist the decision BEFORE any receipt is issued.
            self._audit_decision(decision)

            # 7. Commit through the boundary; cache the outcome.
            receipt = self.effect_boundary.dispatch(intent)
            self._decisions[idem_key] = (
                intent.payload_hash,
                decision,
                receipt,
            )
            return (decision, receipt)

    # -- inspection ------------------------------------------------------------

    def decision_audit_log(self) -> List[Dict[str, Any]]:
        """PolicyDecision audit records (copy)."""
        with self._lock:
            return list(self._decision_audit_log)

    def seen_idempotency_keys(self) -> list:
        """Idempotency keys seen by this runtime (sorted)."""
        with self._lock:
            return sorted(self._decisions)


def build_shadow_runtime(
    profile: Optional[str] = None,
    decision_audit_log: Optional[List[Dict[str, Any]]] = None,
) -> ShadowRuntime:
    """Build the only runtime profile available to agent-facing application code.

    ``profile='live'`` is rejected by ``build_effect_boundary``.  Live execution
    must be a separate artifact/service with independent credentials and must
    never be obtained by changing this process's configuration.

    ``decision_audit_log`` optionally injects the decision audit store (a list);
    a fresh list is used by default.  Tests inject a failing store to prove
    fail-closed audit behavior.
    """
    boundary = build_effect_boundary(profile)

    # Create the runtime first with an empty registry; the per-type
    # entrypoints close over it and route through the single dispatch API.
    runtime = ShadowRuntime(
        mode="shadow",
        effect_boundary=boundary,
        effect_entrypoints=MappingProxyType({}),
        _decision_audit_log=(
            decision_audit_log if decision_audit_log is not None else []
        ),
    )

    entrypoints: dict[str, EffectEntrypoint] = {}
    for effect_type in sorted(EFFECT_TYPES):

        def make_handler(
            expected_effect_type: str = effect_type,
        ) -> EffectEntrypoint:
            def handler(
                intent: EffectIntent, originator: str = "agent"
            ) -> EffectReceipt:
                if not isinstance(intent, EffectIntent):
                    raise TypeError(
                        "shadow effect entrypoints require an EffectIntent"
                    )
                if intent.effect_type != expected_effect_type:
                    raise ValueError(
                        "effect type does not match entrypoint: "
                        f"expected {expected_effect_type!r}, "
                        f"got {intent.effect_type!r}"
                    )
                _, receipt = runtime.dispatch(intent, originator)
                return receipt

            handler.__name__ = (
                f"propose_{expected_effect_type.replace('.', '_')}"
            )
            return handler

        entrypoints[effect_type] = make_handler()

    # Frozen dataclass: install the completed registry via object.__setattr__.
    object.__setattr__(
        runtime, "effect_entrypoints", MappingProxyType(entrypoints)
    )
    return runtime
