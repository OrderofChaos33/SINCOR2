"""Unit tests for the P21 Treasury DAO reference build.

Covers src/sincor2/defi/treasury_dao.py — allocation bands and the 30%
per-venue cap, content-addressed holds (SHA-256 of canonical JSON),
unreviewed-hold publishing blocks, 72h backlog alerts, exact 100%
allocation sums, the always-raising broadcast() gate, EXECUTE_LIVE_ENV
default-off behavior, dashboard unknown-on-failure, and the 8 bps
realized-yield fee behind SKU SINCOR-DEFI-P21-TREASURY. Pure logic, no
chain.

Each test cites the auction-task acceptance criterion it guards.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from src.sincor2.defi.treasury_dao import (
    FEE_BPS,
    AllocationBand,
    BandViolation,
    BroadcastForbidden,
    Hold,
    StaleHold,
    TreasurySwarm,
    Unauthorized,
    UnreviewedHold,
    canonical_json,
    content_id,
    status_payload,
)
from src.sincor2.defi import catalog as catalog_mod

NOW = 1_800_000_000.0
W = 10**18

BANDS = [
    AllocationBand("aave", 2_000, 3_000),
    AllocationBand("compound", 2_000, 3_000),
    AllocationBand("morpho", 2_000, 3_000),
    AllocationBand("cash", 1_000, 3_000),
]


def make_swarm() -> TreasurySwarm:
    return TreasurySwarm(BANDS, reviewers=["reviewer1"])


def good_targets() -> dict:
    return {"aave": 2_500, "compound": 2_500, "morpho": 2_500, "cash": 2_500}


def reviewed_hold(swarm: TreasurySwarm, targets=None,
                  now: float = NOW) -> Hold:
    hold = swarm.propose_hold(targets or good_targets(), "allocator",
                              now=now)
    swarm.reviewer.review(hold, True, "reviewer1", note="looks good",
                          now=now + 10)
    return hold


# -- bands + cap ----------------------------------------------------------------------------------
def test_per_venue_30pct_cap():
    # AC(p21-treasury-dao-risk-bands): per-venue allocation hard-capped
    # at 30%.
    swarm = make_swarm()
    with pytest.raises(BandViolation):
        swarm.propose_hold({"aave": 3_001, "compound": 2_500,
                            "morpho": 2_499, "cash": 2_000}, "allocator",
                           now=NOW)


def test_band_violation_rejected():
    swarm = make_swarm()
    with pytest.raises(BandViolation):
        swarm.propose_hold({"aave": 1_999, "compound": 2_500,
                            "morpho": 3_000, "cash": 2_501}, "allocator",
                           now=NOW)  # aave below its 2_000 band floor


def test_allocations_sum_exactly_100():
    # AC(p21-treasury-dao-allocation-reports): allocation reports sum to
    # exactly 100% with no drift.
    swarm = make_swarm()
    with pytest.raises(BandViolation):
        swarm.propose_hold({"aave": 2_500, "compound": 2_500,
                            "morpho": 2_500, "cash": 2_499}, "allocator",
                           now=NOW)  # 9_999 bps
    hold = swarm.propose_hold(good_targets(), "allocator", now=NOW)
    assert sum(hold.allocations_bps.values()) == 10_000


def test_exact_cap_boundary_allowed():
    swarm = make_swarm()
    hold = swarm.propose_hold({"aave": 3_000, "compound": 2_500,
                               "morpho": 2_500, "cash": 2_000},
                              "allocator", now=NOW)
    assert hold.allocations_bps["aave"] == 3_000


# -- content addressing ----------------------------------------------------------------------------
def test_hold_id_is_content_hash():
    # AC(p21-treasury-dao-review-holds): holds are content-addressed —
    # the ID is the SHA-256 of the canonical JSON.
    hold = make_swarm().propose_hold(good_targets(), "allocator", now=NOW)
    content = {"allocations_bps": good_targets(), "created_by": "allocator",
               "created_at": NOW}
    assert hold.hold_id == content_id(content)
    assert hold.verify_id()
    # Tampering changes the ID.
    tampered = Hold(hold.hold_id, {**good_targets(), "aave": 3_000},
                    "allocator", NOW)
    assert not tampered.verify_id()


def test_canonical_json_deterministic():
    a = canonical_json({"b": 1, "a": [3, 2]})
    b = canonical_json({"a": [3, 2], "b": 1})
    assert a == b


# -- review gate -----------------------------------------------------------------------------------
def test_unreviewed_hold_blocks_publishing():
    # AC(p21-treasury-dao-review-holds): unreviewed holds block
    # publishing.
    swarm = make_swarm()
    hold = swarm.propose_hold(good_targets(), "allocator", now=NOW)
    with pytest.raises(UnreviewedHold):
        swarm.executor.publish(hold, now=NOW + 10)
    assert swarm.executor.publish(reviewed_hold(swarm))["hold_id"]


def test_rejected_hold_cannot_publish():
    swarm = make_swarm()
    hold = swarm.propose_hold(good_targets(), "allocator", now=NOW)
    swarm.reviewer.review(hold, False, "reviewer1", note="no", now=NOW + 5)
    with pytest.raises(UnreviewedHold):
        swarm.executor.publish(hold, now=NOW + 10)


def test_unauthorized_reviewer_rejected():
    swarm = make_swarm()
    hold = swarm.propose_hold(good_targets(), "allocator", now=NOW)
    with pytest.raises(Unauthorized):
        swarm.reviewer.review(hold, True, "mallory", now=NOW + 5)
    assert not hold.reviewed


def test_hold_backlog_72h_alert():
    # AC(p21-treasury-dao-review-holds): holds awaiting review >72h
    # trigger a backlog alert; publish refuses stale unreviewed holds.
    swarm = make_swarm()
    hold = swarm.propose_hold(good_targets(), "allocator", now=NOW)
    assert swarm.reviewer.backlog(swarm.holds, now=NOW + 72 * 3600 + 1) == [hold]
    assert swarm.reviewer.backlog(swarm.holds, now=NOW + 72 * 3600) == []
    with pytest.raises(StaleHold):
        swarm.executor.publish(hold, now=NOW + 72 * 3600 + 1)


# -- broadcast is never allowed ---------------------------------------------------------------------
def test_broadcast_always_raises():
    # AC(p21-treasury-dao-broadcast): every broadcast attempt raises
    # BroadcastForbidden — the swarm has no signing or broadcast
    # capability.
    swarm = make_swarm()
    hold = reviewed_hold(swarm)
    with pytest.raises(BroadcastForbidden):
        swarm.executor.broadcast(hold)


def test_no_signing_imports_in_allocator_path():
    import src.sincor2.defi.treasury_dao as mod
    src = Path(mod.__file__).read_text()
    for lib in ("eth_account", "nacl", "ecdsa", "coincurve", "web3"):
        assert f"import {lib}" not in src and f"from {lib}" not in src


def test_execute_live_env_defaults_off():
    # AC(p21-treasury-dao-broadcast): EXECUTE_LIVE_ENV defaults off; with
    # it off, execute() never dispatches.
    import src.sincor2.defi.treasury_dao as mod
    assert mod.EXECUTE_LIVE_ENV is False
    swarm = make_swarm()
    hold = reviewed_hold(swarm)
    res = swarm.executor.execute(hold, now=NOW + 20)
    assert res["dispatched"] is False
    assert res["reason"] == "EXECUTE_LIVE_ENV off"


def test_execute_requires_reviewed_hold_even_live():
    # Even with the flag on, only reviewed holds dispatch to the
    # approved external executor.
    import src.sincor2.defi.treasury_dao as mod
    calls = []
    swarm = TreasurySwarm(BANDS, ["reviewer1"],
                          external_executor=lambda h: calls.append(h.hold_id)
                          or "ext-1")
    unreviewed = swarm.propose_hold(good_targets(), "allocator", now=NOW)
    with pytest.raises(UnreviewedHold):
        # Force the live path to prove the review gate still holds.
        mod.EXECUTE_LIVE_ENV = True
        try:
            swarm.executor.execute(unreviewed, now=NOW + 10)
        finally:
            mod.EXECUTE_LIVE_ENV = False
    assert calls == []


# -- yield fee ---------------------------------------------------------------------------------------
def test_yield_fee_8bps_positive_only():
    # AC(p21-treasury-dao-yield-accounting): 8 bps fee on positive
    # realized yield; losses take no fee.
    swarm = make_swarm()
    r = swarm.yield_ledger.record_yield(1_000 * W)
    assert r["fee_wei"] == 1_000 * W * FEE_BPS // 10_000
    r2 = swarm.yield_ledger.record_yield(-500 * W)
    assert r2["fee_wei"] == 0
    assert swarm.yield_ledger.realized_yield_wei == 500 * W
    receipt = swarm.yield_ledger.claim_fees()
    assert receipt["treasury"] == catalog_mod.TREASURY
    assert receipt["fee_bps"] == 8
    assert swarm.yield_ledger.fee_owed_wei == 0


# -- status feed --------------------------------------------------------------------------------------
def test_status_unknown_on_failure():
    # AC(p21-treasury-dao-dashboard): dashboard failures surface as
    # unknown, never healthy.
    swarm = make_swarm()
    bad = status_payload(swarm, data_fresh=False)
    assert bad["status"] == "unknown"
    ok = status_payload(swarm, data_fresh=True)
    assert ok["status"] == "ok"


# -- fuzz: allocations ----------------------------------------------------------------------------------
def test_fuzz_allocations():
    # AC(p21-treasury-dao-invariant-fuzz-tests): 1,000 randomized
    # allocation plans — every accepted plan is in-band, under the cap,
    # and sums to exactly 10_000 bps; every rejected plan raises.
    import random
    rng = random.Random(20260929)
    venues = ["aave", "compound", "morpho", "cash"]
    accepted = 0
    for _ in range(1_000):
        # Random split of 10_000 bps across 4 venues (stars and bars).
        cuts = sorted(rng.sample(range(1, 10_000), 3))
        parts = [cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1],
                 10_000 - cuts[2]]
        rng.shuffle(parts)
        targets = dict(zip(venues, parts))
        swarm = make_swarm()
        try:
            hold = swarm.propose_hold(targets, "allocator", now=NOW)
        except BandViolation:
            # Rejected plans must actually violate a rule.
            total = sum(targets.values())
            bad = (total != 10_000
                   or any(v > 3_000 for v in targets.values())
                   or any(not (BANDS[i].min_bps <= v <= BANDS[i].max_bps)
                          for i, v in enumerate(targets.values())))
            assert bad, f"valid plan rejected: {targets}"
            continue
        accepted += 1
        assert sum(hold.allocations_bps.values()) == 10_000
        for v in hold.allocations_bps.values():
            assert v <= 3_000
        assert hold.verify_id()
    assert accepted > 0  # the fuzz actually exercised the accept path
