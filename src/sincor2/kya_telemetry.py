"""KYA Telemetry — runtime execution-reliability loop (KYA extension).

The KYA trust score (``sincor2.kya_trust_score``) reads *standing*
evidence: registry status/stake, stake-ledger reputation, settlement
history. This module is the *runtime* half of recommendation #2
(Decentralized Reputation & Verification Loops): it records what an
agent actually did when it executed work — success / error / timeout,
latency, skill — into a durable store, computes rolling-window
reliability from those real records, and maps the evidence to a
throttle verdict (an allocation multiplier and a rate-limit tier) so a
future engine wiring can scale work DOWN for agents that are failing
live, instead of trusting standing reputation alone.

This pass deliberately builds the brain + store only. Nothing here
changes live allocation, bidding, or rate-limit behaviour: applying a
verdict inside the engine is a separate, founder-gated wiring step.

Honesty contract (same spirit as the trust score):

- A verdict is computed ONLY from execution events actually recorded
  in the telemetry store. There are no invented inputs and no
  external data.
- Insufficient evidence is an explicit ``unknown`` verdict with a
  NEUTRAL multiplier (1.0) and the standard rate-limit tier — never a
  fabricated reliability score and never a punitive throttle on thin
  or missing data. An agent with zero recorded executions is
  ``unknown``, full stop.
- Throttling only ever scales DOWN: the multiplier is clamped to
  ``[floor, ceiling]`` with ceiling 1.0 by default, so telemetry can
  never *boost* an agent beyond its standing allocation.
- Every verdict carries its full evidence (attempts / successes /
  errors / timeouts in the window, the rates derived from them, and
  the policy parameters used) so anyone can recompute the verdict by
  hand from the response itself.

Storage: SQLite via the standard library, one row per execution
event, consistent with the repo's other durable stores
(``a2a_idempotency``, ``a2a_integration``). The clock is injectable
for deterministic tests; timestamps are epoch seconds (float).
"""

from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

OUTCOME_SUCCESS = "success"
OUTCOME_ERROR = "error"
OUTCOME_TIMEOUT = "timeout"
VALID_OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_ERROR, OUTCOME_TIMEOUT)

SOURCE_LABEL = "sincor2.kya_telemetry.TelemetryStore"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS kya_execution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    skill_id TEXT,
    outcome TEXT NOT NULL,
    latency_ms REAL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kya_telemetry_agent_ts
    ON kya_execution_events (agent_id, ts);
