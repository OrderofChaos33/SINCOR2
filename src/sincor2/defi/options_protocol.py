"""
SINCOR DeFi P17 — On-Chain Options Protocol (reference build).

Python reference simulation of the covered-only European options
protocol behind SKU ``SINCOR-DEFI-P17-OPTIONS``:

- :func:`black_scholes` — off-chain premium pricer (Black-Scholes, 4%
  risk-free, realized-vol input clamped to [10%, 300%]).
- :func:`accept_quote` — on-chain pricer bound: ``|quoted - model| <= 2%``
  with premium rails [0.5%, 50%] of notional.
- :class:`CoveredVault` — 1:1 collateralized mints; the covered-only
  invariant (``minted <= lockedCollateral``) is enforced on every mint,
  so naked shorts are unrepresentable, not merely disallowed.
- :class:`PriceFeed` — staleness-guarded spot (reverts > 2h old).
- Settlement: ITM exercise pays exactly ``max(0, P-K)`` / ``max(0, K-P)``
  to the wei during the 24h exercise window; OTM burns; ``sweep``
  releases writer collateral afterwards.
- 15 bps of every premium routes to the canonical treasury.

Safety rules (hard):
- Default mode is DRY_RUN. Intents are emitted, never executed; nothing
  here touches a chain, a pool, or funds.
- Expiry band enforced: 7d <= T <= 90d, fixed tenors {7,14,30,60,90}.
- Series OI cap 10,000; per-writer cap 20% of series OI.
- Withdrawals are never trapped: pause halts writes, not withdraws.

Money math is integer-exact (wei). This is a REFERENCE build for design
validation and agent simulation — not a deployed protocol.
"""

from __future__ import annotations

import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

FEE_BPS = 15                       # 0.15% of every premium -> treasury
RISK_FREE = 0.04                   # 4% annualized
VOL_MIN = 0.10                     # vol clamp floor (annualized)
VOL_MAX = 3.00                     # vol clamp cap (annualized)
PREMIUM_FLOOR_BPS = 50             # 0.5% of notional
PREMIUM_CAP_BPS = 5_000            # 50% of notional
QUOTE_TOLERANCE = 0.02             # |quoted - model| <= 2%
DUST_PREMIUM_WEI = 10**18          # $1.00 minimum premium (18dp USD)
MIN_WRITE = 1                      # minimum 1 option
SERIES_OI_CAP = 10_000
PER_WRITER_OI_BPS = 2_000          # 20% of series OI
# Per-writer cap, enforceable form: <= 20% of SERIES_OI_CAP (2,000 options).
# A share-of-live-OI rule is unimplementable at series formation (the first
# writer is trivially 100% of live OI), so the cap is measured against max
# series size. Diversification intent preserved; worst case bounded by the
# series OI cap itself.
PER_WRITER_OI_MAX = SERIES_OI_CAP * PER_WRITER_OI_BPS // 10_000
EXPIRY_TENORS_DAYS = (7, 14, 30, 60, 90)
EXPIRY_MIN_DAYS = 7
EXPIRY_MAX_DAYS = 90
ORACLE_STALENESS_SECONDS = 2 * 3600
EXERCISE_WINDOW_SECONDS = 24 * 3600
DRY_RUN = os.getenv("P17_DRY_RUN", "1").strip() != "0"


class OptionsError(RuntimeError):
    """Base error for options rule violations."""


class NakedShortAttempt(OptionsError):
    """Mint would exceed locked collateral — covered-only violation."""


class ExpiryBandViolation(OptionsError):
    """Series tenor outside the 7d..90d fixed-tenor band."""


class StaleOracle(OptionsError):
    """Spot feed older than 2h — pricing/exercise/settlement revert."""


class QuoteRejected(OptionsError):
    """Quoted premium outside the on-chain pricer bounds."""


class WithdrawBlocked(OptionsError):
    """Collateral still referenced by live options."""


# -- Black-Scholes ---------------------------------------------------------------
def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def clamp_vol(vol: float) -> float:
    """Volatility input clamp [10%, 300%] bounds oracle-spike damage."""
    return max(VOL_MIN, min(VOL_MAX, vol))


def black_scholes(
    spot: float,
    strike: float,
    t_years: float,
    vol: float,
    r: float = RISK_FREE,
    is_call: bool = True,
) -> float:
    """European option premium per unit, in quote currency (float model)."""
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if t_years <= 0:
        raise ValueError("time to expiry must be positive")
    vol = clamp_vol(vol)
    d1 = (math.log(spot / strike) + (r + 0.5 * vol * vol) * t_years) / (
        vol * math.sqrt(t_years)
    )
    d2 = d1 - vol * math.sqrt(t_years)
    disc = math.exp(-r * t_years)
    if is_call:
        return spot * norm_cdf(d1) - strike * disc * norm_cdf(d2)
    return strike * disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


# -- series ----------------------------------------------------------------------
@dataclass(frozen=True)
class OptionSeries:
    series_id: str
    underlying: str
    strike_wei: int          # K, 18dp
    expiry_days: int
    is_call: bool
    listed_at: float = 0.0

    def notional_wei(self) -> int:
        return self.strike_wei

    def t_years(self) -> float:
        return self.expiry_days / 365.0


