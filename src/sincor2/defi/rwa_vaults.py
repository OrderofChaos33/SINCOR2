"""
SINCOR DeFi P08 — RWA Tokenization Vaults (Python reference model).

ERC-4626-style vault math + compliance-gate state machine, pure Python.
No network calls, no keys, no chain access. The Solidity contracts
(onchain/src/p08/) are the enforcement layer; this module is the
off-chain reference the gate evidence and tests are built against.

Money is integer cents everywhere — share math is exact, no float drift.
Time is an explicit parameter (no time.time() in core logic) so tests are
deterministic.

Load-bearing invariants (mirror the deep spec acceptance criteria):
- Yield accrues ONLY after the compliance gate passes AND promotion
  completes. Pre-gate accrued yield is exactly zero on every path.
- Deposits are always accepted; redemptions of principal are always
  permitted (even mid-gate, even after KYC revocation).
- KYC revocation freezes yield for that address immediately but never
  touches principal.
- 15 bps of every yield distribution routes to the canonical Treasury.
- NAV oracle reverts on stale data (> 36h); a > 5% single-update NAV drop
  pauses accrual until a multisig resume.
- First-deposit share inflation is neutralized by a dead-share floor
  (1,000 shares minted to the zero address at construction).
- Distributions are pull-pattern: one recipient's claim failure can never
  brick another recipient's claim.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .catalog import TREASURY

logger = logging.getLogger(__name__)

# -- locked numeric parameters (deep spec section 1) -------------------------
FEE_BPS = 15                      # 0.15% of every yield distribution -> Treasury
TARGET_APR = 0.08
MIN_CAPITAL_CENTS = 50_000        # $500 per tick
MAX_ALLOC_PCT = 0.10
ATTESTATION_MAX_AGE_S = 24 * 3600          # custodian attestation freshness
NAV_DEVIATION_TRIPWIRE = 0.02              # 2% vs last reported NAV
NAV_STALENESS_LIMIT_S = 36 * 3600          # oracle hard-reverts beyond this
REDEMPTION_DELAY_S = 2 * 86400             # T+2 business days (modelled)
WRITEDOWN_CIRCUIT_PCT = 0.05               # >5% single-update drop -> pause
DEAD_SHARES = 1000                # first-deposit inflation defense
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class GateClosedError(RuntimeError):
    """Raised by any yield-bearing path while the compliance gate is closed."""


class StaleOracleError(RuntimeError):
    """Raised when the NAV oracle data is older than the staleness limit."""


class AccrualPausedError(RuntimeError):
    """Raised when accrual is paused (write-down circuit breaker)."""


class KYCRevokedError(RuntimeError):
    """Raised when a KYC-revoked address attempts a yield claim."""


class LiveBlockedError(RuntimeError):
    """Raised when a live-only path is attempted while the module is gated."""


# -- KYC registry ------------------------------------------------------------
class KYCRegistry:
    """Per-address eligibility flags with revocation + audit events."""

    def __init__(self) -> None:
        self._flags: Dict[str, bool] = {}
        self._events: List[Dict[str, object]] = []

    def attest(self, address: str, eligible: bool = True) -> None:
        self._flags[address.lower()] = eligible
        self._events.append({"type": "attest", "address": address.lower(),
                             "eligible": eligible})

    def revoke(self, address: str) -> None:
        self.attest(address, False)
        self._events.append({"type": "revoke", "address": address.lower()})

    def is_eligible(self, address: str) -> bool:
        return self._flags.get(address.lower(), False)

    def events(self) -> List[Dict[str, object]]:
        return list(self._events)


# -- NAV oracle --------------------------------------------------------------
@dataclass
class NAVReport:
    nav_cents: int
    reported_at: int  # unix seconds
    attested: bool = True


class NAVOracle:
    """Push oracle with staleness hard-revert and write-down circuit breaker."""

    def __init__(self, initial_nav_cents: int, now: int) -> None:
        self._nav = NAVReport(nav_cents=initial_nav_cents, reported_at=now)
        self._paused = False
        self._history: List[NAVReport] = [self._nav]

    @property
    def nav_cents(self) -> int:
        return self._nav.nav_cents

    @property
    def paused(self) -> bool:
        return self._paused

    def update(self, nav_cents: int, reported_at: int, now: int) -> None:
        """Push a NAV update. Reverts on stale data; pauses on >5% drop."""
        if now - reported_at > NAV_STALENESS_LIMIT_S:
            raise StaleOracleError(
                f"NAV data {now - reported_at}s old; limit is {NAV_STALENESS_LIMIT_S}s")
        if nav_cents <= 0:
            raise ValueError("NAV must be positive")
        drop = (self._nav.nav_cents - nav_cents) / self._nav.nav_cents
        if drop > WRITEDOWN_CIRCUIT_PCT:
            self._paused = True
            logger.warning("NAV write-down circuit breaker tripped: %.2f%% drop",
                           drop * 100)
        self._nav = NAVReport(nav_cents=nav_cents, reported_at=reported_at)
        self._history.append(self._nav)

    def resume(self, multisig_approved: bool) -> None:
        """Resume accrual after a write-down pause. Requires multisig."""
        if not multisig_approved:
            raise LiveBlockedError("resume requires multisig approval")
        self._paused = False

    def deviation_vs(self, reference_cents: int) -> float:
        if reference_cents <= 0:
            return 1.0
        return abs(self._nav.nav_cents - reference_cents) / reference_cents


# -- compliance gate ---------------------------------------------------------
@dataclass(frozen=True)
class CompliancePack:
    depositor_addresses: Tuple[str, ...]
    custodian_attested_at: int
    nav_reference_cents: int
    sanctions_blocklist: Tuple[str, ...] = ()


@dataclass
class GateCheck:
    name: str
    ok: bool
    detail: str


class ComplianceGate:
    """The 4-check gate. All four must pass in one atomic evaluation."""

    def __init__(self, kyc: KYCRegistry, oracle: NAVOracle) -> None:
        self._kyc = kyc
        self._oracle = oracle
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def evaluate(self, pack: CompliancePack, now: int) -> List[GateCheck]:
        checks = [
            GateCheck(
                "all_depositors_kyc",
                all(self._kyc.is_eligible(a) for a in pack.depositor_addresses),
                f"{len(pack.depositor_addresses)} depositor(s) checked",
            ),
            GateCheck(
                "custodian_attestation_fresh",
                now - pack.custodian_attested_at <= ATTESTATION_MAX_AGE_S,
                f"attestation age {now - pack.custodian_attested_at}s",
            ),
            GateCheck(
                "nav_deviation_within_tripwire",
                self._oracle.deviation_vs(pack.nav_reference_cents) < NAV_DEVIATION_TRIPWIRE,
                f"deviation {self._oracle.deviation_vs(pack.nav_reference_cents):.4f}",
            ),
            GateCheck(
                "no_sanctioned_addresses",
                not any(a.lower() in {s.lower() for s in pack.sanctions_blocklist}
                        for a in pack.depositor_addresses),
                "sanctions screen",
            ),
        ]
        return checks

    def open(self, pack: CompliancePack, now: int) -> List[GateCheck]:
        """Atomically evaluate and open the gate iff all checks pass."""
        checks = self.evaluate(pack, now)
        if all(c.ok for c in checks):
            self._open = True
        return checks


# -- dry-run capital ledger --------------------------------------------------
@dataclass
class DryRunEntry:
    address: str
    assets_cents: int
    shares: int
    live: bool = False


class DryRunLedger:
    """Off-chain simulated book. Mirrors every deposit with zero live exposure."""

    def __init__(self) -> None:
        self._entries: List[DryRunEntry] = []
        self._promoted = False

    def record_deposit(self, address: str, assets_cents: int, shares: int) -> None:
        self._entries.append(DryRunEntry(address=address.lower(),
                                        assets_cents=assets_cents,
                                        shares=shares,
                                        live=self._promoted))

    def promote(self) -> bool:
        """Atomically mark all capital live. Idempotent: re-call is a no-op."""
        if self._promoted:
            return False
        for e in self._entries:
            e.live = True
        self._promoted = True
        return True

    @property
    def promoted(self) -> bool:
        return self._promoted

    def totals(self) -> Tuple[int, int]:
        assets = sum(e.assets_cents for e in self._entries)
        shares = sum(e.shares for e in self._entries)
        return assets, shares


# -- vault -------------------------------------------------------------------
@dataclass
class PendingWithdrawal:
    address: str
    shares: int
    principal_cents: int
    claimable_yield_cents: int
    due_at: int
    settled: bool = False


@dataclass
class Distribution:
    total_cents: int
    fee_cents: int
    net_cents: int
    fee_to: str = TREASURY


class RWAVault:
    """
    ERC-4626-style vault with a compliance gate in front of every
    yield-bearing path. Deposits/redemptions always work; yield accrues
    only post-gate AND post-promotion.
    """

    def __init__(self, oracle: NAVOracle, kyc: KYCRegistry, gate: ComplianceGate,
                 dry_run: Optional[DryRunLedger] = None) -> None:
        self.oracle = oracle
        self.kyc = kyc
        self.gate = gate
        self.dry_run = dry_run or DryRunLedger()
        self.total_assets_cents = 0
        self.total_supply = DEAD_SHARES  # dead-share floor vs inflation attack
        self._balances: Dict[str, int] = {ZERO_ADDRESS: DEAD_SHARES}
        self._accrued_yield: Dict[str, int] = {}
        self._frozen_yield: Dict[str, int] = {}
        self._pending: List[PendingWithdrawal] = []
        self._distributions: List[Distribution] = []
        self._promoted = False

    # -- share math ------------------------------------------------------
    def _mint(self, address: str, shares: int) -> None:
        a = address.lower()
        self._balances[a] = self._balances.get(a, 0) + shares
        self.total_supply += shares

    def _burn(self, address: str, shares: int) -> None:
        a = address.lower()
        if self._balances.get(a, 0) < shares:
            raise ValueError("insufficient shares")
        self._balances[a] -= shares
        self.total_supply -= shares

    def convert_to_shares(self, assets_cents: int) -> int:
        if self.total_assets_cents == 0:
            return assets_cents  # 1:1 on first deposit (dead floor dilutes attacker)
        return assets_cents * self.total_supply // self.total_assets_cents

    def convert_to_assets(self, shares: int) -> int:
        if self.total_supply == 0:
            return shares
        return shares * self.total_assets_cents // self.total_supply

    def deposit(self, address: str, assets_cents: int) -> int:
        """Deposits are ALWAYS accepted, regardless of gate state."""
        if assets_cents <= 0:
            raise ValueError("deposit must be positive")
        shares = self.convert_to_shares(assets_cents)
        self._mint(address, shares)
        self.total_assets_cents += assets_cents
        self.dry_run.record_deposit(address, assets_cents, shares)
        return shares

    # -- promotion ---------------------------------------------------------
    def promote_to_live(self, pack: CompliancePack, now: int) -> List[GateCheck]:
        """Gate evaluation + atomic promotion. Idempotent no-op when live."""
        if self._promoted:
            return [GateCheck("already_live", True, "promotion is a no-op")]
        checks = self.gate.open(pack, now)
        if all(c.ok for c in checks):
            self.dry_run.promote()
            self._promoted = True
        return checks

    @property
    def is_live(self) -> bool:
        return self._promoted and self.gate.is_open

    def _require_yield_path(self) -> None:
        if not self.is_live:
            raise GateClosedError("yield-bearing path requires open gate + promotion")
        if self.oracle.paused:
            raise AccrualPausedError("accrual paused by write-down circuit breaker")

    # -- yield ---------------------------------------------------------------
    def accrue_yield(self, nav_drift_cents: int) -> Distribution:
        """
        Accrue NAV drift as yield. Reverts unless gate open + promoted.
        15 bps of every distribution is swept to Treasury.
        """
        self._require_yield_path()
        if nav_drift_cents <= 0:
            raise ValueError("accrue_yield expects positive drift; "
                             "write-downs flow through the NAV oracle")
        fee_cents = nav_drift_cents * FEE_BPS // 10_000
        net_cents = nav_drift_cents - fee_cents
        dist = Distribution(total_cents=nav_drift_cents, fee_cents=fee_cents,
                            net_cents=net_cents)
        self._distributions.append(dist)
        self.total_assets_cents += net_cents
        # distribute pro-rata over live (non-dead, KYC-eligible) shares
        eligible_supply = self.total_supply - DEAD_SHARES
        if eligible_supply > 0:
            for addr, bal in self._balances.items():
                if addr == ZERO_ADDRESS or bal == 0:
                    continue
                share_yield = net_cents * bal // eligible_supply
                if self.kyc.is_eligible(addr):
                    self._accrued_yield[addr] = self._accrued_yield.get(addr, 0) + share_yield
                else:
                    # revoked mid-accrual: yield frozen, never seized
                    self._frozen_yield[addr] = self._frozen_yield.get(addr, 0) + share_yield
        return dist

    def claim_yield(self, address: str) -> int:
        """
        Pull-pattern yield claim. One address's claim can never brick
        another's: claims are strictly per-address ledger entries.
        """
        self._require_yield_path()
        a = address.lower()
        if not self.kyc.is_eligible(a):
            # Freeze any accrued-but-unclaimed yield at the enforcement point:
            # segregated into the frozen bucket, never seized. Re-attestation
            # does not auto-release (manual review process, out of scope).
            pending = self._accrued_yield.get(a, 0)
            if pending:
                self._frozen_yield[a] = self._frozen_yield.get(a, 0) + pending
                self._accrued_yield[a] = 0
            raise KYCRevokedError(f"yield frozen for revoked address {a}")
        amount = self._accrued_yield.get(a, 0)
        self._accrued_yield[a] = 0
        self.total_assets_cents -= amount
        return amount

    def frozen_yield_of(self, address: str) -> int:
        return self._frozen_yield.get(address.lower(), 0)

    # -- redemption (principal always redeemable) ------------------------------
    def request_withdraw(self, address: str, shares: int, now: int) -> PendingWithdrawal:
        """Redemption requests are always permitted — even mid-gate."""
        a = address.lower()
        if self._balances.get(a, 0) < shares or shares <= 0:
            raise ValueError("invalid share amount")
        principal = self.convert_to_assets(shares)
        self._burn(a, shares)
        # yield claim stays separate: only unclaimed *eligible* yield attaches
        pw = PendingWithdrawal(address=a, shares=shares,
                               principal_cents=principal,
                               claimable_yield_cents=0,
                               due_at=now + REDEMPTION_DELAY_S)
        self._pending.append(pw)
        self.total_assets_cents -= principal
        return pw

    def settle_withdrawal(self, pw: PendingWithdrawal, now: int) -> int:
        """T+2 settlement. Compliance re-check blocks yield only, never principal."""
        if pw.settled:
            raise ValueError("already settled")
        if now < pw.due_at:
            raise ValueError("settlement delay not elapsed (T+2)")
        pw.settled = True
        return pw.principal_cents

    # -- accounting views ------------------------------------------------------
    def balance_of(self, address: str) -> int:
        return self._balances.get(address.lower(), 0)

    def accrued_yield_of(self, address: str) -> int:
        return self._accrued_yield.get(address.lower(), 0)

    def distributions(self) -> List[Distribution]:
        return list(self._distributions)

    def treasury_fees_cents(self) -> int:
        return sum(d.fee_cents for d in self._distributions)


# -- shared price-oracle wiring ------------------------------------------------
# Declares this product's external price needs against the shared oracle
# (sincor2.defi.price_oracle). Reference-backed until live feeds are wired;
# never treated as a live integration.

PRICE_ASSETS = ['USDC/USD']


def price_feed_for(oracle):
    """Bind the shared price oracle to this product's declared assets.

    Returns a ProductPriceFeed; ``feed.price(asset, now)`` raises on any
    oracle failure (fail-closed). Live Chainlink/Pyth feeds are NOT wired —
    production must inject real adapters (see price_oracle module docs).
    """
    from .price_oracle import wiring_for
    return wiring_for("P08_RWA", oracle)