"""


def default_db_path() -> Path:
    """Production store location: ``<data_dir>/kya_telemetry.db``."""
    from sincor2.data_paths import data_dir

    return data_dir() / "kya_telemetry.db"


# ---------------------------------------------------------------------------
# Durable store
# ---------------------------------------------------------------------------

class TelemetryStore:
    """Durable per-agent execution-outcome store (SQLite).

    ``db_path`` may be a path or ``":memory:"``. ``clock`` is a
    zero-arg callable returning epoch seconds; tests inject a fake
    clock so window math is deterministic. Recording is append-only:
    events are never edited or deleted by this module, so the store
    is an auditable execution history, not a mutable score.
    """

    def __init__(
        self,
        db_path: Optional[Any] = None,
        *,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if db_path is None:
            db_path = default_db_path()
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or time.time
        # A single persistent connection keeps ":memory:" usable and
        # is safe here: all access is short transactions, and the
        # check_same_thread=False + lock pattern mirrors
        # a2a_integration's ReputationLedger store.
        self._conn = sqlite3.connect(
            self._db_path, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- recording ------------------------------------------------------

    def record(
        self,
        agent_id: str,
        outcome: str,
        *,
        skill_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
        ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Append one execution outcome for ``agent_id``.

        ``outcome`` must be one of ``success`` / ``error`` /
        ``timeout``. ``ts`` defaults to the store clock. Returns the
        stored event as a dict (including its row id).
        """
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            raise ValueError("agent_id_required")
        outcome = str(outcome or "").strip().lower()
        if outcome not in VALID_OUTCOMES:
            raise ValueError("invalid_outcome:%s" % outcome)
        if latency_ms is not None:
            latency_ms = float(latency_ms)
            if not math.isfinite(latency_ms) or latency_ms < 0:
                raise ValueError("invalid_latency_ms")
        when = float(ts) if ts is not None else float(self._clock())
        if not math.isfinite(when):
            raise ValueError("invalid_ts")
        skill = str(skill_id).strip() if skill_id else None
        cur = self._conn.execute(
            "INSERT INTO kya_execution_events"
            " (agent_id, skill_id, outcome, latency_ms, ts)"
            " VALUES (?, ?, ?, ?, ?)",
            (agent_id, skill, outcome, latency_ms, when),
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "agent_id": agent_id,
            "skill_id": skill,
            "outcome": outcome,
            "latency_ms": latency_ms,
            "ts": when,
        }

    # -- reading --------------------------------------------------------

    def events(
        self,
        agent_id: str,
        *,
        since: Optional[float] = None,
        until: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """All recorded events for one agent, oldest first,
        optionally bounded to ``since <= ts <= until``."""
        sql = "SELECT * FROM kya_execution_events WHERE agent_id = ?"
        params: List[Any] = [str(agent_id)]
        if since is not None:
            sql += " AND ts >= ?"
            params.append(float(since))
        if until is not None:
            sql += " AND ts <= ?"
            params.append(float(until))
        sql += " ORDER BY ts ASC, id ASC"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def count(self, agent_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM kya_execution_events"
            " WHERE agent_id = ?",
            (str(agent_id),),
        ).fetchone()
        return int(row["n"])

    # -- verdicts -------------------------------------------------------

    def reliability(
        self,
        agent_id: str,
        *,
        policy: Optional["TelemetryPolicy"] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Rolling-window reliability stats for one agent, computed
        from this store's real events."""
        policy = policy or TelemetryPolicy()
        now = float(now) if now is not None else float(self._clock())
        window_events = self.events(
            agent_id, since=now - policy.window_seconds, until=now)
        return compute_reliability(
            window_events, now=now, policy=policy)

    def verdict(
        self,
        agent_id: str,
        *,
        policy: Optional["TelemetryPolicy"] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Throttle verdict for one agent from this store's events.

        Read-only: computing a verdict never records anything and
        never changes engine behaviour by itself."""
        policy = policy or TelemetryPolicy()
        stats = self.reliability(agent_id, policy=policy, now=now)
        return evaluate_policy(stats, policy=policy)


_default_store: Optional[TelemetryStore] = None


def get_default_store() -> TelemetryStore:
    """Process-wide store at the production data-dir path."""
    global _default_store
    if _default_store is None:
        _default_store = TelemetryStore()
    return _default_store


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryPolicy:
    """Throttle policy parameters.

    - ``window_seconds``: rolling evidence window (default 24h).
    - ``min_attempts``: minimum events in the window before ANY
      throttle may apply. Below this the verdict is ``unknown`` and
      neutral — thin evidence never punishes.
    - ``error_rate_full`` / ``timeout_rate_full``: the in-window rate
      at (or above) which throttle pressure saturates and the
      multiplier reaches ``floor``.
    - ``floor`` / ``ceiling``: hard clamp on the multiplier. The
      ceiling is 1.0 by default: telemetry scales down or leaves
      allocation alone, it never boosts.
    """

    window_seconds: float = 86400.0
    min_attempts: int = 20
    error_rate_full: float = 0.50
    timeout_rate_full: float = 0.30
    floor: float = 0.1
    ceiling: float = 1.0

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("window_seconds_must_be_positive")
        if self.min_attempts < 1:
            raise ValueError("min_attempts_must_be_positive")
        if not (0 < self.error_rate_full <= 1):
            raise ValueError("error_rate_full_out_of_range")
        if not (0 < self.timeout_rate_full <= 1):
            raise ValueError("timeout_rate_full_out_of_range")
        if not (0 <= self.floor <= self.ceiling <= 1.0):
            raise ValueError("floor_ceiling_out_of_range")


# ---------------------------------------------------------------------------
# Reliability computation (pure)
# ---------------------------------------------------------------------------

def _percentile(sorted_values: Sequence[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile; None when there are no samples."""
    if not sorted_values:
        return None
    rank = math.ceil(pct / 100.0 * len(sorted_values))
    idx = min(max(rank, 1), len(sorted_values)) - 1
    return float(sorted_values[idx])


def compute_reliability(
    events: Sequence[Dict[str, Any]],
    *,
    now: float,
    policy: Optional[TelemetryPolicy] = None,
) -> Dict[str, Any]:
    """Rolling-window reliability stats from raw event dicts.

    Only events with ``now - window_seconds <= ts <= now`` count;
    events outside the window (including future-dated ones, a clock
    skew guard) are ignored. Pure: the caller supplies the events.
    """
    policy = policy or TelemetryPolicy()
    now = float(now)
    since = now - policy.window_seconds
    windowed = [
        e for e in events
        if since <= float(e.get("ts") or 0) <= now
    ]
    attempts = len(windowed)
    successes = sum(
        1 for e in windowed if e.get("outcome") == OUTCOME_SUCCESS)
    errors = sum(
        1 for e in windowed if e.get("outcome") == OUTCOME_ERROR)
    timeouts = sum(
        1 for e in windowed if e.get("outcome") == OUTCOME_TIMEOUT)
    latencies = sorted(
        float(e["latency_ms"]) for e in windowed
        if e.get("latency_ms") is not None)
    return {
        "attempts": attempts,
        "successes": successes,
        "errors": errors,
        "timeouts": timeouts,
        "error_rate": (errors / attempts) if attempts else 0.0,
        "timeout_rate": (timeouts / attempts) if attempts else 0.0,
        "failure_rate": (
            (errors + timeouts) / attempts) if attempts else 0.0,
        "latency_samples": len(latencies),
        "p50_latency_ms": _percentile(latencies, 50),
        "p95_latency_ms": _percentile(latencies, 95),
        "window_seconds": policy.window_seconds,
        "window_start": since,
        "window_end": now,
    }


# ---------------------------------------------------------------------------
# Throttle verdict (pure)
# ---------------------------------------------------------------------------

def _rate_limit_tier(multiplier: float, ceiling: float) -> str:
    if multiplier >= ceiling:
        return "standard"
    if multiplier >= 0.6:
        return "reduced"
    if multiplier >= 0.3:
        return "restricted"
    return "minimal"


def evaluate_policy(
    stats: Dict[str, Any],
    *,
    policy: Optional[TelemetryPolicy] = None,
) -> Dict[str, Any]:
    """Map reliability stats to a throttle verdict.

    Pressure is the worse of the error and timeout saturation
    ratios, clamped to [0, 1]; the multiplier interpolates linearly
    from ``ceiling`` (no pressure) to ``floor`` (saturated). With
    fewer than ``min_attempts`` events the verdict is ``unknown``
    with a neutral multiplier — insufficient evidence NEVER
    throttles.
    """
    policy = policy or TelemetryPolicy()
    attempts = int(stats.get("attempts") or 0)
    evidence = {
        "attempts": attempts,
        "successes": int(stats.get("successes") or 0),
        "errors": int(stats.get("errors") or 0),
        "timeouts": int(stats.get("timeouts") or 0),
        "error_rate": float(stats.get("error_rate") or 0.0),
        "timeout_rate": float(stats.get("timeout_rate") or 0.0),
        "failure_rate": float(stats.get("failure_rate") or 0.0),
        "latency_samples": int(stats.get("latency_samples") or 0),
        "p50_latency_ms": stats.get("p50_latency_ms"),
        "p95_latency_ms": stats.get("p95_latency_ms"),
        "window_seconds": policy.window_seconds,
        "min_attempts": policy.min_attempts,
    }
    policy_view = {
        "error_rate_full": policy.error_rate_full,
        "timeout_rate_full": policy.timeout_rate_full,
        "floor": policy.floor,
        "ceiling": policy.ceiling,
    }
    if attempts < policy.min_attempts:
        return {
            "status": "unknown",
            "allocation_multiplier": policy.ceiling,
            "rate_limit_tier": "standard",
            "throttled": False,
            "pressure": 0.0,
            "evidence": evidence,
            "policy": policy_view,
            "source": SOURCE_LABEL,
            "detail": (
                "insufficient telemetry evidence in window "
                "(%d of %d required attempts) — reliability unknown, "
                "no throttle applied" % (attempts, policy.min_attempts)
            ),
        }
    pressure = max(
        evidence["error_rate"] / policy.error_rate_full,
        evidence["timeout_rate"] / policy.timeout_rate_full,
    )
    pressure = min(max(pressure, 0.0), 1.0)
    multiplier = policy.ceiling - pressure * (policy.ceiling - policy.floor)
    multiplier = min(max(multiplier, policy.floor), policy.ceiling)
    multiplier = round(multiplier, 4)
    return {
        "status": "evaluated",
        "allocation_multiplier": multiplier,
        "rate_limit_tier": _rate_limit_tier(multiplier, policy.ceiling),
        "throttled": multiplier < policy.ceiling,
        "pressure": round(pressure, 4),
        "evidence": evidence,
        "policy": policy_view,
        "source": SOURCE_LABEL,
        "detail": (
            "verdict computed from %d recorded executions in window"
            % attempts
        ),
    }


# ---------------------------------------------------------------------------
# Trust-score integration (read-only, additive)
# ---------------------------------------------------------------------------

def telemetry_component(
    agent_id: Optional[str],
    *,
    store: Optional[TelemetryStore] = None,
    policy: Optional[TelemetryPolicy] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Telemetry component for the trust-score response shape.

    Returns None when the agent has NO recorded telemetry at all —
    the component appears ONLY when telemetry data exists, mirroring
    the trust score's "no record, no component" honesty. When events
    exist but are too thin to evaluate, the component is present with
    an ``unknown`` status and a neutral multiplier.
    """
    if not agent_id:
        return None
    store = store or get_default_store()
    if store.count(agent_id) == 0:
        return None
    verdict = store.verdict(agent_id, policy=policy, now=now)
    return {
        "status": verdict["status"],
        "allocation_multiplier": verdict["allocation_multiplier"],
        "rate_limit_tier": verdict["rate_limit_tier"],
        "throttled": verdict["throttled"],
        "pressure": verdict["pressure"],
        "source": SOURCE_LABEL,
        "evidence": verdict["evidence"],
        "policy": verdict["policy"],
        "detail": verdict["detail"],
    }


def attach_telemetry_component(
    score_result: Dict[str, Any],
    *,
    store: Optional[TelemetryStore] = None,
    policy: Optional[TelemetryPolicy] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Return a copy of a ``compute_trust_score`` result with a
    ``telemetry`` component added — ONLY when the agent has recorded
    telemetry. The score, status, and tier are never altered, and
    the input dict is not mutated. This is the read-only integration
    seam; serving it on any route is a separate wiring decision.
    """
    component = telemetry_component(
        score_result.get("agent_id"), store=store, policy=policy,
        now=now)
    if component is None:
        return score_result
    enriched = dict(score_result)
    components = dict(score_result.get("components") or {})
    components["telemetry"] = component
    enriched["components"] = components
    return enriched