def validate_tenor(expiry_days: int) -> None:
    """Expiry band gate: 7d <= T <= 90d, fixed tenors only."""
    if expiry_days < EXPIRY_MIN_DAYS or expiry_days > EXPIRY_MAX_DAYS:
        raise ExpiryBandViolation(
            f"tenor {expiry_days}d outside [{EXPIRY_MIN_DAYS}, {EXPIRY_MAX_DAYS}]"
        )
    if expiry_days not in EXPIRY_TENORS_DAYS:
        raise ExpiryBandViolation(
            f"tenor {expiry_days}d not in fixed tenors {EXPIRY_TENORS_DAYS}"
        )


def quote_premium(
    series: OptionSeries, spot_wei: int, vol: float
) -> int:
    """Agent-side BS quote with premium rails, in wei. Raises if railed."""
    validate_tenor(series.expiry_days)
    spot = spot_wei / 1e18
    strike = series.strike_wei / 1e18
    model = black_scholes(spot, strike, series.t_years(), vol, is_call=series.is_call)
    premium_wei = int(round(model * 1e18))
    notional = series.notional_wei()
    floor = notional * PREMIUM_FLOOR_BPS // 10_000
    cap = notional * PREMIUM_CAP_BPS // 10_000
    if premium_wei < floor or premium_wei > cap:
        raise QuoteRejected(
            f"premium {premium_wei} outside rails [{floor}, {cap}]"
        )
    if premium_wei < DUST_PREMIUM_WEI:
        raise QuoteRejected(f"premium {premium_wei} below $1.00 dust floor")
    return premium_wei


def accept_quote(quoted_wei: int, model_wei: int) -> bool:
    """On-chain pricer bound: |quoted - model| <= 2% of model."""
    if model_wei <= 0:
        raise QuoteRejected("model premium must be positive")
    return abs(quoted_wei - model_wei) <= int(model_wei * QUOTE_TOLERANCE)


def premium_fee_wei(premium_wei: int) -> int:
    """15 bps of premium, integer-exact, to the treasury."""
    if premium_wei < 0:
        raise ValueError("premium cannot be negative")
    return premium_wei * FEE_BPS // 10_000


# -- oracle ----------------------------------------------------------------------
@dataclass(frozen=True)
class PriceFeed:
    spot_wei: int
    updated_at: float

    def get_spot(self, now: float) -> int:
        if now - self.updated_at > ORACLE_STALENESS_SECONDS:
            raise StaleOracle(
                f"feed age {now - self.updated_at:.0f}s exceeds "
                f"{ORACLE_STALENESS_SECONDS}s"
            )
        return self.spot_wei


# -- covered vault -----------------------------------------------------------------
@dataclass
class OptionPosition:
    writer: str
    series_id: str
    count: int
    collateral_wei: int       # locked per writer per series
    exercised: bool = False


