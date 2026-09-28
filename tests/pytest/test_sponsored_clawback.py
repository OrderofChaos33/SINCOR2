"""Sponsored-stake clawback: subsidy-extraction invariant.

Platform-fronted capital can never be converted into a poster's reusable
re-auction balance via sybil ghosting. On any slash (ghost or upheld
quality failure), the platform's unrecouped front is clawed back to the
treasury FIRST (senior creditor); only the remainder becomes poster
re-auction credit.

Covers: full clawback on active front, pro-rata split on partial recoup,
zero clawback after full settlement (platform already whole), the
adjudicated quality-slash path, unchanged behavior with no sponsorship,
and audit fields on the slash event.
"""
from __future__ import annotations

import pytest

from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    StakeLedger,
    reset_stake_ledger,
    stake_ledger,
)
from sincor2.sponsored_stake import (
    ENABLE_ENV,
    front_sponsored_stake,
    recoup_sponsored_stake,
    reset_sponsored_ledger,
)

AXM = 10**18
AGENT = "clawback-agent-1"
POSTER = "poster-1"
ADJ = "test-adjudicator"


@pytest.fixture
def ledgers(tmp_path, monkeypatch):
    from sincor2.a2a_inbound import reset_fabric

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    reset_sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(ADJUDICATOR_ENV, ADJ)
    return stake_ledger()


def _front(agent_id=AGENT, amount_axm=25):
    return front_sponsored_stake(agent_id, amount_axm * AXM)


def _ghost(ledger: StakeLedger, agent_id=AGENT, task="t1",
           lock_axm=10, poster_id=POSTER):
    # lock_for_commit locks bounty*50%; bounty = 2x desired lock.
    ledger.lock_for_commit(agent_id, task, lock_axm * 2 * AXM)
    return ledger.slash_ghost(agent_id, task, poster_id=poster_id)


def test_ghost_slash_claws_back_active_front(ledgers):
    _front()
    out = _ghost(ledgers)

    assert out["clawback_wei"] == str(10 * AXM)
    assert out["poster_credit_wei"] == "0"
    assert ledgers.reauction_credit("__platform__") == 10 * AXM
    assert ledgers.reauction_credit(POSTER) == 0


def test_ghost_slash_partial_recoup_splits(ledgers):
    _front()  # 25 fronted
    recoup_sponsored_stake(AGENT, 10 * AXM)  # 15 outstanding

    out = _ghost(ledgers, lock_axm=20)

    assert out["clawback_wei"] == str(15 * AXM)
    assert out["poster_credit_wei"] == str(5 * AXM)
    assert ledgers.reauction_credit("__platform__") == 15 * AXM
    assert ledgers.reauction_credit(POSTER) == 5 * AXM


def test_ghost_slash_after_full_recoup_goes_to_poster(ledgers):
    _front()
    rec = recoup_sponsored_stake(AGENT, 25 * AXM)
    assert rec["status"] == "settled"

    out = _ghost(ledgers)

    # Platform already whole: the poster's ratified expectation holds.
    assert out["clawback_wei"] == "0"
    assert out["poster_credit_wei"] == str(10 * AXM)
    assert ledgers.reauction_credit(POSTER) == 10 * AXM
    assert ledgers.reauction_credit("__platform__") == 0


def test_quality_slash_splits_via_adjudicate(ledgers):
    _front()  # 25 outstanding
    ledgers.deposit(AGENT, 50 * AXM, reference="self-funded")
    ledgers.lock_for_commit(AGENT, "tq", 120 * AXM)  # locks 60

    out = ledgers.adjudicate(AGENT, "tq", True, adjudicator=ADJ,
                             poster_id=POSTER)

    # 50 % of 60 = 30 slashed; 25 clawed back, 5 to the poster.
    assert out["slashed_wei"] == str(30 * AXM)
    assert out["clawback_wei"] == str(25 * AXM)
    assert out["poster_credit_wei"] == str(5 * AXM)
    assert ledgers.reauction_credit("__platform__") == 25 * AXM
    assert ledgers.reauction_credit(POSTER) == 5 * AXM


def test_no_sponsorship_behavior_unchanged(ledgers):
    ledgers.deposit(AGENT, 50 * AXM, reference="self-funded")

    out = _ghost(ledgers)

    assert out["clawback_wei"] == "0"
    assert out["poster_credit_wei"] == str(10 * AXM)
    assert ledgers.reauction_credit(POSTER) == 10 * AXM


