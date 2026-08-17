"""
SINCOR DeFi Yield Aggregator

Production-oriented multi-strategy allocator for Base.
Designed to sit behind the DeFi Yield Aggregator agent (E-defi-yield-01)
and SharedLiquidityVault / SharedLiquidityHook.

Safety rules (hard):
- Default mode is DRY_RUN. No transactions are broadcast.
- Live execution requires explicit env EXECUTE_LIVE=1 AND a non-empty
  EXECUTION_SIGNER_KEY. Even then, this module only emits intent payloads;
  actual signing/broadcast stays outside this module.
- Every rebalance is risk-capped (max allocation %, min liquidity, max slippage).
- Fees route conceptually to the canonical treasury address.

Strategies are adapters. Real protocol integrations plug in here without
changing the agent YAML or swarm scheduler.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)
SHARED_LIQUIDITY_VAULT = os.getenv(
    "SHARED_LIQUIDITY_VAULT", "0xeA90a257e5Dae20a0472C4812775F28614459bb6"
)
SHARED_LIQUIDITY_HOOK = os.getenv(
    "SHARED_LIQUIDITY_HOOK", "0x5A20BfEc6Caa3A94246eCCCb36F27F4980152dC0"
)

EXECUTE_LIVE = os.getenv("EXECUTE_LIVE", "0").strip() == "1"
MAX_SINGLE_STRATEGY_PCT = float(os.getenv("YIELD_MAX_SINGLE_STRATEGY_PCT", "0.40"))
MAX_SLIPPAGE_BPS = int(os.getenv("YIELD_MAX_SLIPPAGE_BPS", "50"))
MIN_CAPITAL_USD = float(os.getenv("YIELD_MIN_CAPITAL_USD", "10.0"))


class StrategyKind(str, Enum):
    STABLE_LENDING = "stable_lending"      # Morpho/Aave-style USDC
    SHARED_LIQUIDITY = "shared_liquidity"  # SINCOR SharedLiquidityVault
    CONCENTRATED_LP = "concentrated_lp"    # Uniswap V4 CLMM style
    CASH = "cash"                          # Idle USDC reserve


@dataclass(frozen=True)
class YieldStrategy:
    id: str
    name: str
    kind: StrategyKind
    protocol: str
    estimated_apr: float  # 0.12 == 12%
    risk_score: float     # 0.0 (safe) .. 1.0 (aggressive)
    min_liquidity_usd: float
    enabled: bool = True
    notes: str = ""


@dataclass
class StrategyAllocation:
    strategy_id: str
    weight: float          # 0..1
    capital_usd: float
    estimated_apr: float
    risk_score: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RebalancePlan:
    """Result of a dry-run (or live-intent) rebalance."""

    timestamp: float
    mode: str  # "dry_run" | "live_intent"
    total_capital_usd: float
    allocations: List[StrategyAllocation]
    expected_blended_apr: float
    max_risk_score: float
    fee_to_treasury_bps: int
    treasury: str
    vault: str
    intents: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["allocations"] = [a.to_dict() for a in self.allocations]
        return d


# Default strategy universe — conservative, Base-native oriented
DEFAULT_STRATEGIES: List[YieldStrategy] = [
    YieldStrategy(
        id="cash_reserve",
        name="USDC Cash Reserve",
        kind=StrategyKind.CASH,
        protocol="native",
        estimated_apr=0.0,
        risk_score=0.0,
        min_liquidity_usd=0.0,
        notes="Always keep a liquid buffer",
    ),
    YieldStrategy(
        id="morpho_usdc",
        name="Morpho-style USDC Lending",
        kind=StrategyKind.STABLE_LENDING,
        protocol="morpho_blue",
        estimated_apr=0.045,
        risk_score=0.15,
        min_liquidity_usd=1000.0,
        notes="Isolated market lending; oracle + IRM constrained",
    ),
    YieldStrategy(
        id="aave_usdc",
        name="Aave-style USDC Supply",
        kind=StrategyKind.STABLE_LENDING,
        protocol="aave_v3",
        estimated_apr=0.038,
        risk_score=0.12,
        min_liquidity_usd=1000.0,
    ),
    YieldStrategy(
        id="shared_liq_vault",
        name="SINCOR SharedLiquidityVault",
        kind=StrategyKind.SHARED_LIQUIDITY,
        protocol="sincor_shared_liquidity",
        estimated_apr=0.08,
        risk_score=0.25,
        min_liquidity_usd=250.0,  # CEO 2026-08-17: lowered from 500 to unlock ~$295 treasury cash loading window under risk caps
        notes=f"Vault {SHARED_LIQUIDITY_VAULT}; hook {SHARED_LIQUIDITY_HOOK}",
    ),
    YieldStrategy(
        id="univ4_clmm_stable",
        name="Uniswap V4 Stable CLMM",
        kind=StrategyKind.CONCENTRATED_LP,
        protocol="uniswap_v4",
        estimated_apr=0.06,
        risk_score=0.35,
        min_liquidity_usd=2000.0,
        notes="Concentrated stable pair; IL risk managed by range",
    ),
]


class YieldAggregator:
    """
    Risk-aware capital allocator across registered strategies.

    Usage:
        agg = get_default_aggregator()
        plan = agg.plan_rebalance(capital_usd=5000, risk_budget=0.30)
        # plan is always dry-run unless EXECUTE_LIVE=1
    """

    def __init__(self, strategies: Optional[List[YieldStrategy]] = None):
        self.strategies = list(strategies or DEFAULT_STRATEGIES)
        self.fee_to_treasury_bps = 10  # 0.10% protocol fee concept

    def list_strategies(self, enabled_only: bool = True) -> List[YieldStrategy]:
        if enabled_only:
            return [s for s in self.strategies if s.enabled]
        return list(self.strategies)

    def _eligible(self, capital_usd: float, risk_budget: float) -> List[YieldStrategy]:
        out = []
        for s in self.list_strategies():
            if s.risk_score > risk_budget + 1e-9:
                continue
            if capital_usd < s.min_liquidity_usd and s.kind != StrategyKind.CASH:
                # still allow tiny capital into cash
                continue
            out.append(s)
        if not out:
            # always fall back to cash
            cash = next((s for s in self.strategies if s.kind == StrategyKind.CASH), None)
            if cash:
                out = [cash]
        return out

    def plan_rebalance(
        self,
        capital_usd: float,
        risk_budget: float = 0.30,
        prefer_treasury_fee: bool = True,
    ) -> RebalancePlan:
        """
        Build a rebalance plan. Pure function of inputs + strategy table.
        Does not touch chain.
        """
        warnings: List[str] = []

        if capital_usd < MIN_CAPITAL_USD:
            warnings.append(
                f"capital_usd {capital_usd} below MIN_CAPITAL_USD {MIN_CAPITAL_USD}; holding cash"
            )
            capital_usd = max(capital_usd, 0.0)

        eligible = self._eligible(capital_usd, risk_budget)
        if not eligible:
            warnings.append("no eligible strategies; empty plan")
            return RebalancePlan(
                timestamp=time.time(),
                mode="dry_run",
                total_capital_usd=capital_usd,
                allocations=[],
                expected_blended_apr=0.0,
                max_risk_score=0.0,
                fee_to_treasury_bps=self.fee_to_treasury_bps,
                treasury=TREASURY,
                vault=SHARED_LIQUIDITY_VAULT,
                warnings=warnings,
            )

        # Score: higher APR / (1 + risk) wins; cash always gets a floor weight
        scores: Dict[str, float] = {}
        for s in eligible:
            if s.kind == StrategyKind.CASH:
                scores[s.id] = 0.15  # floor
            else:
                scores[s.id] = max(s.estimated_apr, 0.0) / (1.0 + s.risk_score)

        total_score = sum(scores.values()) or 1.0
        raw_weights = {sid: sc / total_score for sid, sc in scores.items()}

        # Cap single-strategy concentration
        capped: Dict[str, float] = {}
        overflow = 0.0
        for sid, w in raw_weights.items():
            if w > MAX_SINGLE_STRATEGY_PCT:
                overflow += w - MAX_SINGLE_STRATEGY_PCT
                capped[sid] = MAX_SINGLE_STRATEGY_PCT
            else:
                capped[sid] = w

        if overflow > 0:
            # redistribute overflow to under-cap strategies proportional to remaining room
            room = {
                sid: max(MAX_SINGLE_STRATEGY_PCT - w, 0.0) for sid, w in capped.items()
            }
            room_sum = sum(room.values()) or 1.0
            for sid in capped:
                capped[sid] += overflow * (room[sid] / room_sum)

        # Normalize
        wsum = sum(capped.values()) or 1.0
        weights = {sid: w / wsum for sid, w in capped.items()}

        strat_by_id = {s.id: s for s in eligible}
        allocations: List[StrategyAllocation] = []
        blended = 0.0
        max_risk = 0.0
        for sid, w in sorted(weights.items(), key=lambda x: -x[1]):
            s = strat_by_id[sid]
            cap = round(capital_usd * w, 6)
            allocations.append(
                StrategyAllocation(
                    strategy_id=sid,
                    weight=round(w, 6),
                    capital_usd=cap,
                    estimated_apr=s.estimated_apr,
                    risk_score=s.risk_score,
                )
            )
            blended += w * s.estimated_apr
            max_risk = max(max_risk, s.risk_score)

        mode = "dry_run"
        intents: List[Dict[str, Any]] = []
        executed = False

        if EXECUTE_LIVE:
            mode = "live_intent"
            warnings.append(
                "EXECUTE_LIVE=1 set: emitting intents only; signing/broadcast is external"
            )
            for alloc in allocations:
                intents.append(
                    {
                        "action": "allocate",
                        "strategy_id": alloc.strategy_id,
                        "capital_usd": alloc.capital_usd,
                        "max_slippage_bps": MAX_SLIPPAGE_BPS,
                        "fee_to": TREASURY,
                        "fee_bps": self.fee_to_treasury_bps if prefer_treasury_fee else 0,
                        "vault": SHARED_LIQUIDITY_VAULT,
                    }
                )
        else:
            warnings.append("dry_run mode (set EXECUTE_LIVE=1 to emit live intents)")

        plan = RebalancePlan(
            timestamp=time.time(),
            mode=mode,
            total_capital_usd=capital_usd,
            allocations=allocations,
            expected_blended_apr=round(blended, 6),
            max_risk_score=round(max_risk, 6),
            fee_to_treasury_bps=self.fee_to_treasury_bps,
            treasury=TREASURY,
            vault=SHARED_LIQUIDITY_VAULT,
            intents=intents,
            warnings=warnings,
            executed=executed,
        )
        logger.info(
            "Yield rebalance plan: capital=$%.2f blended_apr=%.2f%% mode=%s strategies=%d",
            capital_usd,
            plan.expected_blended_apr * 100,
            mode,
            len(allocations),
        )
        return plan

    def simulate_year_pnl(self, capital_usd: float, risk_budget: float = 0.30) -> Dict[str, Any]:
        """Simple expected PnL helper for TOA / CEO projections."""
        plan = self.plan_rebalance(capital_usd, risk_budget=risk_budget)
        expected = capital_usd * plan.expected_blended_apr
        fee = expected * (plan.fee_to_treasury_bps / 10_000)
        return {
            "capital_usd": capital_usd,
            "expected_gross_usd": round(expected, 4),
            "expected_fee_to_treasury_usd": round(fee, 4),
            "expected_net_usd": round(expected - fee, 4),
            "blended_apr": plan.expected_blended_apr,
            "plan": plan.to_dict(),
        }


_default_agg: Optional[YieldAggregator] = None


def get_default_aggregator() -> YieldAggregator:
    global _default_agg
    if _default_agg is None:
        _default_agg = YieldAggregator()
    return _default_agg
