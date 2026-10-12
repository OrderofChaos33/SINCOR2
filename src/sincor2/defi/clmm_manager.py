"""
SINCOR DeFi P02 — Concentrated Liquidity Manager (reference build).

Python reference simulation of the JIT-defense CLMM manager behind SKU
``SINCOR-DEFI-P02-CLMM``. Models the Uniswap V4 hook logic off-chain:

- :class:`JITDetector` — flags atomic same-block add/remove liquidity
  sequences around a swap (the JIT attack pattern), with configurable
  sensitivity to avoid false positives on legitimate rebalancing.
- :class:`TickShiftResponder` — shifts the managed position's ticks away
  from the attack range by ``tick_distance`` steps, bounded by
  ``vol_band``, with cooldown + max-shift guards against oscillation.
- :class:`TransientFee` — surcharge on flagged JIT providers computed from
  captured value; isolated math that can never brick a swap (fail-closed
  to zero).
- :class:`ProceedsDistributor` — captured proceeds distributed pro-rata by
  passive liquidity-seconds to LPs holding through the attack block;
  pull-based claims; 15 bps routed to the canonical treasury.
- :class:`VolRangeAgent` — realized-vol range-width computation; emits
  re-range intents. Live LP is hard-blocked until the auditor gate flips.

Safety rules (hard):
- Default mode is DRY_RUN. Intents are emitted, never executed; nothing
  here touches a chain, a pool, or funds.
- Live LP requires the auditor gate constant to be flipped with a signed
  config; the default refuses.
- Defense pause never blocks user withdrawals or claims.
- All money math is integer-exact (wei); conservation is asserted.
- AUDIT-PREP 2026-09-28 float64 sweep: the JIT flag threshold
  (``removed * 10000 >= added * jit_sensitivity_bps``) is integer-exact;
  the old float64 form false-flagged at wei scale. Remaining float64 is
  deliberate and documented: ``JITFlag.confidence`` is an informational
  ratio bounded in [0,1] (never a money-math input), and
  ``VolRangeAgent.realized_vol`` consumes float *prices* (not wei) as a
  heuristic whose output is snapped to tick spacing and clamped to
  ``vol_band`` — neither feeds settlement, fees, or flag decisions.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

# Catalog gates mirrored as code constants.
VOL_BAND_TICKS = int(os.getenv("CLMM_VOL_BAND_TICKS", "240"))      # max shift bound
TICK_SHIFT_STEP = int(os.getenv("CLMM_TICK_SHIFT_STEP", "60"))     # tick_distance


def _jit_sensitivity_bps() -> int:
    """JIT flag threshold as integer basis points (canonical representation).

    AUDIT NOTE 2026-09-28: this was previously ``float(os.getenv(...))``
    compared as ``removed >= added * 0.95``. float64 carries ~15-16
    significant decimal digits, so for wei-scale ``added`` (up to 2**256)
    the product silently drops low-order digits and the flag/no-flag
    decision becomes input-dependent noise near the boundary — a false
    positive means an unjust surcharge on an honest LP. The threshold is
    now integer bps and the comparison is
    ``removed * 10_000 >= added * bps``, exact for arbitrary-size ints.
    """
    raw_bps = os.getenv("CLMM_JIT_SENSITIVITY_BPS")
    if raw_bps is not None:
        return int(raw_bps)
    # Legacy env var carried a decimal fraction ("0.95"); parse it with
    # Decimal so "0.95" -> 9500 exactly — never round-trip through float64.
    from decimal import Decimal

    return int(Decimal(os.getenv("CLMM_JIT_SENSITIVITY", "0.95")) * 10_000)


JIT_SENSITIVITY_BPS = _jit_sensitivity_bps()  # 9500 == 95%
JIT_SENSITIVITY_DEN = 10_000
COOLDOWN_BLOCKS = int(os.getenv("CLMM_COOLDOWN_BLOCKS", "10"))
MAX_SHIFT_TICKS = int(os.getenv("CLMM_MAX_SHIFT_TICKS", "480"))
TREASURY_CUT_BPS = 15                      # 15 bps of captured proceeds
JIT_FEE_CAP_BPS = int(os.getenv("CLMM_JIT_FEE_CAP_BPS", "100"))  # 1% cap
DRY_RUN = os.getenv("CLMM_DRY_RUN", "1").strip() != "0"
LIVE_LP_AUDITOR_GATE = False               # flip only with signed auditor config

MANAGER_ROLE = "MANAGER_ROLE"
GUARDIAN_ROLE = "GUARDIAN_ROLE"


class UnauthorizedError(PermissionError):
    """Raised when a caller lacks the required role."""


class LiveLPBlockedError(RuntimeError):
    """Raised when live LP is attempted before the auditor gate flips."""


# -- events & state ----------------------------------------------------------
@dataclass(frozen=True)
class LiquidityEvent:
    pool_id: str
    position_id: str
    provider: str
    block_number: int
    kind: str            # "add" | "remove" | "swap"
    liquidity_delta: int # wei of liquidity; swaps carry 0
    tick_lower: int = 0
    tick_upper: int = 0


@dataclass(frozen=True)
class JITFlag:
    position_id: str
    provider: str
    pool_id: str
    block_number: int
    added_wei: int
    removed_wei: int
    captured_value_wei: int
    confidence: float


@dataclass
class ManagedPosition:
    position_id: str
    pool_id: str
    tick_lower: int
    tick_upper: int
    liquidity_wei: int
    last_shift_block: int = -10**9
    cumulative_shift_ticks: int = 0


@dataclass
class RebalanceIntent:
    """Dry-run intent. executed is always False from this module."""

    timestamp: float
    action: str
    pool_id: str
    details: Dict[str, Any] = field(default_factory=dict)
    dry_run: bool = True
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PoolConfig:
    pool_id: str
    tick_spacing: int = 60
    fee_tier_bps: int = 30
    vol_band_ticks: int = VOL_BAND_TICKS
    tick_shift_step: int = TICK_SHIFT_STEP
    # Integer-exact flag threshold in basis points (see _jit_sensitivity_bps).
    # Replaces the old float ``jit_sensitivity`` (removed 2026-09-28 audit-prep:
    # float64 threshold misclassified at wei scale).
    jit_sensitivity_bps: int = JIT_SENSITIVITY_BPS
    cooldown_blocks: int = COOLDOWN_BLOCKS
    max_shift_ticks: int = MAX_SHIFT_TICKS


# -- JIT detection -----------------------------------------------------------
class JITDetector:
    """Flags same-block add/remove sequences around a swap.

    Rule: a provider is flagged when, within ONE block containing a swap,
    it adds liquidity and then removes >= ``jit_sensitivity_bps``/10000 of
    what it added. The comparison is integer-exact
    (``removed * 10_000 >= added * jit_sensitivity_bps``) — never float64,
    which misclassifies at wei scale. Multi-position splitting is
    aggregated per provider per block. Two-block (or longer) sequences are
    legitimate rebalancing and are never flagged.
    """

    def __init__(self, config: PoolConfig):
        self.config = config

    def detect(self, events: List[LiquidityEvent]) -> List[JITFlag]:
        flags: List[JITFlag] = []
        by_block: Dict[int, List[LiquidityEvent]] = defaultdict(list)
        for e in events:
            by_block[e.block_number].append(e)
        for block, evs in sorted(by_block.items()):
            swaps = [e for e in evs if e.kind == "swap"]
            if not swaps:
                continue  # no swap in block: no JIT pattern possible
            adds: Dict[Tuple[str, str], int] = defaultdict(int)
            removes: Dict[Tuple[str, str], int] = defaultdict(int)
            first_pos: Dict[str, str] = {}
            for e in evs:
                key = (e.provider, e.position_id)
                if e.kind == "add":
                    adds[key] += e.liquidity_delta
                    first_pos.setdefault(e.provider, e.position_id)
                elif e.kind == "remove":
                    removes[key] += e.liquidity_delta
                    first_pos.setdefault(e.provider, e.position_id)
            # aggregate per provider across positions (splitting attack)
            prov_added: Dict[str, int] = defaultdict(int)
            prov_removed: Dict[str, int] = defaultdict(int)
            for (provider, _), amt in adds.items():
                prov_added[provider] += amt
            for (provider, _), amt in removes.items():
                prov_removed[provider] += amt
            for provider, added in prov_added.items():
                removed = prov_removed.get(provider, 0)
                if added <= 0:
                    continue
                # Integer-exact threshold (audit-prep 2026-09-28): the old
                # float64 form ``removed >= added * 0.95`` dropped low-order
                # digits at wei scale and false-flagged honest LPs.
                if removed * JIT_SENSITIVITY_DEN >= added * self.config.jit_sensitivity_bps:
                    captured = min(removed, added)
                    flags.append(
                        JITFlag(
                            position_id=first_pos.get(provider, ""),
                            provider=provider,
                            pool_id=self.config.pool_id,
                            block_number=block,
                            added_wei=added,
                            removed_wei=removed,
                            captured_value_wei=captured,
                            # Informational ratio only, bounded in [0, 1];
                            # never an input to money math. float64 relative
                            # error (~2e-16) is immaterial here.
                            confidence=min(1.0, removed / added),
                        )
                    )
        return flags


# -- tick-shift response -----------------------------------------------------
class TickShiftResponder:
    """Shifts the managed position off the JIT range.

    Rules: shift by ``tick_shift_step`` per response, capped per-shift by
    ``vol_band_ticks`` and cumulatively by ``max_shift_ticks``, with a
    ``cooldown_blocks`` gap between shifts. All output is a dry-run
    intent; oscillation is impossible by construction.
    """

    def __init__(self, config: PoolConfig):
        self.config = config

    def respond(
        self, flag: JITFlag, position: ManagedPosition, current_block: int,
        attack_tick_lower: int, attack_tick_upper: int,
    ) -> Tuple[ManagedPosition, RebalanceIntent]:
        since_last = current_block - position.last_shift_block
        if since_last < self.config.cooldown_blocks:
            return position, RebalanceIntent(
                timestamp=time.time(),
                action="shift_ticks",
                pool_id=position.pool_id,
                details={"skipped": "cooldown",
                         "blocks_since_last": since_last,
                         "cooldown_blocks": self.config.cooldown_blocks},
            )
        step = self.config.tick_shift_step
        remaining = self.config.max_shift_ticks - position.cumulative_shift_ticks
        if remaining <= 0:
            return position, RebalanceIntent(
                timestamp=time.time(),
                action="shift_ticks",
                pool_id=position.pool_id,
                details={"skipped": "max_shift_guard",
                         "cumulative_shift_ticks": position.cumulative_shift_ticks},
            )
        step = min(step, remaining, self.config.vol_band_ticks)
        # Shift away from the attack range: move the window up if the attack
        # sits at/below our lower tick, else move down.
        mid_attack = (attack_tick_lower + attack_tick_upper) // 2
        mid_pos = (position.tick_lower + position.tick_upper) // 2
        direction = 1 if mid_attack <= mid_pos else -1
        new_lower = position.tick_lower + direction * step
        new_upper = position.tick_upper + direction * step
        # Snap to tick spacing.
        spacing = self.config.tick_spacing
        new_lower -= new_lower % spacing
        new_upper -= new_upper % spacing
        position.tick_lower = new_lower
        position.tick_upper = new_upper
        position.last_shift_block = current_block
        position.cumulative_shift_ticks += step
        return position, RebalanceIntent(
            timestamp=time.time(),
            action="shift_ticks",
            pool_id=position.pool_id,
            details={
                "tick_lower": new_lower,
                "tick_upper": new_upper,
                "shift_ticks": step,
                "direction": direction,
                "cumulative_shift_ticks": position.cumulative_shift_ticks,
                "jit_block": flag.block_number,
            },
        )


# -- transient fee -----------------------------------------------------------
class TransientFee:
    """Surcharge on flagged JIT providers.

    The surcharge scales with captured value and is capped; honest LPs are
    untouched. Math is isolated: any failure degrades to zero surcharge so
    a revert in fee logic can never brick the swap (non-bricking rule).
    """

    def __init__(self, cap_bps: int = JIT_FEE_CAP_BPS):
        self.cap_bps = cap_bps

    def surcharge_bps(self, flag: JITFlag) -> int:
        try:
            # 1 bps per 1e15 wei captured, capped.
            raw = flag.captured_value_wei // 10**15
            return int(max(0, min(self.cap_bps, raw)))
        except Exception:  # noqa: BLE001 - non-bricking by design
            logger.exception("transient fee math failed; degrading to 0")
            return 0

    def effective_fee_bps(
        self, base_fee_bps: int, provider: str, flags: List[JITFlag]
    ) -> int:
        flagged = {f.provider for f in flags}
        if provider in flagged:
            flag = next(f for f in flags if f.provider == provider)
            return base_fee_bps + self.surcharge_bps(flag)
        return base_fee_bps


# -- proceeds distribution ---------------------------------------------------
@dataclass
class PassiveLP:
    lp_id: str
    liquidity_seconds: int  # passive liquidity held through the attack block
    held_through_attack: bool


class ProceedsDistributor:
    """Pull-based, pro-rata distribution of captured JIT proceeds.

    Rules: only LPs holding through the attack block earn; shares are
    pro-rata by liquidity-seconds, integer-exact (wei); 15 bps goes to the
    treasury; conservation holds exactly
    (distributed + treasury_cut + dust == proceeds).
    """

    def __init__(self):
        self._captures: Dict[str, int] = {}          # capture_id -> proceeds_wei
        self._shares: Dict[str, Dict[str, int]] = {} # capture_id -> lp_id -> wei
        self._claimed: Dict[str, set] = defaultdict(set)

    def record_capture(self, capture_id: str, proceeds_wei: int) -> None:
        if proceeds_wei < 0:
            raise ValueError("proceeds cannot be negative")
        self._captures[capture_id] = proceeds_wei

    def distribute(
        self, capture_id: str, lps: List[PassiveLP]
    ) -> Dict[str, Any]:
        proceeds = self._captures[capture_id]
        eligible = [lp for lp in lps if lp.held_through_attack and lp.liquidity_seconds > 0]
        treasury_cut = proceeds * TREASURY_CUT_BPS // 10_000
        distributable = proceeds - treasury_cut
        total_ls = sum(lp.liquidity_seconds for lp in eligible)
        shares: Dict[str, int] = {}
        if eligible and total_ls > 0 and distributable > 0:
            for lp in eligible:
                shares[lp.lp_id] = distributable * lp.liquidity_seconds // total_ls
        distributed = sum(shares.values())
        dust = distributable - distributed  # floor-division remainder -> treasury
        treasury_total = treasury_cut + dust
        assert distributed + treasury_total == proceeds, "conservation violated"
        self._shares[capture_id] = shares
        return {
            "capture_id": capture_id,
            "proceeds_wei": proceeds,
            "treasury_wei": treasury_total,
            "treasury": TREASURY,
            "distributed_wei": distributed,
            "shares": dict(shares),
            "eligible_lps": len(eligible),
        }

    def claim(self, capture_id: str, lp_id: str) -> int:
        """Pull-based claim. Double claims return 0; never pushes."""
        if lp_id in self._claimed[capture_id]:
            return 0
        amount = self._shares.get(capture_id, {}).get(lp_id, 0)
        if amount > 0:
            self._claimed[capture_id].add(lp_id)
        return amount


# -- realized-vol range agent -------------------------------------------------
class VolRangeAgent:
    """Sets managed range width from realized volatility.

    Emits dry-run re-range intents. The live-LP path is hard-blocked until
    the auditor gate flips with a signed config (fail-closed).
    """

    def __init__(self, config: PoolConfig, base_width_ticks: int = 120):
        self.config = config
        self.base_width_ticks = base_width_ticks

    @staticmethod
    def realized_vol(prices: List[float]) -> float:
        if len(prices) < 2:
            return 0.0
        rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))
                if prices[i - 1] > 0 and prices[i] > 0]
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var)

    def range_width_ticks(self, prices: List[float]) -> int:
        vol = self.realized_vol(prices)
        width = int(self.base_width_ticks * (1.0 + 20.0 * vol))
        width -= width % self.config.tick_spacing  # snap to spacing
        return max(self.config.tick_spacing,
                   min(width, self.config.vol_band_ticks))

    def emit_rerange_intent(
        self, position: ManagedPosition, prices: List[float], mid_tick: int
    ) -> RebalanceIntent:
        width = self.range_width_ticks(prices)
        half = width // 2
        lower = mid_tick - half - (mid_tick - half) % self.config.tick_spacing
        upper = lower + width
        return RebalanceIntent(
            timestamp=time.time(),
            action="rerange",
            pool_id=position.pool_id,
            details={
                "tick_lower": lower,
                "tick_upper": upper,
                "width_ticks": width,
                "realized_vol": self.realized_vol(prices),
                "dry_run": DRY_RUN,
            },
        )

    def open_live_position(self, *args: Any, **kwargs: Any) -> None:
        """Hard-blocked until the auditor gate flips with a signed config."""
        if not LIVE_LP_AUDITOR_GATE:
            raise LiveLPBlockedError(
                "live LP is blocked: auditor gate not flipped (dry_run only)"
            )
        raise NotImplementedError("live execution path is intentionally absent")


# -- access control ----------------------------------------------------------
class AccessControl:
    """MANAGER_ROLE (agent) / GUARDIAN_ROLE (pause defense)."""

    def __init__(self):
        self._roles: Dict[str, set] = defaultdict(set)
        self.paused = False

    def grant(self, role: str, account: str) -> None:
        self._roles[role].add(account)

    def require(self, role: str, caller: str) -> None:
        if caller not in self._roles[role]:
            raise UnauthorizedError(f"{caller} lacks {role}")

    def pause_defense(self, caller: str) -> None:
        self.require(GUARDIAN_ROLE, caller)
        self.paused = True

    def unpause_defense(self, caller: str) -> None:
        self.require(GUARDIAN_ROLE, caller)
        self.paused = False

    def defense_active(self) -> bool:
        return not self.paused

    @staticmethod
    def user_exit_allowed(paused: bool) -> bool:
        """Withdrawals and claims are never blocked by a defense pause."""
        return True


def commitment_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# -- shared price-oracle wiring ------------------------------------------------
# Declares this product's external price needs against the shared oracle
# (sincor2.defi.price_oracle). Reference-backed until live feeds are wired;
# never treated as a live integration.

PRICE_ASSETS = ['ETH/USD', 'USDC/USD']


def price_feed_for(oracle):
    """Bind the shared price oracle to this product's declared assets.

    Returns a ProductPriceFeed; ``feed.price(asset, now)`` raises on any
    oracle failure (fail-closed). Live Chainlink/Pyth feeds are NOT wired —
    production must inject real adapters (see price_oracle module docs).
    """
    from .price_oracle import wiring_for
    return wiring_for("P02_CLMM", oracle)
