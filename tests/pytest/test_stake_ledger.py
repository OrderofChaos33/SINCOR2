"""Stake ledger: deposits, locks, slashing, bonds, re-auction credits."""

import os

import pytest

from sincor2.onchain.stake_ledger import (
    CHALLENGER_BOND_WEI,
    InsufficientStake,
    StakeLedger,
    UnauthorizedSlashing,
    required_stake_wei,
)

ONE_AXM = 10**18


@pytest.fixture
def ledger(tmp_path):
    return StakeLedger(path=str(tmp_path / "stake.json"))


def test_required_stake_is_half_bid():
    assert required_stake_wei(2 * ONE_AXM) == ONE_AXM
    assert required_stake_wei(0) == 0


def test_deposit_and_balance(ledger):
    bal = ledger.deposit("agent-a", 3 * ONE_AXM)
    assert bal["deposited_wei"] == str(3 * ONE_AXM)
    assert bal["available_wei"] == str(3 * ONE_AXM)
    assert bal["locked_wei"] == "0"


def test_deposit_rejects_nonpositive(ledger):
    with pytest.raises(ValueError):
        ledger.deposit("agent-a", 0)


def test_lock_for_commit(ledger):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    locked = ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    assert locked == 2 * ONE_AXM  # 50 % of the 4 AXM bounty
    bal = ledger.balance_of("agent-a")
    assert bal["locked_wei"] == str(2 * ONE_AXM)
    assert bal["available_wei"] == str(8 * ONE_AXM)


def test_lock_rejects_insufficient_stake(ledger):
    ledger.deposit("agent-a", ONE_AXM)
    with pytest.raises(InsufficientStake):
        ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    # No partial lock left behind.
    assert ledger.balance_of("agent-a")["locked_wei"] == "0"


def test_top_up_for_reveal(ledger):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)   # locks 2
    extra = ledger.top_up_for_reveal("agent-a", "tsk-1", 6 * ONE_AXM)  # needs 3
    assert extra == ONE_AXM
    assert ledger.balance_of("agent-a")["locked_wei"] == str(3 * ONE_AXM)


def test_top_up_noop_when_covered(ledger):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    assert ledger.top_up_for_reveal("agent-a", "tsk-1", 2 * ONE_AXM) == 0


def test_top_up_rejects_shortfall(ledger):
    ledger.deposit("agent-a", int(2.5 * ONE_AXM))
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)  # locks 2, 0.5 left
    with pytest.raises(InsufficientStake):
        ledger.top_up_for_reveal("agent-a", "tsk-1", 6 * ONE_AXM)  # needs 3


def test_release(ledger):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    assert ledger.release("agent-a", "tsk-1") == 2 * ONE_AXM
    assert ledger.balance_of("agent-a")["available_wei"] == str(10 * ONE_AXM)


def test_slash_ghost_takes_everything_and_credits_poster(ledger):
    ledger.deposit("ghost", 10 * ONE_AXM)
    ledger.lock_for_commit("ghost", "tsk-1", 4 * ONE_AXM)  # 2 locked
    out = ledger.slash_ghost("ghost", "tsk-1", poster_id="poster-p")
    assert out["slashed_wei"] == str(2 * ONE_AXM)
    assert out["reason"] == "ghosting"
    bal = ledger.balance_of("ghost")
    assert bal["deposited_wei"] == str(8 * ONE_AXM)
    assert bal["slashed_wei"] == str(2 * ONE_AXM)
    assert ledger.reauction_credit("poster-p") == 2 * ONE_AXM


def test_slash_ghost_no_lock_is_noop(ledger):
    out = ledger.slash_ghost("nobody", "tsk-9")
    assert out["slashed_wei"] == "0"


def test_slash_requires_adjudicator(ledger, monkeypatch):
    monkeypatch.delenv("SINCOR_ADJUDICATOR_ID", raising=False)
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    with pytest.raises(UnauthorizedSlashing):
        ledger.slash("agent-a", "tsk-1", 5000, "quality", adjudicator="poster-p")
    # Wrong adjudicator is also rejected.
    monkeypatch.setenv("SINCOR_ADJUDICATOR_ID", "judge-1")
    with pytest.raises(UnauthorizedSlashing):
        ledger.slash("agent-a", "tsk-1", 5000, "quality", adjudicator="impostor")


def test_adjudicate_upheld_slashes_half(ledger, monkeypatch):
    monkeypatch.setenv("SINCOR_ADJUDICATOR_ID", "judge-1")
    ledger.deposit("winner", 10 * ONE_AXM)
    ledger.lock_for_commit("winner", "tsk-1", 4 * ONE_AXM)  # 2 locked
    out = ledger.adjudicate("winner", "tsk-1", upheld=True,
                            adjudicator="judge-1", poster_id="poster-p")
    assert out["slashed_wei"] == str(ONE_AXM)  # 50 % of the 2 AXM lock
    assert ledger.reauction_credit("poster-p") == ONE_AXM
    bal = ledger.balance_of("winner")
    assert bal["locked_wei"] == "0"
    assert bal["available_wei"] == str(9 * ONE_AXM)


def test_adjudicate_rejected_releases(ledger, monkeypatch):
    monkeypatch.setenv("SINCOR_ADJUDICATOR_ID", "judge-1")
    ledger.deposit("winner", 10 * ONE_AXM)
    ledger.lock_for_commit("winner", "tsk-1", 4 * ONE_AXM)
    out = ledger.adjudicate("winner", "tsk-1", upheld=False,
                            adjudicator="judge-1")
    assert out["upheld"] is False
    assert ledger.balance_of("winner")["available_wei"] == str(10 * ONE_AXM)


def test_challenger_bond(ledger):
    ledger.deposit("challenger", 10 * ONE_AXM)
    assert ledger.post_challenger_bond("challenger", "tsk-1") == CHALLENGER_BOND_WEI
    assert CHALLENGER_BOND_WEI == int(0.02 * 10**18)
    bal = ledger.balance_of("challenger")
    assert bal["bonded_wei"] == str(CHALLENGER_BOND_WEI)
    assert ledger.release_bond("challenger", "tsk-1") == CHALLENGER_BOND_WEI


def test_challenger_bond_rejects_when_short(ledger):
    ledger.deposit("poor", 10**15)  # far below 0.02 ETH
    with pytest.raises(InsufficientStake):
        ledger.post_challenger_bond("poor", "tsk-1")


def test_ledger_survives_reload(tmp_path):
    path = str(tmp_path / "stake.json")
    StakeLedger(path=path).deposit("agent-a", 5 * ONE_AXM)
    assert StakeLedger(path=path).balance_of("agent-a")["deposited_wei"] == str(5 * ONE_AXM)


def test_reauction_credits_accumulate(ledger):
    ledger.credit_reauction("poster-p", 100, "slash:ghosting:t1")
    ledger.credit_reauction("poster-p", 200, "slash:ghosting:t2")
    assert ledger.reauction_credit("poster-p") == 300
