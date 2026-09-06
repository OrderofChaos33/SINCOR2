from __future__ import annotations

from datetime import datetime

from .types import REASON, IntentMandate, SpendEnvelope, money, parse_iso, utcnow


class Policy:
    """Pure checks. Return a reason code or None if allowed."""

    @staticmethod
    def mandate_live(mandate: IntentMandate | None, now: datetime | None = None) -> str | None:
        if mandate is None:
            return REASON.DENY_NO_MANDATE
        now = now or utcnow()
        if parse_iso(mandate.expires_at) <= now:
            return REASON.DENY_MANDATE_EXPIRED
        return None

    @staticmethod
    def kill(mandate: IntentMandate) -> str | None:
        if mandate.killed:
            return REASON.DENY_KILL_SWITCH
        return None

    @staticmethod
    def ttl(envelope: SpendEnvelope, now: datetime | None = None) -> str | None:
        now = now or utcnow()
        if envelope.status != "active":
            return REASON.DENY_CAP_EXCEEDED if envelope.status in {"exhausted"} else REASON.DENY_MANDATE_EXPIRED
        if parse_iso(envelope.expires_at) <= now:
            return REASON.DENY_MANDATE_EXPIRED
        return None

    @staticmethod
    def amount(mandate: IntentMandate, envelope: SpendEnvelope, amount: str) -> str | None:
        amt = money(amount)
        if amt <= 0:
            return REASON.DENY_CAP_EXCEEDED
        if amt > money(envelope.remaining_usd):
            return REASON.DENY_CAP_EXCEEDED
        if amt > money(envelope.max_tx_usd or mandate.max_single_tx_usd):
            return REASON.DENY_CAP_EXCEEDED
        if amt > money(mandate.max_single_tx_usd):
            return REASON.DENY_CAP_EXCEEDED
        return None

    @staticmethod
    def payee(mandate: IntentMandate, envelope: SpendEnvelope, payee: str) -> str | None:
        denied = set(mandate.denied_payees)
        if payee in denied:
            return REASON.DENY_UNKNOWN_PAYEE
        allow = envelope.allowlist_payees or mandate.allowed_payees
        if allow and payee not in allow:
            return REASON.DENY_UNKNOWN_PAYEE
        return None

    @staticmethod
    def skill(mandate: IntentMandate, envelope: SpendEnvelope, skill_id: str) -> str | None:
        allow = envelope.allowlist_skills or mandate.allowed_skills
        if allow and skill_id not in allow:
            return REASON.DENY_UNKNOWN_PAYEE
        return None
