"""A2A quote response builder — explicit Treasury / burn fee split.

Production contract for POST /api/a2a/quote.

Fee rules (canonical, do not diverge):
  • Platform fee on settlement: 5 % (500 bps) → Treasury
    (see marketplace/settlement.py PLATFORM_FEE_BPS)
  • AXM receipt burn mechanics: 50 % burned to 0x…dEaD, 50 % → Treasury
    (see a2a_integration.record_axm_receipt)
  • SINC is primary task payment token; AXM retained for legacy
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

TREASURY_WALLET = os.getenv("TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac")
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"
AXIOM_CONTRACT = os.getenv("AXIOM_CONTRACT_ADDRESS", "0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822")
SINC_CONTRACT = os.getenv("SINC_CONTRACT_ADDRESS", "0x9C8cd8d3961F445D653713dE65C6578bE11668e7")
CHAIN_ID = int(os.getenv("BASE_CHAIN_ID", "8453"))
A2A_PRIMARY_TOKEN = os.getenv("A2A_PRIMARY_TOKEN", "SINC").upper()
SINC_PRICE_PER_TASK = int(os.getenv("SINC_PRICE_PER_TASK", "1"))
AXM_PRICE_PER_TASK = int(os.getenv("AXM_PRICE_PER_TASK", str(1 * 10**18)))

PLATFORM_FEE_BPS = 500
AXM_BURN_BPS = 5_000
AXM_TREASURY_BPS = 5_000


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
            "treasury_share_on_axm_receipt_pct": "50%",
            "burn_share_on_axm_receipt_bps": AXM_BURN_BPS,
            "burn_share_on_axm_receipt_pct": "50%",
            "treasury": TREASURY_WALLET,
            "burn_to": DEAD_ADDRESS,
            "note": (
                "Platform fee (5%) is deducted from every confirmed settlement "
                "and routed to treasury. On AXM receipts, 50% is burned to "
                "0x…dEaD and 50% is routed to treasury (independent of platform fee)."
            ),
        },
        "note": (
            f"Pay {SINC_PRICE_PER_TASK} SINC (or {axm_display:.4f} AXM for legacy) "
            f"to pay_to on Base (chain {CHAIN_ID}), then include the tx hash in "
            f"your tasks/send or message/send request."
        ),
    }
