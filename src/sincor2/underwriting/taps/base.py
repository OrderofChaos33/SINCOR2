from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class TapReceipt:
    ok: bool
    receipt_id: str
    tap: str
    amount_usd: str
    detail: str = ""


class Tap(Protocol):
    name: str

    def transfer(self, *, from_agent: str, to_payee: str, amount_usd: str, asset: str, meta: dict) -> TapReceipt: ...

    def clawback(self, *, envelope_id: str, amount_usd: str, asset: str) -> TapReceipt: ...
