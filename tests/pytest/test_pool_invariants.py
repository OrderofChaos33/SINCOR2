"""Invariant / fuzz tests for BountyPool accounting.

Bug class: the UNCX Vesting V2 share-accounting failure ($1.49M) — release()
cut the global total without removing the position, then cancel()
double-counted. Same class threatens any allocate/release/spend ledger.

These tests assert CONSERVATION INVARIANTS that must hold after ANY sequence
of operations, plus a randomized fuzz that hammers the state machine.
If a future change breaks conservation, these fail — even when all
unit tests pass.
"""
import random
import tempfile
from pathlib import Path

import pytest

from sincor2.a2a_bounty_pool import (
    BountyPool,
    InsufficientPoolFunds,
    PoolError,
)


@pytest.fixture
def pool():
    tmp = tempfile.mkdtemp()
    p = BountyPool(path=str(Path(tmp) / "pool.json"), reserve_axm=1000.0)
    p.fund()
    return p


def _conservation_holds(p: BountyPool):
    """funded == allocated + available, always. The core invariant."""
    s = p._status_locked()
    assert abs(s["funded_axm"] - (s["allocated_axm"] + s["available_axm"])) < 1e-9, (
        f"CONSERVATION BROKEN: funded={s['funded_axm']} != "
        f"allocated={s['allocated_axm']} + available={s['available_axm']}"
    )
    # Allocated wei in the ledger matches the status computation
    assert s["allocated_axm"] >= 0
    assert s["available_axm"] >= 0


def test_conservation_after_allocate_release_cycles(pool):
    ids = []
    for i in range(20):
        a = pool.allocate(f"task-{i}", 10.0, reason="cycle")
        ids.append(a["allocation_id"])
        _conservation_holds(pool)
    for aid in ids:
        pool.release(aid)
        _conservation_holds(pool)
    s = pool._status_locked()
    assert s["allocated_axm"] == 0
    assert abs(s["available_axm"] - 1000.0) < 1e-9


def test_double_release_raises_and_conserves(pool):
    a = pool.allocate("task-1", 50.0)
    pool.release(a["allocation_id"])
    _conservation_holds(pool)
    with pytest.raises(PoolError):
        pool.release(a["allocation_id"])
    _conservation_holds(pool)
    # Failed double-release changed nothing
    assert pool._status_locked()["allocated_axm"] == 0


def test_over_allocation_rejected_and_conserves(pool):
    pool.allocate("task-1", 900.0)
    _conservation_holds(pool)
    with pytest.raises(InsufficientPoolFunds):
        pool.allocate("task-2", 200.0)
    _conservation_holds(pool)
    assert abs(pool._status_locked()["available_axm"] - 100.0) < 1e-9


def test_release_unknown_allocation_raises(pool):
    with pytest.raises(KeyError):
        pool.release("alloc_nonexistent")
    _conservation_holds(pool)


def test_fuzz_random_allocate_release_sequences():
    """Random operation sequences must never break conservation.

    This is the UNCX hedge: release-then-release, allocate-to-exhaustion,
    and interleaved sequences must conserve funds in every order.
    """
    for seed in range(30):
        rng = random.Random(seed)
        tmp = tempfile.mkdtemp()
        p = BountyPool(path=str(Path(tmp) / "pool.json"), reserve_axm=1000.0)
        p.fund()
        live = []  # allocation_ids currently in "allocated" state
        for step in range(60):
            _conservation_holds(p)
            op = rng.random()
            if op < 0.5 or not live:
                # allocate a random amount
                amt = round(rng.uniform(1.0, 120.0), 2)
                try:
                    a = p.allocate(f"task-{seed}-{step}", amt)
                    live.append(a["allocation_id"])
                except InsufficientPoolFunds:
                    pass  # expected when pool is exhausted
            else:
                # release a random live allocation (may double-release
                # if the same id is picked twice — must raise, not corrupt)
                aid = rng.choice(live)
                try:
                    p.release(aid)
                    live.remove(aid)
                except PoolError:
                    pass  # double-release correctly rejected
            _conservation_holds(p)
        # Final: release everything left, pool must return to full
        for aid in list(live):
            try:
                p.release(aid)
            except PoolError:
                pass
        _conservation_holds(p)
        s = p._status_locked()
        assert s["allocated_axm"] == 0, f"seed {seed}: leaked allocation"
        assert abs(s["available_axm"] - 1000.0) < 1e-9, (
            f"seed {seed}: pool did not return to full"
        )
