"""Fail-closed gating for all outbound send/publish activity.

P1 audit finding #9: outreach/content schedulers must never send unless three
independent gates ALL pass:

  1. Explicit env opt-in — OUTREACH_ENABLED / CONTENT_AGENT_ENABLED must be
     explicitly truthy. Unset, empty, or any other value -> OFF (fail closed).
  2. Approved-recipients allowlist — OUTREACH_APPROVED_RECIPIENTS must be
     non-empty, and every recipient must be on it. Applies to email sends.
  3. Global kill switch — SINCOR_COMMS_KILL_SWITCH truthy -> ALL outbound
     sends/publishes are refused. Checked LIVE on every call (never cached),
     so it can be flipped at runtime without a restart.

The explicit-opt-in layer is ported from WP2 (xioix/shadow-wp2-build), which
made outreach default-off with OUTREACH_ENABLED=true. This module keeps that
fail-closed approach in one place and adds the allowlist + kill-switch layers
the P1 audit requires. Schedulers, engines, and publishers consult this module
instead of reading env vars directly, so the gates are enforced uniformly.

Env vars:
  OUTREACH_ENABLED              "1"/"true"/"yes" (case-insensitive) to arm outreach
  CONTENT_AGENT_ENABLED         "1"/"true"/"yes" to arm content publishing
  OUTREACH_APPROVED_RECIPIENTS  comma- or semicolon-separated allowlist emails
  SINCOR_COMMS_KILL_SWITCH      truthy -> block every send/publish immediately
  AUTONOMOUS_AGENTS             explicitly "false" overrides outreach opt-in
                                (defense in depth against the old default-true chain)
"""

from __future__ import annotations

import logging
import os
from typing import Set, Tuple

logger = logging.getLogger("sincor2.comms_gating")

_TRUTHY = ("1", "true", "yes")


def _truthy(value: str | None) -> bool:
    """Explicit opt-in check: only the documented truthy values count."""
    return (value or "").strip().lower() in _TRUTHY


def kill_switch_engaged() -> bool:
    """Global kill switch. Checked live on every call — never cached.

    When engaged, every send/publish path must refuse, regardless of opt-in
    state or allowlists.
    """
    engaged = _truthy(os.environ.get("SINCOR_COMMS_KILL_SWITCH", ""))
    if engaged:
        logger.warning("[COMMS] Kill switch engaged (SINCOR_COMMS_KILL_SWITCH) — all sends blocked")
    return engaged


def outreach_opted_in() -> bool:
    """Fail-closed outreach opt-in (ported from WP2).

    OUTREACH_ENABLED must be explicitly truthy. Unset/empty/"false"/anything
    else -> False. AUTONOMOUS_AGENTS explicitly "false" overrides even an
    explicit opt-in (defense in depth).
    """
    if not _truthy(os.environ.get("OUTREACH_ENABLED", "")):
        return False
    if (os.environ.get("AUTONOMOUS_AGENTS", "") or "").strip().lower() == "false":
        return False
    return True


def content_opted_in() -> bool:
    """Fail-closed content-publish opt-in. CONTENT_AGENT_ENABLED explicitly truthy."""
    return _truthy(os.environ.get("CONTENT_AGENT_ENABLED", ""))


def approved_recipients() -> Set[str]:
    """Parse OUTREACH_APPROVED_RECIPIENTS into a normalized lowercase set."""
    raw = os.environ.get("OUTREACH_APPROVED_RECIPIENTS", "")
    return {r.strip().lower() for r in raw.replace(";", ",").split(",") if r.strip()}


def outreach_send_allowed(recipient: str) -> Tuple[bool, str]:
    """Check all three gates for a single outreach email recipient.

    Returns (allowed, reason). reason is "ok" when allowed, otherwise a
    machine-readable gate name for logs/tests.
    """
    if kill_switch_engaged():
        return False, "kill_switch_engaged"
    if not outreach_opted_in():
        return False, "outreach_not_opted_in"
    allowlist = approved_recipients()
    if not allowlist:
        return False, "no_approved_recipients"
    if (recipient or "").strip().lower() not in allowlist:
        return False, "recipient_not_allowlisted"
    return True, "ok"


def content_publish_allowed(manual: bool = False) -> Tuple[bool, str]:
    """Check gates for a content publish (e.g. WordPress).

    The recipients allowlist does not apply to publishing to our own site;
    the gates are explicit opt-in + kill switch. ``manual=True`` marks an
    explicit per-action publish (e.g. an operator-triggered async task with an
    explicit do_publish flag) — it waives the CONTENT_AGENT_ENABLED
    requirement but NEVER the kill switch.
    """
    if kill_switch_engaged():
        return False, "kill_switch_engaged"
    if not manual and not content_opted_in():
        return False, "content_not_opted_in"
    return True, "ok"
