"""WP2 comms adapters: typed shadow adapters for communications.

``CommsAdapter`` owns a ``ShadowEffectBoundary`` with a composed policy.
Comms-specific checks run BEFORE the boundary's default policy:

1. Purpose check (D2): ``marketing`` purpose → ``blocked_policy``.
   There is no override; ``is_marketing_allowed()`` is always False.
2. Suppression check (D1/D2): suppressed recipient → ``blocked_policy``.
   No bypass for transactional; malformed email → blocked (fail closed).
3. Originator check: must be in the contract allowlist for the effect type.
4. Otherwise: the boundary's default shadow policy applies.

The adapter never sends. It returns a frozen ``EffectReceipt`` with
``executed=False``. The literals "sent"/"delivered" are unrepresentable.

Design note: the comms decision is registered by idempotency key and
picked up by the composed policy function, so the receipt and audit log
carry the exact comms reason codes (e.g. ``d2_marketing_email_blocked``),
not a generic risk-tier block.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from sincor2.comms.classify import (
    EmailPurpose,
    classify_email_purpose,
    is_marketing_allowed,
)
from sincor2.comms.suppression import SuppressionList, get_suppression_list
from sincor2.shadow_monitor.contract import (
    EFFECT_VOCABULARY,
    is_valid_originator,
)
from sincor2.shadow_monitor.effect_boundary import (
    EffectIntent,
    EffectReceipt,
    ShadowEffectBoundary,
    default_shadow_policy,
)

logger = logging.getLogger("sincor2.comms.adapters")

# Effect types this adapter handles.
COMMS_EFFECT_TYPES = frozenset({"email.send", "message.send", "social.post"})


@dataclass(frozen=True)
class CommsIntent:
    """A communication intent with purpose classification.

    Wraps the fields needed to build an ``EffectIntent``, plus the
    WP2-required ``purpose`` field. Purpose is classified in code
    (see classify.py); callers may provide an explicit purpose, which
    is validated against the classification (explicit "transactional"
    for a marketing-shaped email is rejected).
    """

    effect_type: str
    recipient: str
    purpose: EmailPurpose
    originator: str
    idempotency_key: str
    tenant: str = ""
    agent_id: str = ""
    trace_id: str = ""
    risk_tier: str = "low"
    template: str = ""
    subject: str = ""
    has_service_relationship: bool = False

    def __post_init__(self) -> None:
        if self.effect_type not in COMMS_EFFECT_TYPES:
            raise ValueError(
                f"CommsAdapter handles {sorted(COMMS_EFFECT_TYPES)}, "
                f"not {self.effect_type!r}"
            )
        if self.effect_type not in EFFECT_VOCABULARY:
            raise ValueError(f"unknown effect_type: {self.effect_type!r}")
        if self.purpose not in ("transactional", "marketing"):
            raise ValueError(
                f"purpose must be transactional|marketing, got {self.purpose!r}"
            )
        # Cross-check: an explicit "transactional" claim must survive
        # classification. A marketing-shaped email cannot be laundered by
        # labeling it transactional.
        classified = classify_email_purpose(
            template=self.template,
            subject=self.subject,
            has_service_relationship=self.has_service_relationship,
        )
        if self.purpose == "transactional" and classified == "marketing":
            raise ValueError(
                "purpose='transactional' rejected: classification says marketing "
                f"(template={self.template!r}, subject={self.subject!r}, "
                f"has_service_relationship={self.has_service_relationship})"
            )
        if not is_valid_originator(self.effect_type, self.originator):
            raise ValueError(
                f"originator {self.originator!r} may not originate "
                f"{self.effect_type!r}"
            )
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if not self.recipient:
            raise ValueError("recipient is required")


class CommsAdapter:
    """Shadow adapter for communications with D1/D2 enforcement.

    Owns its ``ShadowEffectBoundary``; the composed policy applies comms
    checks first, then the default shadow policy.

    Usage::

        adapter = CommsAdapter()
        receipt = adapter.send_email(
            recipient="user@example.com",
            purpose="transactional",
            originator="background_worker",
            idempotency_key="welcome-123",
            template="welcome",
            has_service_relationship=True,
        )
        # receipt.status is "would_execute" (proposal) or "blocked_policy"
        # receipt.executed is always False
    """

    adapter_kind = "comms_shadow"

    def __init__(
        self,
        suppression: Optional[SuppressionList] = None,
        kill_switch: Any = None,
        audit_log: Optional[List[Dict[str, Any]]] = None,
    ):
        self.suppression = suppression or get_suppression_list()
        # idempotency_key -> (decision, reason_codes), populated by
        # dispatch() before boundary.dispatch() invokes the policy.
        self._pending: Dict[str, Tuple[str, List[str]]] = {}
        self._pending_lock = threading.Lock()
        self.boundary = ShadowEffectBoundary(
            policy_fn=self._composed_policy,
            kill_switch=kill_switch,
            audit_log=audit_log,
        )

    # -- composed policy ----------------------------------------------------

    def _composed_policy(
        self, intent: EffectIntent
    ) -> Tuple[str, List[str]]:
        """Comms checks first, then default shadow policy.

        Picks up a pre-computed comms decision by idempotency key.
        Fail-closed: missing/invalid → blocked_policy (handled by boundary).
        """
        with self._pending_lock:
            pending = self._pending.pop(intent.idempotency_key, None)
        if pending is not None:
            return pending
        return default_shadow_policy(intent)

    # -- comms policy ---------------------------------------------------------

    def _check_comms_policy(
        self, intent: CommsIntent
    ) -> Tuple[Optional[str], List[str]]:
        """Evaluate comms-specific policy.

        Returns (decision, reason_codes). Decision is "blocked_policy" or
        None (meaning "proceed to default policy").
        """
        # 1. D2: marketing is never allowed.
        if intent.purpose == "marketing":
            return ("blocked_policy", ["d2_marketing_email_blocked"])
        # Belt-and-suspenders: the kill switch is always False, but if
        # it ever changed, marketing stays blocked.
        if not is_marketing_allowed():
            pass  # marketing already handled above; transactional proceeds

        # 2. Suppression: no bypass, fail closed on malformed.
        if self.suppression.is_suppressed(intent.recipient):
            return ("blocked_policy", ["recipient_suppressed"])

        return (None, [])

    # -- dispatch ---------------------------------------------------------------

    def dispatch(self, intent: CommsIntent) -> EffectReceipt:
        """Evaluate comms policy, then dispatch through the boundary.

        Blocked intents are audited with their comms reason codes.
        Nothing is ever sent.
        """
        decision, reasons = self._check_comms_policy(intent)

        payload: Dict[str, Any] = {
            "recipient": intent.recipient,  # hashed by from_payload
            "purpose": intent.purpose,
            "template": intent.template,
        }
        idem_key = f"comms-{intent.idempotency_key}"
        effect_intent = EffectIntent.from_payload(
            effect_type=intent.effect_type,
            target=f"comms://{intent.effect_type}",
            payload=payload,
            idempotency_key=idem_key,
            trace_id=intent.trace_id,
            agent_id=intent.agent_id,
            tenant=intent.tenant,
            risk_tier=intent.risk_tier,
        )

        if decision == "blocked_policy":
            # Register for the composed policy to pick up.
            with self._pending_lock:
                self._pending[idem_key] = (decision, reasons)

        receipt = self.boundary.dispatch(effect_intent)
        logger.info(
            "comms %s: %s purpose=%s -> %s (%s)",
            intent.effect_type,
            intent.recipient,
            intent.purpose,
            receipt.status,
            ",".join(receipt.policy_reason_codes),
        )
        return receipt

    # -- convenience ----------------------------------------------------------------

    def send_email(
        self,
        *,
        recipient: str,
        purpose: EmailPurpose,
        originator: str,
        idempotency_key: str,
        template: str = "",
        subject: str = "",
        has_service_relationship: bool = False,
        **kwargs: Any,
    ) -> EffectReceipt:
        """Propose an email send (never sends)."""
        return self.dispatch(
            CommsIntent(
                effect_type="email.send",
                recipient=recipient,
                purpose=purpose,
                originator=originator,
                idempotency_key=idempotency_key,
                template=template,
                subject=subject,
                has_service_relationship=has_service_relationship,
                **kwargs,
            )
        )

    def delivery_report(self) -> Dict[str, str]:
        """Always not-sent: this adapter cannot deliver."""
        return {"status": "not sent", "reason": "comms-shadow-log-only"}
