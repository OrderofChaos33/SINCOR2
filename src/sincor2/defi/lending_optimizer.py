"""SINCOR DeFi P15 — Lending Protocol Optimizer (reference build).

Python reference simulation of the Morpho/Fluid-style lending optimizer
behind SKU ``SINCOR-DEFI-P15-LEND``:

- :class:`RiskModel` — deterministic logistic-regression risk model over
  on-chain wallet-behavior features (age, activity, liquidation history,
  health trend, borrow frequency). Trains on scripted wallet histories,
  outputs a per-borrower collateralization *requirement multiplier*;
  retrains on schedule with drift detection against the training baseline.
- :class:`RehypothecationEngine` — supplied collateral is deployed into
  allowlisted yield venues up to a cap, with a mandatory liquidity buffer
  so lenders never wait on locked yield; supply/redeem round-trips hold
  the accounting invariant ``cash + deployed == total lender balances``.
- :class:`VenueGate` — ``morpho_only_live`` enforcement: live mode
  accepts exactly one venue, ``morpho_gauntlet_usdc``; any live call to a
  non-allowlisted venue raises. Dry-run mode permits stub venues.
- :class:`LendingPool` — supply / borrow / repay / withdraw with
  utilization-band gating: a borrow that would push utilization above the
  band ceiling reverts.
- :class:`HedgeRouter` — on a health-factor drop below the warning
  threshold, computes the minimum hedge allocation that restores the
  position above the threshold; the dry-run scenario test verifies the
  plan actually recovers the position without liquidation.
- :class:`LoanVault` — ERC-6551-style token-bound loan vault semantics:
  each loan lives in a vault bound to a token id; agent strategies run
  inside the vault only on allowlisted strategies and can never remove
  the borrower's principal.
- 10 bps protocol fee on interest, pull-based, to the canonical treasury
  (immutable post-import; verified against the catalog at import time).

Safety rules (hard):
- Default mode is DRY_RUN. Plans are emitted, never executed; nothing
  here touches a chain, a pool, or funds.
- Live execution requires EXECUTE_LIVE=1 AND the auditor gate; even then
  the venue gate restricts live to Morpho Gauntlet USDC.
- Money math is integer-exact (wei).

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)
# Fail closed if the configured treasury drifts from the catalog constant.
from .catalog import TREASURY as _CATALOG_TREASURY  # noqa: E402

if TREASURY.lower() != _CATALOG_TREASURY.lower():  # pragma: no cover
    raise RuntimeError("P15 treasury address mismatches the catalog constant")

FEE_BPS = 10                       # 0.10% of interest -> treasury
UTIL_BAND_LO = 0.60                # utilization band (governable)
UTIL_BAND_HI = 0.85
LIQUIDITY_BUFFER_PCT = 0.20        # >= 20% of supply stays unencumbered
REHYP_MAX_PCT = 0.70               # <= 70% of supply may be deployed
MORPHO_GAUNTLET_USDC = "morpho_gauntlet_usdc"  # venue IDs are strings only
MIN_HEALTH_FACTOR = 1.0            # below: liquidatable
HEDGE_TRIGGER_HF = 1.25            # below: hedge router intervenes
HEDGE_MAX_PCT = 0.25               # router moves at most 25% of collateral
HEDGE_RECOVERY_TARGET = 1.30       # plan must restore HF to >= this
RISK_MODEL_VERSION = "p15-risk-v1"
DRIFT_ALERT = 1.5                  # standardized mean-shift alert threshold
DRY_RUN = os.getenv("P15_DRY_RUN", "1").strip() != "0"
EXECUTE_LIVE = os.getenv("EXECUTE_LIVE", "0").strip() == "1"
LIVE_AUDITOR_GATE = False          # flip only with signed auditor config


class LendingError(RuntimeError):
    """Base error for lending-optimizer rule violations."""


class UtilizationBreach(LendingError):
    """Borrow would push utilization outside the band."""


class VenueNotAllowlisted(LendingError):
    """Venue not on the timelocked allowlist (or non-Morpho in live)."""


class InsufficientLiquidity(LendingError):
    """Withdraw exceeds the unencumbered liquidity buffer."""


class LiveExecutionBlocked(LendingError):
    """Live execution attempted before the auditor gate flips."""


class ModelStale(LendingError):
    """Risk model flagged drift; requirements frozen until retrain."""


class VaultViolation(LendingError):
    """Token-bound vault rule violated (unlisted strategy / principal move)."""


# -- interest math ----------------------------------------------------------------
SECONDS_PER_YEAR = 31_536_000


def accrue(principal_wei: int, rate_per_year: float, seconds: int) -> int:
    """Simple-interest accrual, integer-exact, rounded in favor of the pool."""
    if principal_wei < 0 or seconds < 0:
        raise ValueError("principal and seconds must be non-negative")
    interest = principal_wei * rate_per_year * seconds / SECONDS_PER_YEAR
    return int(math.ceil(interest - 1e-9))


def utilization(total_borrows_wei: int, total_supply_wei: int) -> float:
    if total_supply_wei <= 0:
        return 0.0
    return total_borrows_wei / total_supply_wei


# -- venue gate -------------------------------------------------------------------
@dataclass
class VenueGate:
    """morpho_only_live enforcement.

    Live mode accepts exactly MORPHO_GAUNTLET_USDC. Dry-run mode permits
    stub venues for simulation. Non-allowlisted venues always raise.
    """

    allowlist: List[str] = field(
        default_factory=lambda: [MORPHO_GAUNTLET_USDC]
    )

    def check(self, venue_id: str, *, live: bool) -> None:
        if venue_id not in self.allowlist:
            raise VenueNotAllowlisted(f"venue not allowlisted: {venue_id}")
        if live and venue_id != MORPHO_GAUNTLET_USDC:
            raise VenueNotAllowlisted(
                f"live mode: only {MORPHO_GAUNTLET_USDC} is eligible, "
                f"got {venue_id}"
            )

    def add_venue(self, venue_id: str) -> None:
        if venue_id not in self.allowlist:
            self.allowlist.append(venue_id)


# -- lending pool -----------------------------------------------------------------
@dataclass
class LendingPool:
    """Reference supply/borrow pool with utilization-band gating and fees."""

    borrow_rate: float = 0.08          # 8% annualized reference rate
    util_lo: float = UTIL_BAND_LO
    util_hi: float = UTIL_BAND_HI
    gate: VenueGate = field(default_factory=VenueGate)
    total_supply: int = 0
    total_borrows: int = 0
    balances: Dict[str, int] = field(default_factory=dict)
    borrows: Dict[str, int] = field(default_factory=dict)
    fee_owed_wei: int = 0             # pull-based treasury claim
    events: List[Dict[str, Any]] = field(default_factory=list)
    last_accrue_ts: float = field(default_factory=time.time)

    def _event(self, kind: str, **kw: Any) -> None:
        self.events.append({"kind": kind, "ts": time.time(), **kw})

    def _accrue_interest(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        dt = max(0, int(now - self.last_accrue_ts))
        if dt and self.total_borrows:
            interest = accrue(self.total_borrows, self.borrow_rate, dt)
            fee = interest * FEE_BPS // 10_000
            self.fee_owed_wei += fee
            # Distribute interest across borrowers pro-rata (largest
            # remainder, exact) so sum(borrows) == total_borrows always.
            old_borrows = self.total_borrows
            alloc_b = []
            for borrower, owed in self.borrows.items():
                q, r = divmod(owed * interest, old_borrows)
                alloc_b.append([borrower, q, r])
            leftover_b = interest - sum(a[1] for a in alloc_b)
            alloc_b.sort(key=lambda a: a[2], reverse=True)
            for a in alloc_b[:leftover_b]:
                a[1] += 1
            for borrower, q, _ in alloc_b:
                self.borrows[borrower] += q
            self.total_borrows = old_borrows + interest
            # Interest accrues to lenders pro-rata (fee excluded).
            # Largest-remainder distribution: the per-lender floor shares
            # plus leftover dust are allocated so that
            # sum(balances) == total_supply exactly — no rounding drift.
            distributable = interest - fee
            old_supply = self.total_supply
            self.total_supply = old_supply + distributable
            if distributable and old_supply:
                alloc = []
                for lender, bal in self.balances.items():
                    q, r = divmod(bal * distributable, old_supply)
                    alloc.append([lender, q, r])
                leftover = distributable - sum(a[1] for a in alloc)
                alloc.sort(key=lambda a: a[2], reverse=True)
                for a in alloc[:leftover]:
                    a[1] += 1
                for lender, q, _ in alloc:
                    self.balances[lender] += q
            self._event("interest_accrued", interest=interest, fee=fee)
        self.last_accrue_ts = now

    def supply(self, lender: str, amount_wei: int,
               now: Optional[float] = None) -> None:
        if amount_wei <= 0:
            raise ValueError("supply amount must be positive")
        self._accrue_interest(now)
        self.total_supply += amount_wei
        self.balances[lender] = self.balances.get(lender, 0) + amount_wei
        self._event("supply", lender=lender, amount=amount_wei)

    def borrow(self, borrower: str, amount_wei: int,
               now: Optional[float] = None) -> None:
        if amount_wei <= 0:
            raise ValueError("borrow amount must be positive")
        self._accrue_interest(now)
        new_util = utilization(self.total_borrows + amount_wei, self.total_supply)
        if new_util > self.util_hi:
            raise UtilizationBreach(
                f"borrow would push utilization to {new_util:.4f} "
                f"(band ceiling {self.util_hi})"
            )
        self.total_borrows += amount_wei
        self.borrows[borrower] = self.borrows.get(borrower, 0) + amount_wei
        self._event("borrow", borrower=borrower, amount=amount_wei,
                    utilization=new_util)

    def repay(self, borrower: str, amount_wei: int,
              now: Optional[float] = None) -> None:
        if amount_wei <= 0:
            raise ValueError("repay amount must be positive")
        self._accrue_interest(now)
        owed = self.borrows.get(borrower, 0)
        pay = min(amount_wei, owed)
        self.borrows[borrower] = owed - pay
        self.total_borrows -= pay
        self._event("repay", borrower=borrower, amount=pay)

    def withdraw(self, lender: str, amount_wei: int,
                 now: Optional[float] = None) -> None:
        if amount_wei <= 0:
            raise ValueError("withdraw amount must be positive")
        self._accrue_interest(now)
        bal = self.balances.get(lender, 0)
        if amount_wei > bal:
            raise InsufficientLiquidity("withdraw exceeds lender balance")
        # Solvency guard: lent-out funds are illiquid until repaid; the
        # liquid portion of the pool is supply - borrows.
        if self.total_supply - amount_wei < self.total_borrows:
            raise InsufficientLiquidity(
                "withdraw exceeds liquid supply (remainder is borrowed out)")
        self.balances[lender] = bal - amount_wei
        self.total_supply -= amount_wei
        self._event("withdraw", lender=lender, amount=amount_wei)

    def claim_fees(self) -> Dict[str, Any]:
        """Pull-based treasury claim of the 10 bps interest fee."""
        amount = self.fee_owed_wei
        self.fee_owed_wei = 0
        self._event("fee_routed", treasury=TREASURY, amount=amount,
                    fee_bps=FEE_BPS)
        return {"treasury": TREASURY, "amount_wei": amount,
                "fee_bps": FEE_BPS}

    def check_invariants(self) -> None:
        assert sum(self.balances.values()) == self.total_supply, \
            "lender balances diverge from total supply"
        assert sum(self.borrows.values()) == self.total_borrows, \
            "borrower debts diverge from total borrows"
        assert self.total_supply >= self.total_borrows, "insolvent"

    def set_band(self, lo: float, hi: float) -> None:
        """Governable band update (audited admin path in production)."""
        if not (0.0 < lo < hi < 1.0):
            raise ValueError("band must satisfy 0 < lo < hi < 1")
        self.util_lo, self.util_hi = lo, hi
        self._event("band_updated", lo=lo, hi=hi)


# -- rehypothecation engine -------------------------------------------------------
@dataclass
class RehypothecationEngine:
    """Deploys supplied collateral into allowlisted yield venues.

    Deploy-time rules: deployed <= REHYP_MAX_PCT of supply and the
    deploy must leave >= LIQUIDITY_BUFFER_PCT of supply liquid.
    Withdrawals route through :meth:`withdraw`, which recalls any
    shortfall from venues first — lenders never wait on locked yield
    and are never trapped by rehypothecation. Perpetual invariants are
    accounting-only (cash + deployed == supply == sum of balances);
    the buffer is a deploy-time liquidity target, not a claim on a
    shrinking supply base.
    """

    pool: LendingPool
    gate: VenueGate
    deployed: Dict[str, int] = field(default_factory=dict)
    dry_run: bool = DRY_RUN

    def _live(self) -> bool:
        return EXECUTE_LIVE and LIVE_AUDITOR_GATE

    def cash(self) -> int:
        return self.pool.total_supply - sum(self.deployed.values())

    def deploy(self, venue_id: str, amount_wei: int) -> None:
        self.gate.check(venue_id, live=self._live())
        if amount_wei <= 0:
            raise ValueError("deploy amount must be positive")
        total = self.pool.total_supply
        if sum(self.deployed.values()) + amount_wei > int(total * REHYP_MAX_PCT):
            raise LendingError("rehypothecation cap exceeded")
        if self.cash() - amount_wei < int(total * LIQUIDITY_BUFFER_PCT):
            raise LendingError("liquidity buffer would be breached")
        self.deployed[venue_id] = self.deployed.get(venue_id, 0) + amount_wei
        self.pool._event("rehyp_deploy", venue=venue_id, amount=amount_wei)

    def recall(self, venue_id: str, amount_wei: int) -> None:
        pos = self.deployed.get(venue_id, 0)
        if amount_wei > pos:
            raise LendingError("recall exceeds deployed position")
        self.deployed[venue_id] = pos - amount_wei
        self.pool._event("rehyp_recall", venue=venue_id, amount=amount_wei)

    def withdraw(self, lender: str, amount_wei: int,
                 now: Optional[float] = None) -> None:
        """Lender withdrawal through the engine.

        Serves the withdrawal from the liquidity buffer first; any
        shortfall is recalled from deployed venues before the pool
        withdrawal runs. Lenders never wait on locked yield and never
        get trapped by rehypothecation — only an insufficient *balance*
        reverts (raised by the pool).
        """
        if amount_wei <= 0:
            raise ValueError("withdraw amount must be positive")
        shortfall = max(0, amount_wei - self.cash())
        if shortfall:
            # Recall largest-first until the withdrawal is covered.
            for venue_id in sorted(self.deployed,
                                   key=lambda v: self.deployed[v],
                                   reverse=True):
                if shortfall <= 0:
                    break
                take = min(shortfall, self.deployed[venue_id])
                self.recall(venue_id, take)
                shortfall -= take
            if shortfall:
                raise InsufficientLiquidity(
                    "deployed positions cannot cover withdrawal")
        self.pool.withdraw(lender, amount_wei, now=now)

    def check_invariants(self) -> None:
        total = self.pool.total_supply
        deployed_total = sum(self.deployed.values())
        assert deployed_total <= total, "deployed exceeds supply"
        assert self.cash() + deployed_total == total, "accounting drift"
        assert sum(self.pool.balances.values()) == total, \
            "lender balances diverge from total supply"


# -- ML risk model ----------------------------------------------------------------
@dataclass(frozen=True)
class WalletFeatures:
    """On-chain wallet behavior features (caller-supplied, no PII)."""

    age_days: float
    tx_count: int
    liquidations: int
    avg_health_factor: float
    borrow_frequency_per_day: float


# Scripted training histories: (features, defaulted). Deterministic,
# versioned with the model; a production build would ingest real chains.
TRAINING_SET: Tuple[Tuple[WalletFeatures, int], ...] = (
    (WalletFeatures(900, 1200, 0, 2.4, 0.05), 0),
    (WalletFeatures(700, 900, 0, 2.1, 0.08), 0),
    (WalletFeatures(450, 400, 1, 1.5, 0.20), 0),
    (WalletFeatures(300, 250, 1, 1.3, 0.35), 1),
    (WalletFeatures(120, 80, 2, 1.1, 0.60), 1),
    (WalletFeatures(45, 30, 3, 1.05, 1.20), 1),
    (WalletFeatures(600, 700, 0, 1.9, 0.10), 0),
    (WalletFeatures(200, 150, 1, 1.4, 0.40), 1),
    (WalletFeatures(800, 1100, 0, 2.6, 0.04), 0),
    (WalletFeatures(90, 60, 2, 1.2, 0.80), 1),
)

FEATURE_ORDER = ("age_days", "tx_count", "liquidations",
                 "avg_health_factor", "borrow_frequency_per_day")
# Signs: age/tx/hf reduce risk (-1); liquidations/borrow-freq raise it (+1).
FEATURE_SIGNS = (-1.0, -1.0, +1.0, -1.0, +1.0)
REQUIREMENT_BASE = 1.25     # collateral factor floor for the safest wallets
REQUIREMENT_SPREAD = 0.75   # additional factor at max risk score


def _feature_vector(f: WalletFeatures) -> Tuple[float, ...]:
    return tuple(getattr(f, name) for name in FEATURE_ORDER)


class RiskModel:
    """Deterministic logistic risk model with drift detection.

    Trains batch gradient descent from a fixed seed over TRAINING_SET.
    Outputs a risk score in [0, 1] and a collateralization requirement
    ``REQUIREMENT_BASE + score * REQUIREMENT_SPREAD``. Retrains on
    schedule; ``drift`` flags when a new batch's standardized feature
    means shift beyond DRIFT_ALERT versus the training baseline, in which
    case requirements are frozen until retrain.
    """

    def __init__(self) -> None:
        self.weights: List[float] = [0.0] * len(FEATURE_ORDER)
        self.bias: float = 0.0
        self.version = RISK_MODEL_VERSION
        self.trained_at: Optional[float] = None
        self.baseline_means: List[float] = [0.0] * len(FEATURE_ORDER)
        self.baseline_stds: List[float] = [1.0] * len(FEATURE_ORDER)
        self.drift = False
        self.published: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _standardize(vec: Tuple[float, ...], means: List[float],
                      stds: List[float]) -> List[float]:
        return [(v - m) / (s or 1.0) for v, m, s in zip(vec, means, stds)]

    def fit(self, dataset: Tuple[Tuple[WalletFeatures, int], ...] = TRAINING_SET,
            iterations: int = 500, lr: float = 0.1) -> None:
        vecs = [_feature_vector(f) for f, _ in dataset]
        labels = [y for _, y in dataset]
        n = len(FEATURE_ORDER)
        self.baseline_means = [sum(v[i] for v in vecs) / len(vecs)
                               for i in range(n)]
        var = [sum((v[i] - self.baseline_means[i]) ** 2 for v in vecs) / len(vecs)
               for i in range(n)]
        self.baseline_stds = [math.sqrt(x) or 1.0 for x in var]
        self.weights = [0.0] * n
        self.bias = 0.0
        # Deterministic batch gradient descent on standardized features.
        for _ in range(iterations):
            gw = [0.0] * n
            gb = 0.0
            for v, y in zip(vecs, labels):
                z = self._standardize(v, self.baseline_means,
                                      self.baseline_stds)
                p = 1.0 / (1.0 + math.exp(
                    -(sum(w * x for w, x in zip(self.weights, z)) + self.bias)))
                err = p - y
                for i in range(n):
                    gw[i] += err * z[i]
                gb += err
            m = len(vecs)
            self.weights = [w - lr * g / m for w, g in zip(self.weights, gw)]
            self.bias -= lr * gb / m
        self.trained_at = time.time()
        self.drift = False

    def score(self, f: WalletFeatures) -> float:
        if self.trained_at is None:
            raise ModelStale("model not trained")
        z = self._standardize(_feature_vector(f), self.baseline_means,
                              self.baseline_stds)
        logit = sum(w * x for w, x in zip(self.weights, z)) + self.bias
        return 1.0 / (1.0 + math.exp(-logit))

    def requirement(self, f: WalletFeatures) -> float:
        """Collateralization requirement multiplier (e.g. 1.25 = 125%)."""
        if self.drift:
            raise ModelStale("drift detected; retrain before publishing")
        return REQUIREMENT_BASE + self.score(f) * REQUIREMENT_SPREAD

    def check_drift(self, batch: List[WalletFeatures]) -> Dict[str, Any]:
        """Flag distribution shift versus the training baseline."""
        if not batch:
            raise ValueError("drift check needs a non-empty batch")
        vecs = [_feature_vector(f) for f in batch]
        n = len(FEATURE_ORDER)
        means = [sum(v[i] for v in vecs) / len(vecs) for i in range(n)]
        shift = max(abs((m - b) / (s or 1.0))
                    for m, b, s in zip(means, self.baseline_means,
                                       self.baseline_stds))
        self.drift = shift > DRIFT_ALERT
        return {"max_shift": shift, "drift": self.drift,
                "threshold": DRIFT_ALERT}

    def publish_requirements(
        self, borrowers: Dict[str, WalletFeatures]
    ) -> Dict[str, Dict[str, Any]]:
        """Publish per-borrower requirement updates consumable by contracts."""
        out = {}
        for borrower, feats in borrowers.items():
            req = self.requirement(feats)
            payload = {"borrower": borrower, "requirement": req,
                       "score": self.score(feats),
                       "model_version": self.version,
                       "published_at": time.time()}
            digest = hashlib.sha256(
                repr(sorted(payload.items())).encode()).hexdigest()
            payload["digest"] = digest
            out[borrower] = payload
        self.published.update(out)
        return out


# -- health-drop hedge router ------------------------------------------------------
@dataclass
class Position:
    borrower: str
    collateral_wei: int
    debt_wei: int
    collateral_factor: float      # e.g. 0.8 = 80% of collateral counts
    requirement: float = REQUIREMENT_BASE


def health_factor(pos: Position) -> float:
    if pos.debt_wei <= 0:
        return float("inf")
    return pos.collateral_wei * pos.collateral_factor / pos.debt_wei


@dataclass
class HedgePlan:
    borrower: str
    hedge_pct: float              # fraction of collateral routed to hedge
    hedge_wei: int
    hf_before: float
    hf_after: float
    dry_run: bool = True


class HedgeRouter:
    """Routes collateral into hedging strategies on health deterioration.

    The plan sizes the minimum hedge allocation (<= HEDGE_MAX_PCT) that
    restores HF >= HEDGE_RECOVERY_TARGET under the modeled shock, using a
    conservative delta-hedge payoff: the hedge sleeve gains the shock's
    magnitude on the routed fraction. Plans are emitted in dry-run; the
    integration test verifies a simulated drop recovers without
    liquidation.
    """

    def __init__(self, dry_run: bool = DRY_RUN):
        self.dry_run = dry_run
        self.plans: List[HedgePlan] = []

    def plan_for(self, pos: Position, shock: float) -> Optional[HedgePlan]:
        """Size a hedge plan for a collateral shock.

        ``shock`` is the collateral value multiplier after the drop
        (e.g. 0.9). Intervention triggers on the *shocked* health factor:
        a healthy position pushed below the warning threshold gets the
        minimum hedge allocation (<= HEDGE_MAX_PCT) that restores it to
        >= HEDGE_RECOVERY_TARGET. Returns None when no intervention is
        needed, or when even the maximum hedge cannot recover the
        position (caller must escalate: partial repay / liquidation
        protection alert).
        """
        hf_shocked = self._hf_shocked(pos, shock)
        if hf_shocked >= HEDGE_TRIGGER_HF:
            return None
        # Binary-search the smallest hedge_pct that recovers the position.
        lo, hi = 0.0, HEDGE_MAX_PCT
        best = None
        for _ in range(40):
            mid = (lo + hi) / 2
            hf = self._hf_after_hedge(pos, mid, shock)
            if hf >= HEDGE_RECOVERY_TARGET:
                best, hi = mid, mid
            else:
                lo = mid
        if best is None:
            return None
        plan = HedgePlan(
            borrower=pos.borrower, hedge_pct=best,
            hedge_wei=int(pos.collateral_wei * best),
            hf_before=hf_shocked,
            hf_after=self._hf_after_hedge(pos, best, shock),
            dry_run=self.dry_run,
        )
        self.plans.append(plan)
        return plan

    @staticmethod
    def _hf_shocked(pos: Position, shock: float) -> float:
        if pos.debt_wei <= 0:
            return float("inf")
        return (pos.collateral_wei * shock * pos.collateral_factor
                / pos.debt_wei)

    @staticmethod
    def _hf_after_hedge(pos: Position, hedge_pct: float,
                        shock: float) -> float:
        routed = pos.collateral_wei * hedge_pct
        unrouted = pos.collateral_wei - routed
        # Shocked unrouted collateral + hedge sleeve that offsets the drop.
        eff_collateral = unrouted * shock + routed * (1.0 + (1.0 - shock))
        if pos.debt_wei <= 0:
            return float("inf")
        return eff_collateral * pos.collateral_factor / pos.debt_wei

    def simulate_health_drop(self, pos: Position, shock: float) -> Dict[str, Any]:
        """Full dry-run scenario: shock -> plan -> apply -> verify."""
        unhedged_hf = (pos.collateral_wei * shock * pos.collateral_factor
                       / pos.debt_wei) if pos.debt_wei else float("inf")
        plan = self.plan_for(pos, shock)
        liquidated_without = unhedged_hf < MIN_HEALTH_FACTOR
        if plan is None:
            return {"plan": None, "hf_shocked": unhedged_hf,
                    "would_liquidate": liquidated_without,
                    "recovered": unhedged_hf >= HEDGE_RECOVERY_TARGET}
        return {"plan": asdict(plan), "hf_shocked": unhedged_hf,
                "would_liquidate": liquidated_without,
                "recovered": plan.hf_after >= HEDGE_RECOVERY_TARGET}


# -- ERC-6551-style token-bound loan vault ------------------------------------------
@dataclass
class LoanVault:
    """Per-loan token-bound vault.

    Agent strategies execute *inside* the vault against an allowlisted
    strategy set; the borrower keeps ownership and withdrawal rights, and
    no strategy may move principal out of the vault.
    """

    token_id: int
    owner: str
    collateral_wei: int = 0
    debt_wei: int = 0
    strategies: List[str] = field(default_factory=list)
    executed: List[Dict[str, Any]] = field(default_factory=list)

    def deposit_collateral(self, amount_wei: int) -> None:
        if amount_wei <= 0:
            raise ValueError("deposit must be positive")
        self.collateral_wei += amount_wei

    def allow_strategy(self, strategy_id: str) -> None:
        if strategy_id not in self.strategies:
            self.strategies.append(strategy_id)

    def execute_strategy(self, strategy_id: str,
                         moves_principal: bool) -> Dict[str, Any]:
        if strategy_id not in self.strategies:
            raise VaultViolation(f"strategy not allowlisted: {strategy_id}")
        if moves_principal:
            raise VaultViolation("strategy may not move borrower principal")
        record = {"token_id": self.token_id, "strategy": strategy_id,
                  "ts": time.time(), "owner": self.owner}
        self.executed.append(record)
        return record

    def withdraw_excess(self, amount_wei: int, requirement: float,
                        caller: str) -> None:
        if caller != self.owner:
            raise VaultViolation("only the borrower-owner may withdraw")
        locked = int(self.debt_wei * requirement)
        excess = max(0, self.collateral_wei - locked)
        if amount_wei > excess:
            raise VaultViolation("withdraw exceeds excess collateral")
        self.collateral_wei -= amount_wei


# -- status feed --------------------------------------------------------------------
def status_payload(pool: LendingPool,
                   engine: Optional[RehypothecationEngine] = None
                   ) -> Dict[str, Any]:
    """Structured JSON for the SINCOR dashboard feed (dry-run sample)."""
    return {
        "product": "SINCOR-DEFI-P15-LEND",
        "ts": time.time(),
        "mode": "dry_run" if DRY_RUN else "live_intent",
        "utilization": utilization(pool.total_borrows, pool.total_supply),
        "util_band": [pool.util_lo, pool.util_hi],
        "total_supply_wei": pool.total_supply,
        "total_borrows_wei": pool.total_borrows,
        "deployed_wei": sum(engine.deployed.values()) if engine else 0,
        "fee_owed_wei": pool.fee_owed_wei,
    }


# -- shared price-oracle wiring ------------------------------------------------
# Declares this product's external price needs against the shared oracle
# (sincor2.defi.price_oracle). Reference-backed until live feeds are wired;
# never treated as a live integration.

PRICE_ASSETS = ['ETH/USD', 'BTC/USD', 'USDC/USD']


def price_feed_for(oracle):
    """Bind the shared price oracle to this product's declared assets.

    Returns a ProductPriceFeed; ``feed.price(asset, now)`` raises on any
    oracle failure (fail-closed). Live Chainlink/Pyth feeds are NOT wired —
    production must inject real adapters (see price_oracle module docs).
    """
    from .price_oracle import wiring_for
    return wiring_for("P15_LENDING", oracle)
