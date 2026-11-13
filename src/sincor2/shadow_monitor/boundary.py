"""Shadow boundary enforcement for SINCOR2 Phase 1 shadow-mode monitoring.

Phase 1 runs every action adapter in *shadow mode*: the system may observe,
classify, and record what it *would* do, but it must never cause a real
outbound side effect (send an email, post to social, write to the CRM,
execute a trade, move funds, or call a contract).

Enforcement lives at the adapter layer, in code -- never in prompts or UI:

    EmailAdapter / SocialAdapter / CrmAdapter / TradeAdapter /
    TransferAdapter / ContractCallAdapter
        --> ShadowBoundary.propose(...)   # records intent as would_* evidence
        --> ShadowBoundaryViolation       # raised if any adapter path tries
                                          # to perform a real side effect

Core guarantees:
    * ACTUAL_SIDE_EFFECTS is a module-level counter that stays 0 in shadow
      mode. The only code that may increment it is the clearly-marked
      _LIVE_ONLY path, which is unreachable while shadow is enabled.
    * Proposed actions are recorded with would_send / would_update /
      would_pay verbs -- never sent / updated / settled.
    * Shadow credentials may hold only non-secret identifiers (labels,
      read-only scope); any key-like value is rejected by validation.
    * A thread-safe global kill switch can freeze even proposed-action
      recording for high-risk action kinds.
    * emit_heartbeat() and check_effective_mode() expose the effective
      configuration so a drifted adapter (not wrapped by the boundary) or
      a non-shadow effective mode is detected loudly.

Stdlib only. Thread-safe where state is shared.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = [
    "ShadowBoundaryViolation",
    "ActionKind",
    "ActionVerb",
    "ProposedAction",
    "ShadowCredentials",
    "CredentialValidationError",
    "ShadowBoundary",
    "EmailAdapter",
    "SocialAdapter",
    "CrmAdapter",
    "TradeAdapter",
    "TransferAdapter",
    "ContractCallAdapter",
    "ActionAdapter",
    "KillSwitch",
    "kill_switch",
    "emit_heartbeat",
    "check_effective_mode",
    "ACTUAL_SIDE_EFFECTS",
    "POLICY_VERSION",
    "SHADOW_MODE",
]

# ---------------------------------------------------------------------------
# Policy / mode constants
# ---------------------------------------------------------------------------

POLICY_VERSION = "shadow-boundary/v1"
SHADOW_MODE = "shadow"


# ---------------------------------------------------------------------------
# Actual side-effect counter (the key invariant)
# ---------------------------------------------------------------------------

ACTUAL_SIDE_EFFECTS: int = 0
_counter_lock = threading.Lock()


def _record_actual_side_effect() -> None:
    """Increment ACTUAL_SIDE_EFFECTS.  See _LIVE_ONLY for the only caller."""
    global ACTUAL_SIDE_EFFECTS
    with _counter_lock:
        ACTUAL_SIDE_EFFECTS += 1


# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------


class ActionKind(str, Enum):
    """What a proposed action would do.  Verbs are *provisional only*."""

    WOULD_SEND = "would_send"      # email, social post, message
    WOULD_UPDATE = "would_update"  # CRM write, record change
    WOULD_PAY = "would_pay"        # trade, transfer, contract call


# Alias kept for readability at call sites.
ActionVerb = ActionKind

# Every adapter kind this boundary governs.
ADAPTER_KINDS = ("email", "social", "crm", "trade", "transfer", "contract_call")


class ShadowBoundaryViolation(Exception):
    """Raised when code attempts a real side effect while shadow is enforced.

    The blocked attempt is preserved on the exception so it doubles as
    evidence: ``violation.proposed_action`` holds the WouldAction that
    describes what *would* have happened.
    """

    def __init__(self, message: str, proposed_action: "ProposedAction | None" = None):
        super().__init__(message)
        self.proposed_action = proposed_action
        self.recorded_at = time.time()

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        base = super().__str__()
        if self.proposed_action is not None:
            base += f" [evidence: {self.proposed_action!r}]"
        return base


# ---------------------------------------------------------------------------
# Proposed actions (evidence, never effects)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposedAction:
    """A record of something the system *would* have done.

    Frozen: evidence is immutable once recorded. ``payload_summary`` must be
    redacted -- it carries shape, never secrets.
    """

    action_kind: ActionKind
    target: str
    payload_summary: str
    estimated_cost: float
    trace_id: str
    recorded_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.action_kind, str) and not isinstance(self.action_kind, ActionKind):
            object.__setattr__(self, "action_kind", ActionKind(self.action_kind))


# ---------------------------------------------------------------------------
# Shadow credentials
# ---------------------------------------------------------------------------


class CredentialValidationError(ValueError):
    """Raised when shadow credentials carry anything secret-like."""


# Common private-key / secret patterns.  Any match in any field value fails
# validation.  Keep the patterns deliberately broad: false positives are
# cheap, a leaked key is not.
_KEY_PATTERNS: Sequence[re.Pattern] = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b0x[0-9a-fA-F]{64}\b"),                       # raw hex private key
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),                      # long base64-ish blob
    re.compile(r"\bsk-(?:live|test)-[A-Za-z0-9]+"),             # Stripe-style secret
    re.compile(r"(?i)\b(?:api[_-]?key|secret|private[_-]?key|mnemonic|seed[_-]?phrase)\b\s*[:=]"),
)


@dataclass(frozen=True)
class ShadowCredentials:
    """Non-secret identifiers for shadow-mode adapters.

    Holds labels and scopes only -- e.g. which credential *would* be used and
    that it is read-only.  ``validate()`` raises CredentialValidationError
    if any field value looks like a private key, secret, or signing authority.
    """

    credential_label: str
    scope: str = "read-only"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Assert no private key or production signing authority is present."""
        for field_name in ("credential_label", "scope"):
            value = str(getattr(self, field_name, ""))
            for pattern in _KEY_PATTERNS:
                if pattern.search(value):
                    raise CredentialValidationError(
                        f"shadow credentials must not contain secrets: "
                        f"field={field_name} matched key-like pattern"
                    )
        if self.scope != "read-only":
            raise CredentialValidationError(
                f"shadow credentials scope must be 'read-only', got {self.scope!r}"
            )


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


