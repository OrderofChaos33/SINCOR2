"""A2A quote response builder — explicit Treasury fee split (no burn).

Helper for wiring the existing POST /api/a2a/quote route.

Fee rules (canonical, do not diverge — founder CONFORM verdict 2026-11-16):
  • Platform fee on settlement: 5 % (500 bps) → Treasury, 0 % burned
    (see marketplace/settlement.py PLATFORM_FEE_BPS)
  • AXM receipts: no burn — the full platform fee routes to Treasury
    (see a2a_integration.record_axm_receipt, locked fee policy 2026-09-26).
    The legacy 50 % burn split is retired everywhere; the burn fields
    below remain in the payload shape, always zero.
  • SINC is primary task payment token; AXM retained for legacy
"""

import os
from typing import Any, Dict, Optional

from sincor2.onchain.constants import (
    AXIOM_TOKEN,
    SINC_TOKEN,
    TREASURY,
    resolve_address,
)

# Canonical live pointers — env may override, but stale/malformed overrides
# fall back to canonical so a forgotten .env cannot resurrect retired
# contracts (see sincor2.onchain.constants.resolve_address).
TREASURY_WALLET = resolve_address("TREASURY_ADDRESS", TREASURY)
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"
AXIOM_CONTRACT = resolve_address("AXIOM_CONTRACT_ADDRESS", AXIOM_TOKEN)
SINC_CONTRACT = resolve_address("SINC_CONTRACT_ADDRESS", SINC_TOKEN)
CHAIN_ID = int(os.getenv("BASE_CHAIN_ID", "8453"))
A2A_PRIMARY_TOKEN = os.getenv("A2A_PRIMARY_TOKEN", "SINC").upper()
SINC_PRICE_PER_TASK = int(os.getenv("SINC_PRICE_PER_TASK", "1"))
AXM_PRICE_PER_TASK = int(os.getenv("AXM_PRICE_PER_TASK", str(1 * 10**18)))

PLATFORM_FEE_BPS = 500
# Founder CONFORM canon (2026-11-16): no burn, ever. AXM_BURN_BPS is a
# hard-coded 0 — not config-readable — so no config edit can silently
# reactivate a burn. The full platform fee routes to Treasury.
AXM_BURN_BPS = 0
AXM_TREASURY_BPS = 10_000


def build_quote_response(skill_id: str, skill_name: Optional[str] = None) -> Dict[str, Any]:
    """Return the canonical A2A quote payload with explicit fee_split."""
    axm_display = AXM_PRICE_PER_TASK / 10**18
    return {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "sinc_amount": SINC_PRICE_PER_TASK,
        "sinc_contract": SINC_CONTRACT,
        "axm_price_wei": str(AXM_PRICE_PER_TASK),
        "axm_price_display": f"{axm_display:.4f} AXM",
        "axiom_contract": AXIOM_CONTRACT,
        "primary_token": A2A_PRIMARY_TOKEN,
        "pay_to": TREASURY_WALLET,
        "chain_id": CHAIN_ID,
        "fee_split": {
            "platform_fee_bps": PLATFORM_FEE_BPS,
            "platform_fee_pct": f"{PLATFORM_FEE_BPS / 100:.2f}%",
            "treasury_share_on_axm_receipt_bps": AXM_TREASURY_BPS,
            "treasury_share_on_axm_receipt_pct": "100%",
            "burn_share_on_axm_receipt_bps": AXM_BURN_BPS,
            "burn_share_on_axm_receipt_pct": "0%",
            "treasury": TREASURY_WALLET,
            "burn_to": DEAD_ADDRESS,
            "note": (
                "Platform fee (5%) is deducted from every confirmed settlement "
                "and routed to treasury. No burn: on AXM receipts the full "
                "platform fee is routed to treasury and 0% is burned "
                "(founder CONFORM canon, 2026-11-16)."
            ),
        },
        "note": (
            f"Pay {SINC_PRICE_PER_TASK} SINC (or {axm_display:.4f} AXM for legacy) "
            f"to pay_to on Base (chain {CHAIN_ID}), then include the tx hash in "
            f"your tasks/send or message/send request."
        ),
    }
