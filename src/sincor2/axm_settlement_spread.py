"""P02 — AXM native settlement spread.

When a task settles in AXM, a spread of ``AXM_SETTLEMENT_SPREAD_BPS``
(default 100 bps = 1.0 % of settled value) is owed to the treasury, on
top of — and separate from — the 5 % A2A platform fee
(``a2a_integration.A2A_PLATFORM_FEE_BPS``).  The 1.0 % rate is the
researched market median for native-token settlement spreads
(pricing-research batch A, 2026-11-16) and is RATIFICATION-PENDING at
the founder's 2026-11-17 session, so the rate lives in exactly one
place: the config constant below, overridable via the
``SINCOR_AXM_SETTLEMENT_SPREAD_BPS`` env var.  No arithmetic anywhere
hard-codes it.

Scope: the spread prices the AXM leg only.  USDC/ETH settlements (the
Lane 3 x402 path) are never annotated and never charged by this module:
``annotate_settlement_record`` returns non-AXM records unchanged.

This module computes and annotates only — like the treasury inflow
ledger, it never moves funds.  The spread is recorded on settlement
records (the proof-of-settlement statement and the realized-fee record
returned at the A2A settlement points) as an obligation, the same way
the statement records the pending fee-conversion obligation.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from sincor2.treasury_settlement import TREASURY as SPREAD_TREASURY_WALLET

# Single source of truth for the P02 rate.  Default 100 bps = 1.0 %.
AXM_SETTLEMENT_SPREAD_BPS = int(os.getenv("SINCOR_AXM_SETTLEMENT_SPREAD_BPS", "100"))

_BPS_DENOM = 10_000

# Assets the spread applies to — mirrors a2a_integration.ALLOWED_A2A_ASSETS.
AXM_ASSETS = frozenset({"AXM", "AXIOM"})


def is_axm_asset(asset: Any) -> bool:
    """True when a settlement denomination is AXM (AXIOM)."""
    if not asset:
        return False
    return str(asset).upper() in AXM_ASSETS


def compute_spread_wei(amount_wei: int, spread_bps: Optional[int] = None) -> int:
    """Spread owed on an AXM settlement of ``amount_wei``, in wei.

    Integer floor math, mirroring ``a2a_integration._compute_platform_fee_wei``:
    negative amounts clamp to zero and the result never exceeds the
    settled amount at sane rates.  ``spread_bps`` defaults to the config
    constant; pass it explicitly only in tests / what-if computations.
    """
    bps = AXM_SETTLEMENT_SPREAD_BPS if spread_bps is None else int(spread_bps)
    amount = amount_wei if amount_wei >= 0 else 0
    if bps <= 0:
        return 0
    return (amount * bps) // _BPS_DENOM


def spread_fields(*, asset: Any, settled_amount_wei: int) -> Dict[str, int]:
    """The two settlement-record fields for one settlement.

    AXM settlements: ``spread_bps_applied`` is the configured rate and
    ``spread_amount`` the spread owed, in wei.  Non-AXM settlements:
    both zero — the spread never applies outside the AXM leg.
    """
    if not is_axm_asset(asset):
        return {"spread_bps_applied": 0, "spread_amount": 0}
    return {
        "spread_bps_applied": AXM_SETTLEMENT_SPREAD_BPS,
        "spread_amount": compute_spread_wei(settled_amount_wei),
    }


def annotate_settlement_record(
    record: Dict[str, Any], *, asset: Any, settled_amount_wei: int
) -> Dict[str, Any]:
    """Return ``record`` with the P02 spread fields added for AXM settlements.

    Non-AXM records are returned unchanged (a shallow copy — same keys,
    same values), so USDC/ETH settlement record shapes are untouched.
    The input dict is never mutated.
    """
    annotated = dict(record)
    if is_axm_asset(asset):
        annotated.update(spread_fields(asset=asset, settled_amount_wei=settled_amount_wei))
    return annotated
