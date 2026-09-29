"""P24 live-block gate. Hard invariant: no live intents, ever."""

from __future__ import annotations


class LiveBlocked(RuntimeError):
    """Raised on any live-intent code path. Live-blocked by catalog design."""


LIVE_BLOCKED = True


def guard_live(intent: str = "") -> None:
    """Every live-intent entrypoint calls this first. It always raises."""
    raise LiveBlocked(
        "P24 is live-blocked by catalog design; live intent refused"
        + (f": {intent}" if intent else "")
    )
