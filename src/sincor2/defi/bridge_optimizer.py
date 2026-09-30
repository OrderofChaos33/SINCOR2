"""
SINCOR DeFi P07 — Cross-Chain Bridge Optimizer (reference build).

Python reference simulation of the async cross-L2 routing engine behind
SKU ``SINCOR-DEFI-P07-BRIDGE`` ("A-SINC" design: agents monitor localized
crunches across isolated L2 rollups, fulfill via private local pools, and
settle trustlessly later via storage proofs):

- :class:`BridgeWhitelist` — allowlisted bridges ONLY (gate
  ``bridge_whitelist``). Any route touching a non-allowlisted bridge
  reverts; there is no arbitrary-bridge path.
- :class:`RouteScorer` — deterministic route scoring: net outcome =
  quoted out - bridge fee - slippage cost - latency penalty, with a
  security-score floor. Ties break deterministically (score desc,
  bridge_id asc) so scoring is reproducible.
- :class:`SlippageCap` — per-quote slippage cap (gate ``slippage_cap``):
  quotes above the cap are rejected before scoring, never routed.
- :class:`ProofVerifierRegistry` — storage-proof verifier allowlist.
  Proofs carry chain, block, state root, and expiry; unknown verifiers
  and expired proofs are rejected.
- :class:`AsyncSettlement` — fulfill-now / settle-later: a routed intent
  locks the commitment, and settlement completes only against a valid
  storage proof. Recipients claim pull-based; a failed transfer never
  bricks the batch.
- :class:`BridgeOptimizer` — route planning with catalog risk caps
  (max 20% of tick capital per route, $100 minimum). 10 bps of routed
  volume routes to the canonical treasury. Default mode is DRY_RUN;
  live routing is blocked until the founder-signed release.

Safety rules (hard):
- Default mode is DRY_RUN. Route plans describe actions; ``executed``
  is always False; nothing here touches a chain, a bridge, or funds.
- Settlement assets move only through the async settlement ledger;
  every credit/debit conserves exactly (integer wei).
- Live routing is blocked until the live-block is released
  (live_blocked).

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
FEE_BPS = 10                                   # 10 bps of routed volume
MAX_ALLOC_PCT = 0.20                           # 20% of tick capital per route
MIN_CAPITAL_WEI = 100_000_000                  # $100 in 6dp stablecoin wei
SLIPPAGE_CAP_BPS = int(os.getenv("BRIDGE_SLIPPAGE_CAP_BPS", "50"))
SECURITY_FLOOR = 0.60                          # min verifier/bridge score
LATENCY_COST_WEI_PER_SEC = int(
    os.getenv("BRIDGE_LATENCY_COST_WEI_PER_SEC", "1000"))
PROOF_TTL_SECONDS = int(os.getenv("BRIDGE_PROOF_TTL_S", "3600"))
SETTLE_DEADLINE_SECONDS = int(os.getenv("BRIDGE_SETTLE_DEADLINE_S", "21600"))
DRY_RUN = os.getenv("BRIDGE_DRY_RUN", "1").strip() != "0"

GUARDIAN_ROLE = "GUARDIAN_ROLE"


class BridgeError(Exception):
    """Base error for bridge-optimizer rule violations."""


class BridgeNotAllowlistedError(BridgeError):
    """Route touches a bridge outside the allowlist: reverts."""


class SlippageCapError(BridgeError):
    """Quote slippage exceeds the cap: rejected, never routed."""


class SecurityFloorError(BridgeError):
    """Quote or proof below the security floor: rejected."""


class ProofError(BridgeError):
    """Unknown verifier, expired proof, or proof/state mismatch."""


class SettlementError(BridgeError):
    """Double-settle, missed deadline, or unknown route."""


class CapitalCapError(BridgeError):
    """Route violates min-capital or max-alloc risk caps."""


class LiveBlockedError(BridgeError):
    """Live routing attempted before the founder-signed release."""


class UnauthorizedError(BridgeError):
    """Caller lacks the required role."""


# -- quotes & allowlist -------------------------------------------------------
@dataclass(frozen=True)
class BridgeQuote:
    """One bridge's quote for moving amount_in_wei from_chain -> to_chain."""
    bridge_id: str
    from_chain: str
    to_chain: str
    asset: str
    amount_in_wei: int
    est_out_wei: int
    fee_wei: int
    slippage_bps: int          # quoted slippage, basis points
    eta_seconds: int
    security_score: float      # 0..1 assessed bridge security
    quote_ts: float = 0.0


