"""
Pool state model + venue/token constants for the solver quoting engine.

State objects are immutable snapshots read at one block. A
:class:`PoolSnapshot` bundles every pool state at a single block number
so quotes and spread measurements are internally consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import pool_math as pm

# ---------------------------------------------------------------------------
# Tokens on Base mainnet (addresses are public, canonical)
# ---------------------------------------------------------------------------

WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
AXM = "0x4c3Fb66F14FbAA2088c9Ae91017bA770dA53715A"
SINC = "0xE1d836087f6573B665D25Ce088793e916D7892F8"

# Decimals as read from the ERC20 contracts (verified live 2026-11-11).
# NOTE: SINC has 8 decimals, not 18 — an early version of this table
# assumed 18 and mispriced SINC by 10**10 in human terms. The reader
# now verifies these against decimals() on-chain at snapshot time and
# refuses to build a snapshot on a mismatch (see pool_reader).
TOKEN_DECIMALS = {WETH: 18, USDC: 6, AXM: 18, SINC: 8}
TOKEN_SYMBOLS = {WETH: "WETH", USDC: "USDC", AXM: "AXM", SINC: "SINC"}
DEFAULT_TOKENS = (WETH, USDC, AXM, SINC)

# ---------------------------------------------------------------------------
# Venues / factories on Base mainnet (public deployment addresses)
# ---------------------------------------------------------------------------

UNISWAP_V3_FACTORY = "0x33128a8fC17869897dcE68Ed026d694621f6FDfD"
UNISWAP_V2_FACTORY = "0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"
AERODROME_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da"

VENUE_UNISWAP_V3 = "uniswap_v3"
VENUE_UNISWAP_V2 = "uniswap_v2"
VENUE_AERODROME = "aerodrome"

V3_FEE_TIERS = (100, 500, 3000, 10000)          # in pips (1e-6 units)
V3_TICK_SPACING = {100: 1, 500: 10, 3000: 60, 10000: 200}
V2_FEE_BPS = 30                                  # Uniswap V2: 0.30%
AERODROME_DEFAULT_FEE_BPS = 30                   # volatile-pool default


def checksum(address: str) -> str:
    """Normalize an address for dict keys (comparisons are case-insensitive)."""
    return "0x" + address[2:].lower() if address.startswith("0x") else address.lower()


TOKEN_DECIMALS_BY_ADDR = {checksum(k): v for k, v in TOKEN_DECIMALS.items()}


# ---------------------------------------------------------------------------
# Pool state snapshots
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class V2PoolState:
    """Constant-product pool state (Uniswap V2 / Aerodrome volatile)."""

    venue: str
    address: str
    token0: str
    token1: str
    reserve0: int
    reserve1: int
    fee_bps: int
    fee_source: str = "factory"   # "factory" | "assumed_default"

    def quote(self, token_in: str, amount_in: int) -> Tuple[int, int]:
        """Return (amount_out, lp_fee_amount) for an exact-input swap."""
        token_in = checksum(token_in)
        if token_in == checksum(self.token0):
            reserve_in, reserve_out = self.reserve0, self.reserve1
        elif token_in == checksum(self.token1):
            reserve_in, reserve_out = self.reserve1, self.reserve0
        else:
            raise ValueError("token not in pool")
        out = pm.v2_amount_out(amount_in, reserve_in, reserve_out, self.fee_bps)
        # LP fee actually withheld by the curve, in input-token terms:
        # fee = amount_in - (the fee-free-equivalent input). Exact expression:
        # withFee = in*(B-fee)/B grossed back up -> fee = in*fee/B rounded down
        # is the standard approximation; the exact withheld amount is
        # in - out_curve_inverse, so we report the bps share of input.
        lp_fee = amount_in * self.fee_bps // 10_000
        return out, lp_fee

    def spot(self, token_in: str) -> Tuple[int, int]:
        """Spot price of token_out per token_in as (numerator, denominator)."""
        if checksum(token_in) == checksum(self.token0):
            return self.reserve1, self.reserve0
        return self.reserve0, self.reserve1

    def depth_proxy(self) -> int:
        return self.reserve0 * self.reserve1


@dataclass(frozen=True)
class V3PoolState:
    """Concentrated-liquidity pool state (Uniswap V3)."""

    venue: str
    address: str
    token0: str
    token1: str
    fee_pips: int
    tick_spacing: int
    sqrt_price_x96: int
    tick: int
    liquidity: int
    initialized_ticks: Dict[int, int] = field(default_factory=dict)

    def quote(self, token_in: str, amount_in: int) -> Tuple[int, int]:
        """Return (amount_out, lp_fee_amount) for an exact-input swap."""
        token_in = checksum(token_in)
        if token_in == checksum(self.token0):
            zero_for_one = True
        elif token_in == checksum(self.token1):
            zero_for_one = False
        else:
            raise ValueError("token not in pool")
        out, _consumed, fee, _end_sqrt = pm.quote_v3_exact_in(
            self.sqrt_price_x96, self.tick, self.liquidity, self.fee_pips,
            zero_for_one, amount_in, self.initialized_ticks)
        return out, fee

    def spot(self, token_in: str) -> Tuple[int, int]:
        """Spot price of token_out per token_in as (numerator, denominator)."""
        num, den = pm.v3_spot_price(self.sqrt_price_x96)  # token1 per token0
        if checksum(token_in) == checksum(self.token0):
            return num, den
        return den, num

    def depth_proxy(self) -> int:
        return self.liquidity


PoolState = object  # V2PoolState | V3PoolState


@dataclass(frozen=True)
class PoolSnapshot:
    """All pool states read at one block."""

    block_number: int
    gas_price_wei: int
    pools: Tuple[PoolState, ...]

    def pools_for_pair(self, token_a: str, token_b: str) -> List[PoolState]:
        a, b = checksum(token_a), checksum(token_b)
        out = []
        for pool in self.pools:
            tokens = {checksum(pool.token0), checksum(pool.token1)}
            if tokens == {a, b}:
                out.append(pool)
        return out

    def pairs_with_multiple_venues(self) -> List[Tuple[str, str]]:
        seen: Dict[Tuple[str, str], int] = {}
        for pool in self.pools:
            key = tuple(sorted((checksum(pool.token0), checksum(pool.token1))))
            seen[key] = seen.get(key, 0) + 1
        return [pair for pair, count in seen.items() if count >= 2]
