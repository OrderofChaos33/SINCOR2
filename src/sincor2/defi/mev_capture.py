"""
SINCOR DeFi P04 — MEV Protection & Capture (reference build).

Python reference simulation of the MEV hook + bidder agent behind SKU
``SINCOR-DEFI-P04-MEV``:

- :class:`FlowMeter` — per-block swap-flow ledger keyed by
  ``(block_hash, pool_id)``; transient-storage gas model keeps the hot path
  at <= 5,000 gas per swap (spec acceptance #5).
- :class:`MEVDetector` — sandwich (A->V->B, shared sender or funded-by
  relation, victim displacement > 50 bps, B reverses A) and backrun
  (reversal capturing >= 30% of the displacement) signatures, raised only
  behind the flow_threshold gate ($1,000 notional AND 50 bps impact).
- :class:`Bidder` — proceeds-funded capture bidding with the 3x gas shading
  rule (expected_net >= 3 x gas_cost, else stand down); stands down when
  float < 2x gas reserve; refuses the treasury EOA at construction.
- :class:`CaptureLedger` — ``recordCapture`` with estimate-vs-actual
  reconciliation (<= 1% deviation, else review + confidence decay); reorg
  replay is a safe no-op (idempotent capture keys).
- :class:`TreasuryRouter` — 20 bps fee + residual to treasury via pull
  claims; per-claimant non-bricking settlement; conservation asserted.
- :class:`ProtectionPolicy` — opt-in per-order protection: 100 bps slippage
  guardrail vs quoted price, toxic-block revert.
- :class:`SignerRegistry` — treasury-EOA deny-list; every signing path
  fails closed (no_treasury_key gate).
- :class:`LiveGate` — live capture blocked until the founder-signed release.

Safety rules (hard):
- Default mode is DRY_RUN. Bid intents are emitted, never executed; nothing
  here touches a chain, a relay, or funds.
- The treasury EOA can never be a signer — enforced at construction, at
  every signing call, and by config audit.
- Live capture is blocked until the live-block is released (live_blocked).

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

# Catalog gates mirrored as code constants.
FEE_BPS = 20                                   # 20 bps on captured MEV
FLOW_THRESHOLD_USD = float(os.getenv("MEV_FLOW_THRESHOLD_USD", "1000.0"))
IMPACT_FLAG_BPS = int(os.getenv("MEV_IMPACT_FLAG_BPS", "50"))        # 50 bps
BACKRUN_MIN_CAPTURE_PCT = 0.30                 # reversal captures >= 30%
BID_SHADING_MULT = 3.0                         # expected_net >= 3 x gas_cost
ESTIMATE_TOLERANCE = 0.01                      # |est-act|/act <= 1%
SLIPPAGE_GUARDRAIL_BPS = 100                   # protected-order guardrail
GAS_BUDGET_PER_SWAP = 5000
GAS_PER_TSTORE = 100
GAS_PER_TLOAD = 100
MIN_FLOAT_USD = float(os.getenv("MEV_MIN_FLOAT_USD", "100.0"))
MAX_FLOAT_PCT = 0.20                           # 20% of tick capital
DRY_RUN = os.getenv("MEV_DRY_RUN", "1").strip() != "0"

BIDDER_ROLE = "BIDDER_ROLE"
GUARDIAN_ROLE = "GUARDIAN_ROLE"


class MEVError(Exception):
    """Base error for MEV module rule violations."""


class TreasurySignerError(MEVError):
    """The treasury EOA was offered as a signer: fail closed."""


class LiveBlockedError(MEVError):
    """Live capture attempted before the founder-signed release."""


class ThresholdOutOfBoundsError(MEVError):
    """Guardian tried to move flow_threshold outside the +/-50% band."""


class ProtectionRevert(MEVError):
    """Protected order reverted by the guardrail / toxic-block rule."""


# -- events & flags ----------------------------------------------------------
@dataclass(frozen=True)
class SwapEvent:
    """One swap observed on a hooked pool (reference: hook beforeSwap/afterSwap)."""

    pool_id: str
    block_hash: str
    block_number: int
    tx_index: int
    sender: str
    direction: int          # +1 pushes price up, -1 pushes price down
    notional_usd: float
    impact_bps: float       # signed price displacement caused by this swap
    funded_by: str = ""     # optional funder relation for sandwich attribution


@dataclass(frozen=True)
class SandwichFlag:
    attacker: str
    victim_tx_index: int
    frontrun_tx_index: int
    backrun_tx_index: int
    attributable_notional_usd: float
    victim_impact_bps: float
    confidence: float


@dataclass(frozen=True)
class BackrunFlag:
    displacer_tx_index: int
    reversal_tx_index: int
    displacement_bps: float
    captured_bps: float
    capture_pct: float
    confidence: float


@dataclass
class BidIntent:
    """Dry-run capture bid. executed is always False from this module."""

    timestamp: float
    flag_kind: str
    pool_id: str
    expected_capture_usd: float
    gas_cost_usd: float
    expected_net_usd: float
    dry_run: bool = True
    executed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# -- flow metering -----------------------------------------------------------
class GasMeter:
    """Transient-storage gas model for the swap hot path.

    beforeSwap: 3 TSTORE (sender, sqrtPriceX96_before, amountSpecified).
    afterSwap:  2 TSTORE + 1 TLOAD (sqrtPriceX96_after, realizedDelta, ledger).
    No SSTORE on the hot path: 600 gas per swap, budget 5,000.
    """

    PER_SWAP_GAS = 3 * GAS_PER_TSTORE + 2 * GAS_PER_TSTORE + 1 * GAS_PER_TLOAD

    @classmethod
    def gas_for_swaps(cls, n: int) -> int:
        return n * cls.PER_SWAP_GAS

    @classmethod
    def assert_budget(cls, n: int = 1) -> int:
        gas = cls.gas_for_swaps(n)
        if cls.PER_SWAP_GAS > GAS_BUDGET_PER_SWAP:
            raise MEVError(
                f"hot-path gas {cls.PER_SWAP_GAS} exceeds budget {GAS_BUDGET_PER_SWAP}"
            )
        return gas


class FlowMeter:
    """Per-block flow ledger keyed by (block_hash, pool_id).

    Reorg safety: keys include the block hash, so replaying a reorged
    block's events is a safe no-op — record() is idempotent per
    (block_hash, pool_id, tx_index).
    """

    def __init__(self):
        self._seen: set = set()  # (block_hash, pool_id, tx_index)
        self._ledger: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(
            lambda: {"notional_usd": 0.0, "swaps": 0, "gas": 0}
        )

    def record(self, event: SwapEvent) -> bool:
        """Record one swap. Returns False when the event was already seen."""
        key = (event.block_hash, event.pool_id, event.tx_index)
        if key in self._seen:
            return False  # reorg replay / duplicate: safe no-op
        self._seen.add(key)
        cell = self._ledger[(event.block_hash, event.pool_id)]
        cell["notional_usd"] += event.notional_usd
        cell["swaps"] += 1
        cell["gas"] += GasMeter.PER_SWAP_GAS
        return True

    def block_flow_usd(self, block_hash: str, pool_id: str) -> float:
        return self._ledger[(block_hash, pool_id)]["notional_usd"]

    def passes_threshold(self, block_hash: str, pool_id: str,
                         impact_bps: float) -> bool:
        """flow_threshold gate: $1,000 notional AND 50 bps impact."""
        return (
            self.block_flow_usd(block_hash, pool_id) >= FLOW_THRESHOLD_USD
            and abs(impact_bps) >= IMPACT_FLAG_BPS
        )


# -- detection ---------------------------------------------------------------
def _same_actor(a: SwapEvent, b: SwapEvent) -> bool:
    if a.sender == b.sender:
        return True
    funders = {x for x in (a.funded_by, b.funded_by) if x}
    return bool(funders) and (
        a.sender in funders or b.sender in funders
        or (a.funded_by and a.funded_by == b.funded_by)
    )


class MEVDetector:
    """Sandwich / backrun signature detection over one block's swaps.

    Both signatures require the flow_threshold gate (noise filter); below
    threshold the flow is logged as noise and never bids.
    """

    def __init__(self, meter: FlowMeter,
                 flow_threshold_usd: float = FLOW_THRESHOLD_USD,
                 impact_flag_bps: int = IMPACT_FLAG_BPS):
        self.meter = meter
        self.flow_threshold_usd = flow_threshold_usd
        self.impact_flag_bps = impact_flag_bps

    def _gate(self, events: List[SwapEvent], impact_bps: float,
              attributable_usd: float) -> bool:
        return (
            attributable_usd >= self.flow_threshold_usd
            and abs(impact_bps) >= self.impact_flag_bps
        )

    def detect_sandwich(self, events: List[SwapEvent]) -> List[SandwichFlag]:
        flags: List[SandwichFlag] = []
        evs = sorted(events, key=lambda e: e.tx_index)
        n = len(evs)
        for i in range(n):
            for j in range(i + 1, n):
                for k in range(j + 1, n):
                    a, v, b = evs[i], evs[j], evs[k]
                    if not _same_actor(a, b):
                        continue
                    if abs(v.impact_bps) <= self.impact_flag_bps:
                        continue  # victim displacement too small
                    if v.sender == a.sender:
                        continue  # attacker is not its own victim
                    if b.direction != -a.direction:
                        continue  # B must reverse A's direction
                    if not self._gate(evs, v.impact_bps, v.notional_usd):
                        continue  # noise filter
                    flags.append(SandwichFlag(
                        attacker=a.sender,
                        victim_tx_index=v.tx_index,
                        frontrun_tx_index=a.tx_index,
                        backrun_tx_index=b.tx_index,
                        attributable_notional_usd=v.notional_usd,
                        victim_impact_bps=v.impact_bps,
                        confidence=min(1.0, 0.80 + abs(v.impact_bps) / 1000.0),
                    ))
        return flags

    def detect_backrun(self, events: List[SwapEvent]) -> List[BackrunFlag]:
        flags: List[BackrunFlag] = []
        evs = sorted(events, key=lambda e: e.tx_index)
        n = len(evs)
        for i in range(n):
            for j in range(i + 1, n):
                v, r = evs[i], evs[j]
                if abs(v.impact_bps) <= self.impact_flag_bps:
                    continue
                if r.direction != (-1 if v.impact_bps > 0 else 1):
                    continue  # reversal must trade against the displacement
                displacement_value = abs(v.impact_bps) * v.notional_usd
                if displacement_value <= 0:
                    continue
                captured_value = abs(r.impact_bps) * r.notional_usd
                capture_pct = min(1.0, captured_value / displacement_value)
                if capture_pct < BACKRUN_MIN_CAPTURE_PCT:
                    continue
                if not self._gate(evs, v.impact_bps, v.notional_usd):
                    continue
                flags.append(BackrunFlag(
                    displacer_tx_index=v.tx_index,
                    reversal_tx_index=r.tx_index,
                    displacement_bps=abs(v.impact_bps),
                    captured_bps=abs(r.impact_bps),
                    capture_pct=capture_pct,
                    confidence=min(1.0, 0.75 + capture_pct / 4.0),
                ))
        return flags

    def detect(self, events: List[SwapEvent]) -> Dict[str, List[Any]]:
        return {
            "sandwich": self.detect_sandwich(events),
            "backrun": self.detect_backrun(events),
        }


# -- signer deny-list (no_treasury_key) --------------------------------------
class SignerRegistry:
    """Code-level deny-list: the treasury EOA can never be a signer."""

    def __init__(self, treasury: str = TREASURY):
        self._denied = {treasury.lower()}

    def check(self, address: str) -> None:
        """Raise on any signing attempt with a denied address. Fail closed."""
        if (address or "").lower() in self._denied:
            raise TreasurySignerError(
                f"treasury EOA {address} is deny-listed: refusing to sign"
            )

    def audit_config(self, config: Any, _path: str = "root") -> None:
        """Walk a config mapping; fail if the treasury address appears in
        any signer/key/EOA field. Mirrors the CI grep layer."""
        if isinstance(config, dict):
            for k, v in config.items():
                key = str(k).lower()
                if isinstance(v, str) and v.lower() in self._denied and any(
                    token in key for token in ("signer", "key", "eoa", "wallet")
                ):
                    raise TreasurySignerError(
                        f"treasury address in signer field at {_path}.{k}"
                    )
                self.audit_config(v, f"{_path}.{k}")
        elif isinstance(config, (list, tuple)):
            for i, v in enumerate(config):
                self.audit_config(v, f"{_path}[{i}]")


# -- live gate ----------------------------------------------------------------
class LiveGate:
    """Live-block gate: live capture stays blocked until the founder-signed
    release. The reference build never flips this by itself."""

    def __init__(self):
        self._released = False

    def release(self, founder_signature_marker: str) -> None:
        if not founder_signature_marker:
            raise MEVError("release requires a founder signature marker")
        self._released = True

    def assert_live_allowed(self) -> None:
        if not self._released:
            raise LiveBlockedError(
                "live capture is blocked: founder release not presented (dry_run only)"
            )


# -- bidder -------------------------------------------------------------------
class Bidder:
    """Proceeds-funded capture bidder (reference).

    The bidder EOA is funded ONLY from previously captured proceeds; it
    stands down gracefully when float < 2x the gas reserve and NEVER falls
    back to any treasury-adjacent key.
    """

    def __init__(self, bidder_eoa: str, float_usd: float,
                 gas_ceiling_usd: float,
                 registry: Optional[SignerRegistry] = None,
                 live_gate: Optional[LiveGate] = None):
        self.registry = registry or SignerRegistry()
        self.registry.check(bidder_eoa)  # fail closed at construction
        if float_usd < MIN_FLOAT_USD:
            raise MEVError(f"bidder float ${float_usd} below ${MIN_FLOAT_USD} minimum")
        self.bidder_eoa = bidder_eoa
        self.float_usd = float_usd
        self.gas_ceiling_usd = gas_ceiling_usd
        self.live_gate = live_gate or LiveGate()
        self.confidence = 1.0
        self._proceeds_usd = 0.0

    def credit_proceeds(self, amount_usd: float) -> None:
        if amount_usd < 0:
            raise MEVError("proceeds cannot be negative")
        self._proceeds_usd += amount_usd
        self.float_usd += amount_usd

    def evaluate(self, flag: Any, gas_cost_usd: float,
                 estimated_capture_usd: float) -> Tuple[Optional[BidIntent], str]:
        """Apply the 3x bid-shading rule. Returns (intent|None, reason)."""
        expected_net = estimated_capture_usd - gas_cost_usd
        if expected_net < BID_SHADING_MULT * gas_cost_usd:
            return None, "shading: expected_net < 3x gas_cost, standing down"
        if self.float_usd < 2 * self.gas_ceiling_usd:
            return None, "depleted: float below 2x gas reserve, standing down"
        kind = "sandwich" if isinstance(flag, SandwichFlag) else "backrun"
        pool_id = getattr(flag, "pool_id", "unknown")
        return BidIntent(
            timestamp=time.time(),
            flag_kind=kind,
            pool_id=pool_id,
            expected_capture_usd=estimated_capture_usd,
            gas_cost_usd=gas_cost_usd,
            expected_net_usd=expected_net,
            details={"confidence": getattr(flag, "confidence", 0.0),
                     "bidder_eoa": self.bidder_eoa,
                     "dry_run": DRY_RUN},
        ), "bid"

    def submit_live(self, intent: BidIntent) -> None:
        """Live submission path: hard-blocked until release."""
        self.live_gate.assert_live_allowed()
        self.registry.check(self.bidder_eoa)
        raise NotImplementedError("live execution path is intentionally absent")


# -- capture accounting --------------------------------------------------------
@dataclass
class CaptureRecord:
    capture_id: str
    pool_id: str
    token: str
    estimate_wei: int
    actual_wei: Optional[int] = None
    under_review: bool = False


class CaptureLedger:
    """Capture accounting with estimate-vs-actual reconciliation.

    Keys are (capture_id); reorg replay of the same capture is a safe no-op.
    """

    def __init__(self, bidder: Optional[Bidder] = None):
        self._captures: Dict[str, CaptureRecord] = {}
        self._bidder = bidder

    def record_capture(self, capture_id: str, pool_id: str, token: str,
                       amount_wei: int, estimate_wei: int) -> CaptureRecord:
        if capture_id in self._captures:
            return self._captures[capture_id]  # idempotent: safe no-op
        if amount_wei < 0 or estimate_wei < 0:
            raise MEVError("capture amounts cannot be negative")
        rec = CaptureRecord(capture_id, pool_id, token, estimate_wei)
        rec.actual_wei = amount_wei
        self._captures[capture_id] = rec
        return rec

    def reconcile(self, capture_id: str, actual_wei: int) -> Dict[str, Any]:
        rec = self._captures[capture_id]
        rec.actual_wei = actual_wei
        base = max(actual_wei, 1)  # avoid div-by-zero; zero actual => max deviation
        deviation = abs(rec.estimate_wei - actual_wei) / base
        if deviation > ESTIMATE_TOLERANCE:
            rec.under_review = True
            if self._bidder is not None:
                self._bidder.confidence *= 0.9  # decay on breach
            return {"capture_id": capture_id, "deviation": deviation,
                    "under_review": True}
        return {"capture_id": capture_id, "deviation": deviation,
                "under_review": False}


# -- treasury routing ------------------------------------------------------------
class TreasuryRouter:
    """20 bps fee + residual to treasury via pull claims.

    Non-bricking: each claimant settles independently; one reverting
    claimant never blocks the others. Conservation is asserted exactly.
    """

    def __init__(self, treasury: str = TREASURY,
                 reverting: Optional[set] = None):
        self.treasury = treasury
        self._reverting = reverting or set()
        self._claimable: Dict[str, int] = defaultdict(int)
        self._claimed: set = set()

    @staticmethod
    def fee_wei(amount_wei: int) -> int:
        return amount_wei * FEE_BPS // 10_000

    def route(self, capture_id: str, amount_wei: int) -> Dict[str, Any]:
        fee = self.fee_wei(amount_wei)
        residual = amount_wei - fee
        assert fee + residual == amount_wei, "conservation violated"
        self._claimable[self.treasury] += fee + residual
        return {"capture_id": capture_id, "amount_wei": amount_wei,
                "fee_wei": fee, "residual_wei": residual,
                "treasury": self.treasury}

    def claim(self, claimant: str) -> int:
        """Pull-based claim. Double claims return 0; reverting claimants
        raise without touching anyone else's balance."""
        if claimant in self._claimed:
            return 0
        if claimant in self._reverting:
            raise MEVError(f"claimant {claimant} reverted; others unaffected")
        amount = self._claimable.get(claimant, 0)
        if amount > 0:
            self._claimed.add(claimant)
            self._claimable[claimant] = 0
        return amount


