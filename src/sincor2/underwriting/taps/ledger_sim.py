from __future__ import annotations

from decimal import Decimal

from ..types import money, money_str, new_id
from .base import TapReceipt


class LedgerSimTap:
    name = "ledger_sim"

    def __init__(self, vault_usd: str = "100.00") -> None:
        self.vault = money(vault_usd)
        self.balances: dict[str, Decimal] = {"vault": self.vault}

    def _credit(self, acct: str, amt: Decimal) -> None:
        self.balances[acct] = money(self.balances.get(acct, Decimal("0")) + amt)

    def transfer(self, *, from_agent: str, to_payee: str, amount_usd: str, asset: str, meta: dict) -> TapReceipt:
        amt = money(amount_usd)
        if self.balances.get("vault", Decimal("0")) < amt:
            return TapReceipt(False, new_id(), self.name, amount_usd, "vault_empty")
        self.balances["vault"] -= amt
        self._credit(to_payee, amt)
        return TapReceipt(True, new_id(), self.name, money_str(amt), f"{from_agent}->{to_payee}")

    def clawback(self, *, envelope_id: str, amount_usd: str, asset: str) -> TapReceipt:
        amt = money(amount_usd)
        self._credit("vault", amt)
        return TapReceipt(True, new_id(), self.name, money_str(amt), f"clawback:{envelope_id}")
