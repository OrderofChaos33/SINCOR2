from __future__ import annotations

from ..types import new_id
from .base import TapReceipt


class BridgeMintTap:
    """Stub. Must not perform HTTP. Partnership keys unlock a real adapter later."""

    name = "bridge_mint"

    def describe(self) -> dict:
        return {
            "status": "stub",
            "would_call": "POST https://api.bridge.xyz/v0/transfers",
            "note": "closed-loop mint on_behalf_of CIP customer — not implemented",
        }

    def transfer(self, *, from_agent: str, to_payee: str, amount_usd: str, asset: str, meta: dict) -> TapReceipt:
        return TapReceipt(False, new_id(), self.name, amount_usd, "stub_refuses_transfer")

    def clawback(self, *, envelope_id: str, amount_usd: str, asset: str) -> TapReceipt:
        return TapReceipt(False, new_id(), self.name, amount_usd, "stub_refuses_clawback")
