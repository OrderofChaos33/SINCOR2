"""Build/version stamping (observability build #5, site audit 2026-10-28).

Every deploy must be identifiable in-product: which commit is serving,
and when that build started. Railway injects ``RAILWAY_GIT_COMMIT_SHA``
into the service environment; ``SINCOR_BUILD_SHA`` is the manual fallback
for non-Railway hosts. With neither set the stamp degrades to "unknown"
— it never raises, so health/boot/footer rendering can call it blindly.

``SINCOR_BUILD_TIME`` may pin an explicit build timestamp; otherwise the
process start time (captured at import) is reported, which on Railway is
the deploy's boot moment.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

UNKNOWN = "unknown"

_PROCESS_STARTED_AT = (
    datetime.now(timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z")
)


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def build_sha() -> str:
    """Deployed commit SHA, or "unknown" when the host provides none."""
    try:
        for var in ("RAILWAY_GIT_COMMIT_SHA", "SINCOR_BUILD_SHA"):
            sha = _clean(os.environ.get(var))
            if sha:
                return sha
    except Exception:  # pragma: no cover - env access cannot fail closed
        pass
    return UNKNOWN


def build_time() -> str:
    """Explicit SINCOR_BUILD_TIME, else this process's start time."""
    try:
        pinned = _clean(os.environ.get("SINCOR_BUILD_TIME"))
        if pinned:
            return pinned
    except Exception:  # pragma: no cover
        pass
    return _PROCESS_STARTED_AT


def build_info() -> dict:
    """{"sha", "short", "time"} — always returns, never raises."""
    sha = build_sha()
    return {
        "sha": sha,
        "short": sha[:12] if sha != UNKNOWN else UNKNOWN,
        "time": build_time(),
    }
