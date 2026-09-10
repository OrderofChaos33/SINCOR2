"""Auction and execution timeout helpers — no Flask import."""

from __future__ import annotations

import os

EXECUTION_TIMEOUT_MS = int(os.environ.get("A2A_EXECUTION_TIMEOUT_MS", "120000"))


def assignment_deadline_ms(
    assigned_at: int,
    time_est_ms: int,
    timeout_ms: int = EXECUTION_TIMEOUT_MS,
) -> int:
    estimate = int(time_est_ms or 0)
    if estimate > 0:
        return int(assigned_at) + min(estimate * 2, timeout_ms * 5)
    return int(assigned_at) + timeout_ms
