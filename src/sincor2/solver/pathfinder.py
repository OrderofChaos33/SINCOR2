"""
Multi-hop route search over a PoolSnapshot.

Enumerates every simple path of up to ``MAX_HOPS`` pools between two
tokens, quotes each hop by chaining exact-input quotes (hop output is
the next hop's input), and ranks routes by *net value*: output after
the SINCOR platform fee, minus the route's gas cost valued in the
output token. All valuation math uses Fraction — no floats in ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

from . import pool_math as pm
from .pools import (
    PoolSnapshot, PoolState, TOKEN_DECIMALS, USDC, VENUE_AERODROME,
    VENUE_UNISWAP_V2, VENUE_UNISWAP_V3, WETH, checksum,
)

MAX_HOPS = 3
PLATFORM_FEE_BPS = 500  # SINCOR platform fee canon: 5%

# Gas model (units of gas). A swap transaction pays the 21k base once;
# each hop costs its venue's swap+transfer profile. These are planning
# estimates from public gas profiles of the canonical routers, labeled
# as estimates in every report — not measured on-chain in this build.
GAS_TX_BASE = 21_000
GAS_PER_HOP = {
    VENUE_UNISWAP_V3: 155_000,
    VENUE_UNISWAP_V2: 125_000,
    VENUE_AERODROME: 135_000,
}


def route_gas_units(pools_in_route: List[PoolState]) -> int:
    return GAS_TX_BASE + sum(GAS_PER_HOP[p.venue] for p in pools_in_route)


# ---------------------------------------------------------------------------
# USD price book derived from snapshot spot prices (BFS from the USDC anchor)
# ---------------------------------------------------------------------------

class PriceBook:
    """token -> USD price as an exact Fraction, from pool spot prices."""

    def __init__(self, prices: Dict[str, Fraction]):
        self._prices = prices  # keys are checksummed (lowercase) addresses

    @classmethod
    def from_snapshot(cls, snapshot: PoolSnapshot) -> "PriceBook":
        prices: Dict[str, Fraction] = {checksum(USDC): Fraction(1)}
        # BFS: repeatedly price any token that shares a pool with a priced one
        changed = True
        while changed:
            changed = False
            for pool in snapshot.pools:
                t0, t1 = checksum(pool.token0), checksum(pool.token1)
                for base, other in ((t0, t1), (t1, t0)):
                    if base in prices and other not in prices:
                        num, den = pool.spot(base)  # other per base, raw units
                        if den == 0:
                            continue
                        raw = Fraction(num, den)
                        # human = human units of `other` per 1 human unit
                        # of `base`; USD-per-other = USD-per-base / human
                        human = raw * Fraction(
                            10 ** TOKEN_DECIMALS_BY_ADDR[base],
                            10 ** TOKEN_DECIMALS_BY_ADDR[other],
                        )
                        if human > 0:
                            prices[other] = prices[base] / human
                            changed = True
        return cls(prices)

    def usd_price(self, token: str) -> Optional[Fraction]:
        return self._prices.get(checksum(token))

    def to_usd(self, token: str, amount_wei: int) -> Optional[Fraction]:
        price = self.usd_price(token)
        if price is None:
            return None
        decimals = TOKEN_DECIMALS_BY_ADDR[checksum(token)]
        return Fraction(amount_wei, 10 ** decimals) * price

    def from_usd(self, token: str, usd: Fraction) -> Optional[int]:
        price = self.usd_price(token)
        if price is None or price == 0:
            return None
        decimals = TOKEN_DECIMALS_BY_ADDR[checksum(token)]
        return int(usd / price * (10 ** decimals))


# decimals keyed by lowercase address
TOKEN_DECIMALS_BY_ADDR = {checksum(k): v for k, v in TOKEN_DECIMALS.items()}


# ---------------------------------------------------------------------------
# Route quoting
# ---------------------------------------------------------------------------

@dataclass
class HopQuote:
    venue: str
    pool_address: str
    token_in: str
    token_out: str
    fee_bps: int            # LP fee normalized to bps
    amount_in: int
    amount_out: int
    lp_fee_amount: int      # in input-token terms


@dataclass
class RouteQuote:
    hops: List[HopQuote]
    amount_in: int
    amount_out_gross: int
    platform_fee_amount: int
    amount_out_net: int
    gas_units: int
    gas_cost_usd: Optional[Fraction]
    gas_cost_in_token_out: Optional[Fraction]
    net_value_usd: Optional[Fraction]   # amount_out_net in USD minus gas USD

    @property
    def token_in(self) -> str:
        return self.hops[0].token_in

    @property
    def token_out(self) -> str:
        return self.hops[-1].token_out


def _fee_bps_of(pool: PoolState) -> int:
    fee = getattr(pool, "fee_bps", None)
    if fee is not None:
        return fee
    return getattr(pool, "fee_pips") // 100  # pips -> bps


def quote_route(route: List[PoolState], token_in: str, amount_in: int,
                snapshot: PoolSnapshot, price_book: PriceBook,
                apply_platform_fee: bool = True) -> Optional[RouteQuote]:
    """Quote one concrete route. Returns None if any hop cannot fill."""
    hops: List[HopQuote] = []
    current_token = checksum(token_in)
    current_amount = amount_in
    for pool in route:
        token_out = (checksum(pool.token1)
                     if current_token == checksum(pool.token0)
                     else checksum(pool.token0))
        try:
            out, lp_fee = pool.quote(current_token, current_amount)
        except (pm.InsufficientLiquidity, pm.InsufficientTickData, ValueError):
            return None
        if out <= 0:
            return None
        hops.append(HopQuote(
            venue=pool.venue, pool_address=pool.address,
            token_in=current_token, token_out=token_out,
            fee_bps=_fee_bps_of(pool), amount_in=current_amount,
            amount_out=out, lp_fee_amount=lp_fee))
        current_token = token_out
        current_amount = out
    gross = current_amount
    platform_fee = gross * PLATFORM_FEE_BPS // 10_000 if apply_platform_fee else 0
    net = gross - platform_fee
    gas_units = route_gas_units(route)
    eth_usd = price_book.usd_price(WETH)
    gas_cost_usd: Optional[Fraction] = None
    gas_cost_in_out: Optional[Fraction] = None
    net_value_usd: Optional[Fraction] = None
    if eth_usd is not None:
        gas_cost_usd = (Fraction(gas_units * snapshot.gas_price_wei, 10 ** 18)
                        * eth_usd)
        net_usd = price_book.to_usd(current_token, net)
        if net_usd is not None:
            net_value_usd = net_usd - gas_cost_usd
        out_price = price_book.usd_price(current_token)
        if out_price:
            gas_cost_in_out = gas_cost_usd / out_price
    return RouteQuote(
        hops=hops, amount_in=amount_in, amount_out_gross=gross,
        platform_fee_amount=platform_fee, amount_out_net=net,
        gas_units=gas_units, gas_cost_usd=gas_cost_usd,
        gas_cost_in_token_out=gas_cost_in_out, net_value_usd=net_value_usd)


def find_routes(snapshot: PoolSnapshot, token_in: str, token_out: str,
                amount_in: int, price_book: PriceBook,
                max_hops: int = MAX_HOPS,
                apply_platform_fee: bool = True) -> List[RouteQuote]:
    """All quotable routes token_in -> token_out, best net value first."""
    start, goal = checksum(token_in), checksum(token_out)
    if start == goal or amount_in <= 0:
        return []
    # adjacency: token -> list of (pool, other_token)
    adjacency: Dict[str, List[Tuple[PoolState, str]]] = {}
    for pool in snapshot.pools:
        t0, t1 = checksum(pool.token0), checksum(pool.token1)
        adjacency.setdefault(t0, []).append((pool, t1))
        adjacency.setdefault(t1, []).append((pool, t0))

    routes: List[RouteQuote] = []

    def dfs(current: str, visited_tokens: set, route: List[PoolState]):
        if len(route) >= max_hops:
            return
        for pool, nxt in adjacency.get(current, []):
            if nxt in visited_tokens:
                continue
            new_route = route + [pool]
            if nxt == goal:
                quoted = quote_route(new_route, start, amount_in, snapshot,
                                     price_book, apply_platform_fee)
                if quoted is not None:
                    routes.append(quoted)
            else:
                dfs(nxt, visited_tokens | {nxt}, new_route)

    dfs(start, {start}, [])
    # rank: net USD value when known, else net output amount
    routes.sort(
        key=lambda r: (r.net_value_usd is not None,
                       r.net_value_usd if r.net_value_usd is not None
                       else Fraction(r.amount_out_net)),
        reverse=True)
    return routes
