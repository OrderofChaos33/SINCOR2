"""Treasury policy numbers. HOLD lifted 2026-09-10 20:54 CDT by founder."""
from __future__ import annotations

from sincor2.onchain.constants import TREASURY as TREASURY_ADDRESS

HOLD_LOCKED_AT = "2026-09-10"
HOLD_LIFTED_AT = "2026-09-10T20:54:00-05:00"
HOLD_ACTIVE = False
TREASURY = TREASURY_ADDRESS

# H1 — realized platform fees still route to treasury
FEE_DESTINATION_PCT = 100
PLATFORM_FEE_BPS = 500

# H2 — reserve freeze revoked; allocations are operator-gated, not auto-deploy
USDC_HOLD_PCT = 0
MORPHO_ALLOCATION_PCT = 0
LP_ALLOCATION_PCT = 0

# H3 — founder cash policy is operator-owned after lift
FOUNDER_CASH_HOLD_PCT = 0
FOUNDER_LOAD_TO_CHAIN_PCT = 0

# H4 — vault still not auto-fed from this module
VAULT_INTAKE_PCT_BEFORE_CONVERSION = 0
VAULT_INTAKE_PCT_AFTER_CONVERSION = 20
CASH_RUNWAY_PCT_AFTER_CONVERSION = 80
VAULT_CASH_FLOOR_USDC = 10_000

# H5 — underwriting floor kept as a warning number, not a hard lock
UNDERWRITING_BACKING_USDC = 10_000

# H6 / H7 — live still requires env. Do not default EXECUTE_LIVE on in git.
EXECUTE_LIVE_DEFAULT = False
HALT_FILE = "data/TREASURY_EXEC_HALT"
MOVER = "founder_signer"

AXM_SETTLEMENT_AUTHORIZED_LIVE = True
POLYCLAW_AUTHORIZED_LIVE = True


def standing_order() -> dict:
    return {
        "hold_active": HOLD_ACTIVE,
        "locked_at": HOLD_LOCKED_AT,
        "lifted_at": HOLD_LIFTED_AT,
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
        "axm_settlement_live": AXM_SETTLEMENT_AUTHORIZED_LIVE,
        "polyclaw_authorized_live": POLYCLAW_AUTHORIZED_LIVE,
    }