def test_slash_event_records_split(ledgers):
    _front()
    _ghost(ledgers)

    slash_events = [e for e in ledgers._data["events"]
                    if e["kind"] == "slash"]
    assert len(slash_events) == 1
    ev = slash_events[0]
    assert ev["clawback_wei"] == str(10 * AXM)
    assert ev["poster_credit_wei"] == "0"
    assert ev["reason"] == "ghosting"


def test_repeated_slashes_capped_at_outstanding(ledgers):
    """Cumulative-cap invariant: two ghost slashes against one 25 AXM
    front can never claw back more than 25 total.  Once the claim is
    settled, further slashes route entirely to poster re-auction credit."""
    from sincor2.sponsored_stake import sponsored_ledger

    _front()  # 25 AXM front
    ledgers.deposit(AGENT, 100 * AXM, reference="self-funded")

    # Ghost 1: lock 60, slash 60 -> 25 clawed back, 35 to the poster.
    ledgers.lock_for_commit(AGENT, "t-cap-1", 120 * AXM)
    out1 = ledgers.slash_ghost(AGENT, "t-cap-1", poster_id=POSTER)
    assert out1["clawback_wei"] == str(25 * AXM)
    assert out1["poster_credit_wei"] == str(35 * AXM)

    # Ghost 2: lock 40, slash 40 -> claim exhausted: 0 clawed back,
    # all 40 to the poster.
    ledgers.lock_for_commit(AGENT, "t-cap-2", 80 * AXM)
    out2 = ledgers.slash_ghost(AGENT, "t-cap-2", poster_id=POSTER)
    assert out2["clawback_wei"] == "0"
    assert out2["poster_credit_wei"] == str(40 * AXM)

    assert ledgers.reauction_credit("__platform__") == 25 * AXM
    assert ledgers.reauction_credit(POSTER) == 75 * AXM
    assert sponsored_ledger().outstanding_wei(AGENT) == 0


def test_clawback_settles_claim_no_double_recoup(ledgers):
    """A clawback reduces the sponsored outstanding, so a later earnings
    recoup cannot double-recover what the slash already settled."""
    from sincor2.sponsored_stake import (
        recoup_sponsored_stake,
        sponsored_ledger,
    )

    _front()  # 25 AXM front
    out = _ghost(ledgers, lock_axm=10)  # slash 10 -> clawback 10
    assert out["clawback_wei"] == str(10 * AXM)
    assert sponsored_ledger().outstanding_wei(AGENT) == 15 * AXM

    rec = recoup_sponsored_stake(AGENT, 100 * AXM, task_id="t-earn")
    assert rec["recouped_wei"] == str(15 * AXM)  # not 25
    assert rec["outstanding_wei"] == "0"
    assert sponsored_ledger().outstanding_wei(AGENT) == 0


def test_full_clawback_settles_sponsorship(ledgers):
    from sincor2.sponsored_stake import sponsored_ledger

    _front()  # 25 AXM front, fully sponsored agent
    out = _ghost(ledgers, lock_axm=25, poster_id=POSTER)
    assert out["clawback_wei"] == str(25 * AXM)
    assert out["poster_credit_wei"] == "0"
    status = sponsored_ledger().status_of(AGENT)
    assert status["status"] == "settled"
    assert status["clawed_back_wei"] == str(25 * AXM)
    # A further slash finds no claim left.
    assert sponsored_ledger().apply_clawback(AGENT, 10 * AXM, "t-x") == 0


def test_apply_clawback_idempotent_per_task(ledgers):
    from sincor2.sponsored_stake import sponsored_ledger

    _front()
    first = sponsored_ledger().apply_clawback(AGENT, 10 * AXM, "t-dedupe")
    second = sponsored_ledger().apply_clawback(AGENT, 10 * AXM, "t-dedupe")
    assert first == 10 * AXM
    assert second == 0
    assert sponsored_ledger().outstanding_wei(AGENT) == 15 * AXM


def test_apply_clawback_unknown_agent_zero(ledgers):
    from sincor2.sponsored_stake import sponsored_ledger

    assert sponsored_ledger().apply_clawback("ghost-agent", 10 * AXM, "t") == 0
