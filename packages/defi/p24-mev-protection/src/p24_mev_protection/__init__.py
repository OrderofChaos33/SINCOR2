"""P24 — MEV Protection & Capture (suite name P04), standalone package.

Extracted from ``src/sincor2/defi/mev_capture.py`` in the SINCOR2
platform repo (see PACKAGING.md for provenance and the extraction
record). MEV protection module: per-order protection policy, sandwich /
backrun *detection* over swap flow, signer deny-list, and dry-run-only
submission gating, as a Python reference build.

Commercial scope is protection / integration work only. This package
makes no MEV capture revenue claims: SINCOR's own backrun lane was
separately measured and closed, and nothing in this package is offered
as a capture-revenue capability.

Exists as a platform module; not a standalone product.
State: EXTERNAL — a buyer/counterparty and a signed engagement are
required before any deployment (see ENGAGEMENT.md).
"""

from .mev_capture import (
    BID_SHADING_MULT,
    BIDDER_ROLE,
    DRY_RUN,
    ESTIMATE_TOLERANCE,
    FEE_BPS,
    FLOW_THRESHOLD_USD,
    GAS_BUDGET_PER_SWAP,
    GUARDIAN_ROLE,
    IMPACT_FLAG_BPS,
    MIN_FLOAT_USD,
    SLIPPAGE_GUARDRAIL_BPS,
    TREASURY,
    AccessControl,
    BackrunFlag,
    BidIntent,
    Bidder,
    CaptureLedger,
    CaptureRecord,
    FlowMeter,
    GasMeter,
    LiveBlockedError,
    LiveGate,
    MEVDetector,
    MEVError,
    ProtectedOrder,
    ProtectionPolicy,
    ProtectionRevert,
    SandwichFlag,
    SignerRegistry,
    SwapEvent,
    ThresholdOutOfBoundsError,
    TreasuryRouter,
    TreasurySignerError,
    commitment_id,
)

__all__ = [
    "BID_SHADING_MULT",
    "BIDDER_ROLE",
    "DRY_RUN",
    "ESTIMATE_TOLERANCE",
    "FEE_BPS",
    "FLOW_THRESHOLD_USD",
    "GAS_BUDGET_PER_SWAP",
    "GUARDIAN_ROLE",
    "IMPACT_FLAG_BPS",
    "MIN_FLOAT_USD",
    "SLIPPAGE_GUARDRAIL_BPS",
    "TREASURY",
    "AccessControl",
    "BackrunFlag",
    "BidIntent",
    "Bidder",
    "CaptureLedger",
    "CaptureRecord",
    "FlowMeter",
    "GasMeter",
    "LiveBlockedError",
    "LiveGate",
    "MEVDetector",
    "MEVError",
    "ProtectedOrder",
    "ProtectionPolicy",
    "ProtectionRevert",
    "SandwichFlag",
    "SignerRegistry",
    "SwapEvent",
    "ThresholdOutOfBoundsError",
    "TreasuryRouter",
    "TreasurySignerError",
    "commitment_id",
]

__version__ = "0.1.0"
