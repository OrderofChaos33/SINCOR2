#!/usr/bin/env python3
"""OBS-ENT Enterprise Mesh — fleet observability aggregation for SINCOR.

STAGE: SERVICE ($2,500/mo). The enterprise tier of the observability SKU
family. It aggregates the sibling observability primitives into a
fleet-level view, evaluates machine-checkable SLAs, emits signed alerts
through configurable outbound adapters, and generates the support
runbook that ships with the engagement.

Sibling primitives (built by sibling tracks; imported defensively)
------------------------------------------------------------------
``obs_skus.vitals`` — per-agent vitals snapshot. Documented schema::

    {
        "agent_id":        str,    # e.g. "sincor-liveness-01"
        "status":          str,    # "healthy" | "degraded" | "critical" | "unknown"
        "heartbeat_ms":    int,    # epoch ms of the agent's last heartbeat
        "checked_at_ms":   int,    # epoch ms when this snapshot was taken
        "tasks_completed": int,
        "tasks_failed":    int,
        "avg_latency_ms":  float,
        "error_rate":      float,  # 0.0 - 1.0
        "uptime_24h":      float,  # 0.0 - 1.0
        "last_error":      str | None,
    }

``obs_skus.audit_trail`` — incident / audit events. Documented schema::

    {
        "event_id":       str,
        "event_type":     str,    # "incident" | "alert" | "escalation" | "note"
        "agent_id":       str | None,
        "severity":       str,    # "info" | "warning" | "error" | "critical"
        "summary":        str,
        "occurred_at_ms": int,    # when the underlying condition happened
        "alerted_at_ms":  int | None,  # when the alert was emitted (None = never)
        "resolved_at_ms": int | None,  # None = still open
        "details":        dict,
    }

``obs_skus.drift_quality`` — quality drift signals. Documented schema::

    {
        "agent_id":       str,
        "metric":         str,    # e.g. "task_success_rate"
        "baseline":       float,
        "current":        float,
        "drift_pct":      float,  # signed percent change vs baseline
        "verdict":        str,    # "nominal" | "drift" | "severe"
        "assessed_at_ms": int,
    }

Every public function in this module accepts plain dicts matching these
schemas, so the mesh works with fixtures and with the real sibling
modules interchangeably. Dataclass instances and simple objects with
matching attribute names are coerced the same way.

Integration reality (read before configuring adapters)
-------------------------------------------------------
Verified 2026-09-29: this repository contains NO Sentry integration and
NO healthchecks.io integration. The only in-repo mentions are an
unrelated junk-email domain filter (``outreach_engine.py``) and an
aspirational TODO line in ``docs/deployment/production.md``.

Accordingly, the adapters in this module are *configurable outbound
HTTP adapters*:

- ``generic_webhook`` — a real, working signed-webhook dispatcher.
- ``sentry_style`` — builds Sentry-compatible event payloads and POSTs
  them to a customer-supplied endpoint. This is NOT a verified Sentry
  integration; no Sentry account is linked.
- ``healthchecks_style`` — emits dead-man's-switch style ping requests
  to a customer-supplied monitor URL, compatible with healthchecks.io-
  style monitors. This is NOT a verified healthchecks.io integration;
  no account is linked.

Every adapter is **disabled by default**, every inventory entry carries
``verified: False`` (end-to-end verification is recorded in the
runbook's onboarding checklist, never self-asserted by this module),
and no function in this module may report an adapter as "connected".

Design rules
------------
- Fleet health uses worst-status-wins with explicit reasons — never
  averages that hide a failing agent.
- Unknown data is escalated, never treated as healthy
  (``unknown`` outranks ``degraded`` in the rollup).
- The webhook dispatcher fails closed: network problems return a
  ``DeliveryResult(ok=False)``, they never raise and never lose the
  payload silently.
- Adapter secrets are passed in by the caller, never stored globally,
  never logged, never rendered into runbooks.
- Customer-facing text uses positive framing.

This module touches no money path, no auth, no stake/pool ledgers, no
task state, no contracts, and no production deploys. It only reads the
observability records it is given and emits outbound alerts.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Status model
# ---------------------------------------------------------------------------

STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_CRITICAL = "critical"
STATUS_UNKNOWN = "unknown"

ALL_STATUSES = (STATUS_HEALTHY, STATUS_DEGRADED, STATUS_CRITICAL, STATUS_UNKNOWN)

# Worst-status-wins ranking. "unknown" deliberately outranks "degraded":
# an unobserved agent cannot be proven healthy, so absence of signal is
# escalated rather than averaged away.
_SEVERITY_RANK = {
    STATUS_HEALTHY: 0,
    STATUS_DEGRADED: 1,
    STATUS_UNKNOWN: 2,
    STATUS_CRITICAL: 3,
}

#: Default heartbeat freshness window (ms). Matches the 60s heartbeat TTL
#: enforced by the marketplace bid path (see tests/pytest/test_liveness_heartbeat.py).
DEFAULT_HEARTBEAT_TTL_MS = 60_000


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_ms(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts_ms / 1000.0))


# ---------------------------------------------------------------------------
# Defensive sibling access
# ---------------------------------------------------------------------------

def _load_sibling(module_name: str) -> Optional[Any]:
    """Import ``sincor2.obs_skus.<module_name>`` if present, else None.

    Never raises: the mesh works on plain dict fixtures when a sibling
    track has not landed its module yet.
    """
    try:
        return importlib.import_module(f"sincor2.obs_skus.{module_name}")
    except Exception:
        return None


# Loaded lazily so importing enterprise_mesh never fails when siblings
# are absent. Callers that want the live sibling helpers can use these.
def vitals_module() -> Optional[Any]:
    """Return the sibling ``vitals`` module if installed, else None."""
    return _load_sibling("vitals")


def audit_trail_module() -> Optional[Any]:
    """Return the sibling ``audit_trail`` module if installed, else None."""
    return _load_sibling("audit_trail")


def drift_quality_module() -> Optional[Any]:
    """Return the sibling ``drift_quality`` module if installed, else None."""
    return _load_sibling("drift_quality")


def _as_dict(record: Any) -> Dict[str, Any]:
    """Coerce a vitals/audit/drift record to a plain dict.

    Accepts dicts, dataclass instances, and simple objects with
    attributes. Anything else coerces to ``{}``.
    """
    if record is None:
        return {}
    if isinstance(record, Mapping):
        return dict(record)
    if is_dataclass(record) and not isinstance(record, type):
        try:
            return asdict(record)
        except Exception:
            return {}
    if hasattr(record, "__dict__"):
        try:
            return {k: v for k, v in vars(record).items() if not k.startswith("_")}
        except Exception:
            return {}
    return {}


def _rec_field(record: Any, name: str, default: Any = None) -> Any:
    """Read one field from a dict / dataclass / attribute-style record."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    if is_dataclass(record) and not isinstance(record, type):
        return getattr(record, name, default)
    return getattr(record, name, default)


