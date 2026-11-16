"""SINCOR P25 — DeFi Risk Mutual (suite name P05), standalone package.

Extracted verbatim from ``src/sincor2/defi/insurance_mutual.py`` in the
SINCOR2 platform repo (PACK-1 of the fulfillment sprint). The module is a
Python reference simulation of an on-chain risk-mutual / coverage pool:
underwriting from risk scores, a stablecoin capital pool with a 130%
reserve-ratio gate, attestation-gated claims, and member governance.

State: EXTERNAL. No mutual is capitalized or open. This package is not
deployed and does not write cover. See ENGAGEMENT.md, CAPITALIZATION.md,
DEPLOY.md, and PACKAGING.md in the package root.
"""

from .insurance_mutual import (
    ASSESSOR_BOND_UNITS,
    ASSESSOR_ROLE,
    DRY_RUN,
    FEE_BPS,
    GUARDIAN_ROLE,
    MAX_ALLOC_PCT,
    MIN_CAPITAL_WEI,
    PREMIUM_BASE_BPS,
    PREMIUM_CAP_BPS,
    RESERVE_RATIO_DEN,
    RESERVE_RATIO_NUM,
    TREASURY,
    UNDERWRITER_ROLE,
    AssessorRegistry,
    Attestation,
    Claim,
    ClaimRuleError,
    Cover,
    Governance,
    ListingProposal,
    LiveBlockedError,
    MutualError,
    MutualPool,
    QuorumError,
    ReserveBreachError,
    RiskInputs,
    RiskScore,
    StaleScoreError,
    TimelockError,
    UnauthorizedError,
    Underwriter,
    commitment_id,
)

__version__ = "0.1.0"

__all__ = [
    "ASSESSOR_BOND_UNITS",
    "ASSESSOR_ROLE",
    "DRY_RUN",
    "FEE_BPS",
    "GUARDIAN_ROLE",
    "MAX_ALLOC_PCT",
    "MIN_CAPITAL_WEI",
    "PREMIUM_BASE_BPS",
    "PREMIUM_CAP_BPS",
    "RESERVE_RATIO_DEN",
    "RESERVE_RATIO_NUM",
    "TREASURY",
    "UNDERWRITER_ROLE",
    "AssessorRegistry",
    "Attestation",
    "Claim",
    "ClaimRuleError",
    "Cover",
    "Governance",
    "ListingProposal",
    "LiveBlockedError",
    "MutualError",
    "MutualPool",
    "QuorumError",
    "ReserveBreachError",
    "RiskInputs",
    "RiskScore",
    "StaleScoreError",
    "TimelockError",
    "UnauthorizedError",
    "Underwriter",
    "commitment_id",
]