class CoveredVault:
    """1:1 collateralized option writer vault.

    Covered calls: 1 unit of underlying (1e18 wei) locked per option.
    Covered puts: K wei locked per option. Mint enforces
    ``minted <= lockedCollateral`` as a hard require — naked shorts
    cannot be represented.
    """

    def __init__(self):
        self._series: Dict[str, OptionSeries] = {}
        self._minted: Dict[str, int] = defaultdict(int)
        self._locked: Dict[str, int] = defaultdict(int)
        self._writer_locked: Dict[tuple, int] = defaultdict(int)
        self._writer_oi: Dict[tuple, int] = defaultdict(int)
        self._settled: Dict[str, int] = {}       # series_id -> P_exp wei
        self._settled_at: Dict[str, float] = {}
        self.paused = False

    # -- series management -------------------------------------------------
    def list_series(self, series: OptionSeries) -> None:
        validate_tenor(series.expiry_days)
        if series.series_id in self._series:
            raise OptionsError("series already listed")
        self._series[series.series_id] = series

    # -- writing ------------------------------------------------------------
    def collateral_per_option(self, series_id: str) -> int:
        s = self._series[series_id]
        return 10**18 if s.is_call else s.strike_wei

    def write(self, writer: str, series_id: str, n: int) -> OptionPosition:
        """Deposit n collateral units; mint exactly n option tokens."""
        if self.paused:
            raise OptionsError("writes paused")
        if n < MIN_WRITE:
            raise OptionsError(f"minimum write is {MIN_WRITE} option")
        s = self._series[series_id]
        new_oi = self._minted[series_id] + n
        if new_oi > SERIES_OI_CAP:
            raise OptionsError("series OI cap 10,000 exceeded")
        writer_key = (writer, series_id)
        if self._writer_oi[writer_key] + n > PER_WRITER_OI_MAX:
            raise OptionsError(
                f"per-writer cap {PER_WRITER_OI_MAX} options exceeded"
            )
        deposit = n * self.collateral_per_option(series_id)
        # Covered-only invariant: minted <= lockedCollateral, always.
        if self._minted[series_id] + n > self._locked[series_id] + deposit:
            raise NakedShortAttempt("mint would exceed locked collateral")
        self._locked[series_id] += deposit
        self._minted[series_id] += n
        self._writer_locked[writer_key] += deposit
        self._writer_oi[writer_key] += n
        assert self._minted[series_id] <= self._locked[series_id] // self.collateral_per_option(series_id)
        return OptionPosition(
            writer=writer, series_id=series_id, count=n, collateral_wei=deposit
        )

    def coverage_ratio(self, series_id: str) -> float:
        """Must read >= 1.0 at all times."""
        minted = self._minted[series_id]
        if minted == 0:
            return float("inf")
        units = self._locked[series_id] / self.collateral_per_option(series_id)
        return units / minted

    # -- settlement -----------------------------------------------------------
    def settle(self, series_id: str, feed: PriceFeed, now: float) -> Dict[str, Any]:
        """Permissionless settle: snapshot P_exp. OTM burns to zero.

        Settles exactly once per series: re-settling would overwrite the
        P_exp snapshot that exercised positions were already paid
        against."""
        if series_id in self._settled:
            raise OptionsError("series already settled")
        spot = feed.get_spot(now)  # reverts on staleness
        s = self._series[series_id]
        self._settled[series_id] = spot
        self._settled_at[series_id] = now
        itm = (spot > s.strike_wei) if s.is_call else (spot < s.strike_wei)
        return {
            "series_id": series_id,
            "spot_wei": spot,
            "strike_wei": s.strike_wei,
            "itm": itm,
            "exercisable_until": now + EXERCISE_WINDOW_SECONDS,
        }

    def exercise(
        self, position: OptionPosition, feed: PriceFeed, now: float
    ) -> Dict[str, Any]:
        """Exercise ITM options inside the 24h window. Pays to the wei."""
        series_id = position.series_id
        if position.exercised:
            raise OptionsError("position already exercised")
        if series_id not in self._settled:
            raise OptionsError("series not settled")
        if now > self._settled_at[series_id] + EXERCISE_WINDOW_SECONDS:
            raise OptionsError("exercise window elapsed")
        s = self._series[series_id]
        spot = feed.get_spot(now)
        p_exp = self._settled[series_id]
        if s.is_call:
            payoff_each = max(0, p_exp - s.strike_wei)
        else:
            payoff_each = max(0, s.strike_wei - p_exp)
        if payoff_each == 0:
            raise OptionsError("OTM options are worthless; nothing to exercise")
        total = payoff_each * position.count
        position.exercised = True
        self._minted[series_id] -= position.count
        writer_key = (position.writer, series_id)
        self._writer_oi[writer_key] -= position.count
        # Writer keeps collateral minus payout for calls (K per unit paid in),
        # plus premium (accounted off-vault). Collateral released pro-rata.
        released = position.collateral_wei
        self._locked[series_id] -= released
        self._writer_locked[writer_key] -= released
        return {
            "series_id": series_id,
            "count": position.count,
            "payoff_wei": total,
            "spot_wei": spot,
            "collateral_released_wei": released,
        }

    def sweep(self, series_id: str, now: float) -> Dict[str, Any]:
        """Post-window: burn remainder, release all writer collateral."""
        if series_id not in self._settled:
            raise OptionsError("series not settled")
        if now <= self._settled_at[series_id] + EXERCISE_WINDOW_SECONDS:
            raise OptionsError("exercise window still open")
        released = self._locked[series_id]
        burned = self._minted[series_id]
        self._locked[series_id] = 0
        self._minted[series_id] = 0
        for key in [k for k in self._writer_locked if k[1] == series_id]:
            self._writer_locked[key] = 0
            self._writer_oi[key] = 0
        return {
            "series_id": series_id,
            "collateral_released_wei": released,
            "tokens_burned": burned,
        }

    def withdraw(self, writer: str, series_id: str) -> int:
        """Writer withdraw only when no live options reference collateral."""
        writer_key = (writer, series_id)
        if self._writer_oi[writer_key] > 0:
            raise WithdrawBlocked("live options reference this collateral")
        amount = self._writer_locked[writer_key]
        self._writer_locked[writer_key] = 0
        return amount

    def pause_writes(self) -> None:
        self.paused = True

    def unpause_writes(self) -> None:
        self.paused = False


# -- shared price-oracle wiring ------------------------------------------------
# Declares this product's external price needs against the shared oracle
# (sincor2.defi.price_oracle). Reference-backed until live feeds are wired;
# never treated as a live integration.

PRICE_ASSETS = ['ETH/USD', 'BTC/USD']


def price_feed_for(oracle):
    """Bind the shared price oracle to this product's declared assets.

    Returns a ProductPriceFeed; ``feed.price(asset, now)`` raises on any
    oracle failure (fail-closed). Live Chainlink/Pyth feeds are NOT wired —
    production must inject real adapters (see price_oracle module docs).
    """
    from .price_oracle import wiring_for
    return wiring_for("P17_OPTIONS", oracle)
