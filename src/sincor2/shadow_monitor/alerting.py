"""Alert routing and delivery for SINCOR2 Phase 1 shadow-mode monitoring.

This module turns :class:`~anomaly.Anomaly` records into routed, sanitized,
deduplicated alerts and delivers them through a caller-supplied delivery
function. It is the *only* automated output the monitoring path produces.

Monitoring-must-not-act
-----------------------
:py:meth:`AlertManager.pause_shadow_worker` is the **only** permitted
automated response: it records that a shadow worker should stop being
observed/run, nothing more. This module deliberately provides **no** methods
for changing agent permissions, clearing halts, retrying actions, or
modifying production data. Monitoring observes and reports; it never acts
on the system it watches. Any such action requires a human operator using a
separate, authorized control path.

Alerting path independence
--------------------------
:class:`AlertManager` takes its ``delivery_fn`` at construction time and
never calls into agent runtime modules. It depends only on this package's
own ``anomaly`` types plus duck-typed dicts, so the alerting path stays
independent of — and cannot be influenced by — the agent layer under
observation. The default delivery function is an in-memory outbox (useful
for tests); production wiring injects a real sender.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from .anomaly import Anomaly, AnomalyClass


class Severity(str, Enum):
    """Alert severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    WARNING = "warning"
    DIGEST = "digest"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# Secret sanitization
# --------------------------------------------------------------------------

_SECRET_PATTERNS = (
    # Ethereum-style private keys: 0x + 64 hex chars.
    re.compile(r"0x[a-fA-F0-9]{64}"),
    # API tokens: sk_live_/sk_test_/sk-..., bearer tokens, xox tokens.
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    re.compile(r"\bxox[bpras]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    # AWS access key ids and generic labeled secrets.
    re.compile(r"\bAKIA[0-9A-Z]{16}"),
    re.compile(
        r"(?i)(?:api[_-]?key|secret|passwd|password|private[_-]?key)\s*[:=]\s*"
        r"['\"]?([^\s'\"]{8,})['\"]?"
    ),
)

_REDACTED = "[REDACTED]"


def sanitize(text: Optional[str]) -> str:
    """Strip anything secret-looking from alert text.

    Never put raw secrets or customer content in an alert: this removes
    private keys, API tokens, bearer credentials, and labeled
    secret values before the alert is stored or delivered.
    """
    if not text:
        return ""
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTED, redacted)
    return redacted


def sanitize_list(items: List[str]) -> List[str]:
    return [sanitize(item) for item in (items or [])]


# --------------------------------------------------------------------------
# Alert model
# --------------------------------------------------------------------------


@dataclass
class Alert:
    """A routed, sanitized alert ready for delivery."""

    severity: Severity
    anomaly_id: str
    anomaly_class: AnomalyClass
    title: str
    explanation: str
    dedup_key: Tuple[Optional[str], Optional[str], Optional[str]]
    alert_id: str = field(default_factory=lambda: str(uuid4()))
    evidence_refs: List[str] = field(default_factory=list)
    affected_scope: str = ""
    recommended_check: str = ""
    created_at: datetime = field(default_factory=_now)

    @classmethod
    def from_anomaly(cls, anomaly: Any) -> "Alert":
        """Build a sanitized :class:`Alert` from an Anomaly (or dict)."""
        severity = route(anomaly)

        def get(name: str, default: Any = None) -> Any:
            if isinstance(anomaly, dict):
                return anomaly.get(name, default)
            return getattr(anomaly, name, default)

        aclass = get("anomaly_class")
        if isinstance(aclass, str):
            aclass = AnomalyClass(aclass)

        return cls(
            severity=severity,
            anomaly_id=str(get("anomaly_id") or ""),
            anomaly_class=aclass,
            title=sanitize(
                f"[{severity.value.upper()}] {get('anomaly_class', '')} — "
                f"{(get('description') or '')[:120]}"
            ),
            explanation=sanitize(str(get("description") or "")),
            evidence_refs=sanitize_list(list(get("evidence_refs") or [])),
            affected_scope=sanitize(
                str(get("tenant") or get("workflow") or "unknown")
            ),
            recommended_check=sanitize(str(get("recommended_check") or "")),
            dedup_key=(
                get("trace_id"),
                get("workflow"),
                get("policy_reason"),
            ),
        )


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def route(anomaly: Any) -> Severity:
    """Map an anomaly to its :class:`Severity`.

    * ``SAFETY_BOUNDARY`` → CRITICAL
    * ``IDENTITY_DATA_ACCESS`` → HIGH (CRITICAL when ``cross_tenant_leak``)
    * ``POLICY_PROMPT_INJECTION`` → HIGH if ``actual_bypass`` else WARNING
    * ``QUALITY_DRIFT`` → WARNING
    * ``RELIABILITY_COST`` → WARNING (HIGH when ``sustained_breaches >= 5``)
    * ``BUSINESS_TRUTH`` → HIGH
    """
    def get(name: str, default: Any = None) -> Any:
        if isinstance(anomaly, dict):
            return anomaly.get(name, default)
        return getattr(anomaly, name, default)

    aclass = get("anomaly_class")
    if isinstance(aclass, str):
        aclass = AnomalyClass(aclass)

    if aclass is AnomalyClass.SAFETY_BOUNDARY:
        return Severity.CRITICAL
    if aclass is AnomalyClass.IDENTITY_DATA_ACCESS:
        return (
            Severity.CRITICAL if get("cross_tenant_leak", False) else Severity.HIGH
        )
    if aclass is AnomalyClass.POLICY_PROMPT_INJECTION:
        return Severity.HIGH if get("actual_bypass", False) else Severity.WARNING
    if aclass is AnomalyClass.QUALITY_DRIFT:
        return Severity.WARNING
    if aclass is AnomalyClass.RELIABILITY_COST:
        return (
            Severity.HIGH
            if int(get("sustained_breaches", 0) or 0) >= 5
            else Severity.WARNING
        )
    if aclass is AnomalyClass.BUSINESS_TRUTH:
        return Severity.HIGH
    return Severity.DIGEST


