"""P25 — Agent-Managed Portfolio.

Offchain-only portfolio layer: turns swarm TOA signals (P01..P25, P26 excluded
as the meta ranker) into per-user target portfolios under a 0.28 blended risk
budget, a 5% USDC cash floor, daily 2.5%-drift rebalancing, and a 10 bps p.a.
fee denominated in AXM/SINC. Advisory + accounting only: it computes plans and
accrues fees; it never holds user keys and never broadcasts.
"""

PARAMS = {
    "fee_bps": 10,                 # 0.10% p.a. on AUM
    "target_apr": 0.07,
    "blended_risk_cap": 0.28,      # sum(w_i * r_i) <= 0.28
    "position_cap": 0.40,          # w_i <= 0.40
    "exclusion_risk": 0.60,        # protocols with r_i > 0.60 ineligible
    "cash_floor_pct": 0.05,        # >= 5% USDC on every plan
    "min_capital_usd": 25.0,       # dust filter for positions and delta trades
    "ingest_cadence_s": 3600,
    "feed_staleness_s": 3600,      # stale -> HOLD
    "rebalance_cadence_s": 86400,
    "drift_trigger": 0.025,        # absolute drift that triggers a plan
    "settlement_batch_floor_usd": 10.0,
    "allocator_max_iterations": 10,
    "fee_denominations": ("AXM", "SINC"),
}

from . import allocator, api, fees, guards, ingestor, rebalancer, risk


__all__ = [
    "allocator",
    "api",
    "fees",
    "guards",
    "ingestor",
    "rebalancer",
    "risk",
    "PARAMS",
]

# Canonical numeric parameters (catalog + auction spec p25-agent-portfolio).
