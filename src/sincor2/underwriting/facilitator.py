from __future__ import annotations

from .envelopes import EnvelopeService
from .mandates import MandateService
from .policy import Policy
from .store import UnderwriteStore
from .taps.base import Tap
from .types import AuthorizeRequest, AuthorizeResult, REASON


class Facilitator:
    """The law. Only this object may call a Tap."""

    def __init__(
        self,
        store: UnderwriteStore,
        mandates: MandateService,
        envelopes: EnvelopeService,
        tap: Tap,
    ) -> None:
        self.store = store
        self.mandates = mandates
        self.envelopes = envelopes
        self.tap = tap

    def authorize(self, req: AuthorizeRequest) -> AuthorizeResult:
        env = self.store.get_envelope(req.envelope_id)
        if env is None or env.status != "active":
            reason = REASON.DENY_NO_MANDATE if env is None else REASON.DENY_CAP_EXCEEDED
            self.store.audit("spend.denied", (env.agent_id if env else "unknown"), {"reason": reason, **req.__dict__}, envelope_id=req.envelope_id)
            return AuthorizeResult(False, reason, env.remaining_usd if env else "0", envelope_status=env.status if env else "missing")

        mandate = self.store.get_mandate(env.mandate_id)
        self.store.audit("spend.attempted", env.agent_id, req.__dict__, env.mandate_id, env.envelope_id)

        reason = (
            Policy.ttl(env)
            or Policy.mandate_live(mandate)
            or (Policy.kill(mandate) if mandate else REASON.DENY_NO_MANDATE)
            or Policy.amount(mandate, env, req.amount_usd)
            or Policy.payee(mandate, env, req.payee)
            or Policy.skill(mandate, env, req.skill_id)
        )
        if reason:
            self.store.audit("spend.denied", env.agent_id, {"reason": reason, **req.__dict__}, env.mandate_id, env.envelope_id)
            return AuthorizeResult(False, reason, env.remaining_usd, envelope_status=env.status)

        receipt = self.tap.transfer(
            from_agent=env.agent_id,
            to_payee=req.payee,
            amount_usd=req.amount_usd,
            asset=env.asset,
            meta={"envelope_id": env.envelope_id, "skill_id": req.skill_id},
        )
        if not receipt.ok:
            self.store.audit("spend.denied", env.agent_id, {"reason": "tap_failed", "detail": receipt.detail}, env.mandate_id, env.envelope_id)
            return AuthorizeResult(False, REASON.DENY_CAP_EXCEEDED, env.remaining_usd, envelope_status=env.status)

        env = self.envelopes.debit(env, req.amount_usd)
        self.store.audit(
            "spend.settled",
            env.agent_id,
            {"amount_usd": req.amount_usd, "payee": req.payee, "receipt": receipt.receipt_id, "remaining": env.remaining_usd},
            env.mandate_id,
            env.envelope_id,
        )
        self.store.audit("spend.allowed", env.agent_id, {"amount_usd": req.amount_usd}, env.mandate_id, env.envelope_id)
        return AuthorizeResult(True, env.reason_codes[0], env.remaining_usd, receipt.receipt_id, env.status)
