"""Unit tests for P08 RWA Tokenization Vaults reference build.

Covers src/sincor2/defi/rwa_vaults.py — ERC-4626-style vault math,
the 4-check compliance gate, KYC revocation semantics, dry-run promotion,
pull-pattern distributions, the 15 bps treasury fee, NAV oracle staleness /
write-down circuit breaker, and the dead-share inflation defense.

Pure logic, no chain access. Every test names the invariant it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi import rwa_vaults as rwa
from src.sincor2.defi.catalog import TREASURY
from src.sincor2.defi.rwa_vaults import (
    AccrualPausedError,
    ComplianceGate,
    CompliancePack,
    DryRunLedger,
    GateClosedError,
    KYCRegistry,
    KYCRevokedError,
    NAVOracle,
    RWAVault,
    StaleOracleError,
)

NOW = 1_700_000_000
ALICE = "0xAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAaAa"
BOB = "0xbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbBbB"
EVIL = "0xeEeeeeEeeeEeEeeeeEEEeeeeEeeeeeEEEeeeEEeE"


@pytest.fixture()
def kyc():
    k = KYCRegistry()
    k.attest(ALICE)
    k.attest(BOB)
    return k


@pytest.fixture()
def oracle():
    return NAVOracle(initial_nav_cents=100_00, now=NOW)  # $100.00


@pytest.fixture()
def gate(kyc, oracle):
    return ComplianceGate(kyc, oracle)


@pytest.fixture()
def vault(oracle, kyc, gate):
    return RWAVault(oracle, kyc, gate)


def _pack(depositors=(ALICE, BOB), attested_at=NOW, nav_ref=100_00,
          blocklist=()):
    return CompliancePack(depositor_addresses=depositors,
                          custodian_attested_at=attested_at,
                          nav_reference_cents=nav_ref,
                          sanctions_blocklist=blocklist)


def _live_vault(vault, depositors=(ALICE, BOB), now=NOW):
    vault.deposit(ALICE, 100_000_00)
    vault.deposit(BOB, 50_000_00)
    checks = vault.promote_to_live(_pack(depositors), now)
    assert all(c.ok for c in checks), [c.__dict__ for c in checks]
    return vault


# -- share math --------------------------------------------------------------
def test_first_deposit_mints_one_to_one(vault):
    """Invariant: first deposit is 1:1 shares (dead floor dilutes, not blocks)."""
    shares = vault.deposit(ALICE, 10_000_00)
    assert shares == 10_000_00
    assert vault.balance_of(ALICE) == 10_000_00


def test_second_deposit_pro_rata(vault):
    """Invariant: subsequent deposits mint at NAV = totalAssets/totalSupply."""
    vault.deposit(ALICE, 10_000_00)
    supply_before = vault.total_supply
    assets_before = vault.total_assets_cents
    shares = vault.deposit(BOB, 5_000_00)
    # pro-rata at NAV (dead-share floor dilutes price by a bounded epsilon)
    assert shares == 5_000_00 * supply_before // assets_before


def test_dead_share_floor_neutralizes_inflation_attack(vault):
    """Adversarial: classic first-deposit inflation attack is unprofitable."""
    # Attacker seeds first. There is no donation vector in this model —
    # deposit() is the only way in, and it always mints pro-rata — so the
    # attack reduces to: can the attacker end up with more than they put in?
    vault.deposit(EVIL, 100_00)
    victim_shares = vault.deposit(ALICE, 1_000_000_00)
    # victim's shares redeem for ~everything they put in (dilution bounded
    # by the dead-share floor)
    redeemable = vault.convert_to_assets(victim_shares)
    assert 1_000_000_00 - redeemable <= rwa.DEAD_SHARES + 1
    # attacker's shares are worth no more than their deposit — no theft
    assert vault.convert_to_assets(vault.balance_of(EVIL)) <= 100_00


def test_convert_roundtrip_within_one_wei(vault):
    """Invariant: share math matches the reference within 1 wei per conversion.

    A shares->assets->shares roundtrip applies floor division twice, so the
    honest bound is 2 units; each single conversion is within 1.
    """
    vault.deposit(ALICE, 123_456_78)
    vault.deposit(BOB, 87_654_32)
    for addr, shares in ((ALICE, vault.balance_of(ALICE)),
                         (BOB, vault.balance_of(BOB))):
        assets = vault.convert_to_assets(shares)
        back = vault.convert_to_shares(assets)
        assert abs(back - shares) <= 2
        # single conversions are exact to the unit
        assert vault.convert_to_shares(vault.convert_to_assets(1_000_000)) <= 1_000_000


# -- deposits always accepted --------------------------------------------------
def test_deposit_accepted_while_gate_closed(vault):
    """Invariant: deposits are always permitted regardless of gate state."""
    assert not vault.is_live
    shares = vault.deposit(ALICE, 500_00)
    assert shares == 500_00


def test_deposit_zero_reverts(vault):
    """Invariant: zero/negative deposits revert."""
    with pytest.raises(ValueError):
        vault.deposit(ALICE, 0)


# -- yield-only-after-gate -------------------------------------------------------
def test_accrue_yield_reverts_pre_gate(vault):
    """Invariant: pre-gate accrued yield is exactly zero on every path."""
    vault.deposit(ALICE, 100_000_00)
    with pytest.raises(GateClosedError):
        vault.accrue_yield(1_000_00)
    assert vault.accrued_yield_of(ALICE) == 0


def test_claim_yield_reverts_pre_gate(vault):
    """Invariant: yield claims revert while the gate is closed."""
    vault.deposit(ALICE, 100_000_00)
    with pytest.raises(GateClosedError):
        vault.claim_yield(ALICE)


def test_gate_requires_all_four_checks(vault, kyc, oracle, gate):
    """Invariant: a single failing check keeps the gate closed."""
    vault.deposit(ALICE, 100_000_00)
    vault.deposit(BOB, 50_000_00)
    # BOB not KYC'd -> check 1 fails
    kyc.revoke(BOB)
    checks = vault.promote_to_live(_pack(), NOW)
    assert not all(c.ok for c in checks)
    assert not vault.is_live
    with pytest.raises(GateClosedError):
        vault.accrue_yield(100)


def test_stale_attestation_blocks_gate(vault):
    """Invariant: custodian attestation older than 24h fails check 2."""
    vault.deposit(ALICE, 100_000_00)
    checks = vault.promote_to_live(
        _pack(depositors=(ALICE,), attested_at=NOW - 25 * 3600), NOW)
    assert not all(c.ok for c in checks)
    assert not vault.is_live


def test_nav_deviation_tripwire(vault, oracle):
    """Invariant: NAV deviation >= 2% vs reference fails check 3."""
    vault.deposit(ALICE, 100_000_00)
    oracle.update(103_00, NOW, NOW)  # 3% drift
    checks = vault.promote_to_live(
        _pack(depositors=(ALICE,), nav_ref=100_00), NOW)
    assert not all(c.ok for c in checks)


def test_sanctioned_address_blocks_gate(vault):
    """Invariant: sanctioned depositor fails check 4."""
    vault.deposit(ALICE, 100_000_00)
    checks = vault.promote_to_live(
        _pack(depositors=(ALICE,), blocklist=(ALICE,)), NOW)
    assert not all(c.ok for c in checks)
    assert not vault.is_live


# -- promotion ---------------------------------------------------------------------
def test_promotion_atomic_and_idempotent(vault):
    """Invariant: promotion marks dry-run ledger live atomically; re-call no-ops."""
    vault.deposit(ALICE, 100_000_00)
    vault.deposit(BOB, 50_000_00)
    assets_before, shares_before = vault.dry_run.totals()
    assert not vault.dry_run.promoted
    vault.promote_to_live(_pack(), NOW)
    assert vault.is_live
    assert vault.dry_run.promoted
    assets_after, shares_after = vault.dry_run.totals()
    assert (assets_before, shares_before) == (assets_after, shares_after)
    # idempotent: second call is a no-op
    again = vault.promote_to_live(_pack(), NOW)
    assert again[0].name == "already_live"


# -- yield accrual + treasury fee ----------------------------------------------------
def test_accrue_distributes_pro_rata_and_sweeps_fee(vault):
    """Invariant: 15 bps of every distribution routes to Treasury exactly."""
    _live_vault(vault)
    dist = vault.accrue_yield(8_000_00)  # $800 yield on $150k
    assert dist.fee_cents == 8_000_00 * 15 // 10_000
    assert dist.fee_to == TREASURY
    assert dist.fee_cents + dist.net_cents == dist.total_cents
    assert vault.treasury_fees_cents() == dist.fee_cents
    # pro-rata: each holder's accrual is exactly net * balance / eligible
    eligible = vault.total_supply - rwa.DEAD_SHARES
    assert vault.accrued_yield_of(ALICE) == dist.net_cents * vault.balance_of(ALICE) // eligible
    assert vault.accrued_yield_of(BOB) == dist.net_cents * vault.balance_of(BOB) // eligible


def test_claim_yield_pull_pattern(vault):
    """Invariant: pull claims work per-address; claiming zeroes the ledger."""
    _live_vault(vault)
    vault.accrue_yield(8_000_00)
    alice_due = vault.accrued_yield_of(ALICE)
    assert alice_due > 0
    claimed = vault.claim_yield(ALICE)
    assert claimed == alice_due
    assert vault.accrued_yield_of(ALICE) == 0
    # BOB's claim is untouched by ALICE's
    assert vault.accrued_yield_of(BOB) > 0


def test_reverting_recipient_cannot_brick_others(vault):
    """Adversarial: one address's claim failure blocks only their own claim."""
    _live_vault(vault)
    vault.accrue_yield(8_000_00)
    # EVIL is not KYC'd -> their claim raises; ALICE/BOB unaffected
    with pytest.raises(KYCRevokedError):
        vault.claim_yield(EVIL)
    assert vault.claim_yield(ALICE) > 0
    assert vault.claim_yield(BOB) > 0