@dataclass
class BridgeConfig:
    bridge_id: str
    max_slippage_bps: int = SLIPPAGE_CAP_BPS
    security_floor: float = SECURITY_FLOOR
    enabled: bool = True


class BridgeWhitelist:
    """Allowlisted bridges only. Non-allowlisted routes revert."""

    def __init__(self):
        self._bridges: Dict[str, BridgeConfig] = {}

    def add(self, config: BridgeConfig, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self._bridges[config.bridge_id] = config

    def remove(self, bridge_id: str, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self._bridges.pop(bridge_id, None)

    def require(self, bridge_id: str) -> BridgeConfig:
        cfg = self._bridges.get(bridge_id)
        if cfg is None or not cfg.enabled:
            raise BridgeNotAllowlistedError(
                f"bridge {bridge_id} is not allowlisted")
        return cfg

    def __contains__(self, bridge_id: str) -> bool:
        cfg = self._bridges.get(bridge_id)
        return cfg is not None and cfg.enabled

    def __len__(self) -> int:
        return sum(1 for c in self._bridges.values() if c.enabled)


# -- scoring ------------------------------------------------------------------
@dataclass
class ScoredRoute:
    quote: BridgeQuote
    net_out_wei: int           # est_out - fee - slippage_cost - latency_penalty
    slippage_cost_wei: int
    latency_penalty_wei: int
    rank: int = 0


class RouteScorer:
    """Deterministic best-net-outcome scoring.

    net = est_out_wei - fee_wei - slippage_cost - latency_penalty, where
    slippage_cost = amount_in * slippage_bps / 10_000 (integer floor) and
    latency_penalty = eta_seconds * LATENCY_COST_WEI_PER_SEC. Quotes below
    the security floor are rejected; ties break on bridge_id ascending so
    scoring is reproducible for identical inputs.
    """

    def __init__(self, whitelist: BridgeWhitelist,
                 slippage_cap_bps: int = SLIPPAGE_CAP_BPS,
                 latency_cost_wei_per_sec: int = LATENCY_COST_WEI_PER_SEC):
        self.whitelist = whitelist
        self.slippage_cap_bps = slippage_cap_bps
        self.latency_cost = latency_cost_wei_per_sec

    @staticmethod
    def _slippage_cost(amount_in_wei: int, slippage_bps: int) -> int:
        return amount_in_wei * slippage_bps // 10_000

    def _validate(self, quote: BridgeQuote) -> BridgeConfig:
        cfg = self.whitelist.require(quote.bridge_id)  # allowlist gate
        cap = min(self.slippage_cap_bps, cfg.max_slippage_bps)
        if quote.slippage_bps > cap:
            raise SlippageCapError(
                f"quote slippage {quote.slippage_bps} bps exceeds cap "
                f"{cap} bps on {quote.bridge_id}")
        if quote.slippage_bps < 0:
            raise BridgeError("negative slippage quote")
        if quote.security_score < max(SECURITY_FLOOR, cfg.security_floor):
            raise SecurityFloorError(
                f"bridge {quote.bridge_id} score {quote.security_score} "
                "below security floor")
        if quote.est_out_wei <= 0 or quote.amount_in_wei <= 0:
            raise BridgeError("quote amounts must be positive")
        return cfg

    def score(self, quote: BridgeQuote) -> ScoredRoute:
        self._validate(quote)
        slip = self._slippage_cost(quote.amount_in_wei, quote.slippage_bps)
        latency = quote.eta_seconds * self.latency_cost
        net = quote.est_out_wei - quote.fee_wei - slip - latency
        return ScoredRoute(quote=quote, net_out_wei=net,
                           slippage_cost_wei=slip,
                           latency_penalty_wei=latency)

    def rank(self, quotes: List[BridgeQuote]) -> List[ScoredRoute]:
        """Score every quote; invalid quotes are dropped with a logged
        reason. Returns routes sorted best-first, deterministically."""
        scored: List[ScoredRoute] = []
        for q in quotes:
            try:
                scored.append(self.score(q))
            except BridgeError as exc:
                logger.info("quote rejected: %s (%s)", q.bridge_id, exc)
        scored.sort(key=lambda s: (-s.net_out_wei, s.quote.bridge_id))
        for i, s in enumerate(scored):
            s.rank = i + 1
        return scored


# -- storage proofs -----------------------------------------------------------
@dataclass(frozen=True)
class StorageProof:
    """Storage-proof attestation of destination-chain state."""
    proof_id: str
    chain: str
    block_number: int
    state_root: str
    commitment: str            # commitment this proof attests
    verifier_id: str
    expires_ts: float


class ProofVerifierRegistry:
    """Allowlisted proof verifiers (storage-proof interface).

    The interface is reserved for the future proof module; this reference
    ships the allowlist + expiry + commitment-match checks.
    """

    def __init__(self):
        self._verifiers: set = set()

    def add(self, verifier_id: str, caller_roles: List[str]) -> None:
        if GUARDIAN_ROLE not in caller_roles:
            raise UnauthorizedError("guardian only")
        self._verifiers.add(verifier_id)

    def verify(self, proof: StorageProof, expected_commitment: str,
               now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        if proof.verifier_id not in self._verifiers:
            raise ProofError(
                f"unknown proof verifier {proof.verifier_id}")
        if now > proof.expires_ts:
            raise ProofError(f"proof {proof.proof_id} expired")
        if proof.commitment != expected_commitment:
            raise ProofError(
                f"proof {proof.proof_id} does not attest this commitment")
        return True


# -- async settlement ----------------------------------------------------------
@dataclass
class RouteRecord:
    route_id: str
    quote: BridgeQuote
    net_out_wei: int
    treasury_fee_wei: int
    commitment: str
    fulfilled_ts: float
    settle_deadline_ts: float
    settled: bool = False


@dataclass
class RoutePlan:
    """Dry-run route plan. executed is always False from this module."""
    timestamp: float
    route_id: str
    quote: BridgeQuote
    scored: ScoredRoute
    treasury_fee_wei: int
    dry_run: bool = True
    executed: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["quote"] = asdict(self.quote)
        d["scored"] = {"net_out_wei": self.scored.net_out_wei,
                       "slippage_cost_wei": self.scored.slippage_cost_wei,
                       "latency_penalty_wei": self.scored.latency_penalty_wei,
                       "rank": self.scored.rank}
        return d


class AsyncSettlement:
    """Fulfill-now / settle-later ledger.

    fulfill() records the route commitment; settle() completes only
    against a valid storage proof from an allowlisted verifier. Recipient
    payouts are pull-based: a failed transfer parks funds for later
    withdrawal instead of bricking the batch.
    """

    def __init__(self, verifier_registry: ProofVerifierRegistry,
                 treasury: str = TREASURY,
                 reverting: Optional[set] = None):
        self.verifiers = verifier_registry
        self.treasury = treasury
        self._reverting = reverting or set()
        self._routes: Dict[str, RouteRecord] = {}
        self._balances: Dict[Tuple[str, str], int] = defaultdict(int)
        self._seq = 0
        self.treasury_collected_wei = 0

    @staticmethod
    def treasury_fee_wei(amount_in_wei: int) -> int:
        return amount_in_wei * FEE_BPS // 10_000

    @staticmethod
    def commitment_for(route_id: str, quote: BridgeQuote) -> str:
        body = "|".join([route_id, quote.bridge_id, quote.from_chain,
                         quote.to_chain, quote.asset,
                         str(quote.amount_in_wei), str(quote.est_out_wei)])
        return hashlib.sha256(body.encode()).hexdigest()

    def fulfill(self, scored: ScoredRoute,
                recipient: str,
                now: Optional[float] = None) -> RouteRecord:
        """Lock the route intent. Returns the pending settlement record."""
        now = time.time() if now is None else now
        self._seq += 1
        route_id = f"route-{self._seq}"
        fee = self.treasury_fee_wei(scored.quote.amount_in_wei)
        commitment = self.commitment_for(route_id, scored.quote)
        rec = RouteRecord(
            route_id=route_id,
            quote=scored.quote,
            net_out_wei=scored.net_out_wei,
            treasury_fee_wei=fee,
            commitment=commitment,
            fulfilled_ts=now,
            settle_deadline_ts=now + SETTLE_DEADLINE_SECONDS,
        )
        self._routes[route_id] = rec
        return rec

    def settle(self, route_id: str, proof: StorageProof, recipient: str,
               now: Optional[float] = None) -> Dict[str, Any]:
        """Complete settlement against a valid storage proof."""
        now = time.time() if now is None else now
        rec = self._routes.get(route_id)
        if rec is None:
            raise SettlementError(f"unknown route {route_id}")
        if rec.settled:
            return {"route_id": route_id, "status": "already-settled",
                    "settled_wei": 0}
        if now > rec.settle_deadline_ts:
            raise SettlementError(
                f"route {route_id} missed its settle deadline")
        self.verifiers.verify(proof, rec.commitment, now)
        payout = rec.net_out_wei - rec.treasury_fee_wei
        if payout < 0:
            raise SettlementError("negative net payout: quote uneconomic")
        # Conservation: net_out == payout + treasury_fee, exact.
        assert payout + rec.treasury_fee_wei == rec.net_out_wei
        self.treasury_collected_wei += rec.treasury_fee_wei
        key = (recipient, rec.quote.asset)
        if recipient in self._reverting:
            # non-bricking: park for pull; treasury fee still accounted
            self._balances[key] += payout
            settled = 0
        else:
            settled = payout
        rec.settled = True
        return {"route_id": route_id, "status": "settled",
                "settled_wei": settled,
                "parked_wei": payout - settled,
                "treasury_fee_wei": rec.treasury_fee_wei,
                "treasury": self.treasury}

    def claim(self, recipient: str, asset: str) -> int:
        """Pull-based claim of parked settlement funds."""
        key = (recipient, asset)
        amount = self._balances.get(key, 0)
        self._balances[key] = 0
        return amount

    def route_of(self, route_id: str) -> RouteRecord:
        return self._routes[route_id]


# -- live gate ------------------------------------------------------------------
class LiveGate:
    """Live routing stays blocked until the founder-signed release."""

    def __init__(self):
        self._released = False

    def release(self, founder_signature_marker: str) -> None:
        if not founder_signature_marker:
            raise BridgeError("release requires a founder signature marker")
        self._released = True

    def assert_live_allowed(self) -> None:
        if not self._released:
            raise LiveBlockedError(
                "live routing is blocked: founder release not presented "
                "(dry_run only)")


# -- optimizer --------------------------------------------------------------------
class BridgeOptimizer:
    """Route planner with catalog risk caps and treasury fee routing."""

    def __init__(self, whitelist: Optional[BridgeWhitelist] = None,
                 scorer: Optional[RouteScorer] = None,
                 settlement: Optional[AsyncSettlement] = None,
                 live_gate: Optional[LiveGate] = None,
                 treasury: str = TREASURY):
        self.whitelist = whitelist or BridgeWhitelist()
        self.scorer = scorer or RouteScorer(self.whitelist)
        self.settlement = settlement or AsyncSettlement(
            ProofVerifierRegistry(), treasury)
        self.live_gate = live_gate or LiveGate()
        self.treasury = treasury

    def plan_route(self, amount_in_wei: int, from_chain: str, to_chain: str,
                   asset: str, quotes: List[BridgeQuote],
                   tick_capital_wei: int) -> RoutePlan:
        """Plan one route. Pure selection; emits a dry-run plan only."""
        if amount_in_wei < MIN_CAPITAL_WEI:
            raise CapitalCapError(
                f"route ${amount_in_wei / 1e6:.2f} below $100 minimum")
        if tick_capital_wei > 0 and \
                amount_in_wei * 100 > tick_capital_wei * int(MAX_ALLOC_PCT * 100):
            raise CapitalCapError(
                "route exceeds 20% of tick capital")
        if from_chain == to_chain:
            raise BridgeError("no-op route: source == destination")
        relevant = [q for q in quotes
                    if q.from_chain == from_chain and q.to_chain == to_chain
                    and q.asset == asset
                    and q.amount_in_wei == amount_in_wei]
        if not relevant:
            raise BridgeError("no quotes for this corridor/amount")
        ranked = self.scorer.rank(relevant)
        if not ranked:
            raise BridgeError(
                "no routable quote: all rejected "
                "(allowlist / slippage cap / security floor)")
        best = ranked[0]
        fee = AsyncSettlement.treasury_fee_wei(amount_in_wei)
        return RoutePlan(
            timestamp=time.time(),
            route_id="",  # assigned at fulfill()
            quote=best.quote,
            scored=best,
            treasury_fee_wei=fee,
            details={
                "dry_run": DRY_RUN,
                "quotes_considered": len(relevant),
                "quotes_routable": len(ranked),
                "treasury": self.treasury,
            },
        )

    def fulfill_live(self, plan: RoutePlan, recipient: str) -> RouteRecord:
        """Live fulfill path: hard-blocked until the founder release."""
        self.live_gate.assert_live_allowed()
        return self.settlement.fulfill(plan.scored, recipient)


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
    return wiring_for("P07_BRIDGE", oracle)
