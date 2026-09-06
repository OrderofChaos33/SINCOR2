from __future__ import annotations

from typing import Any

from .policy import Policy
from .store import UnderwriteStore
from .toa_adapter import SpendUnderwriter
from .types import IntentMandate, SpendEnvelope, money, money_str


class EnvelopeService:
    def __init__(self, store: UnderwriteStore, underwriter: SpendUnderwriter) -> None:
        self.store = store
        self.underwriter = underwriter

    def request(
        self,
        mandate: IntentMandate,
        *,
        requested_usd: str,
        skill_id: str,
        payee: str,
        extra_context: dict[str, Any] | None = None,
    ) -> SpendEnvelope:
        self.store.audit(
            "envelope.requested",
            mandate.agent_id,
            {"requested_usd": requested_usd, "skill_id": skill_id, "payee": payee},
            mandate_id=mandate.mandate_id,
        )
        reason = Policy.mandate_live(mandate) or Policy.kill(mandate)
        if reason:
            env = self.underwriter._denied(mandate, requested_usd, skill_id, payee, reason, reason)
            self.store.write_envelope(env)
            self.store.audit("envelope.denied", mandate.agent_id, {"reason": reason}, mandate.mandate_id, env.envelope_id)
            return env

        env = self.underwriter.propose(
            mandate,
            requested_usd=requested_usd,
            skill_id=skill_id,
            payee=payee,
            extra_context=extra_context,
        )
        self.store.write_envelope(env)
        kind = "envelope.denied" if env.denied else "envelope.issued"
        self.store.audit(
            kind,
            mandate.agent_id,
            {
                "amount_usd": env.amount_usd,
                "reasons": env.reason_codes,
                "toa_run_id": env.toa_run_id,
                "rationale": env.toa_rationale,
            },
            mandate.mandate_id,
            env.envelope_id,
        )
        self.store.audit(
            "toa.run",
            mandate.agent_id,
            {
                "run_id": env.toa_run_id,
                "paths_considered": env.toa_paths_considered,
                "paths_viable": env.toa_paths_viable,
                "rationale": env.toa_rationale,
            },
            mandate.mandate_id,
            env.envelope_id,
        )
        return env

    def debit(self, env: SpendEnvelope, amount_usd: str) -> SpendEnvelope:
        remaining = money(env.remaining_usd) - money(amount_usd)
        env.remaining_usd = money_str(remaining)
        if remaining <= 0:
            env.status = "exhausted"
            env.remaining_usd = "0"
        self.store.write_envelope(env)
        return env

    def revoke(self, envelope_id: str, by: str) -> SpendEnvelope:
        env = self.store.get_envelope(envelope_id)
        if env is None:
            raise KeyError(envelope_id)
        env.status = "revoked"
        self.store.write_envelope(env)
        self.store.audit("envelope.revoked", env.agent_id, {"by": by}, env.mandate_id, env.envelope_id)
        return env