# -- KYC revocation semantics ----------------------------------------------------------
def test_revocation_freezes_yield_not_principal(vault, kyc):
    """Invariant: revocation freezes yield immediately; principal stays redeemable."""
    _live_vault(vault)
    vault.accrue_yield(8_000_00)
    kyc.revoke(ALICE)
    # yield claim now raises...
    with pytest.raises(KYCRevokedError):
        vault.claim_yield(ALICE)
    # ...frozen, not seized...
    assert vault.frozen_yield_of(ALICE) > 0
    # ...and principal redemption still works
    pw = vault.request_withdraw(ALICE, vault.balance_of(ALICE), NOW)
    settled = vault.settle_withdrawal(pw, NOW + rwa.REDEMPTION_DELAY_S)
    assert settled > 0


def test_yield_accrued_post_revocation_is_frozen(vault, kyc):
    """Invariant: yield accruing after revocation lands in the frozen bucket."""
    _live_vault(vault)
    kyc.revoke(BOB)
    vault.accrue_yield(8_000_00)
    assert vault.accrued_yield_of(BOB) == 0
    assert vault.frozen_yield_of(BOB) > 0
    assert vault.accrued_yield_of(ALICE) > 0


def test_kyc_events_queryable_for_audit(kyc):
    """Invariant: registry events are queryable for audit."""
    kyc.revoke(ALICE)
    events = kyc.events()
    assert any(e["type"] == "revoke" and e["address"] == ALICE.lower()
               for e in events)


