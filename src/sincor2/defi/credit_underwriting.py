"""
SINCOR DeFi P19 — Decentralized Credit Underwriting (reference build).

Python reference simulation of the credit pipeline behind SKU
``SINCOR-DEFI-P19-CREDIT``: score -> max LTV -> collateralized line ->
health-monitored position.

- :func:`score_wallet` — interpretable, deterministic behavioral score
  (300-850) from wallet-history features. No real identity data, no
  network calls: features are caller-supplied. Mixer interaction clamps
  to 300.
- :func:`max_ltv` — on-chain score->LTV table (non-decreasing enforced
  by :func:`validate_ltv_table`); < 350 is ineligible (score_floor).
- :class:`ScoreAttestation` — 72h-expiry score attestation; borrows with
  missing, stale, below-floor, or replayed attestations are refused.
- :class:`CreditLine` — ``draw()`` requires
  ``amount <= collateral * maxLTV``; zero-collateral draws revert. There
  is no unsecured book: the score buys better *terms*, never zero
  collateral.
- :class:`CreditBook` — concentration caps: single borrower <= 10% of
  book, single score band <= 40%.
- Health monitor: HF < 1.15 routes to hedge, HF < 1.05 liquidates.
- Fees: 20 bps on originated principal + 20 bps on accrued interest,
  both to the canonical treasury.

Safety rules (hard):
- Default mode is DRY_RUN. Scoring intents are emitted, never executed;
  nothing here touches a chain, a wallet, or funds.
- The scoring model is versioned; weights are constants, auditable.
- Fresh-address abandonment is mitigated by collateral, not denied:
  the score only tunes LTV within 35-75%.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

FEE_ORIG_BPS = 20                  # 20 bps on originated principal
FEE_ACCRUAL_BPS = 20               # 20 bps on accrued interest
SCORE_MIN = 300
SCORE_MAX = 850
SCORE_FLOOR = 350                  # below this: ineligible
ATTEST_TTL_SECONDS = 72 * 3600     # score staleness policy
HF_HEDGE = 1.15                    # below: route up to 25% collateral to hedge
HF_LIQUIDATE = 1.05                # below: permissionless liquidation
HEDGE_MAX_PCT = 0.25
CONC_BORROWER_PCT = 0.10           # single borrower <= 10% of book
CONC_BAND_PCT = 0.40               # single score band <= 40% of book
MIN_BOOK_FOR_CAPS_USD = 1_000.0    # caps bind once the book reaches this size
MODEL_VERSION = "p19-score-v1"
DRY_RUN = os.getenv("P19_DRY_RUN", "1").strip() != "0"


class CreditError(RuntimeError):
    """Base error for credit rule violations."""


class UnsecuredDraw(CreditError):
    """Draw with zero collateral or above collateral * maxLTV."""


class AttestationInvalid(CreditError):
    """Missing, stale, below-floor, or replayed score attestation."""


class ConcentrationBreach(CreditError):
    """Origination would breach the 10% borrower / 40% band caps."""


class LTVTableInvalid(CreditError):
    """LTV table not non-decreasing in score, or mis-banded."""


# -- score -> LTV table ----------------------------------------------------------
# (min_score, max_ltv_bps). Lookups match the spec table exactly.
LTV_TABLE: Tuple[Tuple[int, int], ...] = (
    (750, 7_500),
    (650, 6_500),
    (550, 5_500),
    (450, 4_500),
    (350, 3_500),
)


def validate_ltv_table(
    table: Tuple[Tuple[int, int], ...] = LTV_TABLE,
) -> None:
    """Monotonicity: any non-non-decreasing update reverts."""
    if not table:
        raise LTVTableInvalid("empty table")
    prev_score, prev_ltv = table[0]
    if prev_score < SCORE_FLOOR:
        raise LTVTableInvalid("lowest band must start at the score floor")
    for score, ltv in table[1:]:
        if score >= prev_score:
            raise LTVTableInvalid("bands must descend in score")
        if ltv > prev_ltv:
            raise LTVTableInvalid("LTV must be non-decreasing in score")
        if not 0 < ltv <= 10_000:
            raise LTVTableInvalid("LTV out of range")
        prev_score, prev_ltv = score, ltv


def max_ltv_bps(score: int) -> int:
    """Score -> max LTV in bps. Below floor: ineligible (0)."""
    if score < SCORE_FLOOR:
        return 0
    for min_score, ltv in LTV_TABLE:
        if score >= min_score:
            return ltv
    return 0  # unreachable given the floor check


def score_band(score: int) -> str:
    for min_score, _ in LTV_TABLE:
        if score >= min_score:
            return f"{min_score}+"
    return "ineligible"


# -- behavioral scoring ------------------------------------------------------------
@dataclass(frozen=True)
class BorrowerFeatures:
    """Caller-supplied wallet-history features. No PII, no network."""

    wallet_age_blocks: int       # blocks since first tx
    repaid: int                  # repaid positions (Aave/Compound/Morpho)
    liquidated: int              # liquidated positions
    avg_utilization: float       # 0..1
    tx_count: int
    protocol_diversity: int      # distinct protocols touched
    stablecoin_ratio: float      # 0..1 of volume
    mixer_interaction: bool = False
    rug_interaction: bool = False


def score_wallet(f: BorrowerFeatures) -> int:
    """Interpretable logistic-style score, 300-850. Deterministic.

    Mixer interaction clamps to 300 (negative-signal rule). Fresh wallets
    score near the middle; only proven repayment history earns the top
    bands — the score tunes LTV, never grants unsecured credit.
    """
    if f.mixer_interaction:
        return SCORE_MIN
    # Age: ~2s blocks; full credit at ~2 years.
    age_years = f.wallet_age_blocks * 2.0 / 31_536_000.0
    age_pts = 60.0 * min(age_years / 2.0, 1.0)
    total = f.repaid + f.liquidated
    repay_rate = (f.repaid / total) if total > 0 else 0.0
    repay_pts = 120.0 * repay_rate
    liq_penalty = 80.0 if f.liquidated > 0 else 0.0
    tx_pts = 40.0 * min(f.tx_count / 1000.0, 1.0)
    div_pts = 40.0 * min(f.protocol_diversity / 8.0, 1.0)
    stable_pts = 40.0 * max(0.0, min(1.0, f.stablecoin_ratio))
    util_penalty = 40.0 * max(0.0, min(1.0, f.avg_utilization))
    rug_penalty = 150.0 if f.rug_interaction else 0.0
    raw = (
        500.0
        + age_pts
        + repay_pts
        + tx_pts
        + div_pts
        + stable_pts
        - liq_penalty
        - util_penalty
        - rug_penalty
    )
    return int(max(SCORE_MIN, min(SCORE_MAX, round(raw))))


# -- attestations --------------------------------------------------------------------
@dataclass(frozen=True)
class ScoreAttestation:
    borrower: str
    score: int
    issued_at: float
    nonce: int
    attestor: str
    model_version: str = MODEL_VERSION


def is_fresh(att: ScoreAttestation, now: float) -> bool:
    return 0 <= now - att.issued_at <= ATTEST_TTL_SECONDS


class AttestationRegistry:
    """Replay protection (nonce per borrower) + freshness + floor.

    When ``authorized_attestors`` is provided, attestations from any
    other attestor are rejected: without it the reference accepts
    self-attested scores (the money-safety property does not depend on
    the score — draws are always collateral-capped — but the score
    should come from the scoring agent in production).
    """

    def __init__(self,
                 authorized_attestors: Optional[Set[str]] = None):
        self._seen_nonces: Dict[str, set] = {}
        self._latest: Dict[str, ScoreAttestation] = {}
        self._authorized = (set(authorized_attestors)
                            if authorized_attestors is not None else None)

    def register(self, att: ScoreAttestation, now: float) -> ScoreAttestation:
        if not is_fresh(att, now):
            raise AttestationInvalid("attestation missing or stale (>72h)")
        if att.score < SCORE_FLOOR:
            raise AttestationInvalid(f"score {att.score} below floor {SCORE_FLOOR}")
        if self._authorized is not None and att.attestor not in self._authorized:
            raise AttestationInvalid(
                f"attestor {att.attestor} not authorized")
        seen = self._seen_nonces.setdefault(att.borrower, set())
        if att.nonce in seen:
            raise AttestationInvalid("replayed attestation nonce")
        seen.add(att.nonce)
        self._latest[att.borrower] = att
        return att

    def live_score(self, borrower: str, now: float) -> int:
        att = self._latest.get(borrower)
        if att is None or not is_fresh(att, now):
            raise AttestationInvalid("no live attestation for borrower")
        return att.score


# -- credit line -----------------------------------------------------------------------
@dataclass
class CreditLine:
    """Collateralized line. The score buys better terms, never zero collateral."""

    borrower: str
    collateral_usd: float
    score: int
    debt_usd: float = 0.0
    line_id: str = ""

    def __post_init__(self):
        if self.collateral_usd < 0 or self.debt_usd < 0:
            raise ValueError("collateral and debt cannot be negative")

    @property
    def ltv_bps(self) -> int:
        return max_ltv_bps(self.score)

    @property
    def max_draw_usd(self) -> float:
        return self.collateral_usd * self.ltv_bps / 10_000

    def draw(self, amount_usd: float) -> float:
        """Draw against the line. Zero-collateral or over-cap draws revert."""
        if amount_usd <= 0:
            raise ValueError("draw amount must be positive")
        if self.collateral_usd <= 0:
            raise UnsecuredDraw("zero-collateral draw: no unsecured book")
        if self.ltv_bps == 0:
            raise UnsecuredDraw("borrower ineligible (score below floor)")
        if self.debt_usd + amount_usd > self.max_draw_usd + 1e-9:
            raise UnsecuredDraw(
                f"draw {amount_usd} exceeds collateral*maxLTV "
                f"({self.max_draw_usd:.2f})"
            )
        self.debt_usd += amount_usd
        return self.debt_usd

    def health_factor(self) -> float:
        """HF = collateral*LTVmax / debt. Infinite with no debt."""
        if self.debt_usd <= 0:
            return float("inf")
        return self.collateral_usd * self.ltv_bps / 10_000 / self.debt_usd

    def circuit_action(self) -> str:
        """ok | hedge | liquidate per the 1.15 / 1.05 thresholds."""
        hf = self.health_factor()
        if hf < HF_LIQUIDATE:
            return "liquidate"
        if hf < HF_HEDGE:
            return "hedge"
        return "ok"

    def hedge_amount_usd(self) -> float:
        """Up to 25% of vault collateral routed to the delta-hedge."""
        if self.circuit_action() != "hedge":
            return 0.0
        return self.collateral_usd * HEDGE_MAX_PCT


# -- book + concentration ---------------------------------------------------------------
class CreditBook:
    """Originated book with 10% per-borrower / 40% per-band caps.

    Caps bind once the book reaches MIN_BOOK_FOR_CAPS_USD: a strict
    share-of-book rule is unsatisfiable while bootstrapping from empty
    (the first loan is trivially 100% of the book), so the bootstrap
    phase is exempt and flagged in the result.
    """

    def __init__(self):
        self._by_borrower: Dict[str, float] = {}
        self._by_band: Dict[str, float] = {}
        self.total_usd: float = 0.0

    def originate(self, borrower: str, amount_usd: float, score: int) -> Dict[str, Any]:
        if amount_usd <= 0:
            raise ValueError("amount must be positive")
        band = score_band(score)
        if band == "ineligible":
            raise AttestationInvalid("score below floor")
        new_total = self.total_usd + amount_usd
        caps_enforced = new_total >= MIN_BOOK_FOR_CAPS_USD
        if caps_enforced:
            if self._by_borrower.get(borrower, 0.0) + amount_usd > new_total * CONC_BORROWER_PCT + 1e-9:
                raise ConcentrationBreach("10% per-borrower cap breached")
            if self._by_band.get(band, 0.0) + amount_usd > new_total * CONC_BAND_PCT + 1e-9:
                raise ConcentrationBreach("40% per-band cap breached")
        self._by_borrower[borrower] = self._by_borrower.get(borrower, 0.0) + amount_usd
        self._by_band[band] = self._by_band.get(band, 0.0) + amount_usd
        self.total_usd = new_total
        return {
            "borrower": borrower,
            "amount_usd": amount_usd,
            "band": band,
            "book_total_usd": self.total_usd,
            "caps_enforced": caps_enforced,
        }


# -- fees ---------------------------------------------------------------------------------
def origination_fee_usd(principal_usd: float) -> float:
    """20 bps on originated principal, to the treasury."""
    if principal_usd < 0:
        raise ValueError("principal cannot be negative")
    return round(principal_usd * FEE_ORIG_BPS / 10_000, 2)


def accrual_fee_usd(interest_usd: float) -> float:
    """20 bps on accrued interest, to the treasury."""
    if interest_usd < 0:
        raise ValueError("interest cannot be negative")
    return round(interest_usd * FEE_ACCRUAL_BPS / 10_000, 2)


def treasury_take(principal_usd: float, apr: float, years: float) -> Dict[str, float]:
    """Worked fee example: principal + simple interest accrual."""
    interest = principal_usd * apr * years
    orig = origination_fee_usd(principal_usd)
    accr = accrual_fee_usd(interest)
    return {
        "principal_usd": principal_usd,
        "interest_usd": round(interest, 2),
        "origination_fee_usd": orig,
        "accrual_fee_usd": accr,
        "total_to_treasury_usd": round(orig + accr, 2),
        "treasury": TREASURY,
    }


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
    return wiring_for("P19_CREDIT", oracle)
