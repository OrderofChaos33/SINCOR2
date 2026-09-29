"""P23 acceptance tests: whitelist, utilization cap, oracle, share math, fees.

Maps to the numbered acceptance criteria in
~/workspace/sincor2-auction-tasks/specs/p23-nftfi-pools.md.
Self-contained: Python mirror of the onchain semantics (the Solidity under
onchain/src/p23/ compiles under solc 0.8.24; fork runs are a documented gap).
"""

from __future__ import annotations

import random
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.sincor2.defi.p23 import PARAMS, fees, live_block, manager, oracle, registry, vault
from src.sincor2.defi.p23.live_block import LiveBlocked, guard_live
from src.sincor2.defi.p23.oracle import NftPricingOracle
from src.sincor2.defi.p23.registry import CollectionRegistry, RegistryError
from src.sincor2.defi.p23.vault import FractionalVault, VaultError
from src.sincor2.defi.catalog import TREASURY

R = random.Random(20260929)
COLL_A = "0x" + "aa" * 20
COLL_B = "0x" + "bb" * 20


def _addr():
    return "0x" + "".join(R.choice("0123456789abcdef") for _ in range(40))


def _wired(now=1_000_000.0):
    """Registry + oracle + vault with COLL_A whitelisted and priced."""
    reg = CollectionRegistry()
    op = reg.queue_add(COLL_A, now=now - 200_000.0)
    reg.execute(op, now=now)
    ora = NftPricingOracle()
    for src, px in (("s1", 10_000_000), ("s2", 10_200_000)):  # $10.00 / $10.20
        for i in range(10):
            ora.submit_feed(COLL_A, src, px, ts=now - i * 86400.0)
    v = FractionalVault(reg, ora)
    return reg, ora, v


# -- AC1: whitelist is airtight --------------------------------------------------
def test_ac1_non_whitelisted_deposit_reverts():
    reg, ora, v = _wired()
    with pytest.raises(VaultError, match="not whitelisted"):
        v.deposit("alice", COLL_B, token_id=1, now=1_000_000.0)


def test_ac1_timelock_enforced():
    reg = CollectionRegistry()
    op = reg.queue_add(COLL_A, now=1_000_000.0)
    with pytest.raises(RegistryError, match="timelocked"):
        reg.execute(op, now=1_000_000.0 + 100)  # 100 s < 48 h
    reg.execute(op, now=1_000_000.0 + PARAMS["whitelist_timelock_s"])
    assert reg.is_whitelisted(COLL_A)


def test_ac1_removal_freezes_deposits_not_redemptions():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    shares = v.deposit("alice", COLL_A, token_id=7, now=now)
    assert shares > 0
    op = reg.queue_remove(COLL_A, now=now)
    reg.execute(op, now=now + PARAMS["whitelist_timelock_s"])
    with pytest.raises(VaultError, match="not whitelisted"):
        v.deposit("bob", COLL_A, token_id=8, now=now + 200_000.0)
    payout = v.redeem("alice", COLL_A, shares, now=now + 200_000.0)  # redemption works
    assert payout > 0


def test_ac1_fuzz_1000_addresses_rejected():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    # keep only COLL_A whitelisted; fuzz everything else
    for _ in range(1000):
        c = _addr()
        if c.lower() == COLL_A.lower():
            continue
        with pytest.raises(VaultError, match="not whitelisted"):
            v.deposit("mallory", c, token_id=1, now=now)


# -- AC2: utilization cap holds -----------------------------------------------------
def test_ac2_cap_breach_reverts_withdrawals_open():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    v.deposit("alice", COLL_A, token_id=1, now=now)
    nav = v.pool_nav(COLL_A)
    v.deploy_to_lending(COLL_A, int(nav * 0.75))  # exactly at cap: ok
    assert v.utilization(COLL_A) <= 0.75 + 1e-12
    with pytest.raises(VaultError, match="cap breached"):
        v.deploy_to_lending(COLL_A, 1)  # one wei over: reverts
    # withdrawals are never blocked by the cap
    pos = v._positions[("alice", COLL_A)]
    payout = v.redeem("alice", COLL_A, pos.shares, now=now)
    assert payout > 0


