from __future__ import annotations

import os
from dataclasses import dataclass

from .envelopes import EnvelopeService
from .facilitator import Facilitator
from .mandates import MandateService
from .store import UnderwriteStore
from .taps.bridge_mint import BridgeMintTap
from .taps.ledger_sim import LedgerSimTap
from .toa_adapter import FormulaToa, SpendUnderwriter


def build_tap(name: str | None = None):
    name = (name or os.getenv("UNDERWRITE_TAP") or "ledger_sim").strip()
    if name == "bridge_mint":
        return BridgeMintTap()
    return LedgerSimTap(vault_usd=os.getenv("UNDERWRITE_VAULT_USD", "100.00"))


@dataclass
class UnderwriteRuntime:
    store: UnderwriteStore
    mandates: MandateService
    envelopes: EnvelopeService
    facilitator: Facilitator
    tap_name: str


def boot(data_dir: str | None = None) -> UnderwriteRuntime:
    store = UnderwriteStore(data_dir or os.getenv("UNDERWRITE_DATA_DIR", "data/underwriting"))
    tap = build_tap()
    mandates = MandateService(store)
    underwriter = SpendUnderwriter(port=FormulaToa(), tap_name=getattr(tap, "name", "ledger_sim"))
    envelopes = EnvelopeService(store, underwriter)
    fac = Facilitator(store, mandates, envelopes, tap)
    return UnderwriteRuntime(store, mandates, envelopes, fac, getattr(tap, "name", "ledger_sim"))
