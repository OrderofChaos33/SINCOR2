"""SINCOR P23 (suite P03) — Intent Solver & Dark Pool, standalone package.

State: EXTERNAL. No solver is deployed or bonded for outside flow.
Deployment happens only inside a signed counterparty engagement.
See ENGAGEMENT.md, DEPLOY.md, SOLVER_BONDING.md and PACKAGING.md.
"""

from .intent_dark_pool import (  # noqa: F401
    ADMIN_ROLE,
    GUARDIAN_ROLE,
    MATCHER_ROLE,
    PRICE_ASSETS,
    SETTLEMENT_ASSETS,
    TREASURY,
    Batch,
    BatchVerificationError,
    CommitmentRecord,
    DarkFill,
    DarkPool,
    DarkPoolError,
    Intent,
    IntentReplayError,
    Leg,
    Match,
    Matcher,
    MinOutViolationError,
    RoutePlan,
    Settlement,
    SettlementAssetError,
    SettlementEngine,
    SplitSolver,
    UnauthorizedError,
    VenueQuote,
    match_pair,
    price_feed_for,
)
from .engagement_fees import engagement_fee, fee_breakdown  # noqa: F401

__all__ = [
    "ADMIN_ROLE", "GUARDIAN_ROLE", "MATCHER_ROLE", "PRICE_ASSETS",
    "SETTLEMENT_ASSETS", "TREASURY", "Batch", "BatchVerificationError",
    "CommitmentRecord", "DarkFill", "DarkPool", "DarkPoolError",
    "Intent", "IntentReplayError", "Leg", "Match", "Matcher",
    "MinOutViolationError", "RoutePlan", "Settlement",
    "SettlementAssetError", "SettlementEngine", "SplitSolver",
    "UnauthorizedError", "VenueQuote", "match_pair", "price_feed_for",
    "engagement_fee", "fee_breakdown",
]
__version__ = "0.1.0"