class KillSwitch:
    """Global, thread-safe kill switch.

    Disarmed (default): proposed actions are recorded normally.
    Engaged: even proposed-action recording is blocked for the configured
    high-risk action kinds (default: all WOULD_PAY kinds).
    """

    def __init__(self, blocked_kinds: Sequence[ActionKind] = (ActionKind.WOULD_PAY,)):
        self._lock = threading.Lock()
        self._engaged = False
        self._blocked_kinds = tuple(blocked_kinds)
        self._engagements: List[float] = []

    @property
    def engaged(self) -> bool:
        with self._lock:
            return self._engaged

    def engage(self) -> None:
        """Engage the kill switch (idempotent)."""
        with self._lock:
            self._engaged = True
            self._engagements.append(time.time())

    def disengage(self) -> None:
        """Return to normal shadow operation (idempotent)."""
        with self._lock:
            self._engaged = False

    def blocks(self, kind: ActionKind) -> bool:
        """True if the kill switch currently blocks recording of ``kind``."""
        with self._lock:
            return self._engaged and kind in self._blocked_kinds

    def state(self) -> str:
        return "armed" if self.engaged else "disarmed"

    def engagement_count(self) -> int:
        with self._lock:
            return len(self._engagements)


# Module-global kill switch instance.
kill_switch = KillSwitch()


# ---------------------------------------------------------------------------
# The shadow boundary
# ---------------------------------------------------------------------------