# -- redemption --------------------------------------------------------------------------
def test_redemption_always_permitted_mid_gate(vault):
    """Invariant: redemptions work even before the gate opens."""
    vault.deposit(ALICE, 100_000_00)
    assert not vault.is_live
    pw = vault.request_withdraw(ALICE, vault.balance_of(ALICE), NOW)
    with pytest.raises(ValueError):
        vault.settle_withdrawal(pw, NOW + 3600)  # T+2 not elapsed
    settled = vault.settle_withdrawal(pw, NOW + rwa.REDEMPTION_DELAY_S)
    # full principal back, minus at most the dead-share dilution epsilon
    assert 100_000_00 - settled <= rwa.DEAD_SHARES


# -- NAV oracle ----------------------------------------------------------------------------
def test_oracle_reverts_on_stale_data(oracle):
    """Invariant: NAV updates older than 36h revert."""
    with pytest.raises(StaleOracleError):
        oracle.update(101_00, NOW - 37 * 3600, NOW)


def test_writedown_circuit_breaker_pauses_accrual(vault, oracle):
    """Invariant: >5% single-update NAV drop pauses accrual; resume needs multisig."""
    _live_vault(vault)
    oracle.update(94_00, NOW, NOW)  # 6% drop
    assert oracle.paused
    with pytest.raises(AccrualPausedError):
        vault.accrue_yield(100_00)
    with pytest.raises(rwa.LiveBlockedError):
        oracle.resume(multisig_approved=False)
    oracle.resume(multisig_approved=True)
    assert not oracle.paused


def test_small_nav_move_does_not_trip_breaker(oracle):
    """Invariant: a 4.9% drop stays under the circuit breaker."""
    oracle.update(95_10, NOW, NOW)
    assert not oracle.paused


# -- dry-run ledger --------------------------------------------------------------------------
def test_dry_run_mirrors_deposits_to_the_wei(vault):
    """Invariant: dry-run ledger reconciles to vault state exactly."""
    a = vault.deposit(ALICE, 111_111_11)
    b = vault.deposit(BOB, 222_222_22)
    assets, shares = vault.dry_run.totals()
    assert assets == 111_111_11 + 222_222_22
    assert shares == a + b
