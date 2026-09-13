"""Canonical registry of the 26 DeFi swarm protocols.

This is the assignment list from docs/DEFI_SWARM_EXPANSION_PLAN.md, encoded
as executable specs — not markdown. Every swarm maps 1:1 to a protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
FEE_BPS_DEFAULT = 10  # 0.10% conceptual protocol cut to treasury


@dataclass(frozen=True)
class ProtocolSpec:
    swarm_id: int
    protocol_id: str
    name: str
    category: str
    risk_score: float          # 0..1
    target_apr: float          # expected gross APR at full allocation
    min_capital_usd: float
    max_alloc_pct: float       # cap of a single tick's capital
    fee_bps: int
    live_blocked: bool         # True = never emit live intents from this module
    gates: Tuple[str, ...]
    description: str


PROTOCOLS: List[ProtocolSpec] = [
    ProtocolSpec(1, "P01_YIELD_AGG", "Yield Aggregator Vault", "yield", 0.18, 0.062, 1.0, 1.00, 10, False,
                 ("dry_run_default", "risk_budget", "single_strategy_cap"),
                 "Agent rebalance across Morpho / cash / shared vault. Wraps yield_aggregator."),
    ProtocolSpec(2, "P02_CLMM", "Concentrated Liquidity Manager", "liquidity", 0.42, 0.14, 50.0, 0.35, 15, True,
                 ("vol_band", "tick_distance", "dry_run_default"),
                 "V4/CLMM range width from realized vol. No live LP without auditor."),
    ProtocolSpec(3, "P03_INTENT_DARK", "Intent Solver & Dark Pool", "execution", 0.28, 0.04, 25.0, 0.50, 8, True,
                 ("min_out", "split_threshold", "axm_only_settlement"),
                 "CoW/Renegade-style split for SINC self-funding. Settlement AXM/USDC only."),
    ProtocolSpec(4, "P04_MEV", "MEV Protection & Capture", "mev", 0.55, 0.09, 100.0, 0.20, 20, True,
                 ("flow_threshold", "no_treasury_key"),
                 "Capture estimate from hook flow. Never uses treasury EOA."),
    ProtocolSpec(5, "P05_INSURANCE", "DeFi Risk Mutual", "insurance", 0.48, 0.11, 200.0, 0.15, 25, True,
                 ("reserve_ratio", "claim_window"),
                 "Premium from risk score. Claims require SINAX attestation stub."),
    ProtocolSpec(6, "P06_PERPS", "Perp DEX Hedging Swarm", "derivatives", 0.62, 0.18, 250.0, 0.15, 12, True,
                 ("delta_band", "funding_sign", "liq_buffer"),
                 "Delta-neutral hedge sizing. Blocked live until conversion proof."),
    ProtocolSpec(7, "P07_BRIDGE", "Cross-Chain Bridge Optimizer", "infra", 0.50, 0.05, 100.0, 0.20, 10, True,
                 ("bridge_whitelist", "slippage_cap"),
                 "Route score across allowlisted bridges. No arbitrary bridge."),
    ProtocolSpec(8, "P08_RWA", "RWA Tokenization Vaults", "rwa", 0.40, 0.08, 500.0, 0.10, 15, True,
                 ("compliance_pack", "kyc_flag"),
                 "Yield only after compliance gate. Capital stays dry-run."),
    ProtocolSpec(9, "P09_DAO_GOV", "DAO Governance Optimizer", "governance", 0.22, 0.03, 10.0, 0.25, 5, True,
                 ("quorum", "timelock"),
                 "Vote-weight simulation. No on-chain vote broadcast from swarm."),
    ProtocolSpec(10, "P10_FLASH_ARB", "Flash Loan Arbitrage Engine", "arb", 0.70, 0.22, 0.0, 0.10, 30, True,
                 ("profit_floor", "gas_ceiling"),
                 "Opportunity scan only. EXECUTE_LIVE does not enable flash loans."),
    ProtocolSpec(11, "P11_DELTA_NEUTRAL", "Delta-Neutral Yield", "yield", 0.35, 0.09, 150.0, 0.25, 12, True,
                 ("basis_sign", "funding_flip_kill"),
                 "LST carry vs perp funding. Unwind if basis negative."),
    ProtocolSpec(12, "P12_TWAMM", "TWAMM Large-Order Engine", "execution", 0.30, 0.035, 75.0, 0.40, 8, True,
                 ("slice_count", "impact_cap"),
                 "Time-weighted slice schedule. No single-block dump."),
    ProtocolSpec(13, "P13_AVS", "AVS Tranching & Restaking", "restake", 0.58, 0.13, 300.0, 0.12, 18, True,
                 ("senior_cover", "slash_oracle"),
                 "Senior/junior split from slashing risk. Junior capped."),
    ProtocolSpec(14, "P14_PREDICTION", "Prediction Market Automation", "markets", 0.60, 0.16, 25.0, 0.10, 15, False,
                 ("kelly_cap", "polyclaw_wallet_only"),
                 "Kelly-capped size. Live path is Polyclaw wallet, never treasury."),
    ProtocolSpec(15, "P15_LENDING", "Lending Protocol Optimizer", "lending", 0.32, 0.055, 25.0, 0.40, 10, False,
                 ("utilization_band", "morpho_only_live"),
                 "Morpho Gauntlet USDC is the only live-eligible venue."),
    ProtocolSpec(16, "P16_DEX_AGG", "Best-Execution DEX Aggregator", "execution", 0.26, 0.02, 10.0, 0.50, 6, True,
                 ("min_out", "venue_whitelist"),
                 "Route comparison across allowlisted Base venues."),
    ProtocolSpec(17, "P17_OPTIONS", "On-Chain Options Protocol", "derivatives", 0.52, 0.10, 200.0, 0.12, 15, True,
                 ("covered_only", "expiry_band"),
                 "Covered calls only. Naked shorts forbidden."),
    ProtocolSpec(18, "P18_STRUCTURED", "Structured Product Vaults", "structured", 0.38, 0.07, 250.0, 0.15, 12, True,
                 ("principal_floor", "cap_rate"),
                 "Principal-protected sleeve + yield sleeve."),
    ProtocolSpec(19, "P19_CREDIT", "Decentralized Credit Underwriting", "credit", 0.45, 0.12, 100.0, 0.15, 20, True,
                 ("score_floor", "concentration_cap"),
                 "On-chain score → max LTV. No unsecured book."),
    ProtocolSpec(20, "P20_COMPLIANCE", "DeFi Compliance Automation", "compliance", 0.15, 0.00, 0.0, 0.00, 0, True,
                 ("kyc_aml", "geo_block"),
                 "Gatekeeper. Produces pass/fail, not yield."),
    ProtocolSpec(21, "P21_TREASURY_DAO", "DAO Treasury Management", "treasury", 0.20, 0.04, 1.0, 0.30, 8, False,
                 ("hold_file", "execute_live_env"),
                 "Allocation report for canonical treasury. No broadcast."),
    ProtocolSpec(22, "P22_STABLE_YIELD", "Stablecoin Yield Maximizer", "yield", 0.22, 0.058, 10.0, 0.50, 10, False,
                 ("stable_only", "morpho_preferred"),
                 "USDC/USDT venues only. Prefers Morpho on Base."),
    ProtocolSpec(23, "P23_NFTFI", "NFT-Fi Liquidity Pools", "nftfi", 0.65, 0.15, 400.0, 0.08, 20, True,
                 ("collection_whitelist", "utilization_cap"),
                 "Fractional NFT yield. Live blocked."),
    ProtocolSpec(24, "P24_SOCIALFI", "SocialFi Revenue Share", "social", 0.33, 0.06, 50.0, 0.10, 12, True,
                 ("creator_split", "no_price_talk"),
                 "Creator-token fee split. Content policy intact."),
    ProtocolSpec(25, "P25_PORTFOLIO", "Agent-Managed Portfolio", "portfolio", 0.28, 0.07, 25.0, 0.40, 10, False,
                 ("risk_budget", "cash_floor"),
                 "User portfolio weights from swarm outputs. Fees AXM/SINC."),
    ProtocolSpec(26, "P26_DEFI_OS", "Self-Improving DeFi OS", "meta", 0.25, 0.00, 0.0, 1.00, 0, False,
                 ("rank_by_fee", "kill_negative_roi"),
                 "Meta layer: ranks the other 25, kills negative-ROI ticks."),
]


PROTOCOL_BY_ID: Dict[str, ProtocolSpec] = {p.protocol_id: p for p in PROTOCOLS}
PROTOCOL_BY_SWARM: Dict[int, ProtocolSpec] = {p.swarm_id: p for p in PROTOCOLS}


def assert_catalog_complete() -> None:
    if len(PROTOCOLS) != 26:
        raise RuntimeError(f"catalog size {len(PROTOCOLS)} != 26")
    ids = [p.swarm_id for p in PROTOCOLS]
    if ids != list(range(1, 27)):
        raise RuntimeError(f"swarm ids not 1..26: {ids}")
    if len({p.protocol_id for p in PROTOCOLS}) != 26:
        raise RuntimeError("duplicate protocol_id")
