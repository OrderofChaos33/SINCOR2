"""Identity of the SINCOR Speculative DeFi arm.

One arm, 26 builds, explicitly UNDER TESTING. Nothing in this arm touches
mainnet funds until a product reaches the ``catalog`` lifecycle stage; the
Protocol OS ``dry_run`` default is load-bearing, not advisory.
"""

from __future__ import annotations

from typing import Dict

from .catalog import PROTOCOLS, TREASURY

DIVISION_ID = "speculative-defi"
DIVISION_NAME = "SINCOR Speculative DeFi"
DIVISION_STATUS = "under-testing"

# Standing treasury policy: 5% platform fee to treasury, converted to
# USDC/WETH before deposit. No burn. Deflationary mechanics deferred.
TREASURY_FEE_PCT = 5

# Current treasury risk policy, mirrored from the Protocol OS runtime contract.
RISK_BUDGET = 0.30

PRIME_DIRECTIVE = (
    "Nothing in the speculative-defi arm touches mainnet funds until the "
    "product reaches the 'catalog' lifecycle stage. The Protocol OS dry_run "
    "default is load-bearing: executed is always False, signing stays outside, "
    "and no environment flag may enable broadcasting from this arm."
)

# Swarm N builds protocol N, 1:1. The roster is derived from the catalog so it
# cannot drift from the assignment list.
SWARM_TO_PROTOCOL: Dict[int, str] = {p.swarm_id: p.protocol_id for p in PROTOCOLS}
SWARM_TO_SKU: Dict[int, str] = {}  # filled by products.py after SKU minting


def describe() -> Dict[str, object]:
    return {
        "id": DIVISION_ID,
        "name": DIVISION_NAME,
        "status": DIVISION_STATUS,
        "treasury": TREASURY,
        "treasury_fee_pct": TREASURY_FEE_PCT,
        "risk_budget": RISK_BUDGET,
        "prime_directive": PRIME_DIRECTIVE,
        "protocol_count": len(PROTOCOLS),
    }
