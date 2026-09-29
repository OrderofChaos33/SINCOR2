"""OBS-01 Agent Vitals — customer-facing read model + webhook alerting.

STAGE: BETA. Read-only: this module never writes to the task board, the
stake/pool ledgers, the money path, auth, or contracts. It only *reads*
existing platform state and evaluates threshold alerts.

Real data sources (verified in the codebase — nothing invented):

* ``sincor2.a2a_inbound.get_fabric`` — the A2A directory. Agent records carry
  ``last_heartbeat`` (epoch ms) and ``HEARTBEAT_TTL_S`` (60s) defines "live".
* ``sincor2.a2a_inbound`` fabric ``tasks`` — settled tasks carry
  ``assigned_to`` and ``payout_axm``; cost-per-task is the measured average
  payout per settled task, never an estimate.
* ``sincor2.token_budget_controller.get_controller`` — per-agent daily token
  ceilings, usage, kill state.
* ``sincor2.kya_registry.get_by_agent`` — KYA status (verified / revoked /
  expired) and the SLA ``uptime_30d`` moving average + ``latency_ms`` for
  KYA-linked agents. Agents without KYA get uptime ``None`` ("—" in the UI);
  we do not fabricate an uptime series from a single heartbeat timestamp.

Tool-call budgets: no per-agent tool-call ledger exists in the codebase yet,
so this module accepts an optional *provider* (any callable returning a
per-agent ``ToolCallBudget``). With no provider the field reports
``instrumented=False`` and tool-call alert rules never fire — an honest
"not instrumented" rather than a fictional number. OBS-ENT may supply a
real provider later.

Webhook alert payload schema (``sincor.vitals.alert/1``)::

    {
      "schema": "sincor.vitals.alert/1",
      "event_id": "evt_<uuid>",
      "rule_id": "heartbeat-stale",
      "agent_id": "E-auriga-01",
      "metric": "heartbeat_age_s",
      "operator": ">",
      "threshold": 300.0,
      "observed_value": 812.4,
      "observed_at": "2026-09-29T15:30:00+00:00",
      "fleet_size": 12
    }

Delivery is HMAC-SHA256 signed. Headers on every attempt::

    Content-Type: application/json
    X-Sincor-Timestamp: <unix seconds>
    X-Sincor-Signature: sha256=<hex hmac of "<timestamp>.<canonical json body>">

The canonical body is ``json.dumps(payload, sort_keys=True, separators=(",", ":"))``.

Tests must never send real webhooks: inject a stub ``transport`` into
``WebhookDispatcher`` (see tests/pytest/test_obs01_vitals.py).

Known beta gaps (honest, not hidden):

* Alert cooldown state is per-process. Under multi-worker gunicorn two
  workers can each fire the same rule once before the other sees the
  cooldown — duplicate deliveries are possible until a shared store lands.
* Webhook dispatch runs inside the dashboard read that triggered it. A
  failing webhook endpoint adds retry latency (up to ~13s worst case) to
  that page/API response. A background worker is the GA fix.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger("sincor.obs.vitals")

PAYLOAD_SCHEMA = "sincor.vitals.alert/1"

# ---------------------------------------------------------------------------
# Metrics and operators available to alert rules
# ---------------------------------------------------------------------------

METRIC_HEARTBEAT_AGE_S = "heartbeat_age_s"
METRIC_TOKEN_UTIL_PCT = "token_utilisation_pct"
METRIC_UPTIME_PCT = "uptime_pct"
METRIC_COST_PER_TASK_AXM = "cost_per_task_axm"
METRIC_TASKS_COMPLETED = "tasks_completed"
METRIC_TOOL_CALLS_USED = "tool_calls_used"

KNOWN_METRICS = frozenset({
    METRIC_HEARTBEAT_AGE_S,
    METRIC_TOKEN_UTIL_PCT,
    METRIC_UPTIME_PCT,
    METRIC_COST_PER_TASK_AXM,
    METRIC_TASKS_COMPLETED,
    METRIC_TOOL_CALLS_USED,
})

OPERATORS = {
    ">": lambda obs, thr: obs > thr,
    ">=": lambda obs, thr: obs >= thr,
    "<": lambda obs, thr: obs < thr,
    "<=": lambda obs, thr: obs <= thr,
}

# ---------------------------------------------------------------------------
# Read-model dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TokenBudget:
    daily_ceiling: int
    used_today: int
    remaining: int
    utilisation_pct: float
    allowed: bool
    killed: bool
    source_ok: bool = True  # False when the controller lookup failed (unknown, not "reached")


@dataclass
class ToolCallBudget:
    """Honest wrapper: ``instrumented`` is False until a real provider exists."""

    instrumented: bool = False
    used: Optional[int] = None
    limit: Optional[int] = None
    window: str = "daily"


@dataclass
class AgentVitals:
    agent_id: str
    name: str
    origin: str
    heartbeat_age_s: Optional[float]
    heartbeat_status: str  # live | stale | silent | unknown
    health: str  # live | stale | silent | budget_blocked | kya_suspended | unknown
    uptime_pct: Optional[float]  # from KYA SLA uptime_30d; None = not KYA-linked
    latency_ms: Optional[int]
    kya_status: Optional[str]
    tasks_completed: int
    total_earned_axm: float
    cost_per_task_axm: Optional[float]  # measured avg payout; None = no settled tasks
    token_budget: TokenBudget
    tool_calls: ToolCallBudget
    as_of: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Data-source accessors (thin, exception-safe wrappers)
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_heartbeat_ttl_s() -> int:
    """Real TTL from the A2A engine; falls back to the known constant."""
    try:
        from sincor2.a2a_inbound import HEARTBEAT_TTL_S

        return int(HEARTBEAT_TTL_S)
    except Exception:
        return 60


def read_fabric_agents() -> Dict[str, Dict[str, Any]]:
    """{agent_id: agent record} from the live A2A directory."""
    from sincor2.a2a_inbound import get_fabric

    fabric = get_fabric()
    with fabric.lock:
        return {aid: dict(a) for aid, a in fabric.agents.items()}


def read_settled_task_payouts() -> List[Dict[str, Any]]:
    """Settled tasks with their measured payouts (read-only)."""
    from sincor2.a2a_inbound import get_fabric

    fabric = get_fabric()
    out: List[Dict[str, Any]] = []
    with fabric.lock:
        for task in fabric.tasks.values():
            if task.get("state") != "settled":
                continue
            payout = task.get("payout_axm")
            if payout is None:
                payout = task.get("winning_bid_axm")
            if payout is None:
                payout = task.get("bounty_axm")
            try:
                payout_f = float(payout or 0)
            except (TypeError, ValueError):
                payout_f = 0.0
            out.append(
                {
                    "task_id": task.get("task_id"),
                    "assigned_to": task.get("assigned_to"),
                    "payout_axm": payout_f,
                    "settled_at": task.get("settled_at"),
                }
            )
    return out


def read_kya_record(agent_id: str) -> Optional[Dict[str, Any]]:
    """KYA record for an agent, or None when not KYA-linked / unavailable."""
    try:
        from sincor2 import kya_registry

        rec = kya_registry.get_by_agent(agent_id)
        return dict(rec) if rec else None
    except Exception as exc:
        logger.debug("[VITALS] kya lookup failed for %s: %s", agent_id, exc)
        return None


def read_token_budget(agent_id: str, controller: Any = None) -> TokenBudget:
    """Per-agent token budget snapshot. Never raises: unknown -> zeroed/denied-safe."""

    def _unknown() -> TokenBudget:
        return TokenBudget(
            daily_ceiling=0, used_today=0, remaining=0,
            utilisation_pct=0.0, allowed=False, killed=False,
            source_ok=False,  # honest "unavailable", never "ceiling reached"
        )

    try:
        if controller is None:
            from sincor2.token_budget_controller import get_controller

            controller = get_controller()
        status = controller.get_status(agent_id)
        return TokenBudget(
            daily_ceiling=int(status.get("daily_ceiling") or 0),
            used_today=int(status.get("tokens_used_today") or 0),
            remaining=int(status.get("tokens_remaining") or 0),
            utilisation_pct=float(status.get("utilisation_pct") or 0.0),
            allowed=bool(status.get("is_allowed")),
            killed=bool(status.get("killed")),
        )
    except Exception as exc:
        logger.debug("[VITALS] token budget lookup failed for %s: %s", agent_id, exc)
        return _unknown()


# ---------------------------------------------------------------------------
# Snapshot builder
# ---------------------------------------------------------------------------

KYA_SUSPENDED = {"revoked", "tombstoned"}


def derive_health(
    heartbeat_age_s: Optional[float],
    ttl_s: int,
    kya_status: Optional[str],
    token_killed: bool,
) -> Tuple[str, str]:
    """Return (health, heartbeat_status).

    Ordering is deliberate and fail-closed: a revoked KYA or a killed token
    budget is *never* reported as live.
    """
    if kya_status in KYA_SUSPENDED:
        return "kya_suspended", "unknown"
    if token_killed:
        return "budget_blocked", "unknown"
    if heartbeat_age_s is None:
        return "unknown", "unknown"
    if heartbeat_age_s <= ttl_s:
        return "live", "live"
    if heartbeat_age_s <= 2 * ttl_s:
        return "stale", "stale"
    return "silent", "silent"


def build_agent_snapshot(
    agent: Mapping[str, Any],
    *,
    settled: List[Dict[str, Any]],
    ttl_s: int,
    now_ms: int,
    controller: Any = None,
    tool_call_provider: Optional[Callable[[str], ToolCallBudget]] = None,
) -> AgentVitals:
    agent_id = str(agent.get("agent_id") or "")
    name = str(agent.get("name") or agent_id)
    origin = str(agent.get("origin") or "unknown")

    last_hb = agent.get("last_heartbeat")
    try:
        heartbeat_age_s = (now_ms - int(last_hb)) / 1000.0 if last_hb is not None else None
        if heartbeat_age_s is not None and heartbeat_age_s < 0:
            heartbeat_age_s = 0.0
    except (TypeError, ValueError):
        heartbeat_age_s = None

    kya_rec = read_kya_record(agent_id)
    kya_status = None
    uptime_pct = None
    latency_ms = None
    if kya_rec is not None:
        kya_status = kya_rec.get("status")
        sla = kya_rec.get("sla") or {}
        try:
            u = sla.get("uptime_30d")
            uptime_pct = round(float(u) * 100.0, 1) if u is not None else None
        except (TypeError, ValueError):
            uptime_pct = None
        try:
            latency_ms = int(sla["latency_ms"]) if sla.get("latency_ms") is not None else None
        except (TypeError, ValueError):
            latency_ms = None
    elif agent.get("kya_status"):
        kya_status = str(agent.get("kya_status"))

    budget = read_token_budget(agent_id, controller=controller)
    health, hb_status = derive_health(heartbeat_age_s, ttl_s, kya_status, budget.killed)

    payouts = [t["payout_axm"] for t in settled if t.get("assigned_to") == agent_id]
    tasks_completed = len(payouts)
    total_earned = round(sum(payouts), 6)
    cost_per_task = round(total_earned / tasks_completed, 6) if tasks_completed else None

    tool_calls = ToolCallBudget()
    if tool_call_provider is not None:
        try:
            tool_calls = tool_call_provider(agent_id) or ToolCallBudget()
        except Exception as exc:
            logger.warning("[VITALS] tool-call provider failed for %s: %s", agent_id, exc)

    return AgentVitals(
        agent_id=agent_id,
        name=name,
        origin=origin,
        heartbeat_age_s=round(heartbeat_age_s, 1) if heartbeat_age_s is not None else None,
        heartbeat_status=hb_status,
        health=health,
        uptime_pct=uptime_pct,
        latency_ms=latency_ms,
        kya_status=kya_status,
        tasks_completed=tasks_completed,
        total_earned_axm=total_earned,
        cost_per_task_axm=cost_per_task,
        token_budget=budget,
        tool_calls=tool_calls,
        as_of=_iso_now(),
    )


def build_fleet_snapshot(
    *,
    now_ms: Optional[int] = None,
    controller: Any = None,
    tool_call_provider: Optional[Callable[[str], ToolCallBudget]] = None,
) -> Dict[str, Any]:
    """Customer-facing read model for the whole fleet. Never raises."""
    ts = now_ms if now_ms is not None else _now_ms()
    ttl_s = get_heartbeat_ttl_s()
    try:
        agents = read_fabric_agents()
    except Exception as exc:
        logger.warning("[VITALS] fabric read failed: %s", exc)
        agents = {}
    try:
        settled = read_settled_task_payouts()
    except Exception as exc:
        logger.warning("[VITALS] task read failed: %s", exc)
        settled = []

    snapshots: List[AgentVitals] = []
    for agent_id in sorted(agents):
        try:
            snapshots.append(
                build_agent_snapshot(
                    agents[agent_id],
                    settled=settled,
                    ttl_s=ttl_s,
                    now_ms=ts,
                    controller=controller,
                    tool_call_provider=tool_call_provider,
                )
            )
        except Exception as exc:
            logger.warning("[VITALS] snapshot failed for %s: %s", agent_id, exc)

    health_counts: Dict[str, int] = {}
    for snap in snapshots:
        health_counts[snap.health] = health_counts.get(snap.health, 0) + 1

    return {
        "status": "ok",
        "beta": True,
        "as_of": _iso_now(),
        "heartbeat_ttl_s": ttl_s,
        "fleet_size": len(snapshots),
        "health_counts": health_counts,
        "agents": [s.to_dict() for s in snapshots],
        "notes": [
            "Uptime is shown only for KYA-verified agents (KYA SLA uptime_30d).",
            "Cost per task is the measured average AXM payout per settled task.",
            "Tool-call budgets are not instrumented yet.",
        ],
    }


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


@dataclass
class AlertRule:
    rule_id: str
    metric: str
    op: str
    threshold: float
    agent_id: str = "*"  # "*" = every agent in the fleet
    cooldown_s: int = 3600
    webhook_url: str = ""

    def validate(self) -> None:
        if self.metric not in KNOWN_METRICS:
            raise ValueError(f"unknown metric '{self.metric}'")
        if self.op not in OPERATORS:
            raise ValueError(f"unknown operator '{self.op}'")
        if not self.rule_id:
            raise ValueError("rule_id is required")


@dataclass
class AlertEvent:
    event_id: str
    rule_id: str
    agent_id: str
    metric: str
    operator: str
    threshold: float
    observed_value: float
    observed_at: str
    fleet_size: int


def metric_value(rule: AlertRule, snapshot: AgentVitals) -> Optional[float]:
    """Observed value for a rule against one snapshot; None = no data (no alert)."""
    if rule.metric == METRIC_HEARTBEAT_AGE_S:
        return snapshot.heartbeat_age_s
    if rule.metric == METRIC_TOKEN_UTIL_PCT:
        return snapshot.token_budget.utilisation_pct
    if rule.metric == METRIC_UPTIME_PCT:
        return snapshot.uptime_pct
    if rule.metric == METRIC_COST_PER_TASK_AXM:
        return snapshot.cost_per_task_axm
    if rule.metric == METRIC_TASKS_COMPLETED:
        return float(snapshot.tasks_completed)
    if rule.metric == METRIC_TOOL_CALLS_USED:
        if not snapshot.tool_calls.instrumented:
            return None  # honest: never fire on uninstrumented data
        return float(snapshot.tool_calls.used or 0)
    return None


def evaluate_rules(
    rules: List[AlertRule],
    snapshots: List[AgentVitals],
    *,
    now_s: Optional[float] = None,
    cooldown_state: Optional[Dict[str, float]] = None,
) -> Tuple[List[AlertEvent], Dict[str, float]]:
    """Evaluate rules -> events. Cooldowns dedupe; unknown metrics never fire.

    Returns (events, updated_cooldown_state). ``cooldown_state`` maps
    ``rule_id + "|" + agent_id`` -> last-fired unix seconds.
    """
    now = now_s if now_s is not None else time.time()
    state: Dict[str, float] = dict(cooldown_state or {})
    events: List[AlertEvent] = []

    for rule in rules:
        try:
            rule.validate()
        except ValueError as exc:
            logger.warning("[VITALS] skipping invalid rule %s: %s", rule.rule_id, exc)
            continue
        targets = snapshots if rule.agent_id == "*" else [s for s in snapshots if s.agent_id == rule.agent_id]
        for snap in targets:
            observed = metric_value(rule, snap)
            if observed is None:
                continue  # no data -> no alert, ever
            if not OPERATORS[rule.op](observed, rule.threshold):
                continue
            key = f"{rule.rule_id}|{snap.agent_id}"
            last = state.get(key)
            if last is not None and now - last < rule.cooldown_s:
                continue
            state[key] = now
            events.append(
                AlertEvent(
                    event_id="evt_" + uuid.uuid4().hex[:12],
                    rule_id=rule.rule_id,
                    agent_id=snap.agent_id,
                    metric=rule.metric,
                    operator=rule.op,
                    threshold=rule.threshold,
                    observed_value=observed,
                    observed_at=_iso_now(),
                    fleet_size=len(snapshots),
                )
            )
    return events, state


def load_alert_rules_from_env() -> List[AlertRule]:
    """Parse SINCOR_VITALS_ALERTS (JSON list) into validated AlertRules."""
    raw = (os.environ.get("SINCOR_VITALS_ALERTS") or "").strip()
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("[VITALS] SINCOR_VITALS_ALERTS is not valid JSON: %s", exc)
        return []
    rules: List[AlertRule] = []
    for item in items if isinstance(items, list) else []:
        try:
            rule = AlertRule(
                rule_id=str(item.get("rule_id") or ""),
                metric=str(item.get("metric") or ""),
                op=str(item.get("op") or ">"),
                threshold=float(item.get("threshold")),
                agent_id=str(item.get("agent_id") or "*"),
                cooldown_s=int(item.get("cooldown_s", 3600)),
                webhook_url=str(item.get("webhook_url") or ""),
            )
            rule.validate()
            rules.append(rule)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("[VITALS] skipping bad alert rule %r: %s", item, exc)
    return rules


# ---------------------------------------------------------------------------
# Webhook dispatch — signed, retried, transport-injectable
# ---------------------------------------------------------------------------


def build_alert_payload(event: AlertEvent) -> Dict[str, Any]:
    return {
        "schema": PAYLOAD_SCHEMA,
        "event_id": event.event_id,
        "rule_id": event.rule_id,
        "agent_id": event.agent_id,
        "metric": event.metric,
        "operator": event.operator,
        "threshold": event.threshold,
        "observed_value": event.observed_value,
        "observed_at": event.observed_at,
        "fleet_size": event.fleet_size,
    }


def canonical_json(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    """HMAC-SHA256 over '<timestamp>.<canonical body>' -> 'sha256=<hex>'."""
    mac = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify_signature(secret: str, body: bytes, timestamp: str, signature: str) -> bool:
    """Constant-time verification (used by tests and documented for receivers)."""
    expected = sign_payload(secret, body, timestamp)
    return hmac.compare_digest(expected, signature or "")


def _urllib_transport(url: str, body: bytes, headers: Dict[str, str]) -> Tuple[int, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")[:2000]
    except urllib.error.HTTPError as exc:
        return exc.code, (exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else "")[:2000]
    except Exception as exc:
        raise IOError(f"webhook transport failed: {exc}") from exc


@dataclass
class DispatchResult:
    event_id: str
    webhook_url: str
    ok: bool
    attempts: int
    status_code: Optional[int] = None
    error: str = ""


class WebhookDispatcher:
    """Signed webhook delivery with exponential-backoff retries.

    ``transport`` is a callable ``(url, body, headers) -> (status, text)``.
    Production uses the built-in urllib transport; tests inject a stub so no
    network traffic ever leaves the test process.
    """

    def __init__(
        self,
        secret: str,
        *,
        transport: Optional[Callable[[str, bytes, Dict[str, str]], Tuple[int, str]]] = None,
        max_attempts: int = 3,
        base_backoff_s: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not secret:
            raise ValueError("webhook secret is required")
        self.secret = secret
        self.transport = transport or _urllib_transport
        self.max_attempts = max(1, int(max_attempts))
        self.base_backoff_s = float(base_backoff_s)
        self.sleeper = sleeper

    def dispatch(self, event: AlertEvent, webhook_url: str) -> DispatchResult:
        if not webhook_url:
            return DispatchResult(event.event_id, webhook_url, False, 0, error="no webhook_url configured")
        payload = build_alert_payload(event)
        body = canonical_json(payload)
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Sincor-Timestamp": timestamp,
            "X-Sincor-Signature": sign_payload(self.secret, body, timestamp),
        }
        attempts = 0
        last_error = ""
        last_status: Optional[int] = None
        while attempts < self.max_attempts:
            attempts += 1
            try:
                status, _text = self.transport(webhook_url, body, headers)
                last_status = status
                if 200 <= status < 300:
                    return DispatchResult(event.event_id, webhook_url, True, attempts, status)
                last_error = f"http_{status}"
            except Exception as exc:
                last_error = str(exc)[:300]
            if attempts < self.max_attempts:
                self.sleeper(self.base_backoff_s * (2 ** (attempts - 1)))
        return DispatchResult(event.event_id, webhook_url, False, attempts, last_status, last_error)


# ---------------------------------------------------------------------------
# Module-level alert evaluation (evaluation piggybacks on dashboard reads)
# ---------------------------------------------------------------------------

_ALERT_COOLDOWN_STATE: Dict[str, float] = {}


def evaluate_and_dispatch(
    fleet: Dict[str, Any],
    *,
    rules: Optional[List[AlertRule]] = None,
    secret: Optional[str] = None,
    dispatcher: Optional[WebhookDispatcher] = None,
) -> Dict[str, Any]:
    """Evaluate alert rules over a fleet snapshot and dispatch webhooks.

    Returns a summary dict: events + per-event dispatch results. Alert
    evaluation runs on dashboard reads (documented in the blueprint); there
    is no background scheduler yet — that is an honest beta gap.
    """
    rules = rules if rules is not None else load_alert_rules_from_env()
    if not rules:
        return {"evaluated": 0, "dispatched": 0, "events": [], "results": []}

    snapshots = []
    for item in fleet.get("agents", []):
        try:
            tb = item.get("token_budget") or {}
            snapshots.append(
                AgentVitals(
                    agent_id=item.get("agent_id", ""),
                    name=item.get("name", ""),
                    origin=item.get("origin", ""),
                    heartbeat_age_s=item.get("heartbeat_age_s"),
                    heartbeat_status=item.get("heartbeat_status", "unknown"),
                    health=item.get("health", "unknown"),
                    uptime_pct=item.get("uptime_pct"),
                    latency_ms=item.get("latency_ms"),
                    kya_status=item.get("kya_status"),
                    tasks_completed=int(item.get("tasks_completed") or 0),
                    total_earned_axm=float(item.get("total_earned_axm") or 0),
                    cost_per_task_axm=item.get("cost_per_task_axm"),
                    token_budget=TokenBudget(
                        daily_ceiling=int(tb.get("daily_ceiling") or 0),
                        used_today=int(tb.get("used_today") or 0),
                        remaining=int(tb.get("remaining") or 0),
                        utilisation_pct=float(tb.get("utilisation_pct") or 0.0),
                        allowed=bool(tb.get("allowed")),
                        killed=bool(tb.get("killed")),
                        source_ok=bool(tb.get("source_ok", True)),
                    ),
                    tool_calls=ToolCallBudget(
                        instrumented=bool((item.get("tool_calls") or {}).get("instrumented")),
                        used=(item.get("tool_calls") or {}).get("used"),
                        limit=(item.get("tool_calls") or {}).get("limit"),
                    ),
                    as_of=item.get("as_of", _iso_now()),
                )
            )
        except Exception as exc:
            logger.warning("[VITALS] alert rebuild failed for %r: %s", item, exc)

    global _ALERT_COOLDOWN_STATE
    events, _ALERT_COOLDOWN_STATE = evaluate_rules(
        rules, snapshots, cooldown_state=_ALERT_COOLDOWN_STATE
    )

    results: List[DispatchResult] = []
    if events:
        secret = secret if secret is not None else (os.environ.get("SINCOR_VITALS_WEBHOOK_SECRET") or "")
        disp = dispatcher or (WebhookDispatcher(secret) if secret else None)
        for event in events:
            rule = next((r for r in rules if r.rule_id == event.rule_id), None)
            if disp is None or rule is None or not rule.webhook_url:
                results.append(DispatchResult(event.event_id, "", False, 0, error="no dispatcher/secret/url"))
                continue
            results.append(disp.dispatch(event, rule.webhook_url))

    return {
        "evaluated": len(events),
        "dispatched": sum(1 for r in results if r.ok),
        "events": [asdict(e) for e in events],
        "results": [asdict(r) for r in results],
    }
