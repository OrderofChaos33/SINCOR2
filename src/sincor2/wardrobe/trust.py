"""Published trust formula. New agents stay provisional at 40."""

from __future__ import annotations

from typing import Any


def _clip(n: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, n))


def trust_score(metrics: dict[str, Any]) -> tuple[int, str]:
    completed = int(metrics.get("jobs_completed") or 0)
    failed = int(metrics.get("jobs_failed") or 0)
    total = completed + failed
    if total < 10:
        return 40, "provisional"
    success = completed / total
    uptime = float(metrics.get("uptime_7d") or 0.0)
    auditor = float(metrics.get("auditor_pass_rate") or 0.0)
    age = float(metrics.get("age_factor") or 0.0)
    raw = 40 * uptime + 25 * success + 20 * auditor + 15 * age
    return int(round(_clip(raw))), "settled"
