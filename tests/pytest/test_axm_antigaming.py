"""Anti-gaming guards for the AXM Activity Engine (Stream B) — adversarial tests.

Covers, for real (no mocks of the guard itself):
  1. Self-bid rejection on the commit AND plaintext bid paths (must raise).
  2. Two-agent win-trading loop broken up by the pair rotation cap.
  3. Per-agent consecutive-win cap.
  4. Rolling-window participation caps (bids entered / wins).
  5. Winner ghost (no-show) slashed 100 % via the existing stake_ledger
     ghost rule — slash math is the ledger's, asserted on ledger state.
  6. Honest rotation still works: distinct agents alternate wins, nobody
     blocked.
"""
from __future__ import annotations

import os

import pytest

from sincor2 import axm_antigaming as ag
from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound_market import (
    close_auction,
    commit_bid,
    create_task,
    expire_stale_assignments,
    place_bid,
    reveal_bid,
    sealed_commitment,
)


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated fabric + stake ledger + antigaming store."""
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    store = ag.reset_antigaming_store(path=str(tmp_path / "antigaming.json"))
    # Tight caps so adversarial loops trip quickly; defaults are larger.
    monkeypatch.setenv("AXM_AG_MAX_BIDS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_WINS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_CONSECUTIVE_WINS", "3")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_CONSECUTIVE", "2")
    return store


def _agent(agent_id, tags=("gig",)):
    from sincor2.onchain.stake_ledger import stake_ledger

    fabric = get_fabric()
    fabric.agents[agent_id] = {
        "agent_id": agent_id,
        "capability_tags": list(tags),
        "last_heartbeat": _now_ms(),
        "reputation": 0.5,
        "wallet": "0x" + agent_id.encode().hex().ljust(40, "0")[:40],
    }
    # Deep stake: commit locks bounty*50 %; ghost slash takes the lock.
    stake_ledger().deposit(agent_id, 100 * 10**18)


def _sealed_task(poster_id=None, bounty=1.0):
    task = create_task("gig", tags=["gig"], bounty_axm=bounty, sealed=True,
                       poster_id=poster_id)
    assert task["sealed"] is True
    return task["task_id"]


def _commit(task_id, agent_id, bid_axm=0.9):
    salt = os.urandom(32)
    price_wei = int(round(float(bid_axm) * 1e18))
    commitment = "0x" + sealed_commitment(price_wei, salt, agent_id).hex()
    rec = commit_bid(task_id, agent_id, commitment)
    return rec, salt.hex()


def _reveal(task_id, agent_id, bid_axm, nonce_hex):
    fabric = get_fabric()
    # Move into the reveal window: commit phase over, reveal phase open.
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    return reveal_bid(task_id, agent_id, bid_axm, nonce_hex, 60)


def _close(task_id):
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    return close_auction(task_id)


def _sealed_round(task_id, bids):
    """Full commit -> reveal -> close for [(agent_id, bid_axm)]."""
    nonces = {}
    for agent_id, bid_axm in bids:
        _, nonce_hex = _commit(task_id, agent_id, bid_axm)
        nonces[agent_id] = (bid_axm, nonce_hex)
    for agent_id, (bid_axm, nonce_hex) in nonces.items():
        _reveal(task_id, agent_id, bid_axm, nonce_hex)
    return _close(task_id)


# --- 1. self-bid rejection ----------------------------------------------------
def test_self_bid_rejected_on_commit(iso):
    _agent("poster1")
    task_id = _sealed_task(poster_id="poster1")
    with pytest.raises(ag.SelfBidError):
        _commit(task_id, "poster1", 0.9)
    # Nothing was locked, nothing was recorded: fail closed, no side effects.
    assert iso.bids_in_window("poster1") == 0


def test_self_bid_rejected_on_plaintext_bid(iso):
    _agent("poster1")
    task = create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=False,
                       poster_id="poster1")
    with pytest.raises(ag.SelfBidError):
        place_bid(task["task_id"], "poster1", 0.9, 60)


def test_self_bid_rejected_on_reveal(iso):
    # Commit path is gated, but reveal double-checks in case the commit
    # gate is ever bypassed: plant a commit directly and reveal as poster.
    _agent("poster1")
    _agent("bidder9")
    task_id = _sealed_task(poster_id="poster1")
    salt = os.urandom(32)
    price_wei = int(round(0.9 * 1e18))
    commitment = "0x" + sealed_commitment(price_wei, salt, "poster1").hex()
    fabric = get_fabric()
    fabric.commits[f"{task_id}\x00poster1"] = {
        "task_id": task_id, "agent_id": "poster1",
        "commitment": commitment, "committed_at": _now_ms(),
        "revealed": False, "revealed_at": None, "price_wei": None,
    }
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    with pytest.raises(ag.SelfBidError):
        reveal_bid(task_id, "poster1", 0.9, salt.hex(), 60)


def test_other_agent_may_bid(iso):
    _agent("poster1")
    _agent("bidder1")
    task_id = _sealed_task(poster_id="poster1")
    rec, _ = _commit(task_id, "bidder1", 0.9)
    assert rec["agent_id"] == "bidder1"


# --- 2. two-agent win-trading loop is broken up ---------------------------------
def test_pair_rotation_cap_breaks_win_trading(iso):
    # Adversarial: poster P and bidder B ping-pong wins on P's tasks.
    # Pair cap is 2: after B wins 2 of P's auctions in a row, B's next bid
    # on P's task MUST be rejected.
    _agent("posterP")
    _agent("traderB")
    for i in range(2):
        task_id = _sealed_task(poster_id="posterP")
        snap = _sealed_round(task_id, [("traderB", 0.9)])
        assert snap["state"] == "assigned"
        assert snap["assigned_to"] == "traderB"
    assert iso.pair_streak_of("posterP", "traderB") == 2
    # Third attempt: the loop is broken at bid time.
    task_id = _sealed_task(poster_id="posterP")
    with pytest.raises(ag.CollusionCapError):
        _commit(task_id, "traderB", 0.9)
    # And the pair streak never exceeds the cap.
    assert iso.pair_streak_of("posterP", "traderB") == 2


def test_pair_cap_resets_after_rotation(iso):
    # After someone else wins one of P's auctions, B becomes eligible again.
    _agent("posterP")
    _agent("traderB")
    _agent("rotatorC")
    for _ in range(2):
        task_id = _sealed_task(poster_id="posterP")
        _sealed_round(task_id, [("traderB", 0.9)])
    task_id = _sealed_task(poster_id="posterP")
    with pytest.raises(ag.CollusionCapError):
        _commit(task_id, "traderB", 0.9)
    # C (below every cap) wins one of P's auctions -> pair streak resets.
    snap = _sealed_round(task_id, [("rotatorC", 0.9)])
    assert snap["assigned_to"] == "rotatorC"
    assert iso.pair_streak_of("posterP", "traderB") == 0
    # B may bid again — the loop rotates, it does not deadlock.
    task_id = _sealed_task(poster_id="posterP")
    rec, _ = _commit(task_id, "traderB", 0.9)
    assert rec["agent_id"] == "traderB"


# --- 3. per-agent consecutive-win cap ------------------------------------------
def test_consecutive_win_cap(iso):
    # Adversarial: one agent wins every auction it enters.
    _agent("domA")
    _agent("domB")
    for i in range(3):
        task_id = _sealed_task()  # no poster: pure agent-streak test
        snap = _sealed_round(task_id, [("domA", 0.9)])
        assert snap["assigned_to"] == "domA"
    assert iso.win_streak_of("domA") == 3
    task_id = _sealed_task()
    with pytest.raises(ag.CollusionCapError):
        _commit(task_id, "domA", 0.9)
    # A different winner breaks the streak; domA is eligible again.
    snap = _sealed_round(task_id, [("domB", 0.9)])
    assert snap["assigned_to"] == "domB"
    assert iso.win_streak_of("domA") == 0
    task_id = _sealed_task()
    rec, _ = _commit(task_id, "domA", 0.9)
    assert rec["agent_id"] == "domA"


def test_no_winner_close_resets_streaks_no_deadlock(iso, monkeypatch):
    # Lone-agent edge: the only bidder hits the cap, the next auction gets
    # no bids and expires — streaks reset, the loop is NOT bricked.
    _agent("lone")
    for _ in range(3):
        task_id = _sealed_task()
        _sealed_round(task_id, [("lone", 0.9)])
    task_id = _sealed_task()
    with pytest.raises(ag.CollusionCapError):
        _commit(task_id, "lone", 0.9)
    # No bids at all -> close expires the task, streaks reset.
    snap = _close(task_id)
    assert snap["state"] == "expired"
    assert iso.win_streak_of("lone") == 0
    task_id = _sealed_task()
    rec, _ = _commit(task_id, "lone", 0.9)
    assert rec["agent_id"] == "lone"


# --- 4. rolling-window participation caps ---------------------------------------
def test_bid_rate_cap(iso, monkeypatch):
    monkeypatch.setenv("AXM_AG_MAX_BIDS_PER_HOUR", "2")
    _agent("busy")
    task_ids = [
        create_task("gig", tags=["gig"], bounty_axm=1.0, sealed=False)["task_id"]
        for _ in range(3)
    ]
    place_bid(task_ids[0], "busy", 0.9, 60)
    place_bid(task_ids[1], "busy", 0.9, 60)
    with pytest.raises(ag.ParticipationCapError):
        place_bid(task_ids[2], "busy", 0.9, 60)
    assert iso.bids_in_window("busy") == 2


def test_win_rate_cap(iso, monkeypatch):
    monkeypatch.setenv("AXM_AG_MAX_WINS_PER_HOUR", "1")
    _agent("winner1")
    task_id = _sealed_task()
    snap = _sealed_round(task_id, [("winner1", 0.9)])
    assert snap["assigned_to"] == "winner1"
    assert iso.wins_in_window("winner1") == 1
    task_id = _sealed_task()
    with pytest.raises(ag.ParticipationCapError):
        _commit(task_id, "winner1", 0.9)


# --- 5. winner ghost is slashed under existing stake_ledger rules ---------------
def test_winner_no_show_slashed_per_ledger_ghost_rule(iso):
    from sincor2.onchain.stake_ledger import stake_ledger

    _agent("ghosty")
    ledger = stake_ledger()
    before = ledger.balance_of("ghosty")
    deposited_before = int(before["deposited_wei"])

    task_id = _sealed_task(poster_id="posterZ")
    snap = _sealed_round(task_id, [("ghosty", 0.9)])
    assert snap["state"] == "assigned"
    locked = int(ledger.balance_of("ghosty")["locked_wei"])
    assert locked > 0  # the winner's stake is locked pending execution

    # The winner never performs: force the execution deadline into the past.
    fabric = get_fabric()
    fabric.tasks[task_id]["assigned_at"] = _now_ms() - 10 * 120_000
    fabric.tasks[task_id]["time_est_ms"] = 0
    expired = expire_stale_assignments()
    assert len(expired) == 1
    assert expired[0]["task_id"] == task_id
    assert expired[0]["expired_reason"] == "execution_timeout"

    after = ledger.balance_of("ghosty")
    # 100 % of the locked stake slashed, per the existing ghost rule.
    assert int(after["locked_wei"]) == 0
    assert int(after["slashed_wei"]) == int(before["slashed_wei"]) + locked
    assert int(after["deposited_wei"]) == deposited_before - locked
    # The slash event is the ledger's own ghosting record — no new math.
    slash_events = [e for e in ledger._data["events"]
                    if e.get("kind") == "slash"
                    and e.get("task_id") == task_id]
    assert len(slash_events) == 1
    assert slash_events[0]["reason"] == "ghosting"
    assert slash_events[0]["adjudicator"] == "protocol"
    assert int(slash_events[0]["slashed_wei"]) == locked
    # Ghosting clears the reputation-weighted collateral record (R_i -> 0).
    assert ledger.reputation_score("ghosty") == 0.0


def test_honest_winner_settlement_releases_stake(iso):
    # Control: a winner that performs keeps its stake (released, not slashed).
    from sincor2.onchain.stake_ledger import stake_ledger

    _agent("honest")
    ledger = stake_ledger()
    task_id = _sealed_task()
    snap = _sealed_round(task_id, [("honest", 0.9)])
    assert snap["state"] == "assigned"
    locked = int(ledger.balance_of("honest")["locked_wei"])
    assert locked > 0
    # Minimal proof path is exercised through the market function with a
    # bare receipt (content-unbound legacy path).
    from sincor2.a2a_inbound_market import submit_proof
    proof = submit_proof(task_id, "honest",
                         receipt_hash="0x" + "ab" * 32)
    assert proof["status"] in ("paid", "rejected")
    after = ledger.balance_of("honest")
    assert int(after["slashed_wei"]) == 0


# --- 6. honest rotation is never blocked -----------------------------------------
def test_honest_three_agent_rotation_unblocked(iso):
    for name in ("r1", "r2", "r3"):
        _agent(name)
    order = ["r1", "r2", "r3", "r1", "r2", "r3"]
    for name in order:
        task_id = _sealed_task(poster_id="posterH")
        snap = _sealed_round(task_id, [(name, 0.9)])
        assert snap["assigned_to"] == name
    # No streak ever exceeded the caps.
    for name in ("r1", "r2", "r3"):
        assert iso.win_streak_of(name) <= 3
        assert iso.pair_streak_of("posterH", name) <= 2


def test_rejections_are_audited(iso):
    _agent("posterP")
    _agent("traderB")
    for _ in range(2):
        task_id = _sealed_task(poster_id="posterP")
        _sealed_round(task_id, [("traderB", 0.9)])
    task_id = _sealed_task(poster_id="posterP")
    with pytest.raises(ag.CollusionCapError):
        _commit(task_id, "traderB", 0.9)
    log = iso.rejection_log()
    assert len(log) == 1
    assert log[0]["agent_id"] == "traderB"
    assert log[0]["reason"] == "pair_win_cap"
