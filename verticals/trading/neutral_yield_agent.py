#!/usr/bin/env python3
"""
Neutral Yield Agent — Project #1 (Delta-Neutral Short)

Strategy:
  1. Query Aave v3 subgraph for best-yield collateral asset (health, APR).
  2. Query Hyperliquid API for matching perpetual funding rate.
  3. Open delta-neutral position: deposit → Aave collateral + short perp on HL.
  4. Monitor health factor every cycle; circuit breaker unwinds if HF < threshold.
  5. Harvest net yield (Aave interest + HL funding PnL) → Treasury.
  6. Ingest cycle result to TOA feedback loop + monetization_engine.

Usage:
    from verticals.trading.neutral_yield_agent import NeutralYieldAgent
    agent = NeutralYieldAgent()
    result = agent.run_cycle()

Environment vars:
    HYPERLIQUID_API_URL        (default: https://api.hyperliquid.xyz)
    AAVE_SUBGRAPH_URL          (default: Base Aave v3 subgraph)
    NEUTRAL_YIELD_TREASURY     (default: canonical 0x09E289...)
    NEUTRAL_YIELD_HF_THRESHOLD (default: 1.5)
    NEUTRAL_YIELD_MAX_POSITION_USDC (default: 10000)
    NEUTRAL_YIELD_FEE_BPS      (default: 20 — 0.20% of yield to Treasury)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("sincor.neutral_yield")

# ──────────────────────────── Config ───────────────────────────────────── #

HYPERLIQUID_API_URL: str = os.getenv(
    "HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz"
)
AAVE_SUBGRAPH_URL: str = os.getenv(
    "AAVE_SUBGRAPH_URL",
    "https://api.goldsky.com/api/public/project_clk74pd7lueg738tw9bje4a1o/subgraphs"
    "/aave-v3-base/prod/gn",
)
TREASURY_ADDRESS: str = os.getenv(
    "NEUTRAL_YIELD_TREASURY",
    "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac",
)
HF_THRESHOLD: float = float(os.getenv("NEUTRAL_YIELD_HF_THRESHOLD", "1.5"))
MAX_POSITION_USDC: float = float(
    os.getenv("NEUTRAL_YIELD_MAX_POSITION_USDC", "10000")
)
FEE_BPS: int = int(os.getenv("NEUTRAL_YIELD_FEE_BPS", "20"))

# ──────────────────────────── Data classes ─────────────────────────────── #


@dataclass
class AaveAsset:
    symbol: str
    address: str
    supply_apy: float
    borrow_apy: float
    liquidity_usd: float
    health_factor: float = 0.0


@dataclass
class HyperliquidPerp:
    coin: str
    mark_price: float
    funding_rate_1h: float  # annualised fraction, e.g. 0.03 = 3%
    open_interest_usd: float


@dataclass
class NeutralPosition:
    asset_symbol: str
    aave_collateral_usd: float
    hl_short_notional_usd: float
    entry_mark_price: float
    aave_supply_apy: float
    hl_funding_rate: float
    net_yield_apy: float
    health_factor: float
    opened_at: float = field(default_factory=time.time)
    active: bool = True


@dataclass
class CycleResult:
    status: str
    net_yield_usd: float = 0.0
    fee_to_treasury_usd: float = 0.0
    health_factor: float = 0.0
    circuit_breaker_fired: bool = False
    position: Optional[NeutralPosition] = None
    error: Optional[str] = None


# ──────────────────────────── HTTP helpers ─────────────────────────────── #


def _http_get(url: str, timeout: int = 10) -> Any:
    """Thin wrapper around urllib so we have no mandatory extra deps."""
    import json
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _http_post(url: str, payload: dict, timeout: int = 10) -> Any:
    import json
    import urllib.request

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ──────────────────────────── Subgraph queries ─────────────────────────── #


def fetch_aave_assets(subgraph_url: str = AAVE_SUBGRAPH_URL) -> List[AaveAsset]:
    """
    Query Aave v3 Base subgraph for reserve data.
    Returns top assets by supply APY, with liquidity > $100k.
    """
    query = """
    {
      reserves(
        where: { isActive: true, isFrozen: false }
        orderBy: supplyAPY
        orderDirection: desc
        first: 10
      ) {
        symbol
        underlyingAsset
        supplyAPY
        variableBorrowRate
        availableLiquidity
        decimals
      }
    }
    """
    try:
        data = _http_post(subgraph_url, {"query": query})
        reserves = data.get("data", {}).get("reserves", [])
        assets = []
        for r in reserves:
            supply_apy = float(r.get("supplyAPY", 0))
            borrow_apy = float(r.get("variableBorrowRate", 0)) / 1e27
            liq = float(r.get("availableLiquidity", 0)) / (
                10 ** int(r.get("decimals", 18))
            )
            if liq * 1 >= 100_000:  # min $100k liquidity
                assets.append(
                    AaveAsset(
                        symbol=r["symbol"],
                        address=r["underlyingAsset"],
                        supply_apy=supply_apy,
                        borrow_apy=borrow_apy,
                        liquidity_usd=liq,
                    )
                )
        return assets
    except Exception as exc:
        logger.warning("Aave subgraph fetch failed: %s", exc)
        return []


def fetch_hl_funding_rates(api_url: str = HYPERLIQUID_API_URL) -> List[HyperliquidPerp]:
    """
    Query Hyperliquid perpetuals API for coins with positive funding (shorts earn).
    Returns coins sorted by funding_rate descending.
    """
    try:
        data = _http_post(f"{api_url}/info", {"type": "metaAndAssetCtxs"})
        universe = data[0].get("universe", [])
        asset_ctxs = data[1]
        perps = []
        for coin_info, ctx in zip(universe, asset_ctxs):
            name = coin_info.get("name", "")
            funding = float(ctx.get("funding", 0))          # per-hour rate
            mark_px = float(ctx.get("markPx", 0))
            oi_usd = float(ctx.get("openInterest", 0)) * mark_px
            if mark_px > 0 and funding > 0:  # positive funding = shorts earn
                perps.append(
                    HyperliquidPerp(
                        coin=name,
                        mark_price=mark_px,
                        funding_rate_1h=funding,
                        open_interest_usd=oi_usd,
                    )
                )
        perps.sort(key=lambda p: p.funding_rate_1h, reverse=True)
        return perps
    except Exception as exc:
        logger.warning("Hyperliquid API fetch failed: %s", exc)
        return []


# ──────────────────────────── Circuit breaker ──────────────────────────── #


class LiquidationCircuitBreaker:
    """
    Monitors Aave health factor and fires an emergency unwind when
    health_factor < threshold.  Never bricks: all unwind steps are
    individually try/caught so partial failure still logs + alerts.
    """

    def __init__(self, threshold: float = HF_THRESHOLD):
        self.threshold = threshold
        self._fired = False

    def check(self, health_factor: float) -> bool:
        """Return True if circuit breaker should fire."""
        return health_factor < self.threshold

    def unwind(
        self,
        position: NeutralPosition,
        aave_client: Any = None,
        hl_client: Any = None,
    ) -> Dict[str, Any]:
        """
        Emergency unwind.  In production wire in real Aave/HL SDK clients.
        Returns dict with unwind status for each leg.
        """
        results: Dict[str, Any] = {"position": position.asset_symbol}
        self._fired = True

        # Leg 1: close Hyperliquid short
        try:
            if hl_client:
                hl_client.close_position(position.asset_symbol)
                results["hl_short"] = "closed"
            else:
                logger.warning(
                    "HL client not wired — skipping HL close for %s",
                    position.asset_symbol,
                )
                results["hl_short"] = "skipped_no_client"
        except Exception as exc:
            logger.error("HL unwind failed: %s", exc)
            results["hl_short"] = f"error: {exc}"

        # Leg 2: withdraw Aave collateral
        try:
            if aave_client:
                aave_client.withdraw(position.asset_symbol, position.aave_collateral_usd)
                results["aave_collateral"] = "withdrawn"
            else:
                logger.warning(
                    "Aave client not wired — skipping Aave withdraw for %s",
                    position.asset_symbol,
                )
                results["aave_collateral"] = "skipped_no_client"
        except Exception as exc:
            logger.error("Aave unwind failed: %s", exc)
            results["aave_collateral"] = f"error: {exc}"

        position.active = False
        logger.warning(
            "CIRCUIT BREAKER FIRED for %s (HF %.2f < %.2f): %s",
            position.asset_symbol,
            position.health_factor,
            self.threshold,
            results,
        )
        return results


# ──────────────────────────── Main agent ───────────────────────────────── #


class NeutralYieldAgent:
    """
    Delta-neutral yield agent. Manages a single open position at a time
    for simplicity; extend `positions` list for multi-asset in production.
    """

    def __init__(
        self,
        treasury: str = TREASURY_ADDRESS,
        hf_threshold: float = HF_THRESHOLD,
        max_position_usdc: float = MAX_POSITION_USDC,
        fee_bps: int = FEE_BPS,
        toa: Any = None,
        monetization_engine: Any = None,
    ):
        self.treasury = treasury
        self.max_position_usdc = max_position_usdc
        self.fee_bps = fee_bps
        self.toa = toa
        self.monetization_engine = monetization_engine
        self.circuit_breaker = LiquidationCircuitBreaker(threshold=hf_threshold)
        self.position: Optional[NeutralPosition] = None
        self.cycle_count = 0

    # ── scanning ── #

    def scan_best_opportunity(self) -> Optional[NeutralPosition]:
        """
        Cross-reference Aave supply APY vs Hyperliquid funding rate for the
        same underlying asset. Return the best net-yield match.
        """
        aave_assets = fetch_aave_assets()
        hl_perps = fetch_hl_funding_rates()

        if not aave_assets or not hl_perps:
            logger.info("No data from one or both sources — skipping scan.")
            return None

        hl_map = {p.coin.upper(): p for p in hl_perps}
        best_yield = -999.0
        best: Optional[NeutralPosition] = None

        for asset in aave_assets:
            sym = asset.symbol.upper()
            perp = hl_map.get(sym)
            if perp is None:
                continue  # no matching perp

            funding_annualised = perp.funding_rate_1h * 24 * 365
            net_yield_apy = asset.supply_apy + funding_annualised

            if net_yield_apy > best_yield:
                best_yield = net_yield_apy
                best = NeutralPosition(
                    asset_symbol=sym,
                    aave_collateral_usd=min(self.max_position_usdc, asset.liquidity_usd * 0.05),
                    hl_short_notional_usd=min(self.max_position_usdc, perp.open_interest_usd * 0.001),
                    entry_mark_price=perp.mark_price,
                    aave_supply_apy=asset.supply_apy,
                    hl_funding_rate=funding_annualised,
                    net_yield_apy=net_yield_apy,
                    health_factor=2.5,  # will be refreshed on open
                )

        if best:
            logger.info(
                "Best opportunity: %s net_apy=%.2f%% (Aave %.2f%% + HL funding %.2f%%)",
                best.asset_symbol,
                best.net_yield_apy * 100,
                best.aave_supply_apy * 100,
                best.hl_funding_rate * 100,
            )
        return best

    # ── position management ── #

    def open_position(self, candidate: NeutralPosition) -> bool:
        """
        Open delta-neutral position. Wire real Aave/HL SDK in production.
        Returns True on success.
        """
        logger.info(
            "Opening delta-neutral position: %s collateral=$%.0f short=$%.0f",
            candidate.asset_symbol,
            candidate.aave_collateral_usd,
            candidate.hl_short_notional_usd,
        )
        # TODO: integrate real Aave SDK for collateral deposit
        # TODO: integrate real Hyperliquid SDK for short perpetual open
        self.position = candidate
        return True

    def get_health_factor(self) -> float:
        """
        Query current Aave health factor for open position.
        Stub returns 2.0 — replace with real Aave SDK / on-chain read.
        """
        # TODO: aave_client.get_health_factor(self.treasury)
        return 2.0

    def harvest_yield(self) -> float:
        """
        Collect accumulated yield from Aave interest + Hyperliquid funding PnL.
        Stub returns simulated value — replace with real SDK calls.
        Returns net yield in USD.
        """
        if self.position is None:
            return 0.0
        # Simulate: 1 cycle of net_yield_apy * position / blocks_per_year
        daily_yield = (
            self.position.net_yield_apy
            * self.position.aave_collateral_usd
            / 365
        )
        return max(0.0, daily_yield)

    # ── fee routing ── #

    def route_fee_to_treasury(self, gross_yield_usd: float) -> float:
        """
        Skim fee_bps of gross yield and record to treasury + monetization engine.
        Returns fee amount in USD.
        """
        fee_usd = gross_yield_usd * self.fee_bps / 10_000
        if fee_usd > 0:
            logger.info(
                "Fee skim → Treasury %s: $%.4f (%.0f bps of $%.4f yield)",
                self.treasury,
                fee_usd,
                self.fee_bps,
                gross_yield_usd,
            )
            # Wire real on-chain transfer in production:
            # erc20.transfer(treasury, to_wei(fee_usd))

            # Feed into monetization engine
            if self.monetization_engine:
                try:
                    self.monetization_engine.record_defi_fee_event(
                        source="neutral_yield_agent",
                        amount_usd=fee_usd,
                        asset="USDC",
                    )
                except Exception as exc:
                    logger.warning("monetization_engine.record_defi_fee_event: %s", exc)
        return fee_usd

    # ── main cycle ── #

    def run_cycle(self) -> CycleResult:
        """
        Execute one 5-minute check-in cycle:
          1. Circuit breaker check (if position open)
          2. Scan for new opportunity (if no position)
          3. Open position
          4. Harvest + route fees
          5. Ingest to TOA
        """
        self.cycle_count += 1
        logger.info("NeutralYieldAgent cycle #%d start", self.cycle_count)

        # ── step 1: health check ──
        if self.position and self.position.active:
            hf = self.get_health_factor()
            self.position.health_factor = hf
            if self.circuit_breaker.check(hf):
                unwind = self.circuit_breaker.unwind(self.position)
                result = CycleResult(
                    status="circuit_breaker_fired",
                    health_factor=hf,
                    circuit_breaker_fired=True,
                    position=self.position,
                )
                self._ingest_toa(result)
                return result

        # ── step 2 + 3: open if no position ──
        if self.position is None or not self.position.active:
            candidate = self.scan_best_opportunity()
            if candidate:
                self.open_position(candidate)
            else:
                logger.info("No matching opportunity found — holding.")
                return CycleResult(status="no_opportunity")

        # ── step 4: harvest + fee ──
        gross_yield = self.harvest_yield()
        fee = self.route_fee_to_treasury(gross_yield)
        net_yield = gross_yield - fee

        result = CycleResult(
            status="ok",
            net_yield_usd=net_yield,
            fee_to_treasury_usd=fee,
            health_factor=self.position.health_factor if self.position else 0.0,
            position=self.position,
        )
        logger.info(
            "Cycle #%d result: net_yield=$%.4f fee_treasury=$%.4f hf=%.2f",
            self.cycle_count,
            net_yield,
            fee,
            result.health_factor,
        )

        # ── step 5: TOA feedback ──
        self._ingest_toa(result)
        return result

    def _ingest_toa(self, result: CycleResult) -> None:
        if self.toa is None:
            return
        try:
            self.toa.ingest_feedback(
                {
                    "source": "neutral_yield_agent",
                    "status": result.status,
                    "net_yield_usd": result.net_yield_usd,
                    "fee_to_treasury_usd": result.fee_to_treasury_usd,
                    "health_factor": result.health_factor,
                    "circuit_breaker_fired": result.circuit_breaker_fired,
                    "asset": result.position.asset_symbol if result.position else None,
                    "cycle": self.cycle_count,
                }
            )
        except Exception as exc:
            logger.warning("TOA ingest failed: %s", exc)


# ──────────────────────────── CLI entry ────────────────────────────────── #

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        from agents.toa.orchestrator import TOAOrchestrator

        toa = TOAOrchestrator()
    except Exception:
        toa = None

    agent = NeutralYieldAgent(toa=toa)
    result = agent.run_cycle()
    print(f"Status : {result.status}")
    print(f"Net yield : ${result.net_yield_usd:.4f}")
    print(f"Fee → Treasury: ${result.fee_to_treasury_usd:.4f}")
    print(f"Circuit breaker: {result.circuit_breaker_fired}")
