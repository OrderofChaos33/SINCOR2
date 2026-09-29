"""SINCOR DeFi P21 — Treasury DAO (reference build).

Python reference simulation of the 4-agent treasury swarm behind SKU
``SINCOR-DEFI-P21-TREASURY``:

- ``RiskOfficer`` — enforces allocation bands and the per-venue 30%
  hard cap; refuses any plan that violates them.
- ``Allocator`` — computes a rebalancing plan under the bands and cap;
  allocations sum to exactly 100.00% in integer basis points (no float
  drift).
- ``Reviewer`` — a hold must be *reviewed* (approved or rejected) before
  it can be published or executed. Unreviewed holds block publishing;
  holds older than 72 hours raise a backlog alert.
- ``Executor`` — never broadcasts. ``broadcast()`` unconditionally
  raises :class:`BroadcastForbidden`. With ``EXECUTE_LIVE_ENV=1`` *and*
  a reviewed, in-band hold, it may dispatch to an approved external
  executor injected by the operator — the swarm itself still never
  touches a chain.

Holds are content-addressed: the hold ID is the SHA-256 of the canonical
JSON of the hold content, so a hold's ID *is* its content hash and any
tampering changes the ID.

Dashboard/monitoring rule: on any upstream failure the status feed
reports ``unknown``, never healthy. Realized-yield accounting takes an
8 bps fee on positive realized yield, routed to
``catalog.TREASURY``.

Safety rules (hard):
- Default mode is DRY_RUN; ``EXECUTE_LIVE_ENV`` defaults to off.
- No signing libraries are imported anywhere in the allocation path;
  there is no key material and no broadcast capability.
- Nothing here touches a chain, a vault, or funds.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.sincor2.defi import catalog as catalog_mod

logger = logging.getLogger(__name__)

DRY_RUN = os.getenv("P21_DRY_RUN", "1").strip() != "0"
EXECUTE_LIVE_ENV = os.getenv("EXECUTE_LIVE_ENV", "0").strip() == "1"

FEE_BPS = 8
PER_VENUE_CAP_BPS = 3_000          # 30% hard cap per venue
HOLD_BACKLOG_SECONDS = 72 * 3600   # unreviewed >72h -> backlog alert
TOTAL_BPS = 10_000                 # allocations sum to exactly 100.00%


class TreasuryError(RuntimeError):
    """Base error for treasury rule violations."""


class BroadcastForbidden(TreasuryError):
    """The swarm never broadcasts; this is always raised."""


class BandViolation(TreasuryError):
    """Allocation outside its band or over the per-venue cap."""


class UnreviewedHold(TreasuryError):
    """A hold was offered for publish/execute before review."""


class StaleHold(TreasuryError):
    """Hold older than 72h and still unreviewed."""


class Unauthorized(TreasuryError):
    """Caller lacks the required role."""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def content_id(payload: Dict[str, Any]) -> str:
    """Hold ID = SHA-256 of the canonical JSON of the hold content."""
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True)
class AllocationBand:
    venue: str
    min_bps: int
    max_bps: int


# -- risk officer -------------------------------------------------------------------
class RiskOfficer:
    """Enforces allocation bands and the 30% per-venue hard cap."""

    def __init__(self, bands: List[AllocationBand]):
        self.bands = {b.venue: b for b in bands}

    def validate(self, allocations_bps: Dict[str, int]) -> None:
        total = sum(allocations_bps.values())
        if total != TOTAL_BPS:
            raise BandViolation(
                f"allocations sum to {total} bps, must be exactly 10000")
        for venue, bps in allocations_bps.items():
            if bps > PER_VENUE_CAP_BPS:
                raise BandViolation(
                    f"{venue} at {bps} bps exceeds 30% per-venue cap")
            band = self.bands.get(venue)
            if band is None:
                raise BandViolation(f"{venue} has no allocation band")
            if not (band.min_bps <= bps <= band.max_bps):
                raise BandViolation(
                    f"{venue} at {bps} bps outside band "
                    f"[{band.min_bps}, {band.max_bps}]")


# -- allocator -------------------------------------------------------------------------
class Allocator:
    """Computes a rebalancing plan; integer-bps exact, no float drift."""

    def __init__(self, risk: RiskOfficer):
        self.risk = risk

    def propose(self, targets_bps: Dict[str, int],
                created_by: str, now: Optional[float] = None) -> "Hold":
        self.risk.validate(targets_bps)
        return Hold.create(allocations_bps=dict(targets_bps),
                           created_by=created_by,
                           created_at=now or time.time())


# -- holds -----------------------------------------------------------------------------
@dataclass
class Hold:
    hold_id: str
    allocations_bps: Dict[str, int]
    created_by: str
    created_at: float
    reviewed: bool = False
    review: Optional[Dict[str, Any]] = None

    @classmethod
    def create(cls, allocations_bps: Dict[str, int], created_by: str,
               created_at: float) -> "Hold":
        content = {"allocations_bps": allocations_bps,
                   "created_by": created_by,
                   "created_at": created_at}
        return cls(hold_id=content_id(content),
                   allocations_bps=dict(allocations_bps),
                   created_by=created_by, created_at=created_at)

    def verify_id(self) -> bool:
        """The ID must equal the content hash — tampering changes the ID."""
        content = {"allocations_bps": self.allocations_bps,
                   "created_by": self.created_by,
                   "created_at": self.created_at}
        return content_id(content) == self.hold_id


# -- reviewer ----------------------------------------------------------------------------
class Reviewer:
    """Human/agent review gate. Unreviewed holds block publishing."""

    def __init__(self, reviewers: List[str]):
        self.reviewers = set(reviewers)

    def review(self, hold: Hold, approved: bool, reviewer: str,
               note: str = "",
               now: Optional[float] = None) -> Hold:
        if reviewer not in self.reviewers:
            raise Unauthorized("reviewer not authorized")
        if hold.reviewed:
            raise TreasuryError("hold already reviewed")
        hold.reviewed = True
        hold.review = {"approved": approved, "reviewer": reviewer,
                       "note": note, "reviewed_at": now or time.time()}
        return hold

    def backlog(self, holds: List[Hold],
                now: Optional[float] = None) -> List[Hold]:
        """Unreviewed holds older than 72h raise a backlog alert."""
        now = now if now is not None else time.time()
        return [h for h in holds
                if not h.reviewed and now - h.created_at > HOLD_BACKLOG_SECONDS]


# -- executor ------------------------------------------------------------------------------
# NOTE: no signing library is imported anywhere in this module. There is
# no key material and no broadcast capability by construction.
class Executor:
    """Dispatches reviewed holds to an approved *external* executor.

    The swarm itself never broadcasts: ``broadcast()`` always raises.
    With EXECUTE_LIVE_ENV=1 an operator may inject an external executor
    callable; only reviewed, approved, in-band holds dispatch to it.
    """

    def __init__(self, risk: RiskOfficer,
                 external_executor: Optional[Callable[[Hold], str]] = None):
        self.risk = risk
        self.external_executor = external_executor
        self.dispatched: List[Dict[str, Any]] = []

    def broadcast(self, hold: Hold) -> None:
        raise BroadcastForbidden(
            "the treasury swarm never broadcasts transactions")

    def publish(self, hold: Hold,
                now: Optional[float] = None) -> Dict[str, Any]:
        """Publishing a hold requires review first."""
        now = now if now is not None else time.time()
        if not hold.reviewed:
            if now - hold.created_at > HOLD_BACKLOG_SECONDS:
                raise StaleHold("unreviewed hold older than 72h")
            raise UnreviewedHold("hold must be reviewed before publishing")
        if not hold.review or not hold.review.get("approved"):
            raise UnreviewedHold("hold was rejected in review")
        if not hold.verify_id():
            raise TreasuryError("hold content does not match its ID")
        self.risk.validate(hold.allocations_bps)
        receipt = {"hold_id": hold.hold_id, "published_at": now,
                   "mode": "dry_run" if DRY_RUN else "live_intent"}
        return receipt

    def execute(self, hold: Hold,
                now: Optional[float] = None) -> Dict[str, Any]:
        """Even with the live flag on, only reviewed holds dispatch —
        and only to the approved external executor."""
        receipt = self.publish(hold, now=now)  # all gates re-checked
        if not EXECUTE_LIVE_ENV:
            return {**receipt, "dispatched": False,
                    "reason": "EXECUTE_LIVE_ENV off"}
        if self.external_executor is None:
            raise TreasuryError("no approved external executor configured")
        ref = self.external_executor(hold)
        record = {**receipt, "dispatched": True, "external_ref": ref}
        self.dispatched.append(record)
        return record


# -- realized yield + 8 bps fee --------------------------------------------------------------------
@dataclass
class YieldLedger:
    realized_yield_wei: int = 0
    fee_owed_wei: int = 0

    def record_yield(self, amount_wei: int) -> Dict[str, int]:
        """8 bps fee on *positive* realized yield; losses take no fee."""
        if amount_wei > 0:
            fee = amount_wei * FEE_BPS // TOTAL_BPS
            self.realized_yield_wei += amount_wei
            self.fee_owed_wei += fee
            return {"realized_wei": amount_wei, "fee_wei": fee}
        self.realized_yield_wei += amount_wei
        return {"realized_wei": amount_wei, "fee_wei": 0}

    def claim_fees(self) -> Dict[str, Any]:
        amount = self.fee_owed_wei
        self.fee_owed_wei = 0
        return {"treasury": catalog_mod.TREASURY, "amount_wei": amount,
                "fee_bps": FEE_BPS}


# -- swarm + status -------------------------------------------------------------------------
class TreasurySwarm:
    def __init__(self, bands: List[AllocationBand], reviewers: List[str],
                 external_executor: Optional[Callable[[Hold], str]] = None):
        self.risk = RiskOfficer(bands)
        self.allocator = Allocator(self.risk)
        self.reviewer = Reviewer(reviewers)
        self.executor = Executor(self.risk, external_executor)
        self.yield_ledger = YieldLedger()
        self.holds: List[Hold] = []

    def propose_hold(self, targets_bps: Dict[str, int], created_by: str,
                     now: Optional[float] = None) -> Hold:
        hold = self.allocator.propose(targets_bps, created_by, now=now)
        self.holds.append(hold)
        return hold


def status_payload(swarm: TreasurySwarm,
                   data_fresh: bool = True) -> Dict[str, Any]:
    # Dashboard rule: upstream failure -> "unknown", never healthy.
    if not data_fresh:
        return {"product": "SINCOR-DEFI-P21-TREASURY", "status": "unknown",
                "ts": time.time(),
                "mode": "dry_run" if DRY_RUN else "live_intent"}
    return {
        "product": "SINCOR-DEFI-P21-TREASURY",
        "status": "ok",
        "ts": time.time(),
        "mode": "dry_run" if DRY_RUN else "live_intent",
        "holds": len(swarm.holds),
        "reviewed": sum(1 for h in swarm.holds if h.reviewed),
        "backlog": len(swarm.reviewer.backlog(swarm.holds)),
        "fee_bps": FEE_BPS,
    }
