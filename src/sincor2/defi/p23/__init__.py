"""P23 — NFT-Fi Liquidity Pools (fractionalized NFT yield).

LIVE-BLOCKED posture: this module never emits live intents. Any live-intent
code path raises LiveBlocked. Everything runs on testnets, forks, and local
chains only. The Python here mirrors the onchain/src/p23/ contract semantics
(whitelist registry, utilization cap, fractional vault, pricing oracle) so the
state machine is testable without a node; the Solidity is the deployable
artifact.
"""

PARAMS = {
    "fee_bps": 20,                 # 0.20% of realized per-pool yield per epoch
    "target_apr": 0.15,
    "risk_score_bound": 0.65,
    "max_alloc_pct": 0.08,         # per collection, of managed capital
    "min_capital_usd": 400.0,
    "utilization_cap": 0.75,       # lending sleeve / pool value
    "max_ltv": 0.60,
    "liquidation_threshold": 0.75, # of floor TWAP
    "borrower_grace_s": 86400,     # 24 h
    "oracle_twap_s": 604800,       # 7-day TWAP
    "oracle_staleness_s": 86400,   # 24 h -> deposits freeze
    "oracle_max_move": 0.25,       # per-update clamp, 25%
    "oracle_min_sources": 2,
    "whitelist_timelock_s": 172800,  # 48 h
    "reserve_sleeve_pct": 0.05,    # backstop, untouchable by lending cap
    "deposit_fee_bps": 50,         # 0.5% anti-churn fee
    "epoch_s": 604800,             # 7-day accrual epochs
    "minimum_shares": 1000,        # ERC-4626 inflation-attack guard (burn addr)
    "live_blocked": True,
}

from . import fees, live_block, manager, oracle, registry, vault

from .live_block import LiveBlocked

__all__ = [
    "LiveBlocked",
    "fees",
    "live_block",
    "manager",
    "oracle",
    "registry",
    "vault",
    "PARAMS",
]

# Canonical numeric parameters (catalog + auction spec p23-nftfi-pools).