# --------------------------------------------------------------------------
# Alert manager
# --------------------------------------------------------------------------

#: Capability name paused when the delivery function fails.
DELIVERY_CAPABILITY = "alert_delivery"

#: Components the dead-man check watches for.
WATCHED_COMPONENTS = ("logger", "evaluator", "alert_sender")

DeliveryFn = Callable[[Alert], Dict[str, Any]]


class AlertManager:
    """Routes anomalies into sanitized alerts and delivers them.

    Monitoring-must-not-act: :meth:`pause_shadow_worker` is the ONLY
    automated response this class permits (it merely records that a shadow
    worker was paused). There are deliberately no methods here for changing
    permissions, clearing halts, retrying actions, or modifying production
    data — monitoring observes and reports, it never acts.

    Alerting path independence: the delivery function is injected at
    construction (default: in-memory outbox). This class never imports or
    calls agent runtime modules, so the alerting path stays independent of
    the agents under observation.
    """

    def __init__(self, delivery_fn: Optional[DeliveryFn] = None) -> None:
        self.outbox: List[Alert] = []
        self.failed_deliveries: List[Dict[str, Any]] = []
        self.paused_capabilities: Set[str] = set()
        self.paused_workers: Dict[str, Dict[str, Any]] = {}
        self._seen_dedup_keys: Set[Tuple[Any, ...]] = set()
        self._heartbeats: Dict[str, float] = {}
        if delivery_fn is None:
            self._delivery_fn: DeliveryFn = self._default_delivery
        else:
            self._delivery_fn = delivery_fn

    # -- delivery --------------------------------------------------------

    def _default_delivery(self, alert: Alert) -> Dict[str, Any]:
        self.outbox.append(alert)
        return {"delivered": True, "alert_id": alert.alert_id}

    def send(self, alert: Alert) -> Dict[str, Any]:
        """Deliver an alert; return a delivery receipt dict.

        Alerts are deduplicated by ``dedup_key`` (``(trace_id, workflow,
        policy_reason)``): a second send with the same key returns
        ``{"deduped": True}`` and does not re-deliver.

        If the delivery function raises, the failure is recorded locally in
        ``failed_deliveries``, the ``alert_delivery`` capability is marked
        paused in ``paused_capabilities``, and the alert itself is retained —
        alerts are never lost.
        """
        key = tuple(alert.dedup_key)
        if key in self._seen_dedup_keys:
            return {"deduped": True, "alert_id": alert.alert_id}
        self._seen_dedup_keys.add(key)
        try:
            receipt = self._delivery_fn(alert)
        except Exception as exc:  # noqa: BLE001 - record, never lose
            self.failed_deliveries.append(
                {"alert": alert, "error": f"{type(exc).__name__}: {exc}"}
            )
            self.paused_capabilities.add(DELIVERY_CAPABILITY)
            return {
                "delivered": False,
                "alert_id": alert.alert_id,
                "error": str(exc),
            }
        if not isinstance(receipt, dict):
            receipt = {"delivered": True}
        receipt.setdefault("alert_id", alert.alert_id)
        receipt.setdefault("delivered", True)
        return receipt

    # -- the ONLY permitted automated response ----------------------------

    def pause_shadow_worker(self, worker_id: str, reason: str) -> Dict[str, Any]:
        """Record that a shadow worker was paused.

        This is the ONLY automated response the monitoring path may take.
        It changes nothing in production: it simply logs the pause so an
        operator can investigate. There are intentionally no methods here
        for changing permissions, clearing halts, retrying actions, or
        modifying production data.
        """
        record = {
            "worker_id": worker_id,
            "reason": sanitize(reason),
            "paused_at": _now().isoformat(),
        }
        self.paused_workers[worker_id] = record
        return {"paused": True, **record}

    def agent_cannot_clear_alert(
        self, principal: Dict[str, Any], agent_id: str, alert_id: str
    ) -> bool:
        """Guard: agents may never acknowledge/clear alerts.

        Raises :exc:`PermissionError` when the caller is an agent principal
        (``principal_type == "agent"``). Only human principals may
        acknowledge an alert. Returns ``True`` for a human principal,
        meaning the acknowledgement is permitted (the caller then records
        it through their own authorized path — this manager keeps no
        acknowledgement state to mutate).
        """
        principal_type = (principal or {}).get("principal_type", "")
        if principal_type == "agent":
            raise PermissionError(
                f"Agent principal '{agent_id}' is not permitted to clear or "
                f"acknowledge alert '{alert_id}'; only human principals may."
            )
        return True

    # -- dead-man check -----------------------------------------------------

    def heartbeat(self, component_name: str) -> None:
        """Record a last-seen timestamp for a monitored component."""
        self._heartbeats[component_name] = time.monotonic()

    def check_deadman(self, timeout_s: float) -> List[str]:
        """Return components silent longer than ``timeout_s`` seconds.

        Watches ``logger``, ``evaluator``, and ``alert_sender``. A component
        that has never sent a heartbeat counts as silent.
        """
        now = time.monotonic()
        silent = []
        for component in WATCHED_COMPONENTS:
            last = self._heartbeats.get(component)
            if last is None or (now - last) > timeout_s:
                silent.append(component)
        return silent
