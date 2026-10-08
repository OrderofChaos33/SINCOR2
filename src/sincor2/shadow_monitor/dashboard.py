"""Phase 1 shadow-mode monitoring: read-only dashboard + synthetic verification.

Component 4 of the Phase 1 shadow-monitoring build.

This module is deliberately self-contained: it imports nothing from sibling
``shadow_monitor`` modules. The coordinator wires concrete providers (event
store, anomaly store, alert router, heartbeat, worker registry) into
:class:`ShadowDashboard` as plain callables, and :class:`SyntheticVerification`
runs its own in-memory miniature of the pipeline so the synthetic checks never
touch production state.

Read-only contract
------------------
:class:`ShadowDashboard` is a *read-only* monitoring surface. Every section
method is a pure read over the provider callables. The single exception is
:func:`ShadowDashboard.record_review`, which appends to an in-memory review
log — the human-calibration dataset. That state is explicitly documented (not
a hidden mutation) and is the only writable surface on the class; pause state
for workers lives in the worker registry, not here.

Deliberate non-goals
--------------------
* There is intentionally **no** ``agent_score()`` method. A single aggregate
  "agent score" collapses unrelated failure modes into one number and invites
  gaming; disagreement is reported per workflow via
  :func:`ShadowDashboard.per_workflow_disagreement` only.
* Proposed, human-approved, and verified outcomes are three separate numbers
  in :func:`ShadowDashboard.business_outcomes`. Recommendations are never
  presented as outcomes; a conflation warning is emitted if the approved and
  verified counts ever coincide.
* Injection attempts and confirmed impacts are tracked as two separate
  integers in :func:`ShadowDashboard.safety`; they are never conflated.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Small pure helpers (module level, stdlib only)
# ---------------------------------------------------------------------------

def _utcnow_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _mean(values: List[float]) -> float:
    """Arithmetic mean; 0.0 for an empty input (documented, not hidden)."""
    return sum(values) / len(values) if values else 0.0


def _percentile(values: List[float], pct: float) -> float:
    """Linear-interpolation percentile; 0.0 for an empty input."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return float(ordered[low])
    frac = rank - low
    return float(ordered[low] * (1.0 - frac) + ordered[high] * frac)


_PII_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED:EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED:SSN]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED:PHONE]"),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]+\b"), "[REDACTED:API_KEY]"),
)


