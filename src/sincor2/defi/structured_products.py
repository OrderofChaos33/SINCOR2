"""SINCOR DeFi P18 — Structured Product Vaults (reference build).

Python reference simulation of the principal-protected structured
product vault behind SKU ``SINCOR-DEFI-P18-STRUCTURED``:

- :class:`StructuredVault` — deposit splits into a principal-protection
  sleeve (modeled zero-coupon position sized so PT redemption always
  meets the principal floor at maturity) and a yield sleeve routed to
  an allowlisted yield source. The yield sleeve funds the upside hedge:
  at maturity it delivers the capped participation payoff; the floor
  itself never depends on the hedge.
- :class:`TrancheLedger` — PT (principal token) and YT (yield token)
  minted 1:1 per wei deposited, only by the vault; PT+YT supply always
  conserved with zero drift.
- :func:`payoff` — cap-rate payoff calculator: ``floor + participation
  * min(return, cap)``; integer-exact, rounding always favors the vault
  (rounds down); any computed payout above the cap is clamped and the
  clamp is recorded as a ``CapClamped`` event.
- :meth:`StructuredVault.settle` — permissionless maturity settlement:
  finalizes the underlying return from a staleness-guarded price feed
  (stale oracle reverts, never settles wrong); PT redeems protected
  principal plus capped upside; YT claims residual yield.
- 12 bps fee on deposits and on yield-sleeve harvests, pull-based, to
  the canonical treasury (immutable post-import; verified against the
  catalog at import time).
- Roles: product lister, yield-source allowlister, pauser; cap/floor
  parameter updates behind a 48h timelock; emergency early maturity;
  yield-source failures are non-bricking (a recorded source failure
  forfeits only the modeled upside — PT redemption at the floor never
  depends on the yield adapter).

Safety rules (hard):
- Default mode is DRY_RUN. Intents are emitted, never executed; nothing
  here touches a chain, a pool, or funds.
- The principal floor is unconditional: even a total yield-source
  failure leaves PT redeemable at the floor. Only the upside is
  subject to hedge performance.
- Money math is integer-exact (wei); rates in bps.

This is a REFERENCE build for design validation and agent simulation —
not a deployed protocol.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TREASURY = os.getenv(
    "TREASURY_ADDRESS", "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"
)
from .catalog import TREASURY as _CATALOG_TREASURY  # noqa: E402

if TREASURY.lower() != _CATALOG_TREASURY.lower():  # pragma: no cover
    raise RuntimeError("P18 treasury address mismatches the catalog constant")

FEE_BPS = 12                       # 0.12% on deposits and harvests
TIMELOCK_SECONDS = 48 * 3600       # cap/floor param updates
ORACLE_STALENESS_SECONDS = 3600    # return measurement freshness
MIN_CAPITAL_WEI = 250 * 10**18     # catalog min_capital_usd (reference)
MAX_ALLOC_PCT = 0.15               # catalog max_alloc_pct per tick
DRY_RUN = os.getenv("P18_DRY_RUN", "1").strip() != "0"

# Reference worked example (canonical — asserted exactly by the tests):
# deposit 1_000 units, floor 100%, cap 20%, participation 100%,
# measured underlying return 35% -> payout = 1_000 * (1.00 + 0.20) = 1_200.
EXAMPLE = {
    "deposit_wei": 1_000 * 10**18,
    "floor_bps": 10_000,
    "cap_bps": 2_000,
    "participation_bps": 10_000,
    "underlying_return_bps": 3_500,
    "expected_payout_wei": 1_200 * 10**18,
}


class StructuredError(RuntimeError):
    """Base error for structured-product rule violations."""


class FloorBreach(StructuredError):
    """PT redemption would fall below the principal floor."""


class CapBreach(StructuredError):
    """Payout exceeds the cap rate (should be clamped, never paid)."""


class StaleOracle(StructuredError):
    """Return-measurement feed too old — settlement reverts."""


class Unauthorized(StructuredError):
    """Caller lacks the required role."""


class TimelockPending(StructuredError):
    """Parameter update still inside the 48h timelock."""


class YieldSourceNotAllowlisted(StructuredError):
    """Yield source outside the allowlist."""


# -- payoff calculator ---------------------------------------------------------------
def payoff(deposit_wei: int, floor_bps: int, participation_bps: int,
           cap_bps: int, underlying_return_bps: int) -> Tuple[int, bool]:
    """PT payout for a measured underlying return.

    payout = deposit * (floor + participation * min(return, cap)).
    Integer-exact; rounds DOWN (favors the vault). Returns
    (payout_wei, clamped) where clamped is True when the cap bound.
    """
    if deposit_wei <= 0:
        raise ValueError("deposit must be positive")
    for name, v in (("floor_bps", floor_bps),
                    ("participation_bps", participation_bps),
                    ("cap_bps", cap_bps)):
        if v < 0:
            raise ValueError(f"{name} must be non-negative")
    if participation_bps > 10_000:
        raise ValueError("participation cannot exceed 100%")
    eff_return_bps = min(max(underlying_return_bps, 0), cap_bps)
    clamped = underlying_return_bps > cap_bps
    upside = deposit_wei * participation_bps * eff_return_bps // 10_000 // 10_000
    floor = deposit_wei * floor_bps // 10_000
    return floor + upside, clamped


def discount_factor_bps(discount_rate_bps: int, years: float) -> int:
    """Discount factor 1/(1+r)^T in bps, integer math."""
    if discount_rate_bps < 0 or years <= 0:
        raise ValueError("rate must be >= 0 and tenor positive")
    factor = 1.0 / ((1.0 + discount_rate_bps / 10_000) ** years)
    return int(factor * 10_000)


# -- price feed ----------------------------------------------------------------------
@dataclass
class PriceFeed:
    """Staleness-guarded underlying price feed (reference)."""

    price: float = 1.0
    updated_at: float = field(default_factory=time.time)

    def read(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.time()
        if now - self.updated_at > ORACLE_STALENESS_SECONDS:
            raise StaleOracle("underlying price feed is stale")
        return self.price


# -- tranche ledger ------------------------------------------------------------------
@dataclass
class TrancheLedger:
    """PT/YT accounting. Only the vault mints; supply always conserved."""

    pt_balances: Dict[str, int] = field(default_factory=dict)
    yt_balances: Dict[str, int] = field(default_factory=dict)
    pt_supply: int = 0
    yt_supply: int = 0
    pt_minted: int = 0           # cumulative; never decreases
    yt_minted: int = 0           # cumulative; never decreases
    total_deposits: int = 0

    def mint(self, holder: str, amount_wei: int) -> None:
        if amount_wei <= 0:
            raise ValueError("mint amount must be positive")
        self.pt_balances[holder] = self.pt_balances.get(holder, 0) + amount_wei
        self.yt_balances[holder] = self.yt_balances.get(holder, 0) + amount_wei
        self.pt_supply += amount_wei
        self.yt_supply += amount_wei
        self.pt_minted += amount_wei
        self.yt_minted += amount_wei
        self.total_deposits += amount_wei

    def burn_pt(self, holder: str, amount_wei: int) -> None:
        bal = self.pt_balances.get(holder, 0)
        if amount_wei > bal:
            raise StructuredError("PT burn exceeds balance")
        self.pt_balances[holder] = bal - amount_wei
        self.pt_supply -= amount_wei

    def burn_yt(self, holder: str, amount_wei: int) -> None:
        bal = self.yt_balances.get(holder, 0)
        if amount_wei > bal:
            raise StructuredError("YT burn exceeds balance")
        self.yt_balances[holder] = bal - amount_wei
        self.yt_supply -= amount_wei

    def check_conservation(self) -> None:
        # Minted in exact lockstep with deposits (zero drift); live
        # supplies only ever shrink via redemption/claim.
        assert self.pt_minted == self.yt_minted == self.total_deposits, \
            "PT/YT mint drift"
        assert 0 <= self.pt_supply <= self.pt_minted, "PT supply out of range"
        assert 0 <= self.yt_supply <= self.yt_minted, "YT supply out of range"


# -- product listing -----------------------------------------------------------------
@dataclass(frozen=True)
class ProductTerms:
    product_id: str
    floor_bps: int            # principal floor, bps of deposit
    cap_bps: int              # cap rate on the underlying return
    participation_bps: int    # participation in the capped return
    discount_rate_bps: int    # protection-sleeve discount rate
    sleeve_rate_bps: int      # modeled yield-sleeve rate
    tenor_years: float
    maturity_ts: float
    listed_by: str


# -- the vault -----------------------------------------------------------------------
@dataclass
class StructuredVault:
    """Principal-protected + yield-sleeve vault (reference)."""

    lister: str
    allowlister: str
    pauser: str
    feed: PriceFeed = field(default_factory=PriceFeed)
    ledger: TrancheLedger = field(default_factory=TrancheLedger)
    products: Dict[str, ProductTerms] = field(default_factory=dict)
    # per-product state
    protection_alloc: Dict[str, int] = field(default_factory=dict)
    yield_alloc: Dict[str, int] = field(default_factory=dict)
    harvests: Dict[str, int] = field(default_factory=dict)
    yield_sources: Dict[str, List[str]] = field(default_factory=dict)
    hedge_failed: Dict[str, bool] = field(default_factory=dict)
    settled: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    pending_params: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fee_owed_wei: int = 0
    paused: bool = False
    events: List[Dict[str, Any]] = field(default_factory=list)

    def _event(self, kind: str, **kw: Any) -> None:
        self.events.append({"kind": kind, "ts": time.time(), **kw})

    def _require(self, role: str, caller: str) -> None:
        if caller != role:
            raise Unauthorized(f"caller {caller} lacks role")

    # -- roles ------------------------------------------------------------------
    def list_product(self, terms: ProductTerms, caller: str,
                     now: Optional[float] = None) -> ProductTerms:
        self._require(self.lister, caller)
        if terms.product_id in self.products:
            raise StructuredError("product already listed")
        if terms.maturity_ts <= (now or time.time()):
            raise StructuredError("maturity must be in the future")
        if terms.floor_bps > 10_000:
            raise StructuredError("floor cannot exceed 100%")
        if terms.participation_bps > 10_000:
            raise StructuredError("participation cannot exceed 100%")
        if terms.cap_bps <= 0:
            raise StructuredError("cap rate must be positive")
        if terms.tenor_years <= 0:
            raise StructuredError("tenor must be positive")
        self.products[terms.product_id] = terms
        self.yield_sources[terms.product_id] = []
        self.harvests[terms.product_id] = 0
        self.hedge_failed[terms.product_id] = False
        self._event("product_listed", product=terms.product_id,
                    floor_bps=terms.floor_bps, cap_bps=terms.cap_bps)
        return terms

    def allow_yield_source(self, product_id: str, source_id: str,
                           caller: str) -> None:
        self._require(self.allowlister, caller)
        if product_id not in self.products:
            raise StructuredError("unknown product")
        self.yield_sources[product_id].append(source_id)
        self._event("yield_source_allowed", product=product_id,
                    source=source_id)

    def pause(self, caller: str) -> None:
        self._require(self.pauser, caller)
        self.paused = True
        self._event("paused")

    # -- deposits -----------------------------------------------------------------
    def deposit(self, product_id: str, holder: str, amount_wei: int,
                discount_rate_bps: Optional[int] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
        """Split a deposit into protection + yield sleeves; mint PT/YT."""
        if self.paused:
            raise StructuredError("vault paused")
        terms = self.products.get(product_id)
        if terms is None:
            raise StructuredError("unknown product")
        if amount_wei <= 0:
            raise ValueError("deposit must be positive")
        rate_bps = (discount_rate_bps if discount_rate_bps is not None
                    else terms.discount_rate_bps)
        fee = amount_wei * FEE_BPS // 10_000
        self.fee_owed_wei += fee
        net = amount_wei - fee
        floor_wei = amount_wei * terms.floor_bps // 10_000
        df = discount_factor_bps(rate_bps, terms.tenor_years)
        prot = floor_wei * df // 10_000
        yld = net - prot
        if yld < 0:
            raise StructuredError("fee exceeds yield-sleeve budget")
        self.protection_alloc[product_id] = \
            self.protection_alloc.get(product_id, 0) + prot
        self.yield_alloc[product_id] = \
            self.yield_alloc.get(product_id, 0) + yld
        self.ledger.mint(holder, amount_wei)
        self.ledger.check_conservation()
        self._event("deposit", product=product_id, holder=holder,
                    amount=amount_wei, fee=fee, protection=prot,
                    yield_sleeve=yld)
        return {"pt": amount_wei, "yt": amount_wei, "fee_wei": fee,
                "protection_wei": prot, "yield_wei": yld}

    # -- yield sleeve --------------------------------------------------------------
    def harvest(self, product_id: str, source_id: str, amount_wei: int,
                *, source_ok: bool = True) -> Dict[str, Any]:
        """Collect a yield-sleeve harvest. A failing source is non-bricking:
        the failure is recorded and forfeits only the modeled upside —
        PT redemption at the principal floor is unaffected."""
        if product_id not in self.products:
            raise StructuredError("unknown product")
        if source_id not in self.yield_sources.get(product_id, []):
            raise YieldSourceNotAllowlisted(source_id)
        if not source_ok:
            # Non-bricking: record the failure, change nothing else.
            self.hedge_failed[product_id] = True
            self._event("yield_source_failed", product=product_id,
                        source=source_id)
            return {"harvested_wei": 0, "failed": True}
        if amount_wei < 0:
            raise ValueError("harvest cannot be negative")
        fee = amount_wei * FEE_BPS // 10_000
        self.fee_owed_wei += fee
        net = amount_wei - fee
        self.harvests[product_id] = self.harvests.get(product_id, 0) + net
        self._event("harvest", product=product_id, source=source_id,
                    amount=amount_wei, fee=fee)
        return {"harvested_wei": net, "fee_wei": fee, "failed": False}

    def claim_fees(self) -> Dict[str, Any]:
        amount = self.fee_owed_wei
        self.fee_owed_wei = 0
        self._event("fee_routed", treasury=TREASURY, amount=amount,
                    fee_bps=FEE_BPS)
        return {"treasury": TREASURY, "amount_wei": amount,
                "fee_bps": FEE_BPS}

    # -- parameter timelock ---------------------------------------------------------
    def propose_params(self, product_id: str, *, cap_bps: Optional[int] = None,
                       floor_bps: Optional[int] = None, caller: str = "",
                       now: Optional[float] = None) -> None:
        self._require(self.lister, caller or self.lister)
        self.pending_params[product_id] = {
            "cap_bps": cap_bps, "floor_bps": floor_bps,
            "executable_at": (now or time.time()) + TIMELOCK_SECONDS,
        }
        self._event("params_proposed", product=product_id,
                    cap_bps=cap_bps, floor_bps=floor_bps)

    def execute_params(self, product_id: str, caller: str = "",
                       now: Optional[float] = None) -> None:
        self._require(self.lister, caller or self.lister)
        pending = self.pending_params.get(product_id)
        if not pending:
            raise StructuredError("no pending params")
        if (now or time.time()) < pending["executable_at"]:
            raise TimelockPending("48h timelock not elapsed")
        terms = self.products[product_id]
        self.products[product_id] = ProductTerms(
            **{**asdict(terms),
               "cap_bps": pending["cap_bps"] if pending["cap_bps"] is not None
               else terms.cap_bps,
               "floor_bps": pending["floor_bps"]
               if pending["floor_bps"] is not None else terms.floor_bps})
        del self.pending_params[product_id]
        self._event("params_executed", product=product_id)

    # -- settlement -------------------------------------------------------------------
    def settle(self, product_id: str, initial_price: float,
               now: Optional[float] = None,
               emergency: bool = False) -> Dict[str, Any]:
        """Permissionless maturity settlement.

        Finalizes the measured underlying return, pays PT holders
        (floor + capped upside) and leaves the residual for YT claims.
        A stale oracle reverts — it never settles wrong. Reentrancy is
        modeled: a second settle on the same product reverts.
        """
        terms = self.products.get(product_id)
        if terms is None:
            raise StructuredError("unknown product")
        if product_id in self.settled:
            raise StructuredError("already settled (reentrancy guard)")
        now = now or time.time()
        if not emergency and now < terms.maturity_ts:
            raise StructuredError("not yet mature")
        final_price = self.feed.read(now)  # raises StaleOracle when stale
        if initial_price <= 0:
            raise ValueError("initial price must be positive")
        measured_return_bps = int((final_price / initial_price - 1.0) * 10_000)

        deposits = self.ledger.total_deposits
        full_payout_per_wei, clamped = payoff(
            10**18, terms.floor_bps, terms.participation_bps,
            terms.cap_bps, measured_return_bps)
        floor_per_wei = 10**18 * terms.floor_bps // 10_000
        upside_per_wei = full_payout_per_wei - floor_per_wei
        if clamped:
            self._event("CapClamped", product=product_id,
                        measured_return_bps=measured_return_bps,
                        cap_bps=terms.cap_bps)
        # The upside is subject to hedge performance: a recorded
        # yield-source failure forfeits it, never the floor.
        hedge_ok = not self.hedge_failed.get(product_id, False)
        pt_per_wei = floor_per_wei + (upside_per_wei if hedge_ok else 0)
        pt_payout_total = deposits * pt_per_wei // 10**18
        floor_total = deposits * terms.floor_bps // 10_000
        if pt_payout_total < floor_total:
            raise FloorBreach("PT payout below principal floor")
        # Assets at maturity: the protection sleeve redeems at face
        # (the floor), the hedge delivers the upside, harvests add yield.
        upside_total = deposits * upside_per_wei // 10**18 if hedge_ok else 0
        total_assets = (floor_total + upside_total
                        + self.harvests.get(product_id, 0))
        if pt_payout_total > total_assets:
            raise StructuredError("PT payout exceeds vault assets")
        residual = total_assets - pt_payout_total
        self.settled[product_id] = {
            "measured_return_bps": measured_return_bps,
            "pt_payout_per_wei": pt_per_wei,
            "pt_payout_total": pt_payout_total,
            "yt_residual_total": residual,
            "clamped": clamped,
            "hedge_ok": hedge_ok,
            "settled_at": now,
        }
        self._event("settled", product=product_id,
                    measured_return_bps=measured_return_bps,
                    pt_payout_total=pt_payout_total, residual=residual)
        return dict(self.settled[product_id])

    def redeem_pt(self, product_id: str, holder: str,
                  amount_wei: int) -> int:
        """PT redemption at the settled payout. Floor enforced."""
        s = self.settled.get(product_id)
        if s is None:
            raise StructuredError("product not settled")
        terms = self.products[product_id]
        self.ledger.burn_pt(holder, amount_wei)
        payout = amount_wei * s["pt_payout_per_wei"] // 10**18
        floor = amount_wei * terms.floor_bps // 10_000
        if payout < floor:
            raise FloorBreach("redemption below principal floor")
        self._event("pt_redeemed", product=product_id, holder=holder,
                    pt=amount_wei, payout=payout)
        return payout

    def claim_yt(self, product_id: str, holder: str,
                 amount_wei: int) -> int:
        """YT claim on the residual yield, pro-rata."""
        s = self.settled.get(product_id)
        if s is None:
            raise StructuredError("product not settled")
        yt_supply = self.ledger.yt_supply
        if yt_supply <= 0:
            raise StructuredError("no YT supply")
        self.ledger.burn_yt(holder, amount_wei)
        claim = s["yt_residual_total"] * amount_wei // yt_supply
        self._event("yt_claimed", product=product_id, holder=holder,
                    yt=amount_wei, claim=claim)
        return claim


# -- monitoring ----------------------------------------------------------------------
def metrics_payload(vault: StructuredVault, product_id: str,
                    *, data_fresh: bool = True) -> Dict[str, Any]:
    """Dashboard metrics. Stale data reports 'unknown', never healthy."""
    terms = vault.products.get(product_id)
    if terms is None or not data_fresh:
        return {"product": "SINCOR-DEFI-P18-STRUCTURED",
                "product_id": product_id, "status": "unknown",
                "ts": time.time()}
    deposits = vault.ledger.total_deposits
    floor_total = deposits * terms.floor_bps // 10_000
    protection = vault.protection_alloc.get(product_id, 0)
    coverage = (protection + floor_total) / floor_total if floor_total else 0.0
    return {
        "product": "SINCOR-DEFI-P18-STRUCTURED",
        "product_id": product_id,
        "status": "ok",
        "ts": time.time(),
        "tvl_wei": deposits,
        "pt_coverage_ratio": coverage,
        "realized_yield_wei": vault.harvests.get(product_id, 0),
        "treasury_fee_owed_wei": vault.fee_owed_wei,
    }
