"""P25 fee module: 10 bps per annum on AUM, denominated in AXM or SINC.

Tick accrual is wei-exact via exact rational arithmetic (fractions.Fraction),
floored once at the end:

    fee_token_wei = floor(aum_usd * (fee_bps/10000) * (tick_s/31557600)
                          / price_usd_per_token * 1e18)

Per-user ledger; settlement to the canonical treasury in batches worth at
least $10 equivalent. Fee denomination is chosen once at onboarding (AXM
default, SINC optional) — reversible quarterly by policy, not per tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List

from ..catalog import TREASURY
from . import PARAMS

SECONDS_PER_YEAR = 31557600
TOKEN_DECIMALS = 18


class FeeDenominationError(ValueError):
    """Unknown fee denomination."""


@dataclass
class FeeAccrual:
    user_id: str
    tick_s: float
    aum_usd: float
    denomination: str
    price_usd: float
    fee_token_wei: int
    fee_usd: float


@dataclass
class Settlement:
    user_id: str
    denomination: str
    amount_token_wei: int
    amount_usd: float
    to: str


class FeeLedger:
    """Per-user accrued-but-unsettled P25 management fees."""

    def __init__(self) -> None:
        self._accrued_wei: Dict[str, int] = {}
        self._denomination: Dict[str, str] = {}
        self._accrued_usd: Dict[str, float] = {}
        self.history: List[FeeAccrual] = []
        self.settlements: List[Settlement] = []

    def _check_denom(self, denomination: str) -> None:
        if denomination not in PARAMS["fee_denominations"]:
            raise FeeDenominationError(
                f"unknown denomination {denomination!r}; "
                f"expected one of {PARAMS['fee_denominations']}"
            )

    def accrue(
        self,
        user_id: str,
        aum_usd: float,
        tick_s: float,
        denomination: str = "AXM",
        price_usd: float = 1.0,
    ) -> FeeAccrual:
        """Accrue one tick of the 10 bps p.a. fee. Wei-exact, floored once."""
        self._check_denom(denomination)
        if aum_usd < 0 or tick_s <= 0 or price_usd <= 0:
            raise ValueError("aum_usd >= 0, tick_s > 0, price_usd > 0 required")

        fee_token_wei = int(
            Fraction(str(aum_usd))
            * Fraction(PARAMS["fee_bps"], 10_000)
            * Fraction(str(tick_s)) / SECONDS_PER_YEAR
            / Fraction(str(price_usd))
            * (10 ** TOKEN_DECIMALS)
        )
        fee_usd = (
            float(Fraction(str(aum_usd)))
            * (PARAMS["fee_bps"] / 10_000)
            * (float(tick_s) / SECONDS_PER_YEAR)
        )
        self._accrued_wei[user_id] = self._accrued_wei.get(user_id, 0) + fee_token_wei
        self._accrued_usd[user_id] = self._accrued_usd.get(user_id, 0.0) + fee_usd
        self._denomination[user_id] = denomination
        accrual = FeeAccrual(user_id, tick_s, aum_usd, denomination,
                             price_usd, fee_token_wei, fee_usd)
        self.history.append(accrual)
        return accrual

    def accrued(self, user_id: str) -> int:
        return self._accrued_wei.get(user_id, 0)

    def settle(self, user_id: str) -> Settlement | None:
        """Settle when the accrued batch is worth >= $10 equivalent."""
        usd = self._accrued_usd.get(user_id, 0.0)
        if usd < PARAMS["settlement_batch_floor_usd"]:
            return None
        wei = self._accrued_wei.get(user_id, 0)
        settlement = Settlement(
            user_id=user_id,
            denomination=self._denomination[user_id],
            amount_token_wei=wei,
            amount_usd=usd,
            to=TREASURY,
        )
        self.settlements.append(settlement)
        self._accrued_wei[user_id] = 0
        self._accrued_usd[user_id] = 0.0
        return settlement
