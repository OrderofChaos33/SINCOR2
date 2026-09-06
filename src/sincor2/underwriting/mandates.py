from __future__ import annotations

from datetime import timedelta

from .store import UnderwriteStore
from .types import IntentMandate, iso, new_id, utcnow


class MandateService:
    def __init__(self, store: UnderwriteStore) -> None:
        self.store = store

    def issue(
        self,
        *,
        operator_id: str,
        controller_wallet: str,
        agent_id: str,
        max_notional_usd: str,
        max_single_tx_usd: str,
        hours: float = 2.0,
        allowed_skills: list[str] | None = None,
        allowed_payees: list[str] | None = None,
        asset: str = "USDC",
        chain_id: int = 8453,
        legal_customer_id: str | None = None,
    ) -> IntentMandate:
        now = utcnow()
        m = IntentMandate(
            mandate_id=new_id(),
            issued_at=iso(now),
            expires_at=iso(now + timedelta(hours=hours)),
            operator_id=operator_id,
            controller_wallet=controller_wallet,
            agent_id=agent_id,
            asset=asset,
            chain_id=chain_id,
            max_notional_usd=max_notional_usd,
            max_single_tx_usd=max_single_tx_usd,
            allowed_skills=allowed_skills or [],
            allowed_payees=allowed_payees or [],
            legal_customer_id=legal_customer_id,
        )
        self.store.write_mandate(m)
        self.store.audit(
            "mandate.issued",
            agent_id,
            {"max_notional_usd": max_notional_usd, "max_single_tx_usd": max_single_tx_usd},
            mandate_id=m.mandate_id,
        )
        return m

    def revoke(self, mandate_id: str, by: str) -> IntentMandate:
        m = self.store.get_mandate(mandate_id)
        if m is None:
            raise KeyError(mandate_id)
        m.killed = True
        self.store.write_mandate(m)
        self.store.audit("mandate.revoked", m.agent_id, {"by": by}, mandate_id=m.mandate_id)
        self.store.audit("kill.triggered", m.agent_id, {"by": by, "scope": "mandate"}, mandate_id=m.mandate_id)
        return m

    def active_for(self, agent_id: str) -> IntentMandate | None:
        return self.store.active_mandate_for(agent_id)
