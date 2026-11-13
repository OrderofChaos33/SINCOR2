"""Anomaly detection for SINCOR2 Phase 1 shadow-mode monitoring.

Shadow mode means the agent layer runs fully but its decisions are observed,
not executed. This module classifies observed deviations into six anomaly
classes and emits :class:`Anomaly` records for the alerting path.

Hard rules vs baselines (the load-bearing split in this module)
--------------------------------------------------------------
* **Hard-rule detectors** — :func:`detect_safety_boundary`,
  :func:`detect_identity_data_access`, :func:`detect_policy_prompt_injection` —
  fire **immediately on first occurrence**. They guard invariants that must
  never be violated (no real external effects while shadowed, no cross-tenant
  data access, no prompt-injection bypass). They take no baseline and need no
  minimum sample count.
* **Baseline detectors** — :func:`detect_quality_drift` and
  :func:`detect_reliability_cost` — fire only against a caller-supplied
  baseline with enough evidence (:func:`detect_quality_drift` requires
  ``reviewed_sample_count >= min_samples``; :func:`detect_reliability_cost`
  requires ``consecutive_breaches >= 3`` sustained deviation). These observe
  *drift*, which is meaningless without a comparison point.
* **Ledger-truth checks** — :func:`detect_business_truth` — fire on
  structural ledger inconsistencies (settlement claimed without evidence,
  duplicate/missing ledger ids, count drift). Treated as hard-rule checks on
  ledger integrity.

All detector inputs are duck-typed ``dict`` (DecisionEvent-shaped) on purpose:
this module never imports Component 2 (event capture) so the monitoring path
cannot accidentally couple to the agent runtime it is watching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


class AnomalyClass(str, Enum):
    """The six anomaly classes Phase 1 shadow monitoring recognises."""

    SAFETY_BOUNDARY = "safety_boundary"
    IDENTITY_DATA_ACCESS = "identity_data_access"
    POLICY_PROMPT_INJECTION = "policy_prompt_injection"
    QUALITY_DRIFT = "quality_drift"
    RELIABILITY_COST = "reliability_cost"
    BUSINESS_TRUTH = "business_truth"


# Action kinds that constitute a *real* external effect. In shadow mode only
# hypothetical ("would_*") actions are legal; any of these firing means the
# agent performed (or attempted to perform) a live side effect.
REAL_EFFECT_ACTIONS = frozenset({"sent", "updated", "settled", "paid"})

# Data classifications that are never expected to appear in ordinary shadow
# traffic unless the workflow explicitly handles them.
SENSITIVE_DATA_CLASSIFICATIONS = frozenset(
    {"pii", "phi", "pci", "credentials", "secrets", "biometric"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Anomaly:
    """A single detected deviation, ready for alert routing."""

    anomaly_class: AnomalyClass
    description: str
    anomaly_id: str = field(default_factory=lambda: str(uuid4()))
    severity_hint: str = "warning"
    trace_id: Optional[str] = None
    task_id: Optional[str] = None
    agent_id: Optional[str] = None
    tenant: Optional[str] = None
    workflow: Optional[str] = None
    policy_reason: Optional[str] = None
    detected_at: datetime = field(default_factory=_now)
    evidence_refs: List[str] = field(default_factory=list)
    recommended_check: str = ""
    # Routing flags consumed by alerting.route(); set by detectors.
    cross_tenant_leak: bool = False
    actual_bypass: bool = False
    sustained_breaches: int = 0


def _base_anomaly(
    event: Dict[str, Any],
    anomaly_class: AnomalyClass,
    description: str,
    evidence_refs: List[str],
    recommended_check: str,
    severity_hint: str,
    **flags: Any,
) -> Anomaly:
    return Anomaly(
        anomaly_class=anomaly_class,
        description=description,
        severity_hint=severity_hint,
        trace_id=event.get("trace_id"),
        task_id=event.get("task_id"),
        agent_id=event.get("agent_id"),
        tenant=event.get("tenant"),
        workflow=event.get("workflow"),
        policy_reason=event.get("policy_reason"),
        evidence_refs=list(evidence_refs),
        recommended_check=recommended_check,
        **flags,
    )


# --------------------------------------------------------------------------
# Hard-rule detectors — fire on first occurrence, no baseline needed.
# --------------------------------------------------------------------------


def _safety_anomaly(
    event: Dict[str, Any], description: str, evidence_refs: List[str]
) -> Anomaly:
    return _base_anomaly(
        event,
        AnomalyClass.SAFETY_BOUNDARY,
        description,
        evidence_refs,
        recommended_check=(
            "Freeze the shadow worker (pause_shadow_worker), capture the full "
            "trace, and confirm no external call actually executed before "
            "resuming monitoring."
        ),
        severity_hint="critical",
    )


def detect_safety_boundary(event: Dict[str, Any]) -> Optional[Anomaly]:  # noqa: F811
    """Detect a safety-boundary breach in shadow mode. HARD RULE.

    Fires on the *first* occurrence of any of:
    * a real external-effect ``action_kind`` (``sent``/``updated``/``settled``/
      ``paid``) instead of a hypothetical ``would_*`` action;
    * an action against an unauthorized tool or target;
    * an agent attempt to clear a halt or expand its own permissions.

    Returns an :class:`Anomaly` on breach, else ``None``.
    """
    action_kind = (event.get("action_kind") or "").lower()

    # 1. Real external effect while shadowed — the cardinal violation.
    if action_kind in REAL_EFFECT_ACTIONS:
        return _safety_anomaly(
            event,
            f"Real external-effect action '{action_kind}' observed while "
            f"shadowed (trace {event.get('trace_id')}); only 'would_*' "
            "hypothetical actions are legal in shadow mode.",
            [
                f"trace:{event.get('trace_id')}",
                f"action_kind:{action_kind}",
            ],
        )

    # 2. Unauthorized tool / target.
    if event.get("authorized") is False:
        return _safety_anomaly(
            event,
            f"Agent {event.get('agent_id')} used unauthorized "
            f"tool={event.get('tool')} target={event.get('target')}.",
            [
                f"trace:{event.get('trace_id')}",
                f"tool:{event.get('tool')}",
                f"target:{event.get('target')}",
            ],
        )

    # 3. Attempt to clear a halt or expand permissions.
    if event.get("halt_clear_attempt") or event.get("permission_expansion_attempt"):
        kind = (
            "halt-clear"
            if event.get("halt_clear_attempt")
            else "permission-expansion"
        )
        return _safety_anomaly(
            event,
            f"Agent {event.get('agent_id')} attempted a {kind} while shadowed.",
            [f"trace:{event.get('trace_id')}", f"attempt:{kind}"],
        )

    return None


def detect_identity_data_access(event: Dict[str, Any]) -> Optional[Anomaly]:
    """Detect identity / data-access violations. HARD RULE.

    Fires on the first occurrence of any of:
    * cross-tenant access (``event['tenant']`` differs from the agent's
      ``home_tenant``);
    * a sensitive ``data_classification`` not expected for the workflow;
    * an unknown / unregistered principal;
    * a stale or missing ``authorized`` / ``authorization_valid`` flag.

    Returns an :class:`Anomaly` on breach, else ``None``.
    """
    tenant = event.get("tenant")
    home_tenant = event.get("home_tenant")
    principal = event.get("principal")
    known_principals = event.get("known_principals") or []
    data_classification = (event.get("data_classification") or "").lower()
    auth_flag = event.get("authorization_valid", event.get("authorized"))

    def base(description: str, evidence: List[str], **flags: Any) -> Anomaly:
        return _base_anomaly(
            event,
            AnomalyClass.IDENTITY_DATA_ACCESS,
            description,
            evidence,
            recommended_check=(
                "Revoke the session, verify tenant isolation at the data "
                "layer, and audit what rows/fields the agent actually touched."
            ),
            severity_hint="high",
            **flags,
        )

    # 1. Cross-tenant access.
    if tenant is not None and home_tenant is not None and tenant != home_tenant:
        return base(
            f"Cross-tenant access: agent home tenant '{home_tenant}' touched "
            f"tenant '{tenant}'.",
            [f"trace:{event.get('trace_id')}", f"tenant:{tenant}"],
            cross_tenant_leak=True,
        )

    # 2. Unexpected sensitive data classification.
    expected = set(event.get("expected_data_classifications") or [])
    if data_classification in SENSITIVE_DATA_CLASSIFICATIONS and (
        not expected or data_classification not in expected
    ):
        return base(
            f"Sensitive data classification '{data_classification}' accessed "
            f"unexpectedly by agent {event.get('agent_id')}.",
            [
                f"trace:{event.get('trace_id')}",
                f"classification:{data_classification}",
            ],
        )

    # 3. Unknown principal.
    if principal is not None and known_principals and principal not in known_principals:
        return base(
            f"Unknown principal '{principal}' acted in shadow traffic.",
            [f"trace:{event.get('trace_id')}", f"principal:{principal}"],
        )

    # 4. Stale or missing authorization flag.
    if auth_flag is None or auth_flag is False:
        return base(
            f"Missing or stale authorization flag for agent "
            f"{event.get('agent_id')} (flag={auth_flag!r}).",
            [f"trace:{event.get('trace_id')}"],
        )

    return None


def detect_policy_prompt_injection(event: Dict[str, Any]) -> Optional[Anomaly]:
    """Detect policy denials and prompt-injection signals. HARD RULE.

    Fires on the first occurrence of any of:
    * ``policy_result == 'deny'`` on a high/critical ``risk_tier``;
    * a non-empty ``injection_indicators`` list;
    * repeated bypass attempts (``attempt_count`` > 1).

    The anomaly description distinguishes ``detected_attempt`` (the injection
    was caught / policy denied it) from ``actual_bypass`` (the policy was
    actually circumvented) so routing can escalate the latter.
    """
    policy_result = (event.get("policy_result") or "").lower()
    risk_tier = (event.get("risk_tier") or "").lower()
    indicators = event.get("injection_indicators") or []
    attempt_count = int(event.get("attempt_count", 1) or 1)
    bypassed = bool(event.get("bypass_succeeded", False))

    fired_kind: Optional[str] = None
    if policy_result == "deny" and risk_tier in {"high", "critical"}:
        fired_kind = "policy_deny_high_risk"
    elif indicators:
        fired_kind = "injection_indicators"
    elif attempt_count > 1:
        fired_kind = "repeated_bypass_attempts"

    if fired_kind is None:
        return None

    verdict = "actual_bypass" if bypassed else "detected_attempt"
    return _base_anomaly(
        event,
        AnomalyClass.POLICY_PROMPT_INJECTION,
        f"[{verdict}] {fired_kind}: risk_tier={risk_tier}, "
        f"policy_result={policy_result}, indicators={list(indicators)}, "
        f"attempt_count={attempt_count}.",
        [
            f"trace:{event.get('trace_id')}",
            f"kind:{fired_kind}",
            f"verdict:{verdict}",
        ],
        recommended_check=(
            "Quarantine the prompt chain, diff the injected content against "
            "the trusted policy, and confirm whether any downstream action "
            "executed before the deny."
        ),
        severity_hint="high" if bypassed else "warning",
        actual_bypass=bypassed,
    )


# --------------------------------------------------------------------------
# Baseline detectors — require a baseline and minimum evidence.
# --------------------------------------------------------------------------


def detect_quality_drift(
    event: Dict[str, Any],
    baseline: Dict[str, Any],
    min_samples: int = 30,
) -> Optional[Anomaly]:
    """Detect output-quality drift against a per-(workflow, risk_tier) baseline.

    Fires only when **all** of the following hold:
    * the event carries at least one quality signal (``unsupported_claims``,
      ``missing_evidence``, ``malformed_output``, or ``human_disagreement``);
    * ``baseline['reviewed_sample_count'] >= min_samples`` (default 30);
    * the baseline belongs to the same ``workflow`` as the event.

    Baselines are **never pooled across workflows**: if the baseline's
    ``workflow`` is set and differs from the event's, :exc:`ValueError` is
    raised rather than comparing against another workflow's statistics.
    """
    baseline_workflow = baseline.get("workflow")
    event_workflow = event.get("workflow")
    if baseline_workflow and event_workflow and baseline_workflow != event_workflow:
        raise ValueError(
            "Refusing to compare event workflow "
            f"'{event_workflow}' against baseline for workflow "
            f"'{baseline_workflow}': cross-workflow pooling is forbidden."
        )

    reviewed = int(baseline.get("reviewed_sample_count", 0) or 0)
    if reviewed < min_samples:
        return None

    signals = [
        name
        for name in (
            "unsupported_claims",
            "missing_evidence",
            "malformed_output",
            "human_disagreement",
        )
        if event.get(name)
    ]
    if not signals:
        return None

    baseline_error_rate = float(baseline.get("error_rate", 0.0) or 0.0)
    return _base_anomaly(
        event,
        AnomalyClass.QUALITY_DRIFT,
        f"Quality drift in workflow '{event_workflow}': signals={signals} "
        f"against baseline error_rate={baseline_error_rate:.3f} "
        f"(reviewed_sample_count={reviewed}).",
        [f"trace:{event.get('trace_id')}", f"signals:{','.join(signals)}"],
        recommended_check=(
            "Pull the flagged samples, re-run human review on the drifted "
            "subset, and compare error rate against the frozen baseline "
            "before any promotion decision."
        ),
        severity_hint="warning",
    )


def detect_reliability_cost(
    event: Dict[str, Any],
    window_stats: Dict[str, Any],
) -> Optional[Anomaly]:
    """Detect reliability / cost anomalies over a rolling window.

    Signals: retry loops (``retry_count > max_retries``), timeouts
    (``timed_out``), token/compute spikes vs ``baseline_tokens``, and stalled
    queues (``queue_age_s > queue_age_threshold_s``).

    Fires only on **sustained** deviation: the caller passes
    ``window_stats['consecutive_breaches']`` and an anomaly is emitted only
    when it is ``>= 3``.
    """
    breaches = int(window_stats.get("consecutive_breaches", 0) or 0)
    if breaches < 3:
        return None

    max_retries = int(window_stats.get("max_retries", 3) or 3)
    queue_threshold = float(window_stats.get("queue_age_threshold_s", 300.0) or 300.0)
    baseline_tokens = float(window_stats.get("baseline_tokens", 0.0) or 0.0)
    spike_ratio = float(window_stats.get("token_spike_ratio", 2.0) or 2.0)

    triggers: List[str] = []
    retry_count = int(event.get("retry_count", 0) or 0)
    if retry_count > max_retries:
        triggers.append(f"retry_loop(retry_count={retry_count}>max={max_retries})")
    if event.get("timed_out"):
        triggers.append("timeout")
    tokens = float(event.get("tokens_used", 0.0) or 0.0)
    if baseline_tokens > 0 and tokens >= baseline_tokens * spike_ratio:
        triggers.append(
            f"token_spike({tokens:.0f}>=baseline {baseline_tokens:.0f}x{spike_ratio})"
        )
    queue_age = float(event.get("queue_age_s", 0.0) or 0.0)
    if queue_age > queue_threshold:
        triggers.append(f"stalled_queue(queue_age_s={queue_age:.0f})")

    if not triggers:
        return None

    return _base_anomaly(
        event,
        AnomalyClass.RELIABILITY_COST,
        f"Reliability/cost deviation sustained over {breaches} consecutive "
        f"windows: {', '.join(triggers)}.",
        [f"trace:{event.get('trace_id')}", f"breaches:{breaches}"],
        recommended_check=(
            "Inspect the retry/backoff configuration and the stalled queue "
            "consumer; cap per-task token budgets before the next window."
        ),
        severity_hint="high" if breaches >= 5 else "warning",
        sustained_breaches=breaches,
    )


# --------------------------------------------------------------------------
# Ledger-truth checks — structural integrity of the shadow ledger.
# --------------------------------------------------------------------------


def detect_business_truth(event: Dict[str, Any]) -> Optional[Anomaly]:
    """Detect business-truth violations in the shadow ledger. HARD RULE.

    Fires on the first occurrence of any of:
    * ``status == 'settled'`` claimed without ``evidence_refs``;
    * product/skill count drift vs ``expected_product_count`` /
      ``expected_skill_count``;
    * a duplicate ``ledger_event_id`` (caller passes ``seen_event_ids``);
    * a missing expected ledger event (``expected_event_ids`` minus
      ``seen_event_ids`` non-empty).
    """
    def base(description: str, evidence: List[str]) -> Anomaly:
        return _base_anomaly(
            event,
            AnomalyClass.BUSINESS_TRUTH,
            description,
            evidence,
            recommended_check=(
                "Reconcile the shadow ledger against the source of truth; "
                "halt promotion of any dependent settlement until the ledger "
                "is consistent."
            ),
            severity_hint="high",
        )

    # 1. Settlement claimed without evidence.
    if (event.get("status") or "").lower() == "settled" and not event.get(
        "evidence_refs"
    ):
        return base(
            "Settlement claimed (status=settled) with no evidence_refs.",
            [f"trace:{event.get('trace_id')}", "missing:evidence_refs"],
        )

    # 2. Product / skill count drift.
    for label, actual_key, expected_key in (
        ("product", "product_count", "expected_product_count"),
        ("skill", "skill_count", "expected_skill_count"),
    ):
        expected = event.get(expected_key)
        actual = event.get(actual_key)
        if expected is not None and actual is not None and actual != expected:
            return base(
                f"{label.capitalize()} count drift: observed {actual}, "
                f"expected {expected}.",
                [
                    f"trace:{event.get('trace_id')}",
                    f"{actual_key}:{actual}",
                    f"{expected_key}:{expected}",
                ],
            )

    # 3. Duplicate ledger event ids.
    ledger_id = event.get("ledger_event_id")
    seen = set(event.get("seen_event_ids") or [])
    if ledger_id and ledger_id in seen:
        return base(
            f"Duplicate ledger event id '{ledger_id}'.",
            [f"trace:{event.get('trace_id')}", f"ledger_event_id:{ledger_id}"],
        )

    # 4. Missing expected ledger events.
    expected_ids = set(event.get("expected_event_ids") or [])
    missing = sorted(expected_ids - seen - ({ledger_id} if ledger_id else set()))
    if missing:
        return base(
            f"Missing expected ledger events: {missing}.",
            [f"trace:{event.get('trace_id')}", f"missing:{','.join(missing)}"],
        )

    return None


#: Convenience registry: every detector in definition order.
DETECTORS = (
    detect_safety_boundary,
    detect_identity_data_access,
    detect_policy_prompt_injection,
    detect_quality_drift,
    detect_reliability_cost,
    detect_business_truth,
)