def _rec_ts(record: Any, *names: str, default: Optional[int] = None) -> Optional[int]:
    """First present timestamp field (as int epoch ms) from ``names``."""
    for name in names:
        value = _rec_field(record, name, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return default

# ---------------------------------------------------------------------------
# Fleet aggregation + health rollup
# ---------------------------------------------------------------------------


@dataclass
class AgentMeshEntry:
    """One agent's position inside the fleet view."""

    agent_id: str
    status: str  # effective status after freshness checks
    reported_status: str  # status as reported by the vitals snapshot
    heartbeat_age_ms: Optional[int]
    heartbeat_stale: bool
    vitals: Dict[str, Any]
    open_incidents: List[Dict[str, Any]]
    drift_signals: List[Dict[str, Any]]
    reasons: List[str]


@dataclass
class FleetHealth:
    """Fleet-level health: worst-status-wins, with reasons.

    Rollup rules (documented, machine-checked in tests):
      1. Empty fleet -> ``unknown`` ("no agents reporting").
      2. A stale heartbeat forces that agent to ``unknown`` with a reason.
      3. Any ``critical`` agent -> fleet ``critical``.
      4. Otherwise the worst per-agent status wins
         (critical > unknown > degraded > healthy); unknown outranks
         degraded because an unobserved agent cannot be proven healthy.
      5. Mass failure: if the share of ``critical`` + ``unknown`` agents
         reaches ``mass_failure_ratio`` (default 0.5), the fleet is
         ``critical`` even when no single agent reports critical.
      6. ``reasons`` always names the agents and triggers behind the
         headline status. No averages are used anywhere in this path.
    """

    status: str
    reasons: List[str]
    agent_count: int
    counts: Dict[str, int]
    evaluated_at_ms: int


@dataclass
class FleetView:
    """Point-in-time aggregated view of a whole agent fleet."""

    fleet_name: str
    generated_at_ms: int
    health: FleetHealth
    agents: List[AgentMeshEntry]
    open_incident_count: int
    drift_alert_count: int  # drift signals with verdict != "nominal"


def _effective_status(
    vitals: Dict[str, Any],
    heartbeat_ttl_ms: int,
    now_ms: int,
) -> Tuple[str, str, Optional[int], bool, List[str]]:
    """Return (effective, reported, heartbeat_age_ms, stale, reasons)."""
    reported = str(_rec_field(vitals, "status", STATUS_UNKNOWN) or STATUS_UNKNOWN)
    if reported not in ALL_STATUSES:
        reported = STATUS_UNKNOWN
    heartbeat_ms = _rec_ts(vitals, "heartbeat_ms")
    reasons: List[str] = []
    stale = False
    age_ms: Optional[int] = None
    if heartbeat_ms is None:
        stale = True
        reasons.append("no heartbeat timestamp in vitals snapshot")
    else:
        age_ms = max(0, now_ms - heartbeat_ms)
        if age_ms > heartbeat_ttl_ms:
            stale = True
            reasons.append(
                f"heartbeat stale: last beat {age_ms}ms ago "
                f"(ttl {heartbeat_ttl_ms}ms)"
            )
    effective = STATUS_UNKNOWN if stale else reported
    if stale and reported != STATUS_UNKNOWN:
        reasons.append(f"reported status was {reported!r}; escalated to unknown")
    return effective, reported, age_ms, stale, reasons


def rollup_fleet_health(
    entries: Sequence[AgentMeshEntry],
    *,
    mass_failure_ratio: float = 0.5,
    now_ms: Optional[int] = None,
) -> FleetHealth:
    """Aggregate per-agent entries into fleet health (worst-status-wins)."""
    now = now_ms if now_ms is not None else _now_ms()
    counts: Dict[str, int] = {s: 0 for s in ALL_STATUSES}
    for entry in entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1

    reasons: List[str] = []
    n = len(entries)

    if n == 0:
        return FleetHealth(
            status=STATUS_UNKNOWN,
            reasons=["no agents reporting in this fleet view"],
            agent_count=0,
            counts=counts,
            evaluated_at_ms=now,
        )

    critical_agents = [e.agent_id for e in entries if e.status == STATUS_CRITICAL]
    unknown_agents = [e.agent_id for e in entries if e.status == STATUS_UNKNOWN]
    degraded_agents = [e.agent_id for e in entries if e.status == STATUS_DEGRADED]

    bad_share = (len(critical_agents) + len(unknown_agents)) / n

    if critical_agents:
        status = STATUS_CRITICAL
        reasons.append(
            f"{len(critical_agents)} agent(s) critical: {', '.join(sorted(critical_agents))}"
        )
    elif bad_share >= mass_failure_ratio:
        status = STATUS_CRITICAL
        reasons.append(
            f"mass failure: {len(critical_agents) + len(unknown_agents)}/{n} agents "
            f"critical or unknown (threshold {mass_failure_ratio:.0%})"
        )
    elif unknown_agents:
        status = STATUS_UNKNOWN
        reasons.append(
            f"{len(unknown_agents)} agent(s) without fresh signal: "
            f"{', '.join(sorted(unknown_agents))}"
        )
    elif degraded_agents:
        status = STATUS_DEGRADED
        reasons.append(
            f"{len(degraded_agents)} agent(s) degraded: "
            f"{', '.join(sorted(degraded_agents))}"
        )
    else:
        status = STATUS_HEALTHY
        reasons.append(f"all {n} agents reporting healthy")

    # Carry per-agent detail reasons (e.g. stale-heartbeat explanations).
    for entry in entries:
        for reason in entry.reasons:
            reasons.append(f"{entry.agent_id}: {reason}")

    return FleetHealth(
        status=status,
        reasons=reasons,
        agent_count=n,
        counts=counts,
        evaluated_at_ms=now,
    )


def build_fleet_view(
    fleet_name: str,
    vitals_records: Sequence[Any],
    *,
    audit_events: Sequence[Any] = (),
    drift_signals: Sequence[Any] = (),
    heartbeat_ttl_ms: int = DEFAULT_HEARTBEAT_TTL_MS,
    mass_failure_ratio: float = 0.5,
    now_ms: Optional[int] = None,
) -> FleetView:
    """Build a fleet view from vitals snapshots, audit events, drift signals.

    Each record may be a dict matching the documented schema, a dataclass,
    or an attribute-style object. Unknown/missing fields degrade to
    ``unknown`` status rather than raising.
    """
    now = now_ms if now_ms is not None else _now_ms()

    incidents_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for event in audit_events:
        evt = _as_dict(event)
        if _rec_field(evt, "resolved_at_ms", None) is None:
            aid = str(_rec_field(evt, "agent_id", "") or "")
            incidents_by_agent.setdefault(aid, []).append(evt)

    drift_by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for signal in drift_signals:
        sig = _as_dict(signal)
        aid = str(_rec_field(sig, "agent_id", "") or "")
        drift_by_agent.setdefault(aid, []).append(sig)

    entries: List[AgentMeshEntry] = []
    for record in vitals_records:
        vitals = _as_dict(record)
        agent_id = str(_rec_field(vitals, "agent_id", "") or "unknown-agent")
        effective, reported, age_ms, stale, reasons = _effective_status(
            vitals, heartbeat_ttl_ms, now
        )
        open_incidents = incidents_by_agent.get(agent_id, [])
        for incident in open_incidents:
            reasons.append(
                f"open incident {_rec_field(incident, 'event_id', '?')}: "
                f"{_rec_field(incident, 'summary', '')}"
            )
        drift = drift_by_agent.get(agent_id, [])
        for signal in drift:
            if str(_rec_field(signal, "verdict", "nominal")) != "nominal":
                reasons.append(
                    f"quality drift on {_rec_field(signal, 'metric', '?')}: "
                    f"{_rec_field(signal, 'drift_pct', '?')}% "
                    f"({_rec_field(signal, 'verdict', '')})"
                )
        entries.append(
            AgentMeshEntry(
                agent_id=agent_id,
                status=effective,
                reported_status=reported,
                heartbeat_age_ms=age_ms,
                heartbeat_stale=stale,
                vitals=vitals,
                open_incidents=open_incidents,
                drift_signals=drift,
                reasons=reasons,
            )
        )

    entries.sort(key=lambda e: e.agent_id)
    health = rollup_fleet_health(entries, mass_failure_ratio=mass_failure_ratio, now_ms=now)

    drift_alerts = sum(
        1
        for sig_list in drift_by_agent.values()
        for sig in sig_list
        if str(_rec_field(sig, "verdict", "nominal")) != "nominal"
    )

    return FleetView(
        fleet_name=fleet_name,
        generated_at_ms=now,
        health=health,
        agents=entries,
        open_incident_count=sum(len(v) for v in incidents_by_agent.values()),
        drift_alert_count=drift_alerts,
    )


def fleet_view_to_dict(view: FleetView) -> Dict[str, Any]:
    """Serialise a :class:`FleetView` to plain JSON-compatible dicts."""
    return {
        "fleet_name": view.fleet_name,
        "generated_at_ms": view.generated_at_ms,
        "generated_at": _iso_ms(view.generated_at_ms),
        "health": {
            "status": view.health.status,
            "reasons": view.health.reasons,
            "agent_count": view.health.agent_count,
            "counts": view.health.counts,
            "evaluated_at_ms": view.health.evaluated_at_ms,
        },
        "open_incident_count": view.open_incident_count,
        "drift_alert_count": view.drift_alert_count,
        "agents": [
            {
                "agent_id": e.agent_id,
                "status": e.status,
                "reported_status": e.reported_status,
                "heartbeat_age_ms": e.heartbeat_age_ms,
                "heartbeat_stale": e.heartbeat_stale,
                "open_incidents": e.open_incidents,
                "drift_signals": e.drift_signals,
                "reasons": e.reasons,
            }
            for e in view.agents
        ],
    }

# ---------------------------------------------------------------------------
# Signed webhook dispatcher + integration adapters
# ---------------------------------------------------------------------------


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Canonical JSON: sorted keys, compact separators, no ``signature`` key.

    This is the shared signing pattern used across the observability SKU
    family (same canonicalisation as ``a2a_inbound.sign_payload``): the
    receiver recomputes the HMAC over this exact byte string.
    """
    clean = {k: payload[k] for k in payload if k != "signature"}
    return json.dumps(clean, separators=(",", ":"), sort_keys=True)


def sign_payload(payload: Mapping[str, Any], secret: str) -> str:
    """HMAC-SHA256 hex signature over :func:`canonical_json`."""
    return hmac.new(secret.encode("utf-8"), canonical_json(payload).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_signature(payload: Mapping[str, Any], secret: str, signature: str) -> bool:
    """Constant-time check of a webhook signature. Never raises on bad input."""
    try:
        expected = sign_payload(payload, secret)
        return hmac.compare_digest(expected, str(signature or ""))
    except Exception:
        return False


@dataclass
class DeliveryResult:
    """Outcome of one webhook delivery attempt. Fail-closed: ``ok=False``
    carries the reason in ``error``; the payload is never silently lost."""

    ok: bool
    adapter: str
    url: str
    status_code: Optional[int]
    attempts: int
    error: Optional[str]
    payload_sha256: str
    dry_run: bool


class SignedWebhookDispatcher:
    """REAL generic signed-webhook dispatcher (stdlib only, no new deps).

    POSTs JSON payloads with an HMAC-SHA256 signature header so receivers
    can authenticate the sender::

        X-Sincor-Signature: sha256=<hex>
        X-Sincor-Event:     <event name>
        X-Sincor-Timestamp: <epoch ms>

    Retries transient failures with backoff; every outcome — including
    dry runs — is returned as a :class:`DeliveryResult`.
    """

    def __init__(
        self,
        *,
        timeout_s: float = 5.0,
        max_attempts: int = 3,
        user_agent: str = "sincor-obsent/1.0",
    ) -> None:
        self.timeout_s = timeout_s
        self.max_attempts = max(1, max_attempts)
        self.user_agent = user_agent

    def _headers(self, payload: Mapping[str, Any], secret: str, event: str) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
            "X-Sincor-Signature": f"sha256={sign_payload(payload, secret)}",
            "X-Sincor-Event": event,
            "X-Sincor-Timestamp": str(_now_ms()),
        }

    def dispatch(
        self,
        url: str,
        payload: Mapping[str, Any],
        secret: str,
        *,
        event: str = "sincor.mesh.event",
        adapter: str = "generic_webhook",
        dry_run: bool = False,
        extra_headers: Optional[Mapping[str, str]] = None,
    ) -> DeliveryResult:
        """Deliver ``payload`` to ``url``.

        ``dry_run=True`` builds the exact request (headers + canonical
        body) without touching the network — used by tests and by the
        onboarding "test fire" step before an adapter is enabled.
        """
        payload = dict(payload)
        try:
            body = canonical_json(payload).encode("utf-8")
        except (TypeError, ValueError) as exc:
            # Caller bug (unserializable value), but the alerting path stays
            # fail-closed rather than raising mid-alert.
            return DeliveryResult(
                ok=False, adapter=adapter, url=url, status_code=None,
                attempts=0, error=f"payload not JSON-serializable: {exc}",
                payload_sha256="", dry_run=dry_run,
            )
        digest = hashlib.sha256(body).hexdigest()
        headers = self._headers(payload, secret, event)
        if extra_headers:
            headers.update(dict(extra_headers))

        if dry_run:
            return DeliveryResult(
                ok=True, adapter=adapter, url=url, status_code=None,
                attempts=0, error=None, payload_sha256=digest, dry_run=True,
            )
        if not url or not str(url).startswith(("http://", "https://")):
            return DeliveryResult(
                ok=False, adapter=adapter, url=url, status_code=None,
                attempts=0, error="refused: url must be http(s)", 
                payload_sha256=digest, dry_run=False,
            )

        attempts = 0
        last_error: Optional[str] = None
        status_code: Optional[int] = None
        while attempts < self.max_attempts:
            attempts += 1
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    status_code = int(getattr(resp, "status", 200) or 200)
                if 200 <= status_code < 300:
                    return DeliveryResult(
                        ok=True, adapter=adapter, url=url, status_code=status_code,
                        attempts=attempts, error=None, payload_sha256=digest, dry_run=False,
                    )
                last_error = f"unexpected status {status_code}"
            except urllib.error.HTTPError as exc:
                status_code = int(exc.code)
                last_error = f"http {exc.code}"
                if 400 <= exc.code < 500 and exc.code != 429:
                    break  # client error: retrying will not help
            except Exception as exc:  # network / DNS / TLS / timeout
                last_error = f"{type(exc).__name__}: {exc}"
            if attempts < self.max_attempts:
                time.sleep(min(2.0, 0.25 * (2 ** (attempts - 1))))

        return DeliveryResult(
            ok=False, adapter=adapter, url=url, status_code=status_code,
            attempts=attempts, error=last_error, payload_sha256=digest, dry_run=False,
        )


# ---------------------------------------------------------------------------
# Adapter configuration
# ---------------------------------------------------------------------------

ADAPTER_GENERIC_WEBHOOK = "generic_webhook"
ADAPTER_SENTRY_STYLE = "sentry_style"
ADAPTER_HEALTHCHECKS_STYLE = "healthchecks_style"
ADAPTER_KINDS = (ADAPTER_GENERIC_WEBHOOK, ADAPTER_SENTRY_STYLE, ADAPTER_HEALTHCHECKS_STYLE)

#: Honest capability label per adapter kind. These strings are the
#: customer-facing description of what each adapter REALLY does.
ADAPTER_CAPABILITIES: Dict[str, str] = {
    ADAPTER_GENERIC_WEBHOOK: (
        "Sends signed JSON event payloads to any customer-owned HTTPS "
        "endpoint. Fully working dispatcher; the customer supplies the URL."
    ),
    ADAPTER_SENTRY_STYLE: (
        "Builds Sentry-compatible event payloads and POSTs them to a "
        "customer-configured endpoint. Payload schema follows Sentry's "
        "event format, but this is NOT a verified Sentry integration and "
        "no Sentry account is linked — the customer supplies their own "
        "endpoint (e.g. a Sentry DSN ingest URL) during onboarding."
    ),
    ADAPTER_HEALTHCHECKS_STYLE: (
        "Emits dead-man's-switch style ping requests to a customer-"
        "configured monitor URL, compatible with healthchecks.io-style "
        "monitors. This is NOT a verified healthchecks.io integration "
        "and no account is linked — the customer supplies their own ping "
        "URL during onboarding."
    ),
}


@dataclass
class AdapterConfig:
    """Configuration for one outbound alert adapter.

    ``enabled`` defaults to ``False``: nothing leaves the building until
    the customer configures an endpoint during onboarding and test-fires
    it. ``secret`` is write-only in spirit — it is never logged and never
    rendered into runbooks or inventories.
    """

    name: str
    kind: str = ADAPTER_GENERIC_WEBHOOK
    url: str = ""
    secret: str = field(default="", repr=False)
    enabled: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ADAPTER_KINDS:
            raise ValueError(f"unknown adapter kind {self.kind!r}; expected one of {ADAPTER_KINDS}")

    @property
    def capability(self) -> str:
        """Honest customer-facing description of what this adapter does."""
        return ADAPTER_CAPABILITIES[self.kind]

    @property
    def status_label(self) -> str:
        if not self.enabled:
            return "disabled (default — configure an endpoint during onboarding)"
        if not self.url:
            return "enabled but missing endpoint — no alerts will be sent"
        return "enabled — customer-configured endpoint"


def adapter_inventory(configs: Sequence[AdapterConfig]) -> List[Dict[str, Any]]:
    """Honest inventory of configured adapters.

    Every entry carries ``verified: False``. End-to-end verification is
    not a runtime flag this module can set for itself — it is recorded
    by completing the "test-fire each enabled adapter" step in the
    onboarding checklist of the generated support runbook. This module
    therefore never reports an adapter as connected on its own.
    """
    inventory = []
    for cfg in configs:
        inventory.append({
            "name": cfg.name,
            "kind": cfg.kind,
            "capability": cfg.capability,
            "enabled": cfg.enabled,
            "url_configured": bool(cfg.url),
            "verified": False,
            "status": cfg.status_label,
            "notes": cfg.notes,
        })
    return inventory


# ---------------------------------------------------------------------------
# Adapter payload builders
# ---------------------------------------------------------------------------


def build_sentry_style_payload(alert: Mapping[str, Any], fleet_name: str) -> Dict[str, Any]:
    """Build a Sentry-compatible event payload for ``alert``.

    Follows Sentry's event schema (``event_id``, ``timestamp``,
    ``level``, ``message``, ``culprit``, ``tags``, ``extra``) so a
    customer can point this adapter at their own Sentry ingest endpoint.
    This function only *builds the payload* — delivery happens through
    :class:`SignedWebhookDispatcher` to the customer-configured URL.
    """
    severity = str(alert.get("severity", "warning") or "warning").lower()
    level = {"critical": "fatal", "error": "error"}.get(severity, "warning")
    return {
        "event_id": uuid.uuid4().hex,
        "timestamp": _iso_ms(int(alert.get("occurred_at_ms", _now_ms()))),
        "level": level,
        "logger": "sincor.enterprise_mesh",
        "platform": "python",
        "message": str(alert.get("summary", "SINCOR fleet alert")),
        "culprit": str(alert.get("agent_id") or fleet_name or "fleet"),
        "tags": {
            "fleet": fleet_name,
            "agent_id": str(alert.get("agent_id") or ""),
            "severity": severity,
            "source": "sincor-obs-ent",
        },
        "extra": {
            "reasons": list(alert.get("reasons", []) or []),
            "fleet_status": alert.get("fleet_status", ""),
        },
    }


def build_healthchecks_style_query(status: str, message: str = "") -> Dict[str, str]:
    """Build query params for a healthchecks.io-style ping.

    Returns ``{"status": "up"|"down"|"start", "msg": ...}`` to append to
    the customer's own monitor URL via GET. ``status`` is normalised:
    anything that is not an explicit healthy signal becomes ``"down"``.
    """
    normalised = "up" if str(status).lower() in ("healthy", "up", "ok", "success") else "down"
    query = {"status": normalised}
    if message:
        query["msg"] = message[:500]
    return query


def _ping_via_get(url: str, query: Mapping[str, str], timeout_s: float) -> DeliveryResult:
    """Best-effort GET ping used by the healthchecks-style adapter."""
    import urllib.parse

    digest = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
    if not url or not str(url).startswith(("http://", "https://")):
        return DeliveryResult(ok=False, adapter=ADAPTER_HEALTHCHECKS_STYLE, url=url,
                              status_code=None, attempts=0,
                              error="refused: url must be http(s)",
                              payload_sha256=digest, dry_run=False)
    full_url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    try:
        req = urllib.request.Request(full_url, method="GET",
                                     headers={"User-Agent": "sincor-obsent/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            code = int(getattr(resp, "status", 200) or 200)
        return DeliveryResult(ok=code < 400, adapter=ADAPTER_HEALTHCHECKS_STYLE,
                              url=url, status_code=code, attempts=1,
                              error=None if code < 400 else f"unexpected status {code}",
                              payload_sha256=digest, dry_run=False)
    except Exception as exc:
        return DeliveryResult(ok=False, adapter=ADAPTER_HEALTHCHECKS_STYLE, url=url,
                              status_code=None, attempts=1,
                              error=f"{type(exc).__name__}: {exc}",
                              payload_sha256=digest, dry_run=False)


def send_alert_via_adapters(
    alert: Mapping[str, Any],
    adapter_configs: Sequence[AdapterConfig],
    *,
    dispatcher: Optional[SignedWebhookDispatcher] = None,
    fleet_name: str = "",
    dry_run: bool = True,
    timeout_s: float = 5.0,
) -> List[DeliveryResult]:
    """Route ``alert`` through every enabled adapter with a configured URL.

    ``dry_run=True`` (the default) builds each request without touching
    the network — the safe default for tests and for the onboarding test
    fire. Pass ``dry_run=False`` only when deliberately delivering.
    Disabled adapters and adapters without a URL are skipped, with the
    skip recorded as a failed-closed :class:`DeliveryResult`.
    """
    dispatcher = dispatcher or SignedWebhookDispatcher(timeout_s=timeout_s)
    results: List[DeliveryResult] = []
    for cfg in adapter_configs:
        if not cfg.enabled:
            results.append(DeliveryResult(
                ok=False, adapter=cfg.kind, url=cfg.url, status_code=None,
                attempts=0, error=f"adapter {cfg.name!r} is disabled (default)",
                payload_sha256="", dry_run=dry_run,
            ))
            continue
        if not cfg.url:
            results.append(DeliveryResult(
                ok=False, adapter=cfg.kind, url="", status_code=None,
                attempts=0, error=f"adapter {cfg.name!r} enabled but no endpoint configured",
                payload_sha256="", dry_run=dry_run,
            ))
            continue
        if cfg.kind == ADAPTER_HEALTHCHECKS_STYLE:
            if dry_run:
                query = build_healthchecks_style_query(
                    str(alert.get("fleet_status", "down")),
                    str(alert.get("summary", "")),
                )
                digest = hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()
                results.append(DeliveryResult(
                    ok=True, adapter=cfg.kind, url=cfg.url, status_code=None,
                    attempts=0, error=None, payload_sha256=digest, dry_run=True,
                ))
            else:
                results.append(_ping_via_get(
                    cfg.url,
                    build_healthchecks_style_query(
                        str(alert.get("fleet_status", "down")),
                        str(alert.get("summary", "")),
                    ),
                    timeout_s,
                ))
            continue
        if cfg.kind == ADAPTER_SENTRY_STYLE:
            payload = build_sentry_style_payload(alert, fleet_name)
        else:
            payload = {
                "event": "sincor.mesh.alert",
                "fleet": fleet_name,
                "alert": dict(alert),
                "emitted_at_ms": _now_ms(),
            }
        results.append(dispatcher.dispatch(
            cfg.url, payload, cfg.secret,
            event="sincor.mesh.alert", adapter=cfg.kind, dry_run=dry_run,
        ))
    return results

# ---------------------------------------------------------------------------
# SLA framework — machine-checkable service commitments
# ---------------------------------------------------------------------------

METRIC_UPTIME_PCT = "uptime_pct"
METRIC_ALERT_LATENCY_S = "alert_latency_s"
METRIC_REPORT_CADENCE = "report_cadence"


@dataclass(frozen=True)
class SLADefinition:
    """One machine-checkable service commitment.

    ``metric`` is one of ``uptime_pct`` (percent of window time the fleet
    reported healthy), ``alert_latency_s`` (p95 seconds from incident
    occurrence to alert emission), or ``report_cadence`` (a status report
    was emitted inside the window). ``higher_is_better`` decides which
    side of ``target`` counts as meeting the commitment.
    """

    name: str
    metric: str
    target: float
    window_hours: float = 24.0
    description: str = ""
    higher_is_better: bool = True


DEFAULT_SLA_SET: Tuple[SLADefinition, ...] = (
    SLADefinition(
        name="Fleet uptime",
        metric=METRIC_UPTIME_PCT,
        target=99.5,
        window_hours=24.0,
        description=(
            "Share of the trailing 24h window during which the fleet "
            "reported healthy. Unknown / stale-heartbeat time counts as "
            "down (conservative: unobserved time is never assumed healthy)."
        ),
        higher_is_better=True,
    ),
    SLADefinition(
        name="Alert latency (p95)",
        metric=METRIC_ALERT_LATENCY_S,
        target=60.0,
        window_hours=24.0,
        description=(
            "p95 seconds from incident occurrence to alert emission over "
            "the trailing 24h. Incidents with no recorded alert timestamp "
            "count as missed alerts (infinite latency)."
        ),
        higher_is_better=False,
    ),
    SLADefinition(
        name="Daily status report",
        metric=METRIC_REPORT_CADENCE,
        target=1.0,
        window_hours=24.0,
        description=(
            "At least one fleet status report emitted inside the trailing "
            "24h window."
        ),
        higher_is_better=True,
    ),
)


@dataclass
class SLAEvaluation:
    """Result of evaluating one SLA over real history."""

    sla_name: str
    metric: str
    target: float
    measured: Optional[float]
    met: bool
    status: str  # "met" | "breached" | "insufficient_data"
    margin: Optional[float]  # measured - target in the met direction's sign
    window_hours: float
    evaluated_at_ms: int
    note: str
    evidence: Dict[str, Any]


def fleet_health_history(views: Sequence[FleetView]) -> List[Dict[str, Any]]:
    """Convert periodic :class:`FleetView` snapshots into SLA-ready history.

    The uptime evaluator expects fleet-level snapshots (one status per
    timestamp). Snapshot the fleet view on your report cadence and feed
    the result straight into ``evaluate_sla`` — do not pass raw
    per-agent vitals here, their interleaved timestamps would corrupt
    the time-weighting.
    """
    return [
        {
            "agent_id": view.fleet_name,
            "status": view.health.status,
            "checked_at_ms": view.generated_at_ms,
        }
        for view in views
    ]


def _evaluate_uptime(
    sla: SLADefinition, vitals_history: Sequence[Any], now_ms: int
) -> SLAEvaluation:
    """Time-weighted share of the window with healthy status.

    Expects fleet-level snapshots (one status per timestamp) — build
    them with :func:`fleet_health_history`. Each snapshot covers [its
    timestamp, next snapshot's timestamp), capped to the window; the
    latest snapshot at or before the window start carries the initial
    state. Unknown / stale statuses count as down — the evaluator never
    assumes health it cannot see.
    """
    window_ms = int(sla.window_hours * 3600_000)
    window_start = now_ms - window_ms

    samples: List[Tuple[int, str]] = []
    for record in vitals_history:
        ts = _rec_ts(record, "checked_at_ms", "timestamp_ms", "heartbeat_ms")
        if ts is None:
            continue
        status = str(_rec_field(record, "status", STATUS_UNKNOWN) or STATUS_UNKNOWN)
        samples.append((ts, status))
    samples.sort(key=lambda s: s[0])

    if not samples:
        return SLAEvaluation(
            sla_name=sla.name, metric=sla.metric, target=sla.target,
            measured=None, met=False, status="insufficient_data", margin=None,
            window_hours=sla.window_hours, evaluated_at_ms=now_ms,
            note="no vitals history in scope — commitment cannot be assessed",
            evidence={"samples": 0},
        )

    in_window = [s for s in samples if s[0] >= window_start]
    prior = [s for s in samples if s[0] < window_start]
    if not in_window and not prior:
        # All samples are in the future relative to now (clock skew);
        # assess nothing rather than invent coverage.
        return SLAEvaluation(
            sla_name=sla.name, metric=sla.metric, target=sla.target,
            measured=None, met=False, status="insufficient_data", margin=None,
            window_hours=sla.window_hours, evaluated_at_ms=now_ms,
            note="vitals history is entirely outside the evaluation window",
            evidence={"samples": len(samples)},
        )

    timeline: List[Tuple[int, str]] = []
    if prior:
        timeline.append((window_start, prior[-1][1]))
    for ts, status in in_window:
        timeline.append((max(ts, window_start), status))

    healthy_ms = 0
    for i, (start, status) in enumerate(timeline):
        end = timeline[i + 1][0] if i + 1 < len(timeline) else now_ms
        end = min(end, now_ms)
        if end > start and status == STATUS_HEALTHY:
            healthy_ms += end - start

    uptime_pct = round(100.0 * healthy_ms / window_ms, 3)
    met = uptime_pct >= sla.target
    return SLAEvaluation(
        sla_name=sla.name, metric=sla.metric, target=sla.target,
        measured=uptime_pct, met=met, status="met" if met else "breached",
        margin=round(uptime_pct - sla.target, 3),
        window_hours=sla.window_hours, evaluated_at_ms=now_ms,
        note=(
            f"healthy {healthy_ms/3600000:.2f}h of {sla.window_hours:.0f}h window; "
            "unknown/stale time counted as down"
        ),
        evidence={"samples": len(samples), "healthy_ms": healthy_ms, "window_ms": window_ms},
    )


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int((pct / 100.0) * len(ordered))))
    # Nearest-rank style on a 0-based index; documented, deterministic.
    return ordered[idx]


def _evaluate_alert_latency(
    sla: SLADefinition, audit_events: Sequence[Any], now_ms: int
) -> SLAEvaluation:
    """p95 seconds from incident occurrence to alert emission.

    Incidents inside the window with no ``alerted_at_ms`` are missed
    alerts and count as infinite latency — a missed alert breaches the
    commitment rather than being quietly dropped from the sample.
    """
    window_ms = int(sla.window_hours * 3600_000)
    window_start = now_ms - window_ms

    latencies: List[float] = []
    missed = 0
    considered = 0
    for event in audit_events:
        evt = _as_dict(event)
        if str(_rec_field(evt, "event_type", "")).lower() not in ("incident", "alert"):
            continue
        occurred = _rec_ts(evt, "occurred_at_ms")
        if occurred is None or occurred < window_start or occurred > now_ms:
            continue
        considered += 1
        alerted = _rec_ts(evt, "alerted_at_ms")
        if alerted is None:
            missed += 1
            latencies.append(float("inf"))
        else:
            latencies.append(max(0.0, (alerted - occurred) / 1000.0))

    if considered == 0:
        return SLAEvaluation(
            sla_name=sla.name, metric=sla.metric, target=sla.target,
            measured=None, met=True, status="met", margin=None,
            window_hours=sla.window_hours, evaluated_at_ms=now_ms,
            note="no incidents in the window — latency commitment not exercised",
            evidence={"incidents": 0},
        )

    finite = [v for v in latencies if v != float("inf")]
    p95 = _percentile(latencies, 95)
    measured: Optional[float] = None if p95 == float("inf") else round(p95, 2)
    met = p95 <= sla.target
    return SLAEvaluation(
        sla_name=sla.name, metric=sla.metric, target=sla.target,
        measured=measured, met=met, status="met" if met else "breached",
        margin=None if measured is None else round(sla.target - measured, 2),
        window_hours=sla.window_hours, evaluated_at_ms=now_ms,
        note=(
            f"p95 over {considered} incident(s); {missed} missed alert(s)"
            + (" — missed alerts count as infinite latency" if missed else "")
        ),
        evidence={"incidents": considered, "missed_alerts": missed,
                  "finite_latencies_s": sorted(finite)},
    )


def _evaluate_report_cadence(
    sla: SLADefinition, report_timestamps_ms: Sequence[int], now_ms: int
) -> SLAEvaluation:
    window_ms = int(sla.window_hours * 3600_000)
    window_start = now_ms - window_ms
    in_window = sorted(
        int(ts) for ts in report_timestamps_ms
        if window_start <= int(ts) <= now_ms
    )
    met = len(in_window) >= 1
    last = in_window[-1] if in_window else None
    return SLAEvaluation(
        sla_name=sla.name, metric=sla.metric, target=sla.target,
        measured=1.0 if met else 0.0, met=met,
        status="met" if met else "breached",
        margin=1.0 if met else -1.0,
        window_hours=sla.window_hours, evaluated_at_ms=now_ms,
        note=(
            f"last report {_iso_ms(last)} ({(now_ms - last)//3600000}h ago)"
            if last is not None
            else "no status report emitted inside the window"
        ),
        evidence={"reports_in_window": len(in_window), "last_report_ms": last},
    )


def evaluate_sla(
    sla: SLADefinition,
    *,
    vitals_history: Sequence[Any] = (),
    audit_events: Sequence[Any] = (),
    report_timestamps_ms: Sequence[int] = (),
    now_ms: Optional[int] = None,
) -> SLAEvaluation:
    """Evaluate one SLA definition against real history.

    Dispatches on ``sla.metric``; unknown metrics return
    ``insufficient_data`` rather than guessing.
    """
    now = now_ms if now_ms is not None else _now_ms()
    if sla.metric == METRIC_UPTIME_PCT:
        return _evaluate_uptime(sla, vitals_history, now)
    if sla.metric == METRIC_ALERT_LATENCY_S:
        return _evaluate_alert_latency(sla, audit_events, now)
    if sla.metric == METRIC_REPORT_CADENCE:
        return _evaluate_report_cadence(sla, report_timestamps_ms, now)
    return SLAEvaluation(
        sla_name=sla.name, metric=sla.metric, target=sla.target,
        measured=None, met=False, status="insufficient_data", margin=None,
        window_hours=sla.window_hours, evaluated_at_ms=now,
        note=f"unknown metric {sla.metric!r} — no evaluator implemented",
        evidence={},
    )


def evaluate_sla_set(
    sla_set: Sequence[SLADefinition] = DEFAULT_SLA_SET,
    *,
    vitals_history: Sequence[Any] = (),
    audit_events: Sequence[Any] = (),
    report_timestamps_ms: Sequence[int] = (),
    now_ms: Optional[int] = None,
) -> List[SLAEvaluation]:
    """Evaluate a whole SLA set against the same history window."""
    return [
        evaluate_sla(
            sla,
            vitals_history=vitals_history,
            audit_events=audit_events,
            report_timestamps_ms=report_timestamps_ms,
            now_ms=now_ms,
        )
        for sla in sla_set
    ]


@dataclass
class BreachReport:
    """Fleet SLA compliance report. Customer-facing summary uses
    positive framing: commitments met first, then items needing
    attention — never alarmist language."""

    fleet_name: str
    generated_at_ms: int
    evaluations: List[SLAEvaluation]
    met_count: int
    breached: List[SLAEvaluation]
    insufficient_data: List[SLAEvaluation]
    summary: str


def build_breach_report(
    fleet_name: str,
    evaluations: Sequence[SLAEvaluation],
    *,
    now_ms: Optional[int] = None,
) -> BreachReport:
    """Build the compliance report from evaluated SLAs."""
    now = now_ms if now_ms is not None else _now_ms()
    evals = list(evaluations)
    met = [e for e in evals if e.status == "met"]
    breached = [e for e in evals if e.status == "breached"]
    unknown = [e for e in evals if e.status == "insufficient_data"]

    if not evals:
        summary = "No service commitments were in scope for this report."
    elif not breached and not unknown:
        summary = (
            f"All {len(met)} service commitments met in the trailing window. "
            "Fleet operating within agreed targets."
        )
    elif not breached:
        summary = (
            f"{len(met)} of {len(evals)} service commitments met; "
            f"{len(unknown)} awaiting sufficient data for assessment."
        )
    else:
        summary = (
            f"{len(met)} of {len(evals)} service commitments met; "
            f"{len(breached)} item(s) need attention: "
            + ", ".join(e.sla_name for e in breached)
            + "."
        )

    return BreachReport(
        fleet_name=fleet_name,
        generated_at_ms=now,
        evaluations=evals,
        met_count=len(met),
        breached=breached,
        insufficient_data=unknown,
        summary=summary,
    )


def breach_report_to_dict(report: BreachReport) -> Dict[str, Any]:
    """Serialise a :class:`BreachReport` to plain JSON-compatible dicts."""

    def _ev(e: SLAEvaluation) -> Dict[str, Any]:
        return {
            "sla_name": e.sla_name,
            "metric": e.metric,
            "target": e.target,
            "measured": e.measured,
            "met": e.met,
            "status": e.status,
            "margin": e.margin,
            "window_hours": e.window_hours,
            "note": e.note,
            "evidence": e.evidence,
        }

    return {
        "fleet_name": report.fleet_name,
        "generated_at_ms": report.generated_at_ms,
        "generated_at": _iso_ms(report.generated_at_ms),
        "summary": report.summary,
        "met_count": report.met_count,
        "breached_count": len(report.breached),
        "evaluations": [_ev(e) for e in report.evaluations],
    }

# ---------------------------------------------------------------------------
# Dedicated-support packaging — runbook generator
# ---------------------------------------------------------------------------


@dataclass
class SupportContact:
    """One escalation contact. All fields are templates until onboarding
    fills them in — placeholders are explicit so nothing looks staffed
    that is not."""

    role: str  # e.g. "Primary on-call", "Engineering lead", "Customer sponsor"
    name: str = "TBD — assign during onboarding"
    channel: str = "TBD — assign during onboarding"


@dataclass
class SupportRunbook:
    """Generated engagement artifact for an OBS-ENT customer."""

    fleet_name: str
    generated_at_ms: int
    markdown: str
    sections: Dict[str, Any]


_DEFAULT_CONTACTS: Tuple[SupportContact, ...] = (
    SupportContact(role="Primary on-call (SINCOR)"),
    SupportContact(role="Escalation lead (SINCOR)"),
    SupportContact(role="Customer sponsor"),
    SupportContact(role="Customer technical contact"),
)


def _sla_table_rows(sla_set: Sequence[SLADefinition]) -> List[str]:
    rows = []
    for sla in sla_set:
        direction = "at least" if sla.higher_is_better else "at most"
        rows.append(
            f"| {sla.name} | `{sla.metric}` | {direction} **{sla.target}** "
            f"| trailing {sla.window_hours:.0f}h |"
        )
    return rows


def generate_runbook(
    fleet_name: str,
    *,
    contacts: Sequence[SupportContact] = _DEFAULT_CONTACTS,
    sla_set: Sequence[SLADefinition] = DEFAULT_SLA_SET,
    adapter_configs: Sequence[AdapterConfig] = (),
    heartbeat_ttl_ms: int = DEFAULT_HEARTBEAT_TTL_MS,
    now_ms: Optional[int] = None,
) -> SupportRunbook:
    """Generate the enterprise support runbook for a fleet.

    Returns honest engagement artifacts: an escalation matrix template,
    an onboarding checklist, the SLA commitment table, the adapter
    inventory (with real capability labels), and incident response
    steps. Contact placeholders stay visibly unassigned until onboarding
    fills them in.
    """
    now = now_ms if now_ms is not None else _now_ms()
    inventory = adapter_inventory(adapter_configs)

    lines: List[str] = []
    lines.append(f"# {fleet_name} — Enterprise Support Runbook")
    lines.append("")
    lines.append(f"_Generated {_iso_ms(now)} · OBS-ENT Enterprise Mesh_")
    lines.append("")
    lines.append(
        "This runbook accompanies your Enterprise Mesh engagement. It defines "
        "how your agent fleet is observed, how alerts reach your team, what "
        "service commitments apply, and exactly who responds when something "
        "needs attention."
    )
    lines.append("")

    # -- Escalation matrix -------------------------------------------------
    lines.append("## Escalation matrix")
    lines.append("")
    lines.append("| Level | Trigger | Owner | Response target |")
    lines.append("|---|---|---|---|")
    lines.append(
        "| L1 | Fleet status `degraded`, or a single agent `unknown` | "
        f"{contacts[0].name if len(contacts) > 0 else 'TBD'} "
        f"({contacts[0].role if len(contacts) > 0 else 'primary on-call'}) | "
        "Acknowledge within 15 minutes |"
    )
    lines.append(
        "| L2 | Fleet status `critical`, or an SLA breach report | "
        f"{contacts[1].name if len(contacts) > 1 else 'TBD'} "
        f"({contacts[1].role if len(contacts) > 1 else 'escalation lead'}) | "
        "Engage within 30 minutes |"
    )
    lines.append(
        "| L3 | L2 unresolved after 2 hours, or customer-impacting outage | "
        f"{contacts[2].name if len(contacts) > 2 else 'TBD'} "
        f"({contacts[2].role if len(contacts) > 2 else 'customer sponsor'}) "
        "+ SINCOR leadership | Continuous engagement until resolved |"
    )
    lines.append("")
    lines.append("### Contacts")
    lines.append("")
    for contact in contacts:
        lines.append(f"- **{contact.role}** — {contact.name} · {contact.channel}")
    lines.append("")

    # -- Onboarding checklist ----------------------------------------------
    lines.append("## Onboarding checklist")
    lines.append("")
    lines.append("Complete these with your SINCOR engineer before go-live:")
    lines.append("")
    checklist = [
        "Confirm the fleet agent list under observation",
        "Configure outbound alert adapter endpoints (see Adapter inventory below)",
        "Test-fire each enabled adapter and confirm receipt",
        "Confirm the heartbeat freshness window "
        f"(`{heartbeat_ttl_ms // 1000}s`) suits your agents' cadence",
        "Review and sign off the SLA commitments table",
        "Assign named owners to every escalation level above",
        "Schedule the first daily status report delivery",
        "Confirm report recipients and preferred channel",
    ]
    for item in checklist:
        lines.append(f"- [ ] {item}")
    lines.append("")

    # -- SLA commitments ----------------------------------------------------
    lines.append("## Service commitments (SLA)")
    lines.append("")
    lines.append(
        "Each commitment is evaluated automatically from real fleet history. "
        "Unknown or stale observation time counts as down — the mesh never "
        "assumes health it cannot see."
    )
    lines.append("")
    lines.append("| Commitment | Metric | Target | Window |")
    lines.append("|---|---|---|---|")
    lines.extend(_sla_table_rows(sla_set))
    lines.append("")
    for sla in sla_set:
        if sla.description:
            lines.append(f"- **{sla.name}**: {sla.description}")
    lines.append("")

    # -- Adapter inventory ---------------------------------------------------
    lines.append("## Adapter inventory")
    lines.append("")
    lines.append(
        "Outbound alert adapters. All adapters ship **disabled by default**; "
        "configure an endpoint and test-fire it during onboarding."
    )
    lines.append("")
    if not inventory:
        lines.append("_No adapters configured yet — add them during onboarding._")
    else:
        for entry in inventory:
            lines.append(f"### {entry['name']} (`{entry['kind']}`)")
            lines.append("")
            lines.append(f"- Status: **{entry['status']}**")
            lines.append(f"- What it does: {entry['capability']}")
            lines.append(f"- Endpoint configured: {'yes' if entry['url_configured'] else 'not yet'}")
            lines.append(f"- Verified end-to-end: {'yes' if entry['verified'] else 'not yet'}")
            if entry["notes"]:
                lines.append(f"- Notes: {entry['notes']}")
            lines.append("")
    lines.append("")

    # -- Incident response ----------------------------------------------------
    lines.append("## Incident response")
    lines.append("")
    steps = [
        "The mesh detects the condition: fleet rollup changes status, an SLA "
        "breaches, or a drift signal fires. Every detection carries reasons "
        "naming the affected agents.",
        "Alerts route through your enabled adapters to the L1 owner. "
        "Each alert is signed so your receivers can authenticate it.",
        "L1 acknowledges within 15 minutes and triages using the fleet view: "
        "per-agent status, open incidents, and drift signals in one place.",
        "If the fleet reaches `critical` or an SLA breaches, L2 engages "
        "within 30 minutes with the breach report attached.",
        "After resolution, the incident is closed in the audit trail with a "
        "summary; the next status report includes what changed.",
    ]
    for i, step in enumerate(steps, 1):
        lines.append(f"{i}. {step}")
    lines.append("")
    lines.append("---")
    lines.append(
        f"_OBS-ENT Enterprise Mesh · runbook generated {_iso_ms(now)}_"
    )
    lines.append("")

    markdown = "\n".join(lines)
    return SupportRunbook(
        fleet_name=fleet_name,
        generated_at_ms=now,
        markdown=markdown,
        sections={
            "escalation_levels": 3,
            "contacts": [asdict(c) for c in contacts],
            "onboarding_items": len(checklist),
            "sla_commitments": [
                {"name": s.name, "metric": s.metric, "target": s.target,
                 "window_hours": s.window_hours} for s in sla_set
            ],
            "adapters": inventory,
            "heartbeat_ttl_ms": heartbeat_ttl_ms,
        },
    )


def write_runbook(path: str, runbook: SupportRunbook) -> str:
    """Write a generated runbook's markdown to ``path``. Returns ``path``."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(runbook.markdown)
    return path


__all__ = [
    # status model
    "STATUS_HEALTHY", "STATUS_DEGRADED", "STATUS_CRITICAL", "STATUS_UNKNOWN",
    "ALL_STATUSES", "DEFAULT_HEARTBEAT_TTL_MS",
    # sibling access
    "vitals_module", "audit_trail_module", "drift_quality_module",
    # fleet view
    "AgentMeshEntry", "FleetHealth", "FleetView",
    "build_fleet_view", "rollup_fleet_health", "fleet_view_to_dict",
    # adapters
    "canonical_json", "sign_payload", "verify_signature",
    "DeliveryResult", "SignedWebhookDispatcher",
    "AdapterConfig", "adapter_inventory",
    "ADAPTER_GENERIC_WEBHOOK", "ADAPTER_SENTRY_STYLE", "ADAPTER_HEALTHCHECKS_STYLE",
    "ADAPTER_KINDS", "ADAPTER_CAPABILITIES",
    "build_sentry_style_payload", "build_healthchecks_style_query",
    "send_alert_via_adapters",
    # SLA
    "METRIC_UPTIME_PCT", "METRIC_ALERT_LATENCY_S", "METRIC_REPORT_CADENCE",
    "SLADefinition", "DEFAULT_SLA_SET", "SLAEvaluation",
    "evaluate_sla", "evaluate_sla_set", "fleet_health_history",
    "BreachReport", "build_breach_report", "breach_report_to_dict",
    # runbook
    "SupportContact", "SupportRunbook",
    "generate_runbook", "write_runbook",
]
