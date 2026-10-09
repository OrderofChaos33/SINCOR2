"""WP0 contract freeze: canonical shadow-mode safety contract.

This module is the single source of truth for the shadow safety boundary.
All work packages (WP1-WP5) build against this contract; no agent may
redefine the vocabulary, states, or originator rules.

Frozen on: 2026-10-09
Base: PR #415 (shadow runtime wiring)

Contents:
- EFFECT_VOCABULARY: the 8 canonical effect types (immutable)
- ORIGINATORS: who may originate each intent type
- PolicyDecision: immutable record of every policy evaluation
- Live-operation rule: shadow mode has NO code path to live execution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Mapping

from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    RECEIPT_STATUSES,
    RISK_TIERS,
    WOULD_PAY_EFFECT_TYPES,
)

# ---------------------------------------------------------------------------
# 1. Canonical effect vocabulary (frozen)
# ---------------------------------------------------------------------------

# Re-export as the frozen vocabulary. EFFECT_TYPES is already a frozenset;
# this alias marks it as the WP0-frozen contract version.
EFFECT_VOCABULARY: frozenset[str] = EFFECT_TYPES

# Effect types that move value or change on-chain state.
# The kill switch blocks these first; they require the strictest policy.
VALUE_MOVING_EFFECTS: frozenset[str] = WOULD_PAY_EFFECT_TYPES

# ---------------------------------------------------------------------------
# 2. Originators: who may originate each intent
# ---------------------------------------------------------------------------

# Valid originator roles. Every intent must declare its originator, and the
# originator must be in the allowlist for that effect type.
Originator = Literal["agent", "customer", "operator", "provider_webhook", "background_worker"]

_VALID_ORIGINATORS: frozenset[str] = frozenset(
    {"agent", "customer", "operator", "provider_webhook", "background_worker"}
)

# Per-effect-type originator allowlist.
# - agent: autonomous agent proposing work (always proposal-only in shadow)
# - customer: customer-initiated action (checkout, service request) — these
#   stay on SEPARATE explicitly-authorized flows, NOT forced into shadow
# - operator: human operator / founder action
# - provider_webhook: inbound webhook from a provider (Stripe, etc.)
# - background_worker: scheduled/queue worker proposing an effect
_EFFECT_ORIGINATORS: dict[str, frozenset[str]] = {
    # Communications: agents propose, operators approve; customers trigger
    # transactional notices on their own authorized flows
    "email.send": frozenset({"agent", "operator", "background_worker"}),
    "message.send": frozenset({"agent", "operator", "background_worker"}),
    "social.post": frozenset({"agent", "operator"}),
    # CRM: agents propose writes; deletes are operator-only
    "crm.write": frozenset({"agent", "operator", "background_worker"}),
    "crm.delete": frozenset({"operator"}),
    # Value-moving: agents propose only; execution requires separate
    # human approval AND is disabled until WP4 owner decisions are made
    "payment.transfer": frozenset({"agent", "operator"}),
    "trade.swap": frozenset({"agent", "operator"}),
    "contract.call": frozenset({"agent", "operator"}),
}

# Frozen public view.
EFFECT_ORIGINATORS: Mapping[str, frozenset[str]] = MappingProxyType(_EFFECT_ORIGINATORS)


def is_valid_originator(effect_type: str, originator: str) -> bool:
    """Return True if originator may originate intents of effect_type."""
    if originator not in _VALID_ORIGINATORS:
        return False
    allowed = _EFFECT_ORIGINATORS.get(effect_type)
    if allowed is None:
        return False
    return originator in allowed


# ---------------------------------------------------------------------------
# 3. Policy decision record (immutable)
# ---------------------------------------------------------------------------

PolicyVerdict = Literal["allow_proposal", "deny", "require_approval", "blocked_kill_switch"]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Immutable record of a single policy evaluation.

    Every dispatch through the shadow boundary must produce exactly one
    PolicyDecision, persisted to the audit log before any receipt is issued.
    If the audit append fails, dispatch fails closed (no receipt).
    """

    effect_id: str
    effect_type: str
    originator: str
    verdict: PolicyVerdict
    reason: str
    risk_tier: str = "low"
    decided_at: str = field(default_factory=str)
    # Kill-switch state at decision time (for replay-bypass detection)
    kill_switch_engaged: bool = False
    # Idempotency: decisions are keyed, replays return the ORIGINAL decision
    # (the kill-switch bypass fix: replay must re-evaluate, not hit cache)
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if self.effect_type not in EFFECT_VOCABULARY:
            raise ValueError(f"unknown effect_type: {self.effect_type!r}")
        if self.originator not in _VALID_ORIGINATORS:
            raise ValueError(f"unknown originator: {self.originator!r}")
        if not is_valid_originator(self.effect_type, self.originator):
            raise ValueError(
                f"originator {self.originator!r} may not originate {self.effect_type!r}"
            )
        if self.risk_tier not in RISK_TIERS:
            raise ValueError(f"unknown risk_tier: {self.risk_tier!r}")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if not self.effect_id:
            raise ValueError("effect_id is required")


# ---------------------------------------------------------------------------
# 4. Live-operation rule
# ---------------------------------------------------------------------------

# SHADOW MODE HAS NO CODE PATH TO LIVE EXECUTION.
#
# This is a structural invariant, not a configuration flag:
# - ShadowRuntime.live_executor is None (frozen dataclass, no setter)
# - ShadowRuntime.signer is None (frozen dataclass, no setter)
# - ShadowRuntime.write_credentials is () (empty tuple, frozen)
# - build_shadow_runtime(profile="live") raises (rejected by boundary)
# - LogOnlyAdapter.execute() returns hash-only receipts; executed=False always
#
# A live operation may ONLY be requested through a SEPARATE artifact/service
# with independent credentials, independent audit, and explicit human approval.
# No configuration change to THIS process may create a live path.
# Any code that introduces a live path into the shadow process is a
# contract violation and must fail CI.

# Receipt statuses permitted in shadow (re-exported for contract clarity).
# "sent", "settled", "delivered", "paid" are UNREPRESENTABLE in shadow.
SHADOW_RECEIPT_STATUSES: tuple[str, ...] = RECEIPT_STATUSES


# ---------------------------------------------------------------------------
# 5. Contract version (for migration detection)
# ---------------------------------------------------------------------------

CONTRACT_VERSION = "wp0-2026-10-09"