def test_ac2_fuzz_1000_sequences():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    owners = [f"u{i}" for i in range(5)]
    token_seq = [0]
    for step in range(1000):
        action = R.random()
        try:
            if action < 0.45:
                owner = R.choice(owners)
                token_seq[0] += 1
                v.deposit(owner, COLL_A, token_seq[0], now=now + step)
            elif action < 0.65:
                nav = v.pool_nav(COLL_A)
                if nav > 0:
                    amt = int(nav * R.uniform(0.0, 0.2))
                    if amt > 0:
                        v.deploy_to_lending(COLL_A, amt)
            elif action < 0.80:
                deployed = v._deployed_wei.get(COLL_A, 0)
                if deployed > 0:
                    v.repay_to_pool(COLL_A, R.randint(1, deployed))
            elif action < 0.90:
                v.accrue_interest(COLL_A, R.randint(0, 1_000_000))
            else:
                cand = [o for o in owners
                        if v._positions.get((o, COLL_A))
                        and v._positions[(o, COLL_A)].shares > 0]
                if cand:
                    owner = R.choice(cand)
                    pos = v._positions[(owner, COLL_A)]
                    v.redeem(owner, COLL_A, R.randint(1, pos.shares), now=now + step)
        except VaultError:
            pass  # cap-breach reverts are the expected enforcement
        assert v.utilization(COLL_A) <= 0.75 + 1e-9, f"cap breached at step {step}"
    # every fuzz run ends with the invariant intact


# -- AC3: oracle degrades safely ------------------------------------------------------
def test_ac3_stale_feed_freezes_deposits_serves_last_good():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    # A deposit while feeds are fresh establishes the last-good price (the
    # vault calls oracle.floor on every deposit).
    shares = v.deposit("alice", COLL_A, 1, now=now)
    last_good = ora.floor(COLL_A, now).floor_wei
    assert last_good > 0
    reading = ora.floor(COLL_A, now + 2 * 86400.0)  # 48 h later, no new feeds
    assert reading.deposits_frozen
    assert reading.reason == "stale_or_single_source"
    assert reading.floor_wei == last_good  # last-good-price served
    with pytest.raises(VaultError, match="deposits frozen"):
        v.deposit("alice", COLL_A, 1, now=now + 2 * 86400.0)
    # withdrawals stay open even when frozen
    payout = v.redeem("alice", COLL_A, shares, now=now + 2 * 86400.0)
    assert payout > 0


def test_ac3_single_source_freezes():
    ora = NftPricingOracle()
    now = 1_000_000.0
    ora.submit_feed(COLL_A, "only", 10_000_000, ts=now)
    reading = ora.floor(COLL_A, now)
    assert reading.deposits_frozen and reading.reason == "stale_or_single_source"


def test_ac2_redeem_unwinds_lending_sleeve_to_hold_cap():
    # Redeeming shrinks the pool; the vault must recall from the lending
    # sleeve (orderly unwind, model-level) so the 0.75 cap holds exactly
    # after the redemption — withdrawals stay open AND the cap holds.
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    s1 = v.deposit("alice", COLL_A, token_id=1, now=now)
    v.deposit("bob", COLL_A, token_id=2, now=now)
    nav = v.pool_nav(COLL_A)
    v.deploy_to_lending(COLL_A, int(nav * 0.75))
    assert v.utilization(COLL_A) <= 0.75
    v.redeem("alice", COLL_A, s1, now=now + 1.0)
    assert v.utilization(COLL_A) <= 0.75 + 1e-9


def test_ac3_25pct_clamp_blunts_manipulation():
    # The clamp is per-update vs the last published floor: a single update
    # that pins both feeds at 2x cannot move the served floor more than
    # +25%, even though the raw mean jumps to 15M.
    ora = NftPricingOracle()
    now = 1_000_000.0
    for src in ("s1", "s2"):
        ora.submit_feed(COLL_A, src, 10_000_000, ts=now)
    first = ora.floor(COLL_A, now)
    assert first.floor_wei == 10_000_000 and not first.deposits_frozen
    for src in ("s1", "s2"):
        ora.submit_feed(COLL_A, src, 20_000_000, ts=now + 3600.0)
    second = ora.floor(COLL_A, now + 3600.0)
    assert second.reason == "clamped_up_25pct"
    assert second.floor_wei == 12_500_000  # int(10M * 1.25), not the 15M mean
    # Down-move symmetric: on a fresh book, pinning both feeds at 0.4x in a
    # single update is capped at -25% of the last published floor.
    ora2 = NftPricingOracle()
    for src in ("s1", "s2"):
        ora2.submit_feed(COLL_A, src, 10_000_000, ts=now)
    assert ora2.floor(COLL_A, now).floor_wei == 10_000_000
    for src in ("s1", "s2"):
        ora2.submit_feed(COLL_A, src, 4_000_000, ts=now + 3600.0)
    down = ora2.floor(COLL_A, now + 3600.0)
    assert down.reason == "clamped_down_25pct"
    assert down.floor_wei == 7_500_000  # int(10M * 0.75), not the 7M mean


