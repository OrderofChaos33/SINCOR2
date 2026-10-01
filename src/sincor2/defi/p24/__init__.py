"""P24 — SocialFi Revenue Share (creator tokens + DeFi primitives).

LIVE-BLOCKED posture: this module never emits live intents; any live-intent
path raises LiveBlocked. Testnets, forks, local chains only.

Mechanics: each verified creator gets a bonding-curve token (fixed 1e9
supply, 50% market curve / 50% 5-year linear vesting). Every trade pays a 10%
fee split conservation-exact three ways — creator 4940 / platform 4940 /
treasury 120 bps of the fee (12 bps of trade value) — with dust to treasury.
Revenue accrues in 7-day epochs with snapshot accounting and pull claims.
The content-policy guard (no_price_talk) screens issuance metadata.
The Python mirrors the onchain/src/p24/ contract semantics.
"""

PARAMS = {
    "fee_bps": 12,                 # treasury cut: 12 bps of trade value
    "trade_fee_bps": 1000,         # 10% per buy/sell
    "creator_share_bps": 4940,     # of the fee
    "platform_share_bps": 4940,    # of the fee
    "treasury_share_bps": 120,     # of the fee (= 12 bps of trade value)
    "creator_floor_bps": 4000,     # creator share of fee can never go below 40%
    "token_supply": 1_000_000_000, # 1e9 fixed, immutable at issuance
    "curve_share": 0.50,           # 50% to the bonding curve
    "vesting_days": 1825,          # 5-year linear vesting for creator half
    "graduation_mcap_usd": 69_000.0,
    "target_apr": 0.06,            # staking primitive target
    "risk_score_bound": 0.33,
    "max_alloc_pct": 0.10,         # per creator token in the collateral sleeve
    "min_capital_usd": 50.0,
    "epoch_s": 604800,             # 7-day revenue epochs
    "staking_unbonding_s": 259200,  # 72 h
    "max_ltv": 0.50,
    "staking_rewards_pct_of_platform_leg": 0.10,
    "settlement_batch_floor_usd": 10.0,  # treasury legs settle in >= $10 batches
    "live_blocked": True,
}

from . import accrual, curve, factory, fees, live_block, onboarding, policy, primitives, screener, split

from .live_block import LiveBlocked
from .screener import ContentScreener, DeferredScreener, DenyListScreener, ScreenerDenied

__all__ = [
    "ContentScreener",
    "DeferredScreener",
    "DenyListScreener",
    "LiveBlocked",
    "ScreenerDenied",
    "accrual",
    "curve",
    "factory",
    "fees",
    "live_block",
    "onboarding",
    "policy",
    "primitives",
    "screener",
    "split",
    "PARAMS",
]

# Canonical numeric parameters (catalog + auction spec p24-socialfi-revenue).
