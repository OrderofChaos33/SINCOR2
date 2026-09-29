"""P24 creator token factory: 1e9 fixed supply, 50/50 curve/vesting, 5-yr vesting.

Issuance is factory-only (the only minter). 50% of supply goes to the bonding
curve inventory; 50% vests linearly to the creator over 1825 days. Unvested
tokens cannot be sold or transferred — transfers are checked against the
vested amount at the transfer timestamp.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict

from . import PARAMS


class FactoryError(Exception):
    """Issuance invariant violation."""


TOKEN_DECIMALS = 18
TOTAL_SUPPLY_TOKENS = PARAMS["token_supply"]          # 1e9, immutable
TOTAL_SUPPLY_WEI = TOTAL_SUPPLY_TOKENS * 10 ** TOKEN_DECIMALS
CURVE_SUPPLY_WEI = TOTAL_SUPPLY_WEI // 2
VESTING_SUPPLY_WEI = TOTAL_SUPPLY_WEI - CURVE_SUPPLY_WEI
VESTING_SECONDS = PARAMS["vesting_days"] * 86400


@dataclass
class CreatorToken:
    name: str
    symbol: str
    creator: str
    issued_at: float
    policy_version: str          # content-policy ruleset version at issuance
    curve_supply_wei: int = CURVE_SUPPLY_WEI
    vesting_supply_wei: int = VESTING_SUPPLY_WEI
    # creator balances, wei
    vested_claimed_wei: int = 0
    transferred_wei: int = 0     # of vested tokens moved out

    @property
    def total_supply_wei(self) -> int:
        return TOTAL_SUPPLY_WEI


def vested_amount_wei(token: CreatorToken, now: float) -> int:
    """Linear vesting of the creator half over 1825 days."""
    elapsed = max(0.0, now - token.issued_at)
    if elapsed >= VESTING_SECONDS:
        return token.vesting_supply_wei
    return int(Fraction(token.vesting_supply_wei)
               * Fraction(int(elapsed), VESTING_SECONDS))


def transferable_wei(token: CreatorToken, now: float) -> int:
    """Vested minus already transferred: the spendable creator balance."""
    return vested_amount_wei(token, now) - token.transferred_wei


class CreatorTokenFactory:
    """The only minter. Issues CreatorTokens after policy screening."""

    def __init__(self) -> None:
        self.tokens: Dict[str, CreatorToken] = {}  # symbol -> token

    def issue(
        self,
        name: str,
        symbol: str,
        creator: str,
        policy_version: str,
        screened: bool,
        now: float | None = None,
    ) -> CreatorToken:
        if not screened:
            raise FactoryError("issuance requires a passed content-policy screen")
        if not name or not symbol:
            raise FactoryError("name and symbol required")
        if symbol in self.tokens:
            raise FactoryError(f"symbol {symbol!r} already issued")
        now = time.time() if now is None else now
        token = CreatorToken(name=name, symbol=symbol, creator=creator,
                             issued_at=now, policy_version=policy_version)
        self.tokens[symbol] = token
        return token

    def transfer_creator_tokens(
        self, symbol: str, amount_wei: int, now: float | None = None
    ) -> None:
        """Creator transfer: only vested tokens may move."""
        now = time.time() if now is None else now
        token = self.tokens.get(symbol)
        if token is None:
            raise FactoryError(f"unknown token {symbol!r}")
        if amount_wei <= 0:
            raise FactoryError("transfer amount must be positive")
        if amount_wei > transferable_wei(token, now):
            raise FactoryError(
                "unvested creator tokens cannot be sold or transferred")
        token.transferred_wei += amount_wei