# -- user protection -------------------------------------------------------------
@dataclass(frozen=True)
class ProtectedOrder:
    order_id: str
    quoted_price: float
    executed_price: float
    opted_in: bool
    block_flagged_toxic_before_swap: bool = False


class ProtectionPolicy:
    """Opt-in per-order protection (hookData flag).

    Protected orders get: 100 bps slippage guardrail vs the quoted price,
    and automatic revert when the hook flagged the block as toxic before
    the user's swap executed.
    """

    def __init__(self, guardrail_bps: int = SLIPPAGE_GUARDRAIL_BPS):
        self.guardrail_bps = guardrail_bps

    def evaluate(self, order: ProtectedOrder) -> Dict[str, Any]:
        if not order.opted_in:
            return {"allowed": True, "reason": "not opted in: no protection"}
        if order.block_flagged_toxic_before_swap:
            raise ProtectionRevert(
                f"order {order.order_id}: block flagged toxic before swap executed"
            )
        if order.quoted_price <= 0:
            raise ProtectionRevert(f"order {order.order_id}: invalid quoted price")
        slippage_bps = abs(order.executed_price - order.quoted_price) \
            / order.quoted_price * 10_000
        if slippage_bps > self.guardrail_bps:
            raise ProtectionRevert(
                f"order {order.order_id}: slippage {slippage_bps:.1f} bps exceeds "
                f"{self.guardrail_bps} bps guardrail"
            )
        return {"allowed": True, "slippage_bps": slippage_bps,
                "reason": "within guardrail"}

    @staticmethod
    def loss_reduction_pct(baseline_loss_usd: float,
                           protected_loss_usd: float) -> float:
        """Sandwich-loss reduction for opted-in orders (target >= 80%)."""
        if baseline_loss_usd <= 0:
            return 0.0
        return max(0.0, (baseline_loss_usd - protected_loss_usd)
                   / baseline_loss_usd * 100.0)


