"""P22 — Concentrated Liquidity Manager (suite name P02).

Standalone extraction of the SINCOR2 platform module
``src/sincor2/defi/clmm_manager.py`` (SKU ``SINCOR-DEFI-P02-CLMM``):
concentrated-liquidity position management for v3/v4-style pools —
JIT-attack detection, tick-shift response, transient JIT fees,
pro-rata proceeds distribution, and realized-vol range widths.

Status: EXTERNAL. This package exists as a platform module extraction;
it is not separately deployed. Deployment happens only inside a signed
counterparty engagement (see ENGAGEMENT.md and PACKAGING.md).

The core module is dry-run by default and hard-blocks live LP until an
auditor gate is flipped with a signed config; nothing in this package
touches a chain, a pool, or funds.
"""

from .clmm_manager import (
    COOLDOWN_BLOCKS,
    DRY_RUN,
    GUARDIAN_ROLE,
    JIT_SENSITIVITY_BPS,
    JIT_SENSITIVITY_DEN,
    JIT_FEE_CAP_BPS,
    LIVE_LP_AUDITOR_GATE,
    MANAGER_ROLE,
    MAX_SHIFT_TICKS,
    PRICE_ASSETS,
    TICK_SHIFT_STEP,
    TREASURY,
    TREASURY_CUT_BPS,
    VOL_BAND_TICKS,
    AccessControl,
    JITDetector,
    JITFlag,
    LiquidityEvent,
    LiveLPBlockedError,
    ManagedPosition,
    PassiveLP,
    PoolConfig,
    ProceedsDistributor,
    RebalanceIntent,
    TickShiftResponder,
    TransientFee,
    UnauthorizedError,
    VolRangeAgent,
    commitment_id,
    price_feed_for,
)
from .service_fees import (
    SERVICE_FEE_BPS,
    ServiceFeeLedger,
    net_to_counterparty_wei,
    service_fee_wei,
)

__all__ = [
    "AccessControl",
    "COOLDOWN_BLOCKS",
    "DRY_RUN",
    "GUARDIAN_ROLE",
    "JITDetector",
    "JITFlag",
    "JIT_SENSITIVITY_BPS",
    "JIT_SENSITIVITY_DEN",
    "JIT_FEE_CAP_BPS",
    "LIVE_LP_AUDITOR_GATE",
    "LiquidityEvent",
    "LiveLPBlockedError",
    "MANAGER_ROLE",
    "MAX_SHIFT_TICKS",
    "ManagedPosition",
    "PRICE_ASSETS",
    "PassiveLP",
    "PoolConfig",
    "ProceedsDistributor",
    "RebalanceIntent",
    "SERVICE_FEE_BPS",
    "ServiceFeeLedger",
    "TICK_SHIFT_STEP",
    "TREASURY",
    "TREASURY_CUT_BPS",
    "TickShiftResponder",
    "TransientFee",
    "UnauthorizedError",
    "VOL_BAND_TICKS",
    "VolRangeAgent",
    "commitment_id",
    "net_to_counterparty_wei",
    "price_feed_for",
    "service_fee_wei",
]

__version__ = "0.1.0"
