"""Production money-path gate over the shadow effect boundary (C4 remediation).

The ``ShadowEffectBoundary`` policy evaluation is fail-closed by design, but
nothing in production called it — an unwired fail-closed gate is fail-open in
practice. This module is the wiring: a process-singleton boundary with a
production policy, plus :func:`check_money_effect`, a ``check_action``-style
gate that money-path handlers call before moving value.

Semantics (fail-closed):
  * every money-path intent is dispatched through the boundary and audited
    (hash-only) before the handler proceeds;
  * an engaged boundary kill switch blocks would-pay effect kinds
    (payment.transfer / trade.swap / contract.call) — see
    ``ShadowEffectBoundary.dispatch``;
  * an externally-tripped kill switch (e.g. the treasury HALT file) denies
    via ``kill_switch_tripped=True``;
  * unknown effect types / risk tiers deny;
  * ANY error (policy exception, audit failure, import problem at the call
    site) denies — the function returns ``(False, reason)``, never raises.

The production policy differs from the shadow default on purpose: the
shadow default denies high/critical intents (shadow never executes). On the
production money path the CALLER's auth/approval checks own the allow
decision; the boundary contributes kill-switch enforcement, idempotency,
and a hash-only audit trail. Unknown risk tiers still deny.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Dict, List, Optional, Tuple

from sincor2.shadow_monitor.effect_boundary import (
    EFFECT_TYPES,
    RISK_TIERS,
    EffectIntent,
    ShadowEffectBoundary,
)

logger = logging.getLogger("sincor.governance.money_gate")


def production_money_policy(intent: EffectIntent) -> Tuple[str, List[str]]:
    """Fail-closed production policy for money-path intents.

    * unknown effect type -> blocked_policy
    * unknown risk tier   -> blocked_policy
    * otherwise           -> would_execute (the caller's auth/approval owns
      the allow decision; the boundary enforces the kill switch for
      would-pay kinds inside dispatch() and audits every intent)
    """
    if intent.effect_type not in EFFECT_TYPES:
        return ("blocked_policy", ["unknown_effect_type"])
    if intent.risk_tier not in RISK_TIERS:
        return ("blocked_policy", ["unknown_risk_tier"])
    return ("would_execute", [])


_boundary_lock = threading.Lock()
_boundary: Optional[ShadowEffectBoundary] = None


def get_money_boundary() -> ShadowEffectBoundary:
    """Process-singleton boundary for production money-path intents."""
    global _boundary
    with _boundary_lock:
        if _boundary is None:
            _boundary = ShadowEffectBoundary(policy_fn=production_money_policy)
        return _boundary


def reset_money_boundary() -> None:
    """Test hook: drop the singleton so tests get a fresh boundary."""
    global _boundary
    with _boundary_lock:
        _boundary = None


def check_money_effect(
    *,
    effect_type: str,
    payload: Dict[str, object],
    agent_id: str = "",
    tenant: str = "sincor",
    risk_tier: str = "critical",
    idempotency_key: str = "",
    target: str = "",
    estimated_cost: float = 0.0,
    kill_switch_tripped: bool = False,
) -> Tuple[bool, str]:
    """Gate a money-path intent through the effect boundary.

    Returns ``(allowed, reason)``. Fail-closed: any check failure or any
    internal error returns ``(False, reason)`` — this function never raises
    and never returns ``(True, ...)`` on a partial failure.
    """
    if kill_switch_tripped:
        logger.critical(
            "money gate DENIED %s for %s: external kill switch tripped",
            effect_type, agent_id or "?")
        return (False, "external_kill_switch_tripped")
    try:
        boundary = get_money_boundary()
        intent = EffectIntent.from_payload(
            effect_type=effect_type,
            target=target or effect_type,
            payload=dict(payload),
            idempotency_key=idempotency_key or uuid.uuid4().hex,
            agent_id=agent_id,
            tenant=tenant,
            risk_tier=risk_tier,
            estimated_cost=estimated_cost,
        )
        receipt = boundary.dispatch(intent)
    except Exception as exc:  # noqa: BLE001 - fail-closed by contract
        logger.critical("money gate ERROR on %s for %s: %s",
                        effect_type, agent_id or "?", exc)
        return (False, f"effect_boundary_error:{type(exc).__name__}")
    if receipt.status == "blocked_policy":
        reason = "effect_boundary_blocked:" + ",".join(
            receipt.policy_reason_codes)
        logger.critical("money gate DENIED %s for %s: %s",
                        effect_type, agent_id or "?", reason)
        return (False, reason)
    logger.info("money gate allowed %s for %s (receipt %s)",
                effect_type, agent_id or "?", receipt.effect_id)
    return (True, f"effect_boundary:{receipt.status}:{receipt.effect_id}")
