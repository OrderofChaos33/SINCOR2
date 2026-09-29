"""P24 bonding curve: price = supply^2 / 16000, $69k graduation cap.

Early price discovery runs on-curve:
    buy_price(supply)  = supply^2 / 16000   (quote-asset wei per whole token)
    sell_price(supply) = (supply - 1)^2 / 16000
Curve inventory caps at a $69,000 market cap; at graduation the curve closes
permanently and inventory migrates to a standard AMM pool (one-way, no
re-entry to the curve).

supply here is whole-token units on the curve (<= 5e8, so supply^2 <= 2.5e17
— fits uint256 with wide headroom; still overflow-checked). Prices are in
USDC-wei (1e6) per whole token.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import PARAMS

USDC_WEI = 1_000_000
CURVE_DIVISOR = 16_000


def buy_price_wei(supply_tokens: int) -> int:
    """Marginal buy price at current curve supply, in USDC-wei per token."""
    if supply_tokens < 0:
        raise ValueError("supply cannot be negative")
    return (supply_tokens * supply_tokens * USDC_WEI) // CURVE_DIVISOR


def sell_price_wei(supply_tokens: int) -> int:
    """Marginal sell price at current curve supply, in USDC-wei per token."""
    if supply_tokens <= 0:
        raise ValueError("supply must be positive to sell")
    s = supply_tokens - 1
    return (s * s * USDC_WEI) // CURVE_DIVISOR


def market_cap_usd_wei(curve_supply_tokens: int) -> int:
    """Curve market cap in USDC-wei: supply * marginal buy price."""
    return curve_supply_tokens * buy_price_wei(curve_supply_tokens)


def graduated(curve_supply_tokens: int) -> bool:
    return (market_cap_usd_wei(curve_supply_tokens)
            >= int(PARAMS["graduation_mcap_usd"] * USDC_WEI))


@dataclass
class CurveState:
    """On-curve inventory for one creator token."""
    supply_tokens: int = 0          # tokens sold on the curve so far
    max_supply_tokens: int = 500_000_000  # 50% of the 1e9 fixed supply
    closed: bool = False           # True after graduation (permanent)

    def buy(self, tokens: int) -> int:
        """Buy `tokens` whole tokens on-curve. Returns cost in USDC-wei.

        Cost is the sum of marginal prices over the purchased range —
        exact, not the marginal price times quantity.
        """
        if self.closed:
            raise ValueError("curve closed after graduation")
        if tokens <= 0:
            raise ValueError("buy quantity must be positive")
        if self.supply_tokens + tokens > self.max_supply_tokens:
            raise ValueError("buy exceeds curve inventory")
        cost = sum(
            buy_price_wei(self.supply_tokens + i) for i in range(tokens)
        )
        self.supply_tokens += tokens
        if graduated(self.supply_tokens):
            self.closed = True  # one-way: never re-opens
        return cost

    def sell(self, tokens: int) -> int:
        """Sell `tokens` whole tokens back to the curve. Returns USDC-wei."""
        if self.closed:
            raise ValueError("curve closed after graduation")
        if tokens <= 0 or tokens > self.supply_tokens:
            raise ValueError("sell quantity invalid")
        proceeds = sum(
            sell_price_wei(self.supply_tokens - i) for i in range(tokens)
        )
        self.supply_tokens -= tokens
        return proceeds
