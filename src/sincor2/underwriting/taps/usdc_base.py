from __future__ import annotations

from ..types import new_id
from .base import TapReceipt

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"


class UsdcBaseTap:
    """Phase 2 placeholder. No RPC in v1."""

    name = "usdc_base"

    def transfer(self, *, from_agent: str, to_payee: str, amount_usd: str, asset: str, meta: dict) -> TapReceipt:
        return TapReceipt(False, new_id(), self.name, amount_usd, "phase2_not_wired")

    def clawback(self, *, envelope_id: str, amount_usd: str, asset: str) -> TapReceipt:
        return TapReceipt(False, new_id(), self.name, amount_usd, "phase2_not_wired")