class ShadowBoundary:
    """Adapter-layer guard: the single choke point for outbound actions.

    All action adapters must route through this boundary.  In shadow mode:

    * ``propose(...)`` records the intent as a ProposedAction (evidence) and
      returns it.  Nothing leaves the machine.
    * ``execute(...)`` raises ShadowBoundaryViolation -- it is the API that
      would perform the real side effect, and it is forbidden in shadow.
    """

    _registry_lock = threading.Lock()
    _registry: Dict[str, "ActionAdapter"] = {}
    _wrapped_adapters: set = set()

    def __init__(
        self,
        shadow: bool = True,
        credentials: Optional[ShadowCredentials] = None,
        kill_switch: KillSwitch = kill_switch,
        on_violation: Optional[Callable[[ShadowBoundaryViolation], None]] = None,
    ):
        self.shadow = shadow
        self.credentials = credentials or ShadowCredentials(credential_label="shadow-read-only")
        self.kill_switch = kill_switch
        self.on_violation = on_violation
        self._lock = threading.Lock()
        self._proposed: List[ProposedAction] = []
        self._violations: List[ShadowBoundaryViolation] = []

    # -- adapter registration ------------------------------------------------

    @classmethod
    def register_adapter(cls, adapter: "ActionAdapter") -> None:
        """Record an adapter as routed through the boundary."""
        with cls._registry_lock:
            cls._registry[adapter.adapter_kind] = adapter
            cls._wrapped_adapters.add(adapter.adapter_kind)

    @classmethod
    def registered_adapter_kinds(cls) -> List[str]:
        with cls._registry_lock:
            return sorted(cls._registry)

    @classmethod
    def is_wrapped(cls, adapter_kind: str) -> bool:
        with cls._registry_lock:
            return adapter_kind in cls._wrapped_adapters

    # -- evidence ------------------------------------------------------------

    def proposed_actions(self) -> List[ProposedAction]:
        with self._lock:
            return list(self._proposed)

    def violations(self) -> List[ShadowBoundaryViolation]:
        with self._lock:
            return list(self._violations)

    def _record_proposed(self, action: ProposedAction) -> ProposedAction:
        if self.kill_switch.blocks(action.action_kind):
            raise ShadowBoundaryViolation(
                f"kill switch engaged: proposed-action recording blocked "
                f"for {action.action_kind.value}",
                proposed_action=action,
            )
        with self._lock:
            self._proposed.append(action)
        return action

    # -- the two paths -------------------------------------------------------

    def propose(
        self,
        *,
        action_kind: ActionKind,
        target: str,
        payload_summary: str,
        estimated_cost: float = 0.0,
        trace_id: str = "",
    ) -> ProposedAction:
        """Record what the adapter *would* do.  Safe in shadow mode.

        Never performs a side effect.  Verbs are would_* by construction.
        """
        action = ProposedAction(
            action_kind=action_kind,
            target=target,
            payload_summary=payload_summary,
            estimated_cost=estimated_cost,
            trace_id=trace_id,
        )
        return self._record_proposed(action)

    def execute(
        self,
        *,
        action_kind: ActionKind,
        target: str,
        payload_summary: str,
        estimated_cost: float = 0.0,
        trace_id: str = "",
    ) -> None:
        """Attempt the real side effect.  Forbidden while shadow is on.

        Records the attempt as evidence, then raises ShadowBoundaryViolation.
        """
        action = ProposedAction(
            action_kind=action_kind,
            target=target,
            payload_summary=payload_summary,
            estimated_cost=estimated_cost,
            trace_id=trace_id,
        )
        violation = ShadowBoundaryViolation(
            f"blocked real side effect in shadow mode: "
            f"{action_kind.value} -> {target}",
            proposed_action=action,
        )
        with self._lock:
            self._violations.append(violation)
        if self.on_violation is not None:
            self.on_violation(violation)
        raise violation


# ---------------------------------------------------------------------------
# _LIVE_ONLY code path (unreachable in shadow mode)
# ---------------------------------------------------------------------------


def _perform_live_side_effect(description: str, shadow: bool) -> None:
    """The ONLY place allowed to increment ACTUAL_SIDE_EFFECTS.

    Guard: raises before touching the counter whenever ``shadow`` is True,
    so in shadow mode this path is structurally unreachable and the
    counter cannot move.
    """
    if shadow:
        raise ShadowBoundaryViolation(
            f"blocked real side effect in shadow mode: {description}"
        )
    # _LIVE_ONLY: reachable only when shadow is disabled.  Phase 1 shadow
    # monitoring must never call this with shadow=False.
    _record_actual_side_effect()


