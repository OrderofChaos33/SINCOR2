"""External-agent registration velocity.

The operating directive (2026-09-25) says: post-Genesis, rank external-agent
registration velocity and weight TOA objectives toward volume over vanity.

This module reads the persisted agent directory (``registered_at`` on every
record, ``origin`` stamped at registration) and produces:

* :func:`velocity_report` — totals, external/internal split, per-day series,
  registrations/day, external share, trend.
* :func:`volume_over_vanity_weights` — the directive's TOA objective weights
  with the velocity component driven by MEASURED external registrations/day
  instead of a hardcoded constant.

Origin classification: the platform agent (``sincor-agent-swarm``) is
``internal``; every API registration is ``external``.  Records written before
this module existed are backfilled on read (platform id => internal,
everything else => external).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional

PLATFORM_AGENT_ID = "sincor-agent-swarm"

ORIGIN_INTERNAL = "internal"
ORIGIN_EXTERNAL = "external"

DAY_MS = 86_400_000

# The directive's volume-over-vanity posture, with velocity as the anchor.
# volume_over_vanity_weights() scales the velocity component by measured
# external registrations/day; the rest stay fixed.
BASE_WEIGHTS = {
    "velocity": 0.30,
    "revenue": 0.20,
    "treasury_inflow": 0.15,
    "risk": 0.15,
    "timeline": 0.10,
    "compliance": 0.05,
    "governance": 0.05,
}

# Normalization: 10 external registrations/day saturates the velocity weight
# at 2x its base.  Below that it scales linearly.  Tune post-Genesis once
# real registration flow exists.
VELOCITY_SATURATION_PER_DAY = 10.0
VELOCITY_MAX_MULTIPLIER = 2.0


def classify_origin(agent: Mapping[str, Any]) -> str:
    """Return the agent's origin, backfilling records that predate the field."""
    origin = str(agent.get("origin") or "").strip().lower()
    if origin in (ORIGIN_INTERNAL, ORIGIN_EXTERNAL):
        return origin
    if str(agent.get("agent_id") or "") == PLATFORM_AGENT_ID:
        return ORIGIN_INTERNAL
    return ORIGIN_EXTERNAL


def _day_bucket(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts_ms / 1000))


def velocity_report(agents: Mapping[str, Mapping[str, Any]],
                    days: int = 30,
                    now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Build the registration velocity report from the agent directory."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff = now - days * DAY_MS

    total = 0
    external = 0
    internal = 0
    per_day: Dict[str, Dict[str, int]] = {}
    external_recent = 0

    for agent in agents.values():
        total += 1
        origin = classify_origin(agent)
        if origin == ORIGIN_INTERNAL:
            internal += 1
        else:
            external += 1
        ts = int(agent.get("registered_at") or 0)
        if ts >= cutoff and ts <= now:
            bucket = _day_bucket(ts)
            entry = per_day.setdefault(bucket, {"external": 0, "internal": 0})
            entry[origin] += 1
            if origin == ORIGIN_EXTERNAL:
                external_recent += 1

    # Fill the full window so the series is chart-ready.
    series: List[Dict[str, Any]] = []
    for d in range(days):
        bucket = _day_bucket(now - d * DAY_MS)
        entry = per_day.get(bucket, {"external": 0, "internal": 0})
        series.append({"date": bucket, **entry})
    series.reverse()

    ext_per_day = external_recent / days if days else 0.0
    return {
        "window_days": days,
        "generated_at": _day_bucket(now),
        "total_registered": total,
        "external": external,
        "internal": internal,
        "external_share": (external / total) if total else 0.0,
        "external_registrations_in_window": external_recent,
        "external_per_day": round(ext_per_day, 4),
        "daily_series": series,
    }


def volume_over_vanity_weights(report: Mapping[str, Any]) -> Dict[str, float]:
    """TOA objective weights with the velocity component driven by measured
    external registration velocity.

    Scaling: velocity_weight = base * (1 + min(ext_per_day / saturation, 1)
    capped so the weight never exceeds base * VELOCITY_MAX_MULTIPLIER.
    The remaining weights are renormalized so the dict still sums to 1.
    """
    ext_per_day = float(report.get("external_per_day") or 0.0)
    saturation = min(ext_per_day / VELOCITY_SATURATION_PER_DAY, 1.0)
    multiplier = 1.0 + saturation * (VELOCITY_MAX_MULTIPLIER - 1.0)

    weights = dict(BASE_WEIGHTS)
    weights["velocity"] = BASE_WEIGHTS["velocity"] * multiplier
    total = sum(weights.values())
    return {k: round(v / total, 4) for k, v in weights.items()}
