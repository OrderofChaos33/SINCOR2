"""
SINCOR DeFi P06 — Perp DEX Hedging Swarm (reference build).

Python reference simulation of the delta-neutral funding harvest behind
SKU ``SINCOR-DEFI-P06-PERPS`` (LST collateral + equal perp short):

- :func:`size_short_notional` — sizing engine: short notional = spot
  exposure x (1 + 5% liq_headroom). Integer-only math: the on-chain model
  and this reference agree to the wei by construction.
- :class:`DeltaBand` — 3% band on DRIFT FROM ENTRY with 1% hysteresis
  (re-arm only inside 2%): zero redundant rebalances by construction.
  (Deviation: the mandated 5% sizing over-hedge makes a naive absolute
  3% band unsatisfiable at open; drift-banding is the consistent reading
  — see class docstring; flagged for audit.)
  (re-arm only inside 2%): zero redundant rebalances by construction.
- :class:`FundingMonitor` — 15-min check-ins; entry requires annualized
  funding >= +8% now AND 24h average >= +5%; two consecutive negative
  hourly checkpoints -> unwind.
- :class:`LiqBuffer` — 4x / 2x / 1.5x maintenance-margin ladder:
  agent-unwind below 2x, contract pauses new opens below 1.5x.
- :class:`HedgeEngine` — collateral custody, gate checks, open/rebalance/
  unwind, 12 bps fee on positive realized P&L only, pull-fallback
  unwind that can never brick.
- :class:`LiveGate` — liveEnabled=false default; every live path reverts
  until the conversion proof is presented.

Money math is integer-only (microdollars for USD, 1e8 fixed-point prices,
token wei for collateral): no floats anywhere near sizing or P&L.

Safety rules (hard):
- Default mode is DRY_RUN. Intents describe actions; ``executed`` is
  always False; nothing here touches a venue, a wallet, or funds.
- Isolated margin only — cross margin is rejected at the adapter layer.
- Oracle prices older than 120s: no new actions (fail-static).
- Unwind can never brick: reverting recipients fall back to pull claims.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

# Catalog gates mirrored as code constants (all integer).
FEE_BPS = 12                                   # on positive realized P&L only
DELTA_BAND_BPS = 300                           # |net delta| <= 3% of NAV
HYSTERESIS_REARM_BPS = 200                     # re-arm only inside 2%
FUNDING_ENTRY_NOW_BPS = 800                    # +8% annualized now
FUNDING_ENTRY_AVG_BPS = 500                    # +5% 24h average
FUNDING_KILL_NEGATIVE_RUN = 2                  # 2 consecutive negative hours
LIQ_TARGET_MULT_BP = 40_000                    # 4x maintenance margin
LIQ_AGENT_UNWIND_MULT_BP = 20_000              # 2x -> agent unwind
LIQ_PAUSE_MULT_BP = 15_000                     # 1.5x -> pause new opens
LIQ_HEADROOM_BPS = 500                         # 5% sizing over-hedge
ORACLE_STALENESS_S = 120
CHECKIN_S = 15 * 60
MIN_CAPITAL_MICRO = 250_000_000                # $250 in microdollars
MAX_ALLOC_PCT = 0.15
PRICE_FP = 10**8                               # fixed-point price scale
TOKEN_WEI = 10**18
DRY_RUN = os.getenv("PERP_DRY_RUN", "1").strip() != "0"

GUARDIAN_ROLE = "GUARDIAN_ROLE"


class PerpError(Exception):
    """Base error for perp-swarm rule violations."""


class LiveBlockedError(PerpError):
    """Live path attempted before the conversion proof."""


class StaleOracleError(PerpError):
    """Oracle price older than 120s: no new actions."""


class MarginModeError(PerpError):
    """Cross margin requested: isolated margin only."""


class GateViolationError(PerpError):
    """A delta/funding/liq gate refused the action."""


class UnauthorizedError(PerpError):
    """Caller lacks the required role."""


# -- fixed-point helpers -------------------------------------------------------
def microdollars(usd: float) -> int:
    return int(round(usd * 1_000_000))


def spot_exposure_micro(collateral_wei: int, price_fp: int) -> int:
    """LST collateral value in microdollars. Integer-only."""
    return collateral_wei * price_fp // (TOKEN_WEI * 100)


def size_short_notional(collateral_wei: int, price_fp: int,
                        headroom_bps: int = LIQ_HEADROOM_BPS) -> int:
    """Perp short notional (microdollars) = spot x (1 + liq_headroom).

    Integer-only: the on-chain sizing matches this reference to the wei
    by construction (acceptance #1).
    """
    spot = spot_exposure_micro(collateral_wei, price_fp)
    return spot * (10_000 + headroom_bps) // 10_000


# -- delta band ------------------------------------------------------------------
class DeltaBand:
    """3% delta band with 1% hysteresis, measured on DRIFT FROM ENTRY.

    Spec-deviation note (flagged for audit): the spec mandates a 5%
    liq_headroom sizing over-hedge, which puts absolute net delta at
    ~500 bps at entry — unsatisfiable under a naive absolute reading of
    the 3% band (the spec's own "the band absorbs it" parenthetical only
    holds if the band measures drift). This reference therefore bands
    drift from the entry hedge ratio: the band absorbs drift, exactly as
    the spec's sizing note describes. Audit must either ratify
    drift-banding or cut headroom to <= 3%.

    State machine: ARMED -> (drift exits 3%) -> TRIPPED (rebalance) ->
    re-arm only when drift is back inside 2%. Signals fire only on
    transitions, so band-edge sequences produce zero redundant rebalances.
    """

    def __init__(self, band_bps: int = DELTA_BAND_BPS,
                 rearm_bps: int = HYSTERESIS_REARM_BPS):
        self.band_bps = band_bps
        self.rearm_bps = rearm_bps
        self.tripped = False

    def check(self, net_delta_bps_abs: int) -> str:
        """Returns 'rebalance' | 'rearm' | 'hold'."""
        if not self.tripped and net_delta_bps_abs > self.band_bps:
            self.tripped = True
            return "rebalance"
        if self.tripped and net_delta_bps_abs <= self.rearm_bps:
            self.tripped = False
            return "rearm"
        return "hold"


# -- funding monitor ---------------------------------------------------------------
@dataclass(frozen=True)
class FundingCheckpoint:
    hour_ts: int
    annualized_bps: int  # signed


class FundingMonitor:
    """Hourly funding checkpoints; entry gate and flip-kill rule."""

    def __init__(self):
        self._checkpoints: List[FundingCheckpoint] = []

    def record(self, hour_ts: int, annualized_bps: int) -> None:
        self._checkpoints.append(FundingCheckpoint(hour_ts, annualized_bps))

    def _last24h(self) -> List[FundingCheckpoint]:
        if not self._checkpoints:
            return []
        cutoff = self._checkpoints[-1].hour_ts - 24 * 3600
        return [c for c in self._checkpoints if c.hour_ts > cutoff]

    def entry_allowed(self) -> Tuple[bool, str]:
        """Entry: >= +8% now AND 24h average >= +5% (no spike-chasing)."""
        if not self._checkpoints:
            return False, "no funding data"
        now_bps = self._checkpoints[-1].annualized_bps
        window = self._last24h()
        avg_bps = sum(c.annualized_bps for c in window) // max(len(window), 1)
        if now_bps < FUNDING_ENTRY_NOW_BPS:
            return False, f"funding now {now_bps} bps < +8% entry"
        if avg_bps < FUNDING_ENTRY_AVG_BPS:
            return False, f"24h avg {avg_bps} bps < +5%: spike-chasing refused"
        return True, "entry gate passed"

    def kill_signaled(self) -> bool:
        """Kill: negative for 2 consecutive hourly checkpoints -> unwind."""
        if len(self._checkpoints) < FUNDING_KILL_NEGATIVE_RUN:
            return False
        return all(c.annualized_bps < 0
                   for c in self._checkpoints[-FUNDING_KILL_NEGATIVE_RUN:])


# -- liquidation buffer --------------------------------------------------------------
class LiqBuffer:
    """4x / 2x / 1.5x maintenance-margin health ladder (integer bps)."""

    @staticmethod
    def ratio_bp(equity_micro: int, maintenance_micro: int) -> int:
        if maintenance_micro <= 0:
            raise PerpError("maintenance margin must be positive")
        return equity_micro * 10_000 // maintenance_micro

    @classmethod
    def health(cls, equity_micro: int,
               maintenance_micro: int) -> Tuple[str, int]:
        """Returns (status, ratio_bp): ok | agent_unwind | pause_opens."""
        ratio = cls.ratio_bp(equity_micro, maintenance_micro)
        if ratio < LIQ_PAUSE_MULT_BP:
            return "pause_opens", ratio
        if ratio < LIQ_AGENT_UNWIND_MULT_BP:
            return "agent_unwind", ratio
        return "ok", ratio


# -- live gate -----------------------------------------------------------------------
class LiveGate:
    """Conversion-proof gate: liveEnabled=false until the ceremony."""

    def __init__(self):
        self.live_enabled = False

    def present_conversion_proof(self, founder_marker: str) -> None:
        if not founder_marker:
            raise PerpError("conversion proof requires founder marker")
        self.live_enabled = True

    def assert_live(self) -> None:
        if not self.live_enabled:
            raise LiveBlockedError(
                "live path blocked: conversion proof not presented")


# -- oracle ----------------------------------------------------------------------------
@dataclass
class OraclePrice:
    price_fp: int
    updated_ts: float


class PriceFeed:
    def __init__(self):
        self._price: Optional[OraclePrice] = None

    def update(self, price_fp: int, ts: Optional[float] = None) -> None:
        self._price = OraclePrice(price_fp, ts if ts is not None else time.time())

    def get(self, now: Optional[float] = None) -> int:
        now = now if now is not None else time.time()
        if self._price is None:
            raise StaleOracleError("no oracle price published")
        if now - self._price.updated_ts > ORACLE_STALENESS_S:
            raise StaleOracleError(
                f"oracle price stale: {now - self._price.updated_ts:.0f}s > 120s")
        return self._price.price_fp

    def sync_from_oracle(self, feed, asset: str,
                         now: Optional[float] = None) -> int:
        """Pull the latest shared-oracle price into this feed.

        ``feed`` is a ProductPriceFeed bound to P06 (see
        :func:`price_feed_for`). The shared oracle's guards (staleness,
        deviation, circuit breaker) apply first; any oracle failure raises
        and this feed is left untouched (fail-closed, fail-static).
        """
        now = now if now is not None else time.time()
        price_fp = feed.price(asset, now)
        self.update(price_fp, now)
        return price_fp


# -- hedge engine ------------------------------------------------------------------------
@dataclass
class Position:
    position_id: str
    collateral_wei: int
    short_notional_micro: int
    entry_price_fp: int
    opened_ts: float
    entry_net_bps: int = 0  # signed net-delta baseline for drift banding
    margin_mode: str = "isolated"


@dataclass
class HedgeIntent:
    timestamp: float
    action: str  # open | rebalance | unwind | hold
    details: Dict[str, Any] = field(default_factory=dict)
    dry_run: bool = True
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class HedgeEngine:
    """Custodian + gate enforcer (reference for PerpHedgeEngine.sol)."""

    def __init__(self, treasury: str = TREASURY,
                 reverting: Optional[set] = None,
                 live_gate: Optional[LiveGate] = None):
        self.treasury = treasury
        self._reverting = reverting or set()
        self.live_gate = live_gate or LiveGate()
        self.feed = PriceFeed()
        self.funding = FundingMonitor()
        self.band = DeltaBand()
        self._positions: Dict[str, Position] = {}
        self._seq = 0
        self.opens_paused = False
        self._pending: Dict[str, int] = defaultdict(int)  # pull fallback
        self.treasury_fees_micro = 0

    # -- gates ---------------------------------------------------------
    def _check_open_gates(self, collateral_wei: int, price_fp: int,
                          swarm_capital_micro: int,
                          margin_mode: str) -> Tuple[int, int]:
        if margin_mode != "isolated":
            raise MarginModeError("cross margin forbidden: isolated only")
        if self.opens_paused:
            raise GateViolationError("opens paused: liq buffer below 1.5x")
        self.live_gate.assert_live()
        spot = spot_exposure_micro(collateral_wei, price_fp)
        if spot < MIN_CAPITAL_MICRO:
            raise GateViolationError(
                f"position ${spot / 1e6:.2f} below $250 minimum")
        if swarm_capital_micro > 0 and \
                spot * 100 > swarm_capital_micro * int(MAX_ALLOC_PCT * 100):
            raise GateViolationError("position exceeds 15% of swarm capital")
        short = size_short_notional(collateral_wei, price_fp)
        # delta_band gate: measured on drift from entry (see DeltaBand
        # deviation note). At open, drift is 0 by construction; the gate
        # therefore cannot trip here. What IS enforced: the sizing matches
        # the reference model (it does by construction) and the projected
        # entry net-delta stays within the designed headroom envelope.
        nav = max(spot, 1)
        entry_net_bps = (spot - short) * 10_000 // nav
        if abs(entry_net_bps) > LIQ_HEADROOM_BPS + 100:
            raise GateViolationError(
                f"entry net delta {entry_net_bps} bps outside headroom envelope")
        ok, reason = self.funding.entry_allowed()
        if not ok:
            raise GateViolationError(f"funding_sign gate: {reason}")
        return short, entry_net_bps

    def open(self, collateral_wei: int, swarm_capital_micro: int,
             margin_mode: str = "isolated",
             now: Optional[float] = None) -> Tuple[Position, HedgeIntent]:
        now = now if now is not None else time.time()
        price_fp = self.feed.get(now)  # staleness raises: fail-static
        short, entry_net_bps = self._check_open_gates(collateral_wei, price_fp,
                                                      swarm_capital_micro,
                                                      margin_mode)
        self._seq += 1
        pos = Position(f"pos-{self._seq}", collateral_wei, short, price_fp,
                       now, entry_net_bps, margin_mode)
        self._positions[pos.position_id] = pos
        return pos, HedgeIntent(now, "open",
                                {"position_id": pos.position_id,
                                 "collateral_wei": collateral_wei,
                                 "short_notional_micro": short,
                                 "entry_price_fp": price_fp})

    # -- maintain --------------------------------------------------------
    def check_in(self, position_id: str, equity_micro: int,
                 maintenance_micro: int,
                 now: Optional[float] = None) -> HedgeIntent:
        """One 15-min check-in: funding kill, liq ladder, delta band."""
        now = now if now is not None else time.time()
        pos = self._positions[position_id]
        if self.funding.kill_signaled():
            return self._unwind(pos, now, reason="funding_sign flip kill")
        status, ratio = LiqBuffer.health(equity_micro, maintenance_micro)
        if status == "pause_opens":
            self.opens_paused = True
        if status in ("agent_unwind", "pause_opens"):
            return self._unwind(pos, now,
                                reason=f"liq_buffer breach: {status} @ {ratio}bp")
        price_fp = self.feed.get(now)
        spot = spot_exposure_micro(pos.collateral_wei, price_fp)
        nav = max(spot, 1)
        net_bps = (spot - pos.short_notional_micro) * 10_000 // nav
        drift_bps = abs(net_bps - pos.entry_net_bps)
        signal = self.band.check(drift_bps)
        if signal == "rebalance":
            return HedgeIntent(now, "rebalance",
                               {"position_id": position_id,
                                "net_delta_bps": net_bps,
                                "drift_bps": drift_bps})
        return HedgeIntent(now, "hold",
                           {"position_id": position_id,
                            "net_delta_bps": net_bps,
                            "drift_bps": drift_bps,
                            "margin_ratio_bp": ratio})

    def rebalance(self, position_id: str,
                  now: Optional[float] = None) -> HedgeIntent:
        """Resize the short to 105% of current spot and reset the drift
        baseline. Returns the executed-reference intent.

        Spec acceptance #5 names rebalance as a live path: it reverts
        with liveEnabled=false. (check_in may still return rebalance
        *intents* — intents are dry-run signals, never execution.)
        """
        self.live_gate.assert_live()
        now = now if now is not None else time.time()
        pos = self._positions[position_id]
        price_fp = self.feed.get(now)
        new_short = size_short_notional(pos.collateral_wei, price_fp)
        spot = spot_exposure_micro(pos.collateral_wei, price_fp)
        pos.short_notional_micro = new_short
        pos.entry_net_bps = (spot - new_short) * 10_000 // max(spot, 1)
        self.band.tripped = False
        return HedgeIntent(now, "rebalance",
                           {"position_id": position_id,
                            "new_short_notional_micro": new_short,
                            "baseline_reset": True})

    # -- unwind ----------------------------------------------------------
    @staticmethod
    def fee_micro(realized_pnl_micro: int) -> int:
        """12 bps on positive realized P&L only."""
        return max(0, realized_pnl_micro) * FEE_BPS // 10_000

    def _unwind(self, pos: Position, now: float, reason: str) -> HedgeIntent:
        price_fp = self.feed.get(now)
        spot = spot_exposure_micro(pos.collateral_wei, price_fp)
        # P&L on the oracle price basis (the venue settles funding on oracle).
        entry_spot = spot_exposure_micro(pos.collateral_wei, pos.entry_price_fp)
        # short leg P&L: short profits when price falls.
        short_pnl = pos.short_notional_micro * (pos.entry_price_fp - price_fp) \
            // pos.entry_price_fp
        realized = (spot - entry_spot) + short_pnl
        fee = self.fee_micro(realized)
        self.treasury_fees_micro += fee
        payout = spot + realized - fee
        recipient = pos.position_id  # swarm vault claim key (reference)
        if recipient in self._reverting:
            self._pending[recipient] += payout  # pull fallback: never bricks
            settled = 0
        else:
            settled = payout
        del self._positions[pos.position_id]
        return HedgeIntent(now, "unwind",
                           {"position_id": pos.position_id, "reason": reason,
                            "realized_pnl_micro": realized,
                            "fee_micro": fee, "treasury": self.treasury,
                            "settled_micro": settled,
                            "pending_micro": payout - settled})

    def withdraw_pending(self, recipient: str) -> int:
        amount = self._pending.get(recipient, 0)
        self._pending[recipient] = 0
        return amount

    # -- admin -----------------------------------------------------------
    def guardian_pause_opens(self, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self.opens_paused = True

    def guardian_unpause_opens(self, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self.opens_paused = False


def commitment_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


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
    return wiring_for("P06_PERPS", oracle)