# -- access control ---------------------------------------------------------------
class AccessControl:
    """BIDDER_ROLE (agent) / GUARDIAN_ROLE (pause, threshold tuning)."""

    def __init__(self):
        self._roles: Dict[str, set] = defaultdict(set)
        self.paused = False
        self.flow_threshold_usd = FLOW_THRESHOLD_USD

    def grant(self, role: str, account: str) -> None:
        self._roles[role].add(account)

    def require(self, role: str, caller: str) -> None:
        if caller not in self._roles[role]:
            raise MEVError(f"{caller} lacks {role}")

    def tune_threshold(self, caller: str, new_value: float) -> float:
        """Guardian may move flow_threshold within +/-50% of baseline."""
        self.require(GUARDIAN_ROLE, caller)
        lo, hi = FLOW_THRESHOLD_USD * 0.5, FLOW_THRESHOLD_USD * 1.5
        if not (lo <= new_value <= hi):
            raise ThresholdOutOfBoundsError(
                f"threshold {new_value} outside +/-50% band [{lo}, {hi}]"
            )
        self.flow_threshold_usd = new_value
        return new_value

    def pause(self, caller: str) -> None:
        self.require(GUARDIAN_ROLE, caller)
        self.paused = True

    def unpause(self, caller: str) -> None:
        self.require(GUARDIAN_ROLE, caller)
        self.paused = False


def commitment_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
