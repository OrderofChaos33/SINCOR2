"""P24 content-screener interface: pluggable policy screening for issuance.

The standing phrase-based deny-list (no_price_talk, ruleset 1.0.0) lives in
:mod:`sincor2.defi.p24.policy`. This module makes the *screener* pluggable:
whoever decides content is acceptable plugs in as a ``ContentScreener``.

Fail-closed posture:
- ``get_screener()`` returns the configured screener, or a
  ``DeferredScreener`` when none is configured. The deferred stub ALWAYS
  denies with reason ``"deferred: no screener configured"`` — it never
  fabricates an allow.
- ``OnboardingAgent.register`` enforces the standing onboarding screener
  (see :func:`sincor2.defi.p24.screener.default_onboarding_screener` — the
  deny-list) unless constructed with another. Setting
  ``P24_SCREENER=deferred`` (or ``configure_screener(DeferredScreener())``)
  halts all issuance through the onboarding path.
- Every decision is logged with screener id + ruleset version. Denials log
  the field and reason — never the submitted text.

The onchain ``ContentPolicyGuard.screener`` role (constructor arg) is a
separate founder decision; see docs/architecture/P24_SCREENER_SELECTION.md.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional, Protocol

from . import policy

log = logging.getLogger("sincor2.defi.p24.screener")

#: Env override: "deny-list" (default) or "deferred".
SCREENER_ENV = "P24_SCREENER"
DEFERRED_REASON = "deferred: no screener configured"


@dataclass(frozen=True)
class ScreenDecision:
    allowed: bool
    reason: str          # human-readable; on deny, names the rule, not the text
    screener_id: str     # which screener decided
    ruleset_version: str = policy.RULESET_VERSION
    field: str = ""      # metadata field that failed (deny path only)
    matched_phrase: str = ""  # deny-list phrase, when applicable


class ContentScreener(Protocol):
    """Pluggable issuance content screener.

    Implementations must return a decision without mutating anything and
    without ever echoing the submitted text into logs on the deny path.
    """

    screener_id: str

    def screen(
        self, name: str, symbol: str, description: str, bio: str
    ) -> ScreenDecision: ...


class ScreenerDenied(policy.PolicyViolation):
    """Issuance refused by the content screener.

    Subclasses :class:`policy.PolicyViolation` deliberately: every screener
    denial is a content-policy rejection, and existing callers (the issuance
    route, onboarding tests) already map ``PolicyViolation`` to a 400 with
    the ruleset version. Carries the full decision for richer handling.
    """

    def __init__(self, decision: ScreenDecision):
        super().__init__(
            field=decision.field or "metadata",
            phrase=decision.matched_phrase or decision.reason,
        )
        # A screener may carry its own ruleset version (future screeners).
        self.ruleset_version = decision.ruleset_version
        self.decision = decision
        self.reason = decision.reason
        self.screener_id = decision.screener_id


class DenyListScreener:
    """The standing phrase-based no_price_talk screen (ruleset 1.0.0)."""

    screener_id = "deny-list"

    def screen(
        self, name: str, symbol: str, description: str, bio: str
    ) -> ScreenDecision:
        try:
            policy.require_clean(name, symbol, description, bio)
        except policy.PolicyViolation as exc:
            return ScreenDecision(
                allowed=False,
                reason=(
                    f"no_price_talk violation in {exc.field!r}: "
                    f"matched phrase {exc.matched_phrase!r}"
                ),
                screener_id=self.screener_id,
                field=exc.field,
                matched_phrase=exc.matched_phrase,
            )
        return ScreenDecision(
            allowed=True,
            reason="metadata passes the no_price_talk deny-list",
            screener_id=self.screener_id,
        )


class DeferredScreener:
    """Fail-closed stub: no screener configured, so issuance is deferred.

    Always denies. Exists so the onboarding path can be halted until the
    founder pins the screener identity — never a fake allow.
    """

    screener_id = "deferred"

    def screen(
        self, name: str, symbol: str, description: str, bio: str
    ) -> ScreenDecision:
        return ScreenDecision(
            allowed=False,
            reason=DEFERRED_REASON,
            screener_id=self.screener_id,
        )


_configured: Optional[ContentScreener] = None


def configure_screener(screener: Optional[ContentScreener]) -> None:
    """Install the process-wide screener (None = back to fail-closed)."""
    global _configured
    _configured = screener
    log.info("p24 screener configured: %s",
             screener.screener_id if screener else "deferred (none)")


def reset_screener() -> None:
    """Test isolation hook — drop the configured screener."""
    global _configured
    _configured = None


def get_screener() -> ContentScreener:
    """Process-wide screener registry.

    Returns the configured screener, or the fail-closed ``DeferredScreener``
    when none is configured. New consumers that ask for "the screener"
    without one configured are denied — never silently allowed.
    """
    if _configured is not None:
        return _configured
    return DeferredScreener()


def default_onboarding_screener() -> ContentScreener:
    """The onboarding path's standing screener.

    An explicitly configured screener (see :func:`configure_screener`) wins.
    Otherwise the deny-list (ruleset 1.0.0) is the standing configured
    screen for issuance — this preserves the existing onboarding behavior.
    Set ``P24_SCREENER=deferred`` to halt all issuance through the
    onboarding path until the founder pins the screener identity.
    """
    if _configured is not None:
        return _configured
    if os.environ.get(SCREENER_ENV, "").strip().lower() == "deferred":
        return DeferredScreener()
    return DenyListScreener()


def require_screened(
    screener: ContentScreener,
    creator_id: str,
    name: str,
    symbol: str,
    description: str,
    bio: str,
) -> str:
    """Run the screener; return the ruleset version or raise ScreenerDenied.

    Logs the decision (screener id + ruleset). Denial logs cite the field
    and reason — the submitted text is never logged.
    """
    decision = screener.screen(name, symbol, description, bio)
    if decision.allowed:
        log.info(
            "p24 screen allow: creator=%s screener=%s ruleset=%s",
            creator_id, decision.screener_id, decision.ruleset_version,
        )
        return decision.ruleset_version
    log.warning(
        "p24 screen deny: creator=%s screener=%s ruleset=%s field=%s reason=%s",
        creator_id, decision.screener_id, decision.ruleset_version,
        decision.field, decision.reason,
    )
    raise ScreenerDenied(decision)
