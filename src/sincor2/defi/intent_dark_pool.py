"""
SINCOR DeFi P03 — Intent Solver & Dark Pool (reference build).

Python reference simulation of the intent-based dark pool behind SKU
``SINCOR-DEFI-P03-DARKPOOL`` ("dark water" design: zk co-processor style
matching computed off-chain, net balances settled on-chain):

- :class:`DarkPool` — intent lifecycle. Only hash commitments are stored
  on-chain; plaintext (size/price) never touches chain or mempool. Cancel,
  expiry auto-refund, per-user nonces, replay protection.
- :class:`Matcher` — off-chain matching (99% of compute off-chain per the
  coordination spec). Price-valid, size-consistent matches with pro-rata
  partial fills; idempotent batch submission; invalid batches rejected
  with intents left reusable.
- :class:`SettlementEngine` — net-balance settlement in AXM/USDC only
  (``axm_only_settlement`` gate enforced in code); 8 bps fee to the
  canonical treasury; pull-based claims; failed transfers never brick a
  batch (non-bricking rule).
- :class:`SplitSolver` — CoW-style split routing: intents above
  ``split_threshold`` are split across dark-pool fills and allowlisted
  DEX venues when splitting improves net execution; ``min_out`` enforced
  on every leg.

Safety rules (hard):
- Default mode is DRY_RUN. Intents describe actions; ``executed`` is
  always False; nothing here touches a chain or funds.
- Settlement assets are AXM/USDC only — anything else reverts.
- Pause blocks new intents but never withdrawals or claims.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)

# Catalog gates mirrored as code constants.
SETTLEMENT_ASSETS = ("AXM", "USDC")          # axm_only_settlement
FEE_BPS = 8                                  # 8 bps on settled volume
SPLIT_THRESHOLD = int(os.getenv("DARKPOOL_SPLIT_THRESHOLD", "5000"))
DRY_RUN = os.getenv("DARKPOOL_DRY_RUN", "1").strip() != "0"

MATCHER_ROLE = "MATCHER_ROLE"
GUARDIAN_ROLE = "GUARDIAN_ROLE"
ADMIN_ROLE = "ADMIN_ROLE"


class DarkPoolError(Exception):
    """Base error for dark-pool rule violations."""


class UnauthorizedError(DarkPoolError):
    """Caller lacks the required role."""


class IntentReplayError(DarkPoolError):
    """Intent already settled / wrong nonce: replay rejected."""


class SettlementAssetError(DarkPoolError):
    """Non-AXM/USDC settlement asset: reverts."""


class BatchVerificationError(DarkPoolError):
    """Batch failed verification: rejected, intents stay reusable."""


class MinOutViolationError(DarkPoolError):
    """A routing leg violates min_out."""


# -- intents -----------------------------------------------------------------
@dataclass(frozen=True)
class Intent:
    """Plaintext intent — lives OFF-CHAIN. Only the commitment is published."""

    intent_id: str
    user: str
    asset_in: str
    asset_out: str
    amount_in: int      # wei, plaintext off-chain only
    min_out: int        # wei limit price, plaintext off-chain only
    expiry_ts: float
    nonce: int
    salt: str = ""

    @property
    def commitment(self) -> str:
        body = "|".join([
            self.intent_id, self.user, self.asset_in, self.asset_out,
            str(self.amount_in), str(self.min_out),
            str(self.expiry_ts), str(self.nonce), self.salt,
        ])
        return hashlib.sha256(body.encode()).hexdigest()


@dataclass
class CommitmentRecord:
    """On-chain view: commitment only. No size, no price, no mempool leak."""

    intent_id: str
    user: str
    commitment: str
    expiry_ts: float
    nonce: int
    status: str = "open"  # open | cancelled | expired | settling | settled

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DarkPool:
    """Intent lifecycle: submit (commit), cancel, expiry, replay protection."""

    def __init__(self):
        self._records: Dict[str, CommitmentRecord] = {}
        self._nonces: Dict[str, int] = {}
        self.paused = False
        self.events: List[Dict[str, Any]] = []

    # -- nonces -----------------------------------------------------------
    def nonce_of(self, user: str) -> int:
        return self._nonces.get(user, 0)

    # -- lifecycle --------------------------------------------------------
    def submit(self, intent: Intent) -> CommitmentRecord:
        if self.paused:
            raise DarkPoolError("pool paused: new intents blocked")
        if intent.intent_id in self._records:
            raise DarkPoolError(f"duplicate intent_id: {intent.intent_id}")
        if intent.expiry_ts <= time.time():
            raise DarkPoolError("intent already expired")
        if intent.nonce != self.nonce_of(intent.user):
            raise IntentReplayError(
                f"bad nonce for {intent.user}: got {intent.nonce}, "
                f"expected {self.nonce_of(intent.user)}"
            )
        rec = CommitmentRecord(
            intent_id=intent.intent_id,
            user=intent.user,
            commitment=intent.commitment,
            expiry_ts=intent.expiry_ts,
            nonce=intent.nonce,
        )
        self._records[intent.intent_id] = rec
        self._nonces[intent.user] = intent.nonce + 1
        self.events.append({"event": "IntentSubmitted",
                            "intent_id": intent.intent_id, "user": intent.user})
        return rec

    def cancel(self, intent_id: str, caller: str) -> CommitmentRecord:
        rec = self._records[intent_id]
        if rec.user != caller:
            raise UnauthorizedError("only the intent owner can cancel")
        if rec.status != "open":
            raise DarkPoolError(f"cannot cancel intent in status {rec.status}")
        rec.status = "cancelled"
        self.events.append({"event": "IntentCancelled", "intent_id": intent_id})
        return rec

    def sweep_expiry(self, now: Optional[float] = None) -> List[str]:
        now = time.time() if now is None else now
        expired = []
        for rec in self._records.values():
            if rec.status == "open" and rec.expiry_ts <= now:
                rec.status = "expired"
                expired.append(rec.intent_id)
                self.events.append({"event": "IntentExpired",
                                    "intent_id": rec.intent_id})
        return expired

    def mark_settling(self, intent_id: str) -> None:
        rec = self._records[intent_id]
        if rec.status == "settled":
            raise IntentReplayError(f"intent {intent_id} already settled")
        if rec.status != "open":
            raise DarkPoolError(f"intent {intent_id} not open (status={rec.status})")
        rec.status = "settling"

    def mark_settled(self, intent_id: str) -> None:
        self._records[intent_id].status = "settled"

    def record_of(self, intent_id: str) -> CommitmentRecord:
        return self._records[intent_id]

    # -- access -----------------------------------------------------------
    def pause(self, roles: Dict[str, set], caller: str) -> None:
        if caller not in roles.get(GUARDIAN_ROLE, set()):
            raise UnauthorizedError(f"{caller} lacks {GUARDIAN_ROLE}")
        self.paused = True

    def unpause(self, roles: Dict[str, set], caller: str) -> None:
        if caller not in roles.get(GUARDIAN_ROLE, set()):
            raise UnauthorizedError(f"{caller} lacks {GUARDIAN_ROLE}")
        self.paused = False


# -- matching ----------------------------------------------------------------
@dataclass(frozen=True)
class Match:
    buy_intent_id: str   # intent giving asset_in, receiving asset_out
    sell_intent_id: str
    amount_in_buy: int   # wei of buy.asset_in the buyer gives
    amount_in_sell: int  # wei of sell.asset_in the seller gives
    partial: bool = False


@dataclass
class Batch:
    batch_id: str
    matches: List[Match]
    created_ts: float = field(default_factory=time.time)
    dry_run: bool = DRY_RUN
    executed: bool = False


def _feasible(a: Intent, b: Intent, q1: int, q2: int) -> bool:
    """Both sides meet their pro-rata limit price at (q1, q2)."""
    if q1 <= 0 or q2 <= 0:
        return False
    return (q2 * a.amount_in >= a.min_out * q1
            and q1 * b.amount_in >= b.min_out * q2)


def match_pair(a: Intent, b: Intent) -> Optional[Match]:
    """CoW-style pair match with pro-rata partial fills.

    Returns None when no price-valid, size-consistent fill exists.
    """
    if not (a.asset_in == b.asset_out and a.asset_out == b.asset_in):
        return None
    q1, q2 = a.amount_in, b.amount_in
    if _feasible(a, b, q1, q2):
        return Match(a.intent_id, b.intent_id, q1, q2, partial=False)
    # Try scaling the buyer down to the max that still meets its limit.
    q1p = q2 * a.amount_in // a.min_out if a.min_out > 0 else 0
    if _feasible(a, b, q1p, q2):
        return Match(a.intent_id, b.intent_id, q1p, q2, partial=True)
    # Try scaling the seller down symmetrically.
    q2p = q1 * b.amount_in // b.min_out if b.min_out > 0 else 0
    if _feasible(a, b, q1, q2p):
        return Match(a.intent_id, b.intent_id, q1, q2p, partial=True)
    return None


class Matcher:
    """Off-chain matcher: collects intents, computes matches, batches them."""

    def __init__(self):
        self._batches: Dict[str, Batch] = {}

    def find_matches(self, intents: List[Intent]) -> List[Match]:
        live = [i for i in intents if i.expiry_ts > time.time()]
        matches: List[Match] = []
        used: set = set()
        for i, a in enumerate(live):
            if a.intent_id in used:
                continue
            for b in live[i + 1:]:
                if b.intent_id in used:
                    continue
                m = match_pair(a, b) or match_pair(b, a)
                if m:
                    matches.append(m)
                    used.add(m.buy_intent_id)
                    used.add(m.sell_intent_id)
                    break
        return matches

    def build_batch(self, batch_id: str, matches: List[Match]) -> Batch:
        return Batch(batch_id=batch_id, matches=list(matches))

    def verify_batch(self, batch: Batch,
                     intents: Dict[str, Intent]) -> bool:
        """Re-derive every match; any deviation rejects the batch."""
        seen: set = set()
        for m in batch.matches:
            if m.buy_intent_id in seen or m.sell_intent_id in seen:
                return False
            seen.add(m.buy_intent_id)
            seen.add(m.sell_intent_id)
            a, b = intents.get(m.buy_intent_id), intents.get(m.sell_intent_id)
            if a is None or b is None:
                return False
            expected = match_pair(a, b) or match_pair(b, a)
            if expected is None:
                return False
            if (expected.amount_in_buy != m.amount_in_buy
                    or expected.amount_in_sell != m.amount_in_sell):
                return False  # tampered amounts
        return True

    def submit_batch(self, batch: Batch,
                     intents: Dict[str, Intent]) -> Dict[str, Any]:
        """Idempotent: a duplicate batch_id is a safe no-op."""
        if batch.batch_id in self._batches:
            return {"status": "duplicate-noop", "batch_id": batch.batch_id}
        if not self.verify_batch(batch, intents):
            raise BatchVerificationError(
                f"batch {batch.batch_id} failed verification; intents reusable")
        self._batches[batch.batch_id] = batch
        return {"status": "accepted", "batch_id": batch.batch_id,
                "matches": len(batch.matches)}


# -- settlement ---------------------------------------------------------------
@dataclass
class Settlement:
    batch_id: str
    net: Dict[Tuple[str, str], int]          # (user, asset) -> claimable wei
    fees: Dict[str, int]                     # asset -> treasury fee wei
    treasury: str = TREASURY
    events: List[Dict[str, Any]] = field(default_factory=list)


class SettlementEngine:
    """Net-balance settlement. AXM/USDC only. Pull claims. Non-bricking."""

    FEE_BPS = FEE_BPS

    def __init__(self):
        self._balances: Dict[Tuple[str, str], int] = {}
        self._settled_batches: set = set()
        self.transfer_errors: List[Dict[str, Any]] = []

    @staticmethod
    def _fee(amount_wei: int) -> int:
        return amount_wei * FEE_BPS // 10_000

    def settle(self, batch: Batch, intents: Dict[str, Intent],
               pool: DarkPool) -> Settlement:
        if batch.batch_id in self._settled_batches:
            raise DarkPoolError(f"batch {batch.batch_id} already settled")
        # axm_only_settlement gate: enforced before any state change.
        for m in batch.matches:
            for iid in (m.buy_intent_id, m.sell_intent_id):
                intent = intents[iid]
                if (intent.asset_in not in SETTLEMENT_ASSETS
                        or intent.asset_out not in SETTLEMENT_ASSETS):
                    raise SettlementAssetError(
                        f"intent {iid} uses non-settlement asset "
                        f"{intent.asset_in}->{intent.asset_out}")
        # Mark intents settling (replay protection); never touches transfers.
        for m in batch.matches:
            pool.mark_settling(m.buy_intent_id)
            pool.mark_settling(m.sell_intent_id)

        net: Dict[Tuple[str, str], int] = {}
        fees: Dict[str, int] = {}
        events: List[Dict[str, Any]] = []
        gross_by_asset: Dict[str, int] = {}
        for m in batch.matches:
            buy, sell = intents[m.buy_intent_id], intents[m.sell_intent_id]
            # Leg 1: buyer gives buy.asset_in -> seller receives it.
            # Leg 2: seller gives sell.asset_in -> buyer receives it.
            for giver, taker, asset, gross in [
                (buy.user, sell.user, buy.asset_in, m.amount_in_buy),
                (sell.user, buy.user, sell.asset_in, m.amount_in_sell),
            ]:
                fee = self._fee(gross)
                recv_net = gross - fee
                key = (taker, asset)
                net[key] = net.get(key, 0) + recv_net
                fees[asset] = fees.get(asset, 0) + fee
                gross_by_asset[asset] = gross_by_asset.get(asset, 0) + gross
                events.append({
                    "event": "LegSettled",
                    "batch_id": batch.batch_id,
                    "from": giver, "to": taker, "asset": asset,
                    "gross_wei": gross, "fee_wei": fee,
                    "net_wei": recv_net,
                })
        # Conservation: per asset, gross in == net out + fees (exact, wei).
        for asset, gross in gross_by_asset.items():
            paid_out = sum(v for (u, a), v in net.items() if a == asset)
            assert paid_out + fees.get(asset, 0) == gross, \
                f"conservation violated for {asset}"

        for key, amt in net.items():
            self._balances[key] = self._balances.get(key, 0) + amt
        for m in batch.matches:
            pool.mark_settled(m.buy_intent_id)
            pool.mark_settled(m.sell_intent_id)
        self._settled_batches.add(batch.batch_id)
        events.append({"event": "BatchSettled", "batch_id": batch.batch_id,
                       "fees": dict(fees), "treasury": TREASURY})
        return Settlement(batch_id=batch.batch_id, net=net, fees=fees,
                          events=events)

    def claim(self, user: str, asset: str,
              transfer: Optional[Callable[[str, str, int], None]] = None) -> int:
        """Pull-based claim. A failed transfer never bricks: funds stay
        claimable and the error is recorded."""
        key = (user, asset)
        amount = self._balances.get(key, 0)
        if amount <= 0:
            return 0
        if transfer is not None:
            try:
                transfer(user, asset, amount)
            except Exception as exc:  # noqa: BLE001 - non-bricking by design
                self.transfer_errors.append(
                    {"user": user, "asset": asset, "amount": amount,
                     "error": str(exc)})
                logger.exception("claim transfer failed; funds remain claimable")
                return 0
        self._balances[key] = 0
        return amount

    def balance_of(self, user: str, asset: str) -> int:
        return self._balances.get((user, asset), 0)


# -- split solver -------------------------------------------------------------
@dataclass(frozen=True)
class VenueQuote:
    name: str
    rate_num: int   # out = amount_in * rate_num // rate_den
    rate_den: int


@dataclass(frozen=True)
class DarkFill:
    available_in: int
    rate_num: int
    rate_den: int


@dataclass
class Leg:
    venue: str
    amount_in: int
    expected_out: int
    min_out_leg: int


@dataclass
class RoutePlan:
    intent_id: str
    legs: List[Leg]
    total_expected_out: int
    split: bool
    dry_run: bool = DRY_RUN
    executed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SplitSolver:
    """CoW-style split routing with per-leg min_out enforcement."""

    def __init__(self, split_threshold: int = SPLIT_THRESHOLD):
        self.split_threshold = split_threshold

    @staticmethod
    def _quote(amount_in: int, num: int, den: int) -> int:
        return amount_in * num // den

    def _best_venue(self, amount_in: int,
                    venues: List[VenueQuote]) -> Tuple[VenueQuote, int]:
        best, best_out = venues[0], -1
        for v in venues:
            out = self._quote(amount_in, v.rate_num, v.rate_den)
            if out > best_out:
                best, best_out = v, out
        return best, best_out

    def route(self, intent: Intent, dark: Optional[DarkFill],
              venues: List[VenueQuote]) -> RoutePlan:
        if not venues:
            raise DarkPoolError("no venues allowlisted")
        # Best single-venue execution (baseline).
        best_v, best_out = self._best_venue(intent.amount_in, venues)
        if best_out < intent.min_out:
            raise MinOutViolationError("no single venue meets min_out")
        best_single = RoutePlan(
            intent_id=intent.intent_id,
            legs=[Leg(best_v.name, intent.amount_in, best_out, intent.min_out)],
            total_expected_out=best_out,
            split=False,
        )
        if intent.amount_in < self.split_threshold:
            return best_single  # below split_threshold: never split
        if dark is None or dark.available_in <= 0:
            return best_single
        # Try dark + best-venue split.
        dark_amt = min(dark.available_in, intent.amount_in)
        venue_amt = intent.amount_in - dark_amt
        if venue_amt <= 0:
            # Dark can fill the whole intent: compare directly.
            dark_out = self._quote(intent.amount_in, dark.rate_num, dark.rate_den)
            if dark_out >= intent.min_out and dark_out > best_out:
                return RoutePlan(
                    intent_id=intent.intent_id,
                    legs=[Leg("dark-pool", intent.amount_in, dark_out,
                              intent.min_out)],
                    total_expected_out=dark_out,
                    split=False,
                )
            return best_single
        dark_out = self._quote(dark_amt, dark.rate_num, dark.rate_den)
        best_v2, venue_out = self._best_venue(venue_amt, venues)
        dark_min = intent.min_out * dark_amt // intent.amount_in
        venue_min = intent.min_out - dark_min  # pro-rata remainder
        if dark_out < dark_min or venue_out < venue_min:
            return best_single  # a leg violates min_out: no split
        total = dark_out + venue_out
        if total <= best_out:
            return best_single  # split does not improve: no split
        return RoutePlan(
            intent_id=intent.intent_id,
            legs=[
                Leg("dark-pool", dark_amt, dark_out, dark_min),
                Leg(best_v2.name, venue_amt, venue_out, venue_min),
            ],
            total_expected_out=total,
            split=True,
        )
