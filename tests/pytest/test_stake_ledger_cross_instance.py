"""W-208: StakeLedger cross-instance lost updates.

Two StakeLedger instances on one path: the second instance was
constructed before the first instance's writes, so it holds a stale
snapshot.  Its non-deposit mutators must reload under the file lock
before mutating/saving, or they erase the first instance's deposits,
locks, tx-hash dedupe entries, bonds and credits.
"""

import pytest

from sincor2.onchain.stake_ledger import StakeLedger

ONE = 10**18
TX_A = "0x" + "aa" * 32


def fresh(path):
    return StakeLedger(path=path)


def test_stale_lock_for_commit_preserves_other_instance_deposit_and_tx_dedupe(tmp_path):
    path = str(tmp_path / "stake.json")
    a = StakeLedger(path=path)
    b = StakeLedger(path=path)  # constructed BEFORE a's deposit (stale)
    a.deposit("agent-a", 10 * ONE, reference=TX_A)
    # b's stale snapshot has no deposit; after reload it must see it.
    locked = b.lock_for_commit("agent-a", "tsk-1", 4 * ONE)
    assert locked == 2 * ONE
    check = fresh(path)
    bal = check.balance_of("agent-a")
    assert bal["deposited_wei"] == str(10 * ONE)
    assert bal["locked_wei"] == str(2 * ONE)
    # tx-hash dedupe entry written by a must survive b's save:
    dup = check.deposit("agent-a", 10 * ONE, reference=TX_A)
    assert dup["duplicate"] is True
    assert fresh(path).balance_of("agent-a")["deposited_wei"] == str(10 * ONE)


def test_two_stale_instances_different_mutators_both_survive(tmp_path):
    path = str(tmp_path / "stake.json")
    seed = StakeLedger(path=path)
    seed.deposit("agent-a", 10 * ONE)
    seed.deposit("agent-b", 10 * ONE)
    a = StakeLedger(path=path)
    b = StakeLedger(path=path)
    a.lock_for_commit("agent-a", "tsk-1", 4 * ONE)   # locks 2
    b.post_challenger_bond("agent-b", "tsk-1")       # stale: no a-lock in snapshot
    check = fresh(path)
    assert check.balance_of("agent-a")["locked_wei"] == str(2 * ONE)
    from sincor2.onchain.stake_ledger import CHALLENGER_BOND_WEI
    assert check.balance_of("agent-b")["bonded_wei"] == str(CHALLENGER_BOND_WEI)


def test_stale_credit_reauction_and_lock_both_survive(tmp_path):
    path = str(tmp_path / "stake.json")
    seed = StakeLedger(path=path)
    seed.deposit("agent-a", 10 * ONE)
    a = StakeLedger(path=path)
    b = StakeLedger(path=path)
    a.lock_for_commit("agent-a", "tsk-1", 4 * ONE)
    b.credit_reauction("poster-p", 300, "slash:ghosting:t-x")
    check = fresh(path)
    assert check.balance_of("agent-a")["locked_wei"] == str(2 * ONE)
    assert check.reauction_credit("poster-p") == 300


def test_stale_slash_does_not_erase_other_instance_lock_or_deposit(tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_ADJUDICATOR_ID", "judge-1")
    path = str(tmp_path / "stake.json")
    seed = StakeLedger(path=path)
    seed.deposit("agent-a", 10 * ONE)
    seed.lock_for_commit("agent-a", "tsk-1", 4 * ONE)  # 2 locked
    stale = StakeLedger(path=path)
    other = StakeLedger(path=path)
    other.deposit("agent-a", 10 * ONE)                 # now 20 deposited
    other.lock_for_commit("agent-a", "tsk-2", 4 * ONE)  # +2 locked
    out = stale.slash("agent-a", "tsk-1", 5000, "quality_failure_upheld",
                      adjudicator="judge-1", poster_id="poster-p")
    assert out["slashed_wei"] == str(ONE)  # 50% of tsk-1's 2
    check = fresh(path)
    bal = check.balance_of("agent-a")
    assert bal["deposited_wei"] == str(19 * ONE)
    assert bal["locked_wei"] == str(2 * ONE)  # tsk-2 lock survives
    assert check.locked_for("agent-a", "tsk-2") == 2 * ONE


def test_stale_slash_ghost_does_not_erase_other_deposit(tmp_path):
    path = str(tmp_path / "stake.json")
    seed = StakeLedger(path=path)
    seed.deposit("ghost", 10 * ONE)
    seed.lock_for_commit("ghost", "tsk-1", 4 * ONE)
    stale = StakeLedger(path=path)
    StakeLedger(path=path).deposit("ghost", 5 * ONE)
    stale.slash_ghost("ghost", "tsk-1", poster_id="poster-p")
    check = fresh(path)
    assert check.balance_of("ghost")["deposited_wei"] == str(13 * ONE)
    assert check.reauction_credit("poster-p") == 2 * ONE


def test_stale_record_completion_preserves_deposit(tmp_path):
    path = str(tmp_path / "stake.json")
    a = StakeLedger(path=path)
    b = StakeLedger(path=path)
    a.deposit("agent-a", 7 * ONE)
    b.record_completion("agent-a", "tsk-1", poster_id="poster-p")
    check = fresh(path)
    assert check.balance_of("agent-a")["deposited_wei"] == str(7 * ONE)


def test_stale_top_up_preserves_other_instance_lock(tmp_path):
    path = str(tmp_path / "stake.json")
    seed = StakeLedger(path=path)
    seed.deposit("agent-a", 10 * ONE)
    seed.deposit("agent-b", 10 * ONE)
    a = StakeLedger(path=path)
    b = StakeLedger(path=path)
    a.lock_for_commit("agent-b", "tsk-b", 4 * ONE)
    b.lock_for_commit("agent-a", "tsk-1", 4 * ONE)   # locks 2
    extra = b.top_up_for_reveal("agent-a", "tsk-1", 6 * ONE)  # needs 3
    assert extra == ONE
    check = fresh(path)
    assert check.balance_of("agent-a")["locked_wei"] == str(3 * ONE)
    assert check.balance_of("agent-b")["locked_wei"] == str(2 * ONE)


def test_stale_release_and_bond_both_survive(tmp_path):
    path = str(tmp_path / "stake.json")
    seed = StakeLedger(path=path)
    seed.deposit("agent-a", 10 * ONE)
    seed.deposit("agent-b", 10 * ONE)
    seed.lock_for_commit("agent-a", "tsk-1", 4 * ONE)
    a = StakeLedger(path=path)
    b = StakeLedger(path=path)
    assert a.release("agent-a", "tsk-1") == 2 * ONE
    b.post_challenger_bond("agent-b", "tsk-9")
    check = fresh(path)
    assert check.balance_of("agent-a")["locked_wei"] == "0"
    assert check.balance_of("agent-a")["deposited_wei"] == str(10 * ONE)
    from sincor2.onchain.stake_ledger import CHALLENGER_BOND_WEI
    assert check.balance_of("agent-b")["bonded_wei"] == str(CHALLENGER_BOND_WEI)
