"""P22 — Stablecoin Yield Maximizer.

Offchain allocation swarm for USDC/USDT on Base (chain id 8453). Scans
allowlisted lending venues, optimizes allocation under hard caps, accrues a
10 bps fee on realized yield to the canonical treasury. Ships dry-run by
default; live execution needs an explicit operator flag (standing directive).
"""

PARAMS = {
    "fee_bps": 10,                 # 0.10% of realized yield (not AUM)
    "target_apr": 0.058,
    "risk_score_bound": 0.22,      # blended venue risk must stay <= 0.22
    "max_alloc_pct": 0.50,         # w_i <= 0.50 per venue
    "min_capital_usd": 10.0,       # positions below $10 are not opened
    "depth_cap": 0.10,             # w_i * capital <= 10% of venue depth
    "chain_id": 8453,              # Base
    "allowed_assets": ("USDC", "USDT"),
    "scan_cadence_s": 300,
    "quote_staleness_s": 600,
    "twap_window_s": 14400,        # 4-hour TWAP on quoted APR
    "rebalance_delta_bps": 50,     # rotate only if ΔAPR >= 50 bps
    "rebalance_cooldown_s": 14400, # >= 4 h since last rotation
    "gas_vs_gain_ratio": 0.25,     # gas < 25% of annualized yield gain
    "tie_break_bps": 5,            # prefer Morpho when |ΔAPR| <= 5 bps
    "settlement_batch_floor_usd": 10.0,
}

from . import adapters, fees, gate, optimizer, scanner, twap

from .interfaces import PoolQuote, StableVenueAdapter

__all__ = [
    "PoolQuote",
    "StableVenueAdapter",
    "adapters",
    "fees",
    "gate",
    "optimizer",
    "scanner",
    "twap",
    "PARAMS",
]

# Canonical numeric parameters (catalog + auction spec p22-stablecoin-yield).
