"""P24 creator onboarding: registration + content-policy screening.

The onboarding agent guides registration and enforces the content-policy
guard offchain *before* any onchain issuance. Rejections log the violated
rule version and matched phrase — never the full submitted text. Never
signs; the module is live-blocked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List

from . import screener as screener_module
from .factory import CreatorToken, CreatorTokenFactory
from .live_block import guard_live
from .screener import ContentScreener, ScreenerDenied


@dataclass
class Registration:
    creator_id: str
    name: str
    symbol: str
    description: str
    bio: str
    policy_version: str
    registered_at: float
    token: CreatorToken


@dataclass
class RejectionLog:
    creator_id: str
    field: str
    matched_phrase: str
    ruleset_version: str
    ts: float
    # NOTE: the submitted text is deliberately NOT stored.


class OnboardingAgent:
    """Guides creator registration; screens metadata before issuance.

    The content screener is pluggable (see :mod:`sincor2.defi.p24.screener`).
    The default is the standing deny-list screen; pass another
    ``ContentScreener`` (or set ``P24_SCREENER=deferred`` process-wide) to
    change the posture. Enforcement stays here — callers cannot bypass it.
    """

    def __init__(
        self,
        factory: CreatorTokenFactory | None = None,
        screener: ContentScreener | None = None,
    ) -> None:
        self.factory = factory or CreatorTokenFactory()
        self.screener = (
            screener if screener is not None
            else screener_module.default_onboarding_screener()
        )
        self.registrations: Dict[str, Registration] = {}
        self.rejections: List[RejectionLog] = []

    def register(
        self,
        creator_id: str,
        name: str,
        symbol: str,
        description: str,
        bio: str,
        now: float | None = None,
    ) -> Registration:
        now = time.time() if now is None else now
        if not creator_id:
            raise ValueError("creator_id required")
        if creator_id in self.registrations:
            raise ValueError(f"creator {creator_id!r} already registered")
        try:
            version = screener_module.require_screened(
                self.screener, creator_id, name, symbol, description, bio)
        except ScreenerDenied as exc:
            self.rejections.append(RejectionLog(
                creator_id=creator_id,
                field=exc.field,
                matched_phrase=exc.matched_phrase,
                ruleset_version=exc.ruleset_version,
                ts=now,
            ))
            raise
        token = self.factory.issue(
            name=name, symbol=symbol, creator=creator_id,
            policy_version=version, screened=True, now=now)
        reg = Registration(creator_id, name, symbol, description, bio,
                           version, now, token)
        self.registrations[creator_id] = reg
        return reg

    def sign_live(self, *args, **kwargs) -> None:
        guard_live("OnboardingAgent.sign_live")
