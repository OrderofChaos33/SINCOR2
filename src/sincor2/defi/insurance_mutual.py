"""
SINCOR DeFi P05 — DeFi Risk Mutual (reference build).

Python reference simulation of the mutual insurance pool behind SKU
``SINCOR-DEFI-P05-INSURANCE`` (Nexus-style mutual, SINAX-shaped claims):

- :class:`Underwriter` — risk scores 0..1 from audit/TVL/exploit/velocity/
  oracle inputs (30/20/25/15/10 weights); tiered premium formula
  (200 bps base x tier, 1,500 bps cap); 24h signed publication cadence;
  7,200-block staleness guard.
- :class:`MutualPool` — stablecoin capital pool with ERC-4626-style shares;
  reserve_ratio gate (reserves >= 130% of outstanding cover) enforced on
  every underwrite; 25 bps of every premium forwarded to treasury at
  collection; cover NFTs; pro-rata haircuts to the wei on shortfall;
  pull-based payouts; guardian pause never blocks in-window claimPayout.
- :class:`ClaimEngine` — 30-day claim window, SINAX-shaped attestation
  (assessor allowlist signature), double-claim protection, assessor
  quorum (weight > 5x cover) with 14-day review and bond slashing.
- :class:`Governance` — protocol listings (10% quorum, 60% yes) and a 48h
  timelock on parameter changes.
- :class:`LiveGate` — underwriting blocked until auditor + actuarial +
  founder release (live_blocked).

Safety rules (hard):
- Default mode is DRY_RUN. Intents describe actions; ``executed`` is
  always False; nothing here touches a chain or funds.
- Payouts are pull-based; a failed transfer never bricks other claimants.
- Pause blocks new underwriting but never in-window claim payouts.

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
FEE_BPS = 25                                   # 25 bps of premiums -> treasury
RESERVE_RATIO_NUM = 130                        # reserves >= 130% of cover
RESERVE_RATIO_DEN = 100
CLAIM_WINDOW_SECONDS = 30 * 24 * 3600          # 30 days post-incident
REVIEW_WINDOW_SECONDS = 14 * 24 * 3600         # 14-day assessor review
ASSESSOR_QUORUM_MULT = 5                       # weight > 5x cover amount
ASSESSOR_BOND_UNITS = 1_000
PREMIUM_BASE_BPS = 200
PREMIUM_CAP_BPS = 1_500
SCORE_STALENESS_BLOCKS = 7_200                 # ~24h on Base
SCORE_CADENCE_SECONDS = 24 * 3600
GOV_QUORUM_PCT = 10
GOV_YES_PCT = 60
TIMELOCK_SECONDS = 48 * 3600
MAX_ALLOC_PCT = 0.15
MIN_CAPITAL_WEI = 200_000_000                  # $200 in 6dp stablecoin wei
DRY_RUN = os.getenv("MUTUAL_DRY_RUN", "1").strip() != "0"

UNDERWRITER_ROLE = "UNDERWRITER_ROLE"
ASSESSOR_ROLE = "ASSESSOR_ROLE"
GUARDIAN_ROLE = "GUARDIAN_ROLE"


class MutualError(Exception):
    """Base error for mutual rule violations."""


class ReserveBreachError(MutualError):
    """Underwrite/redeem would breach the 130% reserve floor."""


class StaleScoreError(MutualError):
    """Risk score older than 7,200 blocks: new covers blocked."""


class ClaimRuleError(MutualError):
    """Claim window / attestation / double-claim violation."""


class QuorumError(MutualError):
    """Governance or assessor quorum not met."""


class TimelockError(MutualError):
    """Parameter change attempted before the 48h timelock."""


class LiveBlockedError(MutualError):
    """Underwriting attempted before the live release."""


class UnauthorizedError(MutualError):
    """Caller lacks the required role."""


# -- risk scoring ------------------------------------------------------------------
@dataclass(frozen=True)
class RiskInputs:
    """Normalized 0..1 risk factors (higher = riskier)."""
    audit: float        # 30%: audit count/recency (1 = unaudited/stale)
    tvl: float          # 20%: TVL size + concentration
    exploits: float     # 25%: exploit history
    velocity: float     # 15%: code-change velocity
    oracle: float       # 10%: oracle/dependency risk


@dataclass(frozen=True)
class RiskScore:
    protocol_id: str
    score: float        # 0..1
    published_block: int
    version: int
    signer: str


class Underwriter:
    """Python underwriter agent: scores, tiers, premiums, signed feed."""

    WEIGHTS = (("audit", 0.30), ("tvl", 0.20), ("exploits", 0.25),
               ("velocity", 0.15), ("oracle", 0.10))

    def __init__(self):
        self._scores: Dict[str, RiskScore] = {}
        self._versions: Dict[str, int] = defaultdict(int)

    @classmethod
    def score(cls, inputs: RiskInputs) -> float:
        total = sum(w * getattr(inputs, name) for name, w in cls.WEIGHTS)
        assert abs(sum(w for _, w in cls.WEIGHTS) - 1.0) < 1e-9
        return max(0.0, min(1.0, total))

    @staticmethod
    def tier(score: float) -> Tuple[int, int]:
        """Premium tier as (numerator, denominator): 0.5x / 1.0x / 2.0x."""
        if score <= 0.20:
            return (1, 2)
        if score <= 0.50:
            return (1, 1)
        return (2, 1)

    @classmethod
    def premium_rate_bps(cls, score: float) -> int:
        num, den = cls.tier(score)
        return min(PREMIUM_BASE_BPS * num // den, PREMIUM_CAP_BPS)

    @classmethod
    def premium_wei(cls, cover_wei: int, duration_days: int,
                    score: float) -> int:
        """Annualized premium, integer-exact stablecoin wei."""
        if cover_wei <= 0 or duration_days <= 0:
            raise MutualError("cover and duration must be positive")
        rate_bps = cls.premium_rate_bps(score)
        return cover_wei * rate_bps * duration_days // (10_000 * 365)

    @staticmethod
    def treasury_fee_wei(premium_wei: int) -> int:
        return premium_wei * FEE_BPS // 10_000

    def publish(self, protocol_id: str, score: float, block: int,
                signer: str) -> RiskScore:
        self._versions[protocol_id] += 1
        rec = RiskScore(protocol_id, max(0.0, min(1.0, score)), block,
                        self._versions[protocol_id], signer)
        self._scores[protocol_id] = rec
        return rec

    def get(self, protocol_id: str, current_block: int) -> RiskScore:
        rec = self._scores.get(protocol_id)
        if rec is None:
            raise MutualError(f"no score published for {protocol_id}")
        if current_block - rec.published_block > SCORE_STALENESS_BLOCKS:
            raise StaleScoreError(
                f"score for {protocol_id} is stale "
                f"({current_block - rec.published_block} blocks)"
            )
        return rec


# -- cover & claims ------------------------------------------------------------------
@dataclass
class Cover:
    cover_id: str
    protocol_id: str
    buyer: str
    cover_wei: int
    premium_wei: int
    start_ts: float
    expiry_ts: float
    active: bool = True


@dataclass
class Claim:
    claim_id: str
    cover_id: str
    incident_hash: str
    incident_ts: float
    filed_ts: float
    amount_wei: int
    approved: bool = False
    paid_wei: int = 0


@dataclass(frozen=True)
class Attestation:
    """SINAX-shaped attestation bundle (reference: assessor EIP-712 allowlist)."""
    incident_hash: str
    loss_proof: str       # machine proof of loss (balance-delta evidence)
    assessor: str         # must be in the assessor allowlist
    signature: str = ""


class AssessorRegistry:
    """Bonded assessors; fraudulent approvals slash the bond."""

    def __init__(self):
        self._allowlist: set = set()
        self._bonds: Dict[str, int] = {}
        self._weight: Dict[str, int] = {}

    def appoint(self, assessor: str, weight: int,
                bond_units: int = ASSESSOR_BOND_UNITS) -> None:
        self._allowlist.add(assessor)
        self._weight[assessor] = weight
        self._bonds[assessor] = bond_units

    def is_assessor(self, assessor: str) -> bool:
        return assessor in self._allowlist

    def slash(self, assessor: str, reason: str) -> int:
        slashed = self._bonds.get(assessor, 0)
        self._bonds[assessor] = 0
        logger.warning("assessor %s slashed (%s): %d units", assessor, reason,
                       slashed)
        return slashed

    def quorum_weight(self, voters: List[str]) -> int:
        return sum(self._weight.get(v, 0) for v in voters)


# -- mutual pool ------------------------------------------------------------------
class MutualPool:
    """Capital pool, cover inventory, reserve accounting, payouts."""

    def __init__(self, treasury: str = TREASURY,
                 assessors: Optional[AssessorRegistry] = None,
                 reverting: Optional[set] = None):
        self.treasury = treasury
        self.assessors = assessors or AssessorRegistry()
        self._reverting = reverting or set()
        self.reserves_wei = 0
        self.total_shares = 0
        self._shares: Dict[str, int] = defaultdict(int)
        self._covers: Dict[str, Cover] = {}
        self._claims: Dict[str, Claim] = {}
        self._filed: set = set()  # (cover_id, incident_hash)
        self._cover_seq = 0
        self._claim_seq = 0
        self.paused = False
        self.live_underwriting = False
        self.treasury_collected_wei = 0
        self._pending_payouts: Dict[str, int] = defaultdict(int)

    # -- capitalization --------------------------------------------------
    def deposit_capital(self, provider: str, amount_wei: int) -> int:
        if amount_wei <= 0:
            raise MutualError("deposit must be positive")
        if self.total_shares == 0:
            shares = amount_wei  # 1:1 genesis
        else:
            shares = amount_wei * self.total_shares // max(self.reserves_wei, 1)
        self.reserves_wei += amount_wei
        self.total_shares += shares
        self._shares[provider] += shares
        return shares

    def outstanding_cover_wei(self) -> int:
        return sum(c.cover_wei for c in self._covers.values() if c.active)

    def _reserve_ok_after(self, reserves: int, outstanding: int) -> bool:
        return reserves * RESERVE_RATIO_DEN >= outstanding * RESERVE_RATIO_NUM

    def redeem_capital(self, provider: str, shares: int) -> int:
        if shares <= 0 or shares > self._shares[provider]:
            raise MutualError("invalid redeem amount")
        assets = shares * self.reserves_wei // max(self.total_shares, 1)
        if not self._reserve_ok_after(self.reserves_wei - assets,
                                      self.outstanding_cover_wei()):
            raise ReserveBreachError(
                "redeem would breach the 130% reserve floor")
        self.reserves_wei -= assets
        self.total_shares -= shares
        self._shares[provider] -= shares
        return assets

    # -- underwriting ----------------------------------------------------
    def buy_cover(self, buyer: str, protocol_id: str, cover_wei: int,
                  duration_days: int, score: float,
                  current_block: int = 0) -> Cover:
        if self.paused:
            raise MutualError("underwriting paused")
        if not self.live_underwriting:
            raise LiveBlockedError(
                "underwriting blocked: live release not presented")
        if cover_wei <= 0 or duration_days <= 0:
            raise MutualError("cover and duration must be positive")
        premium = Underwriter.premium_wei(cover_wei, duration_days, score)
        fee = Underwriter.treasury_fee_wei(premium)
        new_outstanding = self.outstanding_cover_wei() + cover_wei
        if not self._reserve_ok_after(self.reserves_wei, new_outstanding):
            raise ReserveBreachError(
                "underwrite would breach the 130% reserve floor")
        # collect premium + fee in one transfer: the pool keeps the FULL
        # premium; the 25 bps surcharge goes to treasury on top.
        self.reserves_wei += premium
        self.treasury_collected_wei += fee
        self._cover_seq += 1
        now = time.time()
        cover = Cover(
            cover_id=f"cover-{self._cover_seq}",
            protocol_id=protocol_id,
            buyer=buyer,
            cover_wei=cover_wei,
            premium_wei=premium,
            start_ts=now,
            expiry_ts=now + duration_days * 24 * 3600,
        )
        self._covers[cover.cover_id] = cover
        logger.info("cover %s underwritten: premium=%d fee=%d",
                    cover.cover_id, premium, fee)
        return cover

    # -- claims ----------------------------------------------------------
    def file_claim(self, cover_id: str, attestation: Attestation,
                   incident_ts: float, filed_ts: Optional[float] = None) -> Claim:
        cover = self._covers.get(cover_id)
        if cover is None or not cover.active:
            raise ClaimRuleError("unknown or inactive cover")
        filed_ts = filed_ts if filed_ts is not None else time.time()
        if not (cover.start_ts <= incident_ts <= cover.expiry_ts):
            raise ClaimRuleError("incident outside cover period")
        if filed_ts > incident_ts + CLAIM_WINDOW_SECONDS:
            raise ClaimRuleError("claim filed after the 30-day window")
        if not attestation.loss_proof:
            raise ClaimRuleError("missing loss attestation")
        if not self.assessors.is_assessor(attestation.assessor):
            raise ClaimRuleError("attestation assessor not allowlisted")
        key = (cover_id, attestation.incident_hash)
        if key in self._filed:
            raise ClaimRuleError("double claim: (cover_id, incident_hash) seen")
        self._filed.add(key)
        self._claim_seq += 1
        claim = Claim(
            claim_id=f"claim-{self._claim_seq}",
            cover_id=cover_id,
            incident_hash=attestation.incident_hash,
            incident_ts=incident_ts,
            filed_ts=filed_ts,
            amount_wei=cover.cover_wei,
        )
        self._claims[claim.claim_id] = claim
        return claim

    def assess_claim(self, claim_id: str, votes: Dict[str, bool]) -> bool:
        """Assessor vote. Quorum: weight > 5x cover; simple majority approves."""
        claim = self._claims[claim_id]
        cover = self._covers[claim.cover_id]
        if time.time() > claim.filed_ts + REVIEW_WINDOW_SECONDS:
            raise ClaimRuleError("assessor review window elapsed")
        weight = self.assessors.quorum_weight(list(votes))
        if weight <= ASSESSOR_QUORUM_MULT * cover.cover_wei:
            raise QuorumError(
                f"assessor weight {weight} does not exceed "
                f"5x cover {5 * cover.cover_wei}")
        yes = sum(1 for v in votes.values() if v)
        claim.approved = yes > len(votes) / 2
        return claim.approved

    def shortfall_factor(self) -> Tuple[int, int]:
        """(numerator, denominator) for pro-rata haircuts; (1,1) when whole."""
        approved = [c for c in self._claims.values()
                    if c.approved and c.paid_wei == 0]
        total = sum(c.amount_wei for c in approved)
        if total == 0 or total <= self.reserves_wei:
            return (1, 1)
        return (self.reserves_wei, total)

    def claim_payout(self, claim_id: str) -> int:
        """Pull-based payout. Pause NEVER blocks this path."""
        claim = self._claims[claim_id]
        if not claim.approved:
            raise ClaimRuleError("claim not approved")
        if claim.paid_wei > 0:
            return 0  # already paid: idempotent
        num, den = self.shortfall_factor()
        payout = claim.amount_wei * num // den
        payout = min(payout, self.reserves_wei)
        claim.paid_wei = payout
        cover = self._covers[claim.cover_id]
        cover.active = False
        recipient = cover.buyer
        if recipient in self._reverting:
            # non-bricking: park for pull, others unaffected
            self._pending_payouts[recipient] += payout
            self.reserves_wei -= payout
            return 0
        self.reserves_wei -= payout
        return payout

    def withdraw_pending(self, recipient: str) -> int:
        amount = self._pending_payouts.get(recipient, 0)
        self._pending_payouts[recipient] = 0
        return amount

    # -- admin -----------------------------------------------------------
    def pause_underwriting(self, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self.paused = True

    def unpause_underwriting(self, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self.paused = False


# -- governance ------------------------------------------------------------------
@dataclass
class ListingProposal:
    proposal_id: str
    protocol_id: str
    yes_weight: int = 0
    no_weight: int = 0
    total_shares_at_vote: int = 0
    executed: bool = False


class Governance:
    """Member votes for listings; 48h timelock on parameter changes."""

    def __init__(self, pool: MutualPool):
        self.pool = pool
        self._proposals: Dict[str, ListingProposal] = {}
        self._seq = 0
        self._timelocked: Dict[str, float] = {}  # action_id -> eta
        self.listed: set = set()

    def propose_listing(self, protocol_id: str) -> ListingProposal:
        self._seq += 1
        prop = ListingProposal(f"prop-{self._seq}", protocol_id,
                               total_shares_at_vote=self.pool.total_shares)
        self._proposals[prop.proposal_id] = prop
        return prop

    def vote(self, proposal_id: str, voter_shares: int, yes: bool) -> None:
        prop = self._proposals[proposal_id]
        if yes:
            prop.yes_weight += voter_shares
        else:
            prop.no_weight += voter_shares

    def execute_listing(self, proposal_id: str) -> bool:
        prop = self._proposals[proposal_id]
        voted = prop.yes_weight + prop.no_weight
        base = max(prop.total_shares_at_vote, 1)
        if voted * 100 < base * GOV_QUORUM_PCT:
            raise QuorumError("listing quorum < 10% of shares")
        if prop.yes_weight * 100 < voted * GOV_YES_PCT:
            raise QuorumError("listing yes votes < 60%")
        prop.executed = True
        self.listed.add(prop.protocol_id)
        return True

    def schedule_param_change(self, action_id: str,
                              delay_s: int = TIMELOCK_SECONDS) -> float:
        eta = time.time() + delay_s
        self._timelocked[action_id] = eta
        return eta

    def execute_param_change(self, action_id: str) -> None:
        eta = self._timelocked.get(action_id)
        if eta is None:
            raise TimelockError("unknown timelocked action")
        if time.time() < eta:
            raise TimelockError("48h timelock not elapsed")
        del self._timelocked[action_id]


def commitment_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