def _redact_value(value: Any) -> Any:
    """Recursively redact PII-shaped strings inside alert payloads."""
    if isinstance(value, str):
        for pattern, replacement in _PII_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {key: _redact_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# ShadowDashboard
# ---------------------------------------------------------------------------

class ShadowDashboard:
    """Read-only dashboard over Phase 1 shadow-mode monitoring data.

    Constructed with five provider callables (duck-typed; plain dicts/lists
    are fine — no sibling ``shadow_monitor`` imports anywhere in this module):

    * ``get_events()`` — list of event dicts (trace/audit trail).
    * ``get_anomalies()`` — list of anomaly dicts.
    * ``get_alerts()`` — list of alert dicts.
    * ``get_heartbeat()`` — dict describing monitor health/mode.
    * ``get_paused_workers()`` — list of paused worker records.

    Read-only contract: every ``*_section`` method below is a pure read. The
    class holds exactly one piece of state, the in-memory review log written
    by :meth:`record_review` — the human-calibration dataset. Nothing else on
    this class mutates anything.
    """

    #: Decisions a human reviewer may record on a trace.
    REVIEW_DECISIONS = ("accept", "edit", "reject")

    def __init__(
        self,
        get_events: Callable[[], List[Dict[str, Any]]],
        get_anomalies: Callable[[], List[Dict[str, Any]]],
        get_alerts: Callable[[], List[Dict[str, Any]]],
        get_heartbeat: Callable[[], Dict[str, Any]],
        get_paused_workers: Callable[[], List[Dict[str, Any]]],
    ) -> None:
        self._get_events = get_events
        self._get_anomalies = get_anomalies
        self._get_alerts = get_alerts
        self._get_heartbeat = get_heartbeat
        self._get_paused_workers = get_paused_workers
        # The ONE piece of state this class may hold: the human-calibration
        # dataset. Appended to by record_review(); read by calibration_dataset()
        # and folded into the quality/business-outcome sections.
        self._review_log: List[Dict[str, Any]] = []

    # -- provider accessors (private; keep reads centralized) ----------------

    def _events(self) -> List[Dict[str, Any]]:
        return list(self._get_events() or [])

    def _anomalies(self) -> List[Dict[str, Any]]:
        return list(self._get_anomalies() or [])

    def _alerts(self) -> List[Dict[str, Any]]:
        return list(self._get_alerts() or [])

    def _heartbeat(self) -> Dict[str, Any]:
        return dict(self._get_heartbeat() or {})

    def _paused_workers(self) -> List[Dict[str, Any]]:
        return list(self._get_paused_workers() or [])

    def _reviewed_trace_ids(self) -> set:
        return {record["trace_id"] for record in self._review_log}

    @staticmethod
    def _sequence_gaps(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect missing ``seq`` numbers per ``trace_id`` (audit completeness)."""
        by_trace: Dict[Any, List[int]] = {}
        for event in events:
            trace_id = event.get("trace_id")
            seq = event.get("seq")
            if trace_id is not None and isinstance(seq, int):
                by_trace.setdefault(trace_id, []).append(seq)
        gaps: List[Dict[str, Any]] = []
        for trace_id in sorted(by_trace, key=str):
            ordered = sorted(set(by_trace[trace_id]))
            missing = [
                n for n in range(ordered[0], ordered[-1] + 1)
                if n not in set(ordered)
            ]
            if missing:
                gaps.append({"trace_id": trace_id, "missing_seq": missing})
        return gaps

    # -- section 1: mode coverage --------------------------------------------

    def mode_coverage(self) -> Dict[str, Any]:
        """Shadow-mode coverage: is the mode effective and is the trail whole?"""
        heartbeat = self._heartbeat()
        events = self._events()
        return {
            "effective_mode": heartbeat.get("effective_mode", "unknown"),
            "policy_version": heartbeat.get("policy_version", "unknown"),
            "active_shadow_workers": heartbeat.get(
                "active_shadow_workers", len(heartbeat.get("shadow_workers", []))
            ),
            "traces_logged": len(events),
            "event_gaps": self._sequence_gaps(events),
            "disabled_adapters": heartbeat.get("disabled_adapters", []),
        }

    # -- section 2: safety ----------------------------------------------------

    def safety(self) -> Dict[str, Any]:
        """Safety posture.

        Injection *attempts* and *confirmed* impacts are reported as two
        separate integers and are never conflated: an attempt is evidence of
        probing, a confirmed impact is evidence of harm.
        """
        anomalies = self._anomalies()
        denied_by_reason: Dict[str, int] = {}
        for anomaly in anomalies:
            if anomaly.get("category") == "safety" and anomaly.get("action") == "denied":
                reason = str(anomaly.get("reason", "unknown"))
                denied_by_reason[reason] = denied_by_reason.get(reason, 0) + 1
        return {
            "denied_by_reason": denied_by_reason,
            "boundary_violations": [
                a for a in anomalies if a.get("subtype") == "boundary_violation"
            ],
            "injection_attempts": sum(
                1 for a in anomalies if a.get("subtype") == "injection_attempt"
            ),
            "confirmed_impacts": sum(
                1 for a in anomalies if a.get("subtype") == "confirmed_impact"
            ),
        }

    # -- section 3: quality ---------------------------------------------------

    def quality(self) -> Dict[str, Any]:
        """Output quality: schema/evidence completeness, human judgement, drift."""
        events = self._events()
        anomalies = self._anomalies()
        proposals = [e for e in events if e.get("type") == "proposal"]

        by_trace: Dict[Any, List[Dict[str, Any]]] = {}
        for record in self._review_log:
            by_trace.setdefault(record["trace_id"], []).append(record)
        multi_reviewer = [
            records
            for records in by_trace.values()
            if len({r["reviewer"] for r in records}) >= 2
        ]
        disagreed = sum(
            1 for records in multi_reviewer
            if len({r["decision"] for r in records}) > 1
        )
        disagreement_rate = (disagreed / len(multi_reviewer)) if multi_reviewer else 0.0

        drift_by_workflow: Dict[str, int] = {}
        for anomaly in anomalies:
            if anomaly.get("kind") == "drift" and anomaly.get("workflow"):
                workflow = str(anomaly["workflow"])
                drift_by_workflow[workflow] = drift_by_workflow.get(workflow, 0) + 1

        return {
            # 0.0 when there is no data: nothing observed means nothing verified.
            "schema_completeness_pct": (
                100.0 * sum(1 for e in events if e.get("schema_valid")) / len(events)
                if events else 0.0
            ),
            "evidence_completeness_pct": (
                100.0 * sum(1 for p in proposals if p.get("evidence")) / len(proposals)
                if proposals else 0.0
            ),
            "accepted_count": sum(1 for r in self._review_log if r["decision"] == "accept"),
            "edited_count": sum(1 for r in self._review_log if r["decision"] == "edit"),
            "rejected_count": sum(1 for r in self._review_log if r["decision"] == "reject"),
            "reviewer_disagreement_rate": disagreement_rate,
            "drift_by_workflow": drift_by_workflow,
        }

    # -- section 4: reliability & cost ----------------------------------------

    def reliability_cost(self) -> Dict[str, Any]:
        """Reliability and cost: latency distribution, errors, retries, spend."""
        events = self._events()
        anomalies = self._anomalies()
        latencies = [
            float(e["latency_ms"]) for e in events
            if isinstance(e.get("latency_ms"), (int, float))
        ]
        queue_ages = [
            float(e["queue_age_s"]) for e in events
            if isinstance(e.get("queue_age_s"), (int, float))
        ]
        return {
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "error_rate": _mean([1.0 if e.get("error") is True else 0.0 for e in events]),
            "retry_rate": _mean([1.0 if e.get("retried") is True else 0.0 for e in events]),
            "queue_age_s_max": max(queue_ages) if queue_ages else 0.0,
            "token_cost_total": sum(
                e.get("tokens", e.get("token_cost", 0)) or 0 for e in events
            ),
            "budget_cap_events": sum(
                1 for a in anomalies if a.get("subtype") == "budget_cap"
            ),
        }

    # -- section 5: data compliance -------------------------------------------

    def data_compliance(self) -> Dict[str, Any]:
        """Data compliance: sensitive-data flags, retention, opt-outs, exceptions."""
        anomalies = self._anomalies()
        return {
            "sensitive_data_flags": sum(
                1 for a in anomalies if a.get("subtype") == "sensitive_data"
            ),
            "retention_status": self._heartbeat().get("retention_status", "unknown"),
            "suppression_optout_open": sum(
                1 for a in anomalies
                if a.get("subtype") == "suppression_optout" and a.get("status") == "open"
            ),
            "open_exceptions": [
                a for a in anomalies
                if a.get("subtype") == "exception" and a.get("status") == "open"
            ],
        }

    # -- section 6: business outcomes -----------------------------------------

    def business_outcomes(self) -> Dict[str, Any]:
        """Business outcomes — three SEPARATE numbers, never conflated.

        * ``proposed_count`` — agent recommendations made.
        * ``human_approved_count`` — recommendations a human accepted/edited.
        * ``verified_result_count`` — outcomes independently verified.

        Approval is a recommendation; verification is an outcome. If the two
        counts ever coincide, ``conflation_warning`` carries a warning string
        (someone may be presenting approved work as verified results).
        """
        proposed_count = sum(1 for e in self._events() if e.get("type") == "proposal")
        human_approved_count = sum(
            1 for r in self._review_log if r["decision"] in ("accept", "edit")
        )
        verified_result_count = sum(
            1 for e in self._events() if e.get("outcome_verified") is True
        )
        conflation_warning = ""
        if human_approved_count and human_approved_count == verified_result_count:
            conflation_warning = (
                "WARNING: human_approved_count equals verified_result_count. "
                "Approval is a recommendation; verification is an outcome. "
                "Never present approved work as verified results."
            )
        return {
            "proposed_count": proposed_count,
            "human_approved_count": human_approved_count,
            "verified_result_count": verified_result_count,
            "conflation_warning": conflation_warning,
        }

    # -- human review loop -----------------------------------------------------

    def review_queue(self, sample_size: int = 10) -> Dict[str, Any]:
        """Build the human review queue.

        All high-risk traces that have not been reviewed are always included.
        Lower-risk traces contribute a deterministic stratified sample: within
        each risk stratum, traces are ordered by the SHA-256 hash of their
        trace_id and the first ``sample_size`` (split across strata) are taken.
        Deterministic — the same inputs always produce the same queue.
        """
        reviewed = self._reviewed_trace_ids()
        high_risk: List[Any] = []
        strata: Dict[str, List[Dict[str, Any]]] = {}
        for event in self._events():
            trace_id = event.get("trace_id")
            if trace_id is None or trace_id in reviewed:
                continue
            risk = str(event.get("risk", "unknown"))
            if risk == "high":
                if trace_id not in high_risk:
                    high_risk.append(trace_id)
            elif risk in ("low", "medium"):
                bucket = strata.setdefault(risk, [])
                if not any(e.get("trace_id") == trace_id for e in bucket):
                    bucket.append(event)

        per_stratum = max(1, sample_size // max(1, len(strata)))
        sampled: List[Any] = []
        for risk in sorted(strata):
            ordered = sorted(
                strata[risk],
                key=lambda e: int(
                    hashlib.sha256(str(e.get("trace_id")).encode()).hexdigest(), 16
                ),
            )
            sampled.extend(e.get("trace_id") for e in ordered[:per_stratum])
        sampled = sampled[: max(0, sample_size)]

        return {
            "high_risk": high_risk,
            "sampled": sampled,
            "sample_size": sample_size,
        }

    def record_review(
        self,
        trace_id: str,
        decision: str,
        reason: str,
        reviewer: str,
    ) -> Dict[str, Any]:
        """Record a human review decision.

        This is the dashboard's one documented writable surface: it appends to
        the in-memory calibration dataset (see :meth:`calibration_dataset`).
        Nothing else on this class mutates state.
        """
        if decision not in self.REVIEW_DECISIONS:
            raise ValueError(
                f"decision must be one of {self.REVIEW_DECISIONS}, got {decision!r}"
            )
        record = {
            "trace_id": trace_id,
            "decision": decision,
            "reason": reason,
            "reviewer": reviewer,
            "recorded_at": _utcnow_iso(),
        }
        self._review_log.append(record)
        return dict(record)

    def calibration_dataset(self) -> List[Dict[str, Any]]:
        """Return a copy of the in-memory human-calibration dataset."""
        return [dict(record) for record in self._review_log]

    def per_workflow_disagreement(self) -> Dict[str, float]:
        """Reviewer disagreement rate, per workflow only.

        For each workflow, the fraction of multi-reviewer traces where the
        reviewers recorded different decisions. There is intentionally no
        aggregate "agent score" — disagreement is reported per workflow so a
        strong workflow cannot mask a weak one.
        """
        workflow_by_trace = {
            e.get("trace_id"): e.get("workflow")
            for e in self._events()
            if e.get("trace_id") is not None
        }
        by_trace: Dict[Any, List[Dict[str, Any]]] = {}
        for record in self._review_log:
            by_trace.setdefault(record["trace_id"], []).append(record)

        totals: Dict[str, Dict[str, int]] = {}
        for trace_id, records in by_trace.items():
            workflow = workflow_by_trace.get(trace_id)
            if not workflow:
                continue
            entry = totals.setdefault(str(workflow), {"traces": 0, "disagreed": 0})
            if len({r["reviewer"] for r in records}) >= 2:
                entry["traces"] += 1
                if len({r["decision"] for r in records}) > 1:
                    entry["disagreed"] += 1
        return {
            workflow: (entry["disagreed"] / entry["traces"] if entry["traces"] else 0.0)
            for workflow, entry in sorted(totals.items())
        }


# ---------------------------------------------------------------------------
# Synthetic verification harness
# ---------------------------------------------------------------------------

class _SyntheticPipeline:
    """In-memory miniature of the shadow monitoring pipeline for synthetic tests.

    Appends events to a hash-chained, JSONL-persistable audit trail, runs a
    small set of detectors, routes alerts (with dedup + PII redaction), and
    enforces that only a human principal may clear alerts. Nothing here
    executes side effects: every side-effect-class event is recorded as
    blocked/simulated, which is exactly what the harness asserts.
    """

    #: Failures on one trace before a retry-loop reliability anomaly fires.
    RETRY_LOOP_THRESHOLD = 5

    def __init__(self) -> None:
        self.chain: List[Dict[str, Any]] = []
        self.anomalies: List[Dict[str, Any]] = []
        self.alerts: List[Dict[str, Any]] = []
        self.completeness_gaps: List[Dict[str, Any]] = []
        self.delivery_failures: List[Dict[str, Any]] = []
        self.paused_capabilities: List[str] = []
        self.preserved_alerts: List[Dict[str, Any]] = []
        self.delivery_outage = False
        self.side_effects_executed = 0
        self._alert_keys: set = set()
        self._retry_counts: Dict[Any, int] = {}
        self._retry_loop_fired: set = set()
        self._expected_seq: Dict[Any, int] = {}

    # -- audit trail ----------------------------------------------------------

    @staticmethod
    def _canon(event: Dict[str, Any]) -> str:
        return json.dumps(event, sort_keys=True, separators=(",", ":"))

    def append_event(self, event: Dict[str, Any]) -> str:
        """Append an event to the hash-chained trail and run detectors."""
        payload = self._canon(event)
        event_hash = hashlib.sha256(payload.encode()).hexdigest()
        prev_hash = self.chain[-1]["event_hash"] if self.chain else "0" * 64
        self.chain.append(
            {
                "event": dict(event),
                "event_hash": event_hash,
                "prev_hash": prev_hash,
                "position": len(self.chain),
            }
        )
        if event.get("side_effect_executed"):
            self.side_effects_executed += 1
        self._detect(event)
        return event_hash

    def verify_chain(self) -> bool:
        """Recompute every link; True iff the trail is intact and ordered."""
        prev_hash = "0" * 64
        for link in self.chain:
            if link["prev_hash"] != prev_hash:
                return False
            expected = hashlib.sha256(
                self._canon(link["event"]).encode()
            ).hexdigest()
            if expected != link["event_hash"]:
                return False
            prev_hash = link["event_hash"]
        return True

    def persist(self, path: str) -> None:
        """Persist the full pipeline state as JSONL."""
        with open(path, "w", encoding="utf-8") as handle:
            for link in self.chain:
                handle.write(json.dumps({"record": "chain_link", "link": link}) + "\n")
            for anomaly in self.anomalies:
                handle.write(json.dumps({"record": "anomaly", "anomaly": anomaly}) + "\n")
            for alert in self.alerts:
                handle.write(json.dumps({"record": "alert", "alert": alert}) + "\n")

    @classmethod
    def rebuild(cls, path: str) -> "_SyntheticPipeline":
        """Rebuild pipeline state from a JSONL file written by :meth:`persist`."""
        pipeline = cls()
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                kind = record.get("record")
                if kind == "chain_link":
                    pipeline.chain.append(record["link"])
                elif kind == "anomaly":
                    pipeline.anomalies.append(record["anomaly"])
                elif kind == "alert":
                    alert = record["alert"]
                    pipeline.alerts.append(alert)
                    pipeline._alert_keys.add((alert["kind"], alert["trace_id"]))
        return pipeline

    # -- detectors ------------------------------------------------------------

    def _anomaly(
        self, kind: str, subtype: str, severity: str, trace_id: Any, detail: str
    ) -> Dict[str, Any]:
        anomaly = {
            "anomaly_id": f"ANOM-{len(self.anomalies) + 1:04d}",
            "kind": kind,
            "subtype": subtype,
            "severity": severity,
            "trace_id": trace_id,
            "detail": detail,
            "ts": _utcnow_iso(),
        }
        self.anomalies.append(anomaly)
        return anomaly

    def _raise_alert(
        self,
        kind: str,
        severity: str,
        trace_id: Any,
        detail: str,
        payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Create an alert, deduplicated on (kind, trace_id).

        Returns the alert, or None when an identical alert already exists.
        PII in the payload is redacted before storage. CRITICAL alerts are
        marked routed. During a delivery outage the failure is recorded, the
        delivery capability is paused, and the alert is preserved (never lost).
        """
        key = (kind, trace_id)
        if key in self._alert_keys:
            return None  # deduplicated: same synthetic event, one alert
        self._alert_keys.add(key)
        alert = {
            "alert_id": f"ALT-{len(self.alerts) + 1:04d}",
            "kind": kind,
            "severity": severity,
            "trace_id": trace_id,
            "detail": detail,
            "payload": _redact_value(payload),
            "status": "active",
            "routed": severity == "CRITICAL",
        }
        self.alerts.append(alert)
        if self.delivery_outage:
            self.delivery_failures.append(
                {"alert_id": alert["alert_id"], "reason": "delivery_outage"}
            )
            if "alert_delivery" not in self.paused_capabilities:
                self.paused_capabilities.append("alert_delivery")
            self.preserved_alerts.append(alert)
        return alert

    def _detect(self, event: Dict[str, Any]) -> None:
        event_type = event.get("type")
        trace_id = event.get("trace_id", "unknown")

        if event_type == "email_send_attempt":
            self._anomaly(
                "safety", "attempted_side_effect", "CRITICAL", trace_id,
                "Email send attempted in shadow mode; blocked, no side effect executed.",
            )
            self._raise_alert(
                "attempted_email_send", "CRITICAL", trace_id,
                "CRITICAL: email send attempted in shadow mode", dict(event),
            )
        elif event_type == "tool_invocation" and not event.get("authorized", True):
            self._anomaly(
                "safety", "unauthorized_tool", "HIGH", trace_id,
                f"Tool '{event.get('tool')}' invoked without authorization.",
            )
            self._raise_alert(
                "unauthorized_tool", "HIGH", trace_id,
                "Unauthorized tool invocation blocked", dict(event),
            )
        elif (
            event_type == "data_read"
            and event.get("requester_tenant") != event.get("tenant")
        ):
            self._anomaly(
                "identity", "cross_tenant_read", "HIGH", trace_id,
                "Read attempted against a tenant other than the requester's.",
            )
            self._raise_alert(
                "cross_tenant_read", "HIGH", trace_id,
                "Cross-tenant read blocked", dict(event),
            )
        elif (
            event_type == "proposal"
            and event.get("consequential")
            and event.get("policy_result") is None
        ):
            self._anomaly(
                "policy", "missing_policy_decision", "HIGH", trace_id,
                "Consequential proposal has no policy decision recorded.",
            )
            self._raise_alert(
                "missing_policy_decision", "HIGH", trace_id,
                "Consequential proposal missing policy decision", dict(event),
            )
        elif event_type == "task_attempt":
            count = self._retry_counts.get(trace_id, 0)
            if not event.get("ok"):
                count += 1
            self._retry_counts[trace_id] = count
            if (
                count >= self.RETRY_LOOP_THRESHOLD
                and trace_id not in self._retry_loop_fired
            ):
                self._retry_loop_fired.add(trace_id)
                self._anomaly(
                    "reliability", "retry_loop", "HIGH", trace_id,
                    f"Sustained failures: {count} failed attempts on one trace.",
                )
                self._raise_alert(
                    "retry_loop", "HIGH", trace_id,
                    "Retry loop detected after sustained breaches", dict(event),
                )
        elif event_type == "settlement_claim" and not (
            event.get("verified") and event.get("proof")
        ):
            self._anomaly(
                "business_truth", "unsupported_settlement_claim", "CRITICAL", trace_id,
                "Settlement claimed without supporting proof; not settled.",
            )
            self._raise_alert(
                "unsupported_settlement_claim", "CRITICAL", trace_id,
                "CRITICAL: unsupported settlement claim", dict(event),
            )
        elif event_type == "audit_seq":
            expected = self._expected_seq.get(trace_id, 1)
            seq = event.get("seq", 1)
            if isinstance(seq, int) and seq > expected:
                gap = {"trace_id": trace_id, "missing_seq": list(range(expected, seq))}
                self.completeness_gaps.append(gap)
                self._anomaly(
                    "completeness", "missing_audit_event", "MEDIUM", trace_id,
                    f"Audit sequence gap: missing seq {gap['missing_seq']}.",
                )
            if isinstance(seq, int):
                self._expected_seq[trace_id] = max(expected, seq + 1)

    # -- alert lifecycle ------------------------------------------------------

    def ack_alert(self, alert_id: str, principal: str) -> Dict[str, Any]:
        """Acknowledge an alert. Only a human principal may clear alerts.

        Raises PermissionError for any non-human principal (an agent must
        never be able to clear its own alerts).
        """
        if principal != "human":
            raise PermissionError(
                f"principal {principal!r} may not clear alerts; only 'human' may"
            )
        for alert in self.alerts:
            if alert["alert_id"] == alert_id:
                alert["status"] = "acknowledged"
                return alert
        raise KeyError(f"unknown alert_id: {alert_id}")


# ---------------------------------------------------------------------------
# SyntheticVerification
# ---------------------------------------------------------------------------

class SyntheticVerification:
    """Self-contained synthetic verification harness for shadow-mode monitors.

    Each scenario feeds synthetic events into a fresh in-memory
    :class:`_SyntheticPipeline` and asserts the monitors react as required.
    ``run_all()`` executes every scenario plus the cross-cutting checks
    (deduplication, redaction, restart recovery, agent-cannot-clear) and
    returns ``(scenario_name, passed, detail)`` tuples.
    """

    SCENARIOS = (
        "attempted_email_send",
        "unauthorized_tool",
        "cross_tenant_read",
        "missing_policy_decision",
        "retry_loop",
        "unsupported_settlement_claim",
        "missing_audit_event",
        "alert_delivery_outage",
    )

    EXTRA_CHECKS = (
        "deduplication",
        "redaction",
        "restart_recovery",
        "agent_cannot_clear_alert",
    )

    # -- the eight non-negotiable scenarios -----------------------------------

    def _scenario_attempted_email_send(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "email_send_attempt", "trace_id": "syn-email-1",
             "to": "ops@example.com", "disposition": "blocked"}
        )
        anomaly_ok = any(
            a["kind"] == "safety" and a["subtype"] == "attempted_side_effect"
            for a in pipeline.anomalies
        )
        routed = [
            al for al in pipeline.alerts
            if al["kind"] == "attempted_email_send" and al["severity"] == "CRITICAL"
            and al["routed"]
        ]
        passed = anomaly_ok and len(routed) == 1
        return passed, (
            f"safety anomaly fired={anomaly_ok}, CRITICAL alert routed={bool(routed)}"
        )

    def _scenario_unauthorized_tool(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "tool_invocation", "trace_id": "syn-tool-1",
             "tool": "shell.exec", "authorized": False}
        )
        fired = any(
            a["kind"] == "safety" and a["subtype"] == "unauthorized_tool"
            for a in pipeline.anomalies
        )
        return fired, f"safety anomaly for unauthorized tool fired={fired}"

    def _scenario_cross_tenant_read(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "data_read", "trace_id": "syn-tenant-1",
             "tenant": "acme", "requester_tenant": "globex"}
        )
        fired = any(
            a["kind"] == "identity" and a["subtype"] == "cross_tenant_read"
            for a in pipeline.anomalies
        )
        return fired, f"identity anomaly for cross-tenant read fired={fired}"

    def _scenario_missing_policy_decision(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "proposal", "trace_id": "syn-policy-1",
             "consequential": True, "policy_result": None}
        )
        fired = any(
            a["kind"] == "policy" and a["subtype"] == "missing_policy_decision"
            for a in pipeline.anomalies
        )
        return fired, (
            "anomaly fired for consequential proposal with policy_result=None: "
            f"{fired}"
        )

    def _scenario_retry_loop(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        for i in range(_SyntheticPipeline.RETRY_LOOP_THRESHOLD - 1):
            pipeline.append_event(
                {"type": "task_attempt", "trace_id": "syn-retry-1",
                 "attempt": i + 1, "ok": False}
            )
        early = any(
            a["kind"] == "reliability" and a["subtype"] == "retry_loop"
            for a in pipeline.anomalies
        )
        pipeline.append_event(
            {"type": "task_attempt", "trace_id": "syn-retry-1",
             "attempt": _SyntheticPipeline.RETRY_LOOP_THRESHOLD, "ok": False}
        )
        fired = any(
            a["kind"] == "reliability" and a["subtype"] == "retry_loop"
            for a in pipeline.anomalies
        )
        passed = (not early) and fired
        return passed, (
            f"no anomaly before threshold={not early}, "
            f"reliability anomaly after {_SyntheticPipeline.RETRY_LOOP_THRESHOLD} "
            f"sustained breaches={fired}"
        )

    def _scenario_unsupported_settlement_claim(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "settlement_claim", "trace_id": "syn-settle-1",
             "claim": "settled $1,000", "proof": None, "verified": False}
        )
        fired = any(
            a["kind"] == "business_truth"
            and a["subtype"] == "unsupported_settlement_claim"
            for a in pipeline.anomalies
        )
        return fired, f"business-truth anomaly for proof-less claim fired={fired}"

    def _scenario_missing_audit_event(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.append_event({"type": "audit_seq", "trace_id": "syn-audit-1", "seq": 1})
        pipeline.append_event({"type": "audit_seq", "trace_id": "syn-audit-1", "seq": 3})
        gaps = pipeline.completeness_gaps
        passed = gaps == [{"trace_id": "syn-audit-1", "missing_seq": [2]}]
        return passed, f"completeness gaps detected={gaps}"

    def _scenario_alert_delivery_outage(self) -> Tuple[bool, str]:
        pipeline = _SyntheticPipeline()
        pipeline.delivery_outage = True
        pipeline.append_event(
            {"type": "email_send_attempt", "trace_id": "syn-outage-1",
             "disposition": "blocked"}
        )
        failure_recorded = len(pipeline.delivery_failures) > 0
        paused = "alert_delivery" in pipeline.paused_capabilities
        preserved = len(pipeline.preserved_alerts) == 1
        passed = failure_recorded and paused and preserved
        return passed, (
            f"delivery failure recorded={failure_recorded}, "
            f"capability paused={paused}, alert preserved={preserved}"
        )

    # -- cross-cutting checks ---------------------------------------------------

    def verify_deduplication(self) -> Tuple[bool, str]:
        """Same synthetic event twice must produce exactly one alert."""
        pipeline = _SyntheticPipeline()
        event = {"type": "email_send_attempt", "trace_id": "syn-dedup-1",
                 "disposition": "blocked"}
        pipeline.append_event(dict(event))
        pipeline.append_event(dict(event))
        count = sum(
            1 for al in pipeline.alerts if al["kind"] == "attempted_email_send"
        )
        passed = count == 1
        return passed, f"alerts for duplicated event={count} (expected 1)"

    def verify_redaction(self) -> Tuple[bool, str]:
        """Synthetic PII in an event must not appear raw in any alert."""
        pipeline = _SyntheticPipeline()
        raw_pii = [
            "jane.doe@example.com",
            "123-45-6789",
            "415-555-0132",
            "sk_test_abcdefgh1234",
        ]
        pipeline.append_event(
            {"type": "email_send_attempt", "trace_id": "syn-pii-1",
             "disposition": "blocked",
             "body": "Contact jane.doe@example.com, SSN 123-45-6789, "
                     "phone 415-555-0132, key sk_test_abcdefgh1234"}
        )
        alerts = [al for al in pipeline.alerts if al["trace_id"] == "syn-pii-1"]
        if not alerts:
            return False, "no alert raised for PII event"
        dumped = json.dumps([al["payload"] for al in alerts])
        leaked = [pii for pii in raw_pii if pii in dumped]
        redacted_markers = "[REDACTED:EMAIL]" in dumped and "[REDACTED:SSN]" in dumped
        passed = not leaked and redacted_markers
        return passed, (
            f"raw PII leaked={leaked or 'none'}, "
            f"redaction markers present={redacted_markers}"
        )

    def verify_restart_recovery(self) -> Tuple[bool, str]:
        """Rebuild from persisted JSONL; the hash chain must verify."""
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "email_send_attempt", "trace_id": "syn-restart-1",
             "disposition": "blocked"}
        )
        pipeline.append_event(
            {"type": "data_read", "trace_id": "syn-restart-2",
             "tenant": "acme", "requester_tenant": "acme"}
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = f"{tmpdir}/shadow.jsonl"
            pipeline.persist(path)
            rebuilt = _SyntheticPipeline.rebuild(path)
        chain_ok = rebuilt.verify_chain()
        state_ok = (
            len(rebuilt.chain) == len(pipeline.chain)
            and len(rebuilt.anomalies) == len(pipeline.anomalies)
            and len(rebuilt.alerts) == len(pipeline.alerts)
        )
        passed = chain_ok and state_ok
        return passed, (
            f"chain verifies after rebuild={chain_ok}, "
            f"state restored (chain/anomalies/alerts)=({len(rebuilt.chain)}/"
            f"{len(rebuilt.anomalies)}/{len(rebuilt.alerts)})={state_ok}"
        )

    def verify_agent_cannot_clear_alert(self) -> Tuple[bool, str]:
        """An agent principal acknowledging an alert must raise PermissionError."""
        pipeline = _SyntheticPipeline()
        pipeline.append_event(
            {"type": "email_send_attempt", "trace_id": "syn-ack-1",
             "disposition": "blocked"}
        )
        alert_id = pipeline.alerts[0]["alert_id"]
        try:
            pipeline.ack_alert(alert_id, principal="agent")
            return False, "agent principal cleared an alert without error"
        except PermissionError:
            still_active = pipeline.alerts[0]["status"] == "active"
            human_ok = pipeline.ack_alert(alert_id, principal="human")["status"]
            passed = still_active and human_ok == "acknowledged"
            return passed, (
                f"PermissionError raised for agent principal, alert stayed "
                f"active={still_active}, human ack works={human_ok == 'acknowledged'}"
            )

    # -- runner -----------------------------------------------------------------

    def run_all(self) -> List[Tuple[str, bool, str]]:
        """Execute all scenarios and cross-cutting checks.

        Returns a list of ``(scenario_name, passed, detail)`` tuples, one per
        scenario plus one per extra check. A scenario that raises is reported
        as failed with the exception in the detail string — never swallowed.
        """
        checks: List[Tuple[str, Callable[[], Tuple[bool, str]]]] = [
            ("attempted_email_send", self._scenario_attempted_email_send),
            ("unauthorized_tool", self._scenario_unauthorized_tool),
            ("cross_tenant_read", self._scenario_cross_tenant_read),
            ("missing_policy_decision", self._scenario_missing_policy_decision),
            ("retry_loop", self._scenario_retry_loop),
            ("unsupported_settlement_claim", self._scenario_unsupported_settlement_claim),
            ("missing_audit_event", self._scenario_missing_audit_event),
            ("alert_delivery_outage", self._scenario_alert_delivery_outage),
            ("deduplication", self.verify_deduplication),
            ("redaction", self.verify_redaction),
            ("restart_recovery", self.verify_restart_recovery),
            ("agent_cannot_clear_alert", self.verify_agent_cannot_clear_alert),
        ]
        results: List[Tuple[str, bool, str]] = []
        for name, check in checks:
            try:
                passed, detail = check()
            except Exception as exc:  # noqa: BLE001 - report, don't swallow
                passed, detail = False, f"raised {type(exc).__name__}: {exc}"
            results.append((name, bool(passed), detail))
        return results

    # -- non-negotiable first monitors ------------------------------------------

    def first_monitors_status(self) -> Dict[str, Any]:
        """Status of the non-negotiable first monitors in shadow mode.

        Runs a fixed synthetic fixture through the in-memory pipeline and
        reports: the effective mode under test, that zero real side effects
        executed, policy-decision coverage over consequential proposals, the
        audit-event completeness gap list, and whether email/settlement state
        transitions are reported truthfully (blocked or verified — never
        silently executed or claimed).
        """
        pipeline = _SyntheticPipeline()
        # Policy decisions: 3 of 4 consequential proposals carry a decision.
        for i in range(4):
            pipeline.append_event(
                {"type": "proposal", "trace_id": f"fm-pol-{i}",
                 "consequential": True,
                 "policy_result": "allow" if i < 3 else None}
            )
        # Audit trail with one known gap (seq 2 missing).
        pipeline.append_event({"type": "audit_seq", "trace_id": "fm-audit-1", "seq": 1})
        pipeline.append_event({"type": "audit_seq", "trace_id": "fm-audit-1", "seq": 3})
        # Email attempt: blocked, truthfully reported as blocked.
        pipeline.append_event(
            {"type": "email_send_attempt", "trace_id": "fm-email-1",
             "disposition": "blocked"}
        )
        # Settlement claim WITH proof: verified, truthfully reported as settled.
        pipeline.append_event(
            {"type": "settlement_claim", "trace_id": "fm-settle-1",
             "proof": "0xproof", "verified": True}
        )

        trail = [link["event"] for link in pipeline.chain]
        proposals = [e for e in trail if e.get("type") == "proposal"]
        decided = sum(1 for p in proposals if p.get("policy_result") is not None)

        email_events = [e for e in trail if e.get("type") == "email_send_attempt"]
        truthful_email = bool(email_events) and all(
            e.get("disposition") in ("blocked", "simulated")
            and not e.get("side_effect_executed")
            for e in email_events
        )

        claims = [e for e in trail if e.get("type") == "settlement_claim"]
        truthful_settlement = bool(claims) and all(
            (c.get("verified") and c.get("proof"))
            or any(
                a["trace_id"] == c.get("trace_id") and a["kind"] == "business_truth"
                for a in pipeline.anomalies
            )
            for c in claims
        )

        return {
            "effective_mode_check": "shadow",
            "actual_side_effects_in_shadow": pipeline.side_effects_executed,
            "policy_decision_coverage_pct": (
                100.0 * decided / len(proposals) if proposals else 0.0
            ),
            "audit_event_completeness": list(pipeline.completeness_gaps),
            "truthful_email_transitions": truthful_email,
            "truthful_settlement_transitions": truthful_settlement,
        }