def test_ac3_spread_over_30pct_freezes():
    ora = NftPricingOracle()
    now = 1_000_000.0
    ora.submit_feed(COLL_A, "s1", 10_000_000, ts=now)
    ora.submit_feed(COLL_A, "s2", 20_000_000, ts=now)  # 100% spread
    reading = ora.floor(COLL_A, now)
    assert reading.deposits_frozen and "spread" in reading.reason


# -- AC4: share math is wei-exact -------------------------------------------------------
def test_ac4_deposit_accrue_redeem_matches_nav_formula():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    floor_wei = ora.floor(COLL_A, now).floor_wei
    net = Fraction(floor_wei) * Fraction(10_000 - PARAMS["deposit_fee_bps"], 10_000)
    price_before = v.share_price(COLL_A)
    shares = v.deposit("alice", COLL_A, token_id=1, now=now)
    assert shares == int(net / price_before)  # exact NAV formula
    # reconcile: totalSupply * share_price == pool NAV exactly (rational)
    total = v._total_shares[COLL_A]
    assert Fraction(total) * v.share_price(COLL_A) == v.pool_nav(COLL_A)
    # accrue borrower interest, then redeem everything: payout == pro-rata NAV
    v.accrue_interest(COLL_A, 1_000_000)  # $1.00 of interest
    nav2 = v.pool_nav(COLL_A)
    total2 = v._total_shares[COLL_A]
    payout = v.redeem("alice", COLL_A, shares, now=now)
    assert payout == shares * nav2 // total2
    assert payout >= shares  # NAV per share >= 1 wei by construction here


def test_positions_keyed_by_owner_and_collection():
    # Regression: the same owner across two collections used to overwrite a
    # single owner-keyed position, mixing share balances. Positions are now
    # keyed (owner, collection).
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    op = reg.queue_add(COLL_B, now=now - 200_000.0)
    reg.execute(op, now=now)
    for src, px in (("s1", 20_000_000), ("s2", 20_400_000)):
        ora.submit_feed(COLL_B, src, px, ts=now)
    shares_a = v.deposit("alice", COLL_A, token_id=1, now=now)
    shares_b = v.deposit("alice", COLL_B, token_id=2, now=now)
    assert v._positions[("alice", COLL_A)].shares == shares_a
    assert v._positions[("alice", COLL_B)].shares == shares_b
    assert v._positions[("alice", COLL_A)].token_ids == [1]
    assert v._positions[("alice", COLL_B)].token_ids == [2]
    # redeeming from one collection leaves the other untouched
    v.redeem("alice", COLL_A, shares_a, now=now)
    assert v._positions[("alice", COLL_A)].shares == 0
    assert v._positions[("alice", COLL_B)].shares == shares_b


def test_ac4_first_deposit_mints_minimum_shares_to_burn():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    user_shares = v.deposit("alice", COLL_A, 1, now=now)
    assert v._positions[(vault.BURN_ADDRESS, COLL_A)].shares == PARAMS["minimum_shares"]
    assert v._total_shares[COLL_A] == PARAMS["minimum_shares"] + user_shares


# -- AC5: fee math is wei-exact ------------------------------------------------------------
def test_ac5_fee_wei_exact_worked_example():
    # docs/spec/P23_ECONOMICS.md: $1M pool at 15% -> $150,000/yr -> $300 at 20 bps
    ledger = fees.PoolFeeLedger()
    rec = ledger.settle_epoch(COLL_A, 1_000_000_000_000, 1_150_000_000_000,
                              0.0, PARAMS["epoch_s"])
    assert rec.yield_wei == 150_000_000_000
    assert rec.fee_wei == 300_000_000  # floor(150e9 * 20 / 10000)
    assert rec.to == TREASURY == "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


def test_ac5_losses_pay_no_fee():
    ledger = fees.PoolFeeLedger()
    rec = ledger.settle_epoch(COLL_A, 1_000_000_000_000, 900_000_000_000, 0.0, 1.0)
    assert rec.yield_wei == 0 and rec.fee_wei == 0


# -- AC6: live-blocked is provable --------------------------------------------------------------
def test_ac6_every_live_path_raises():
    with pytest.raises(LiveBlocked):
        guard_live("anything")
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    mgr = manager.LiquidityManager(reg, v)
    with pytest.raises(LiveBlocked):
        mgr.execute_live([])
    assert live_block.LIVE_BLOCKED is True
    assert PARAMS["live_blocked"] is True


def test_ac6_manager_is_advisory_only():
    now = 1_000_000.0
    reg, ora, v = _wired(now)
    v.deposit("alice", COLL_A, 1, now=now)
    mgr = manager.LiquidityManager(reg, v)
    actions = mgr.propose(managed_capital_wei=10_000_000_000, now=now)
    assert all(a.kind in ("deploy", "recall", "hold") for a in actions)
    report = mgr.utilization_report()
    assert COLL_A in report
