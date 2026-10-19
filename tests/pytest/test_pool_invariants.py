"""Invariant / fuzz tests for BountyPool accounting.

Bug class: the UNCX Vesting V2 share-accounting failure ($1.49M) — release()
cut the global total without removing the position, then cancel()
double-counted. Same class threatens any allocate/release/spend ledger.

W-101 (2026-10-18): the original suite asserted
``funded == allocated + available`` where ``available`` is DEFINED as
``funded - allocated`` — the algebraic identity x == y + (x - y), true for
any ledger contents including corrupted ones. Red-team PoC proved three
corrupted states and a 1-wei record-drift mutant all passed green.

These tests now assert RECONCILIATION: the append-only history journal is
replayed independently and must agree with the record-derived state.
Journal/record disagreement — drift, phantoms, lost state flips — fails.
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


def _mkpool():
    tmp = tempfile.mkdtemp()
    p = BountyPool(path=str(Path(tmp) / "pool.json"), reserve_axm=1000.0)
    p.fund()
    return p


def _ledger_reconciles(p: BountyPool):
    """Journal replay must agree with record-derived state (W-101).

    This is NOT the tautology funded == allocated + (funded - allocated):
    verify_ledger() replays the append-only history journal as an
    independent book and asserts it matches the allocation records.
    """
    v = p.verify_ledger()
    assert v["inconclusive"] is None, (
        f"ledger verification inconclusive: {v['inconclusive']}")
    assert v["ok"], f"LEDGER DIVERGENCE (W-101): {v['discrepancies']}"
    # Non-negativity sanity on the derived status
    s = p._status_locked()
    assert s["allocated_axm"] >= 0
    assert s["available_axm"] >= 0


def test_conservation_after_allocate_release_cycles(pool):
    ids = []
    for i in range(20):
        a = pool.allocate(f"task-{i}", 10.0, reason="cycle")
        ids.append(a["allocation_id"])
        _ledger_reconciles(pool)
    for aid in ids:
        pool.release(aid)
        _ledger_reconciles(pool)
    s = pool._status_locked()
    assert s["allocated_axm"] == 0
    assert abs(s["available_axm"] - 1000.0) < 1e-9


def test_double_release_raises_and_conserves(pool):
    a = pool.allocate("task-1", 50.0)
    pool.release(a["allocation_id"])
    _ledger_reconciles(pool)
    with pytest.raises(PoolError):
        pool.release(a["allocation_id"])
    _ledger_reconciles(pool)
    # Failed double-release changed nothing
    assert pool._status_locked()["allocated_axm"] == 0


def test_over_allocation_rejected_and_conserves(pool):
    pool.allocate("task-1", 900.0)
    _ledger_reconciles(pool)
    with pytest.raises(InsufficientPoolFunds):
        pool.allocate("task-2", 200.0)
    _ledger_reconciles(pool)
    assert abs(pool._status_locked()["available_axm"] - 100.0) < 1e-9


def test_release_unknown_allocation_raises(pool):
    with pytest.raises(KeyError):
        pool.release("alloc_nonexistent")
    _ledger_reconciles(pool)


def test_fuzz_random_allocate_release_sequences():
    """Random operation sequences must keep journal and records in agreement.

    This is the UNCX hedge: release-then-release, allocate-to-exhaustion,
    and interleaved sequences must reconcile in every order.
    """
    for seed in range(30):
        rng = random.Random(seed)
        tmp = tempfile.mkdtemp()
        p = BountyPool(path=str(Path(tmp) / "pool.json"), reserve_axm=1000.0)
        p.fund()
        live = []  # allocation_ids currently in "allocated" state
        for step in range(60):
            _ledger_reconciles(p)
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
            _ledger_reconciles(p)
        # Final: release everything left, pool must return to full
        for aid in list(live):
            try:
                p.release(aid)
            except PoolError:
                pass
        _ledger_reconciles(p)
        s = p._status_locked()
        assert s["allocated_axm"] == 0, f"seed {seed}: leaked allocation"
        assert abs(s["available_axm"] - 1000.0) < 1e-9, (
            f"seed {seed}: pool did not return to full"
        )


# --- W-101 regression: corrupted states must FAIL reconciliation -----------
class _DriftPool(BountyPool):
    """Seeded fault: the record understates each allocation by 1 wei.

    The allocate() guard still charges the full amount, but the persisted
    record drifts — the unit/rounding-bug shape. verify_ledger() must
    catch it: the journal holds the true amount, the records drift.
    """

    def allocate(self, task_id, amount_axm, reason=""):
        rec = super().allocate(task_id, amount_axm, reason)
        aid = rec["allocation_id"]
        self._allocations[aid]["amount_wei"] -= 1
        return rec


def test_w101_zeroed_record_fails_verification():
    """600-AXM allocation record zeroed post-hoc: journal says 600
    allocated and never released, records say 0. Must FAIL."""
    p = _mkpool()
    rec = p.allocate("bounty-a", 600.0)
    p._allocations[rec["allocation_id"]]["amount_wei"] = 0
    v = p.verify_ledger()
    assert not v["ok"], "zeroed record passed verification (W-101)"
    assert v["inconclusive"] is None


def test_w101_phantom_allocation_fails_verification():
    """400-AXM allocation injected with no journal entry. Must FAIL."""
    p = _mkpool()
    p._allocations["alloc_phantom"] = {
        "allocation_id": "alloc_phantom", "task_id": "ghost",
        "amount_wei": int(400e18), "state": "allocated"}
    v = p.verify_ledger()
    assert not v["ok"], "phantom allocation passed verification (W-101)"
    assert v["inconclusive"] is None


def test_w101_journal_record_disagreement_fails_verification():
    """Journal says released, record says allocated (the UNCX shape).
    Must FAIL."""
    p = _mkpool()
    r = p.allocate("bounty-c", 900.0)
    p._history.append({"action": "release",
                       "amount_wei": str(int(900e18)),
                       "at": 0,
                       "detail": {"allocation_id": r["allocation_id"]}})
    p._history_writes += 1  # keep the journal self-consistent
    v = p.verify_ledger()
    assert not v["ok"], "journal/record disagreement passed (W-101)"
    assert v["inconclusive"] is None


def test_w101_record_drift_mutant_is_killed():
    """The 1-wei record-drift mutant must FAIL the suite (mutation gate).

    Per the repo mutation-testing gate: a seeded fault on touched lines
    must be killed. If this test ever goes green on the mutant, the
    invariant suite has regressed to vacuity.
    """
    p = _DriftPool(path=str(Path(tempfile.mkdtemp()) / "pool.json"),
                   reserve_axm=1000.0)
    p.fund()
    p.allocate("t", 123.45)
    v = p.verify_ledger()
    assert not v["ok"], "record-drift mutant survived verification (W-101)"
    with pytest.raises(AssertionError):
        _ledger_reconciles(p)


def test_verify_ledger_trimmed_journal_is_inconclusive_not_pass():
    """A pool loaded from a trimmed journal must report inconclusive,
    never a false pass."""
    p = _mkpool()
    p.allocate("t1", 100.0)
    # Simulate a trim: drop journal entries but keep the write counter
    p._history = p._history[-1:]
    v = p.verify_ledger()
    assert v["inconclusive"] is not None
    assert not v["ok"]