# ---------------------------------------------------------------------------
# Action adapters (all route through the boundary)
# ---------------------------------------------------------------------------


class ActionAdapter:
    """Base class: every adapter kind funnels through ShadowBoundary."""

    adapter_kind: str = "base"
    action_kind: ActionKind = ActionKind.WOULD_UPDATE

    def __init__(self, boundary: ShadowBoundary, credentials: Optional[ShadowCredentials] = None):
        self.boundary = boundary
        self.credentials = credentials or boundary.credentials
        self.credentials.validate()
        ShadowBoundary.register_adapter(self)

    def shadow_action(
        self,
        target: str,
        payload_summary: str,
        estimated_cost: float = 0.0,
        trace_id: str = "",
    ) -> ProposedAction:
        """The adapter's shadow-mode entry point: record, never act."""
        return self.boundary.propose(
            action_kind=self.action_kind,
            target=target,
            payload_summary=payload_summary,
            estimated_cost=estimated_cost,
            trace_id=trace_id,
        )

    def live_action(
        self,
        target: str,
        payload_summary: str,
        estimated_cost: float = 0.0,
        trace_id: str = "",
    ) -> None:
        """The adapter's real-effect entry point: always blocked in shadow."""
        self.boundary.execute(
            action_kind=self.action_kind,
            target=target,
            payload_summary=payload_summary,
            estimated_cost=estimated_cost,
            trace_id=trace_id,
        )
        # Defense in depth: even if the boundary were bypassed, the live
        # path re-checks the shadow flag before counting a real effect.
        _perform_live_side_effect(f"{self.adapter_kind}:{target}", shadow=self.boundary.shadow)


class EmailAdapter(ActionAdapter):
    adapter_kind = "email"
    action_kind = ActionKind.WOULD_SEND


class SocialAdapter(ActionAdapter):
    adapter_kind = "social"
    action_kind = ActionKind.WOULD_SEND


class CrmAdapter(ActionAdapter):
    adapter_kind = "crm"
    action_kind = ActionKind.WOULD_UPDATE


class TradeAdapter(ActionAdapter):
    adapter_kind = "trade"
    action_kind = ActionKind.WOULD_PAY


class TransferAdapter(ActionAdapter):
    adapter_kind = "transfer"
    action_kind = ActionKind.WOULD_PAY


class ContractCallAdapter(ActionAdapter):
    adapter_kind = "contract_call"
    action_kind = ActionKind.WOULD_PAY


# ---------------------------------------------------------------------------
# Heartbeat and effective-mode check
# ---------------------------------------------------------------------------


def emit_heartbeat(boundary: ShadowBoundary) -> Dict[str, Any]:
    """Configuration heartbeat: what mode is *actually* in effect."""
    return {
        "effective_mode": SHADOW_MODE if boundary.shadow else "live",
        "policy_version": POLICY_VERSION,
        "enabled_capabilities": ShadowBoundary.registered_adapter_kinds(),
        "kill_switch_state": boundary.kill_switch.state(),
        "timestamp": time.time(),
    }


class EffectiveModeError(RuntimeError):
    """Raised when the effective configuration is not shadow-safe."""


def check_effective_mode(
    boundary: ShadowBoundary,
    expected_adapter_kinds: Sequence[str] = ADAPTER_KINDS,
    on_alert: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """Verify the deployment is really running in shadow mode.

    Raises (and optionally alerts) if:
      * ``boundary.shadow`` is not True (effective_mode != "shadow"), or
      * any expected adapter kind is not routed through the boundary.

    Returns the heartbeat dict when everything checks out.
    """
    problems: List[str] = []
    if not boundary.shadow:
        problems.append("effective_mode is not 'shadow' (live effects enabled)")
    for kind in expected_adapter_kinds:
        if not ShadowBoundary.is_wrapped(kind):
            problems.append(f"adapter not routed through boundary: {kind}")
    if problems:
        message = "shadow-mode check failed: " + "; ".join(problems)
        if on_alert is not None:
            on_alert(message)
        raise EffectiveModeError(message)
    return emit_heartbeat(boundary)


# Re-export for test convenience.
__all__ = __all__ + ["EffectiveModeError"]
