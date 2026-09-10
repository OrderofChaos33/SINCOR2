"""Treasury HOLD rules as numbers, not vibes.

Locked 2026-09-10. Import these constants. Do not guess at 2am.
"""
from __future__ import annotations

from sincor2.onchain.constants import TREASURY as TREASURY_ADDRESS

HOLD_LOCKED_AT = "2026-09-10"
TREASURY = TREASURY_ADDRESS

# H1 — 100% of realized platform fees → treasury
FEE_DESTINATION_PCT = 100
PLATFORM_FEE_BPS = 500

# H2 — on-chain USDC is cash runway
USDC_HOLD_PCT = 100
MORPHO_ALLOCATION_PCT = 0
LP_ALLOCATION_PCT = 0

# H3 — founder cash stays off-chain
FOUNDER_CASH_HOLD_PCT = 100
FOUNDER_LOAD_TO_CHAIN_PCT = 0

# H4 — vault intake
VAULT_INTAKE_PCT_BEFORE_CONVERSION = 0
VAULT_INTAKE_PCT_AFTER_CONVERSION = 20
CASH_RUNWAY_PCT_AFTER_CONVERSION = 80
VAULT_CASH_FLOOR_USDC = 10_000

# H5 — underwriting
UNDERWRITING_BACKING_USDC = 10_000

# H6 / H7 — movers
EXECUTE_LIVE_DEFAULT = False
HALT_FILE = "data/TREASURY_EXEC_HALT"
MOVER = "founder_signer"


def standing_order() -> dict:
    return {
        "locked_at": HOLD_LOCKED_AT,
        "treasury": TREASURY,
        "H1_fee_destination_pct": FEE_DESTINATION_PCT,
        "H1_platform_fee_bps": PLATFORM_FEE_BPS,
        "H2_usdc_hold_pct": USDC_HOLD_PCT,
        "H2_morpho_pct": MORPHO_ALLOCATION_PCT,
        "H3_founder_hold_pct": FOUNDER_CASH_HOLD_PCT,
        "H4_vault_before_conversion_pct": VAULT_INTAKE_PCT_BEFORE_CONVERSION,
        "H4_vault_after_conversion_pct": VAULT_INTAKE_PCT_AFTER_CONVERSION,
        "H4_vault_floor_usdc": VAULT_CASH_FLOOR_USDC,
        "H5_underwriting_backing_usdc": UNDERWRITING_BACKING_USDC,
        "H6_mover": MOVER,
        "H7_execute_live_default": EXECUTE_LIVE_DEFAULT,
        "H7_halt_file": HALT_FILE,
    }
