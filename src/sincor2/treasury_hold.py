"""Treasury policy numbers. HOLD lifted 2026-09-10 20:54 CDT by founder."""
from __future__ import annotations

from sincor2.onchain.constants import TREASURY as TREASURY_ADDRESS

HOLD_LOCKED_AT = "2026-09-10"
HOLD_LIFTED_AT = "2026-09-10T20:54:00-05:00"
HOLD_ACTIVE = False
TREASURY = TREASURY_ADDRESS

FEE_DESTINATION_PCT = 100
PLATFORM_FEE_BPS = 500

USDC_HOLD_PCT = 0
MORPHO_ALLOCATION_PCT = 0
LP_ALLOCATION_PCT = 0

FOUNDER_CASH_HOLD_PCT = 0
FOUNDER_LOAD_TO_CHAIN_PCT = 0

VAULT_INTAKE_PCT_BEFORE_CONVERSION = 0
VAULT_INTAKE_PCT_AFTER_CONVERSION = 20
CASH_RUNWAY_PCT_AFTER_CONVERSION = 80
VAULT_CASH_FLOOR_USDC = 10_000
UNDERWRITING_BACKING_USDC = 10_000

# Founder authorized live. Railway still wins if EXECUTE_LIVE is set.
EXECUTE_LIVE_DEFAULT = True
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
        "H1_platform_fee_bps": PLATFORM_FEE_BPS,
        "H7_execute_live_default": EXECUTE_LIVE_DEFAULT,
        "axm_settlement_live": AXM_SETTLEMENT_AUTHORIZED_LIVE,
        "polyclaw_authorized_live": POLYCLAW_AUTHORIZED_LIVE,
    }
