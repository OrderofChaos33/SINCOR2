"""Hunt-15 W-78: 50 AXM absolute aggregate open-escrow cap per agent.

A fully veteran agent could hold UNLIMITED simultaneous winning escrows at
~10 % effective collateral.  The cap binds at WIN / escrow-open time
(close_auction), never at bid commit: the first ranking candidate whose win
keeps aggregate open exposure within MAX_AGENT_OPEN_ESCROW_AXM takes the
auction; over-cap candidates are skipped (become ordinary losers); if none
qualifies the close fails closed (task stays open, close_blocked_reason set).
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import (
    MAX_AGENT_OPEN_ESCROW_AXM,
    close_auction,
    open_escrow_exposure_axm,
)
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

ONE_AXM = 10**18


@pytest.fixture
def env(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _mk_agent(agent_id, stake_axm=1000):
    get_fabric().agents[agent_id] = {"agent_id": agent_id, "reputation": 1.0}
    if stake_axm:
        stake_ledger().deposit(agent_id, int(stake_axm * ONE_AXM))


def _open_win(agent_id, task_id, bid_axm, sealed=True, state="assigned"):
    """Simulate an open winning escrow: assigned task + held stake lock."""
    get_fabric().tasks[task_id] = {
        "task_id": task_id,
        "state": state,
        "assigned_to": agent_id,
        "winning_bid_axm": bid_axm,
        "bounty_axm": bid_axm,
        "sealed": sealed,
    }
    if sealed:
        stake_ledger().lock_for_commit(agent_id, task_id, int(bid_axm * ONE_AXM))


def _auction(task_id, bids):
    """Open sealed auction with revealed bids.  bids: [(agent, bid_axm, score)].

    Each bidder also holds a commit-phase stake lock, mirroring production
    (lock_for_commit runs at commit time, before close).
    """
    fabric = get_fabric()
    fabric.tasks[task_id] = {
        "task_id": task_id,
        "state": "auction",
        "sealed": True,
        "reveal_deadline": _now_ms() - 1000,
        "tags": [],
    }
    for i, (agent_id, bid_axm, score) in enumerate(bids):
        bid_id = f"bid_{task_id}_{i}"
        fabric.bids[bid_id] = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": bid_axm,
            "score": score,
            "received_at": i,
            "revealed": True,
        }
        stake_ledger().lock_for_commit(agent_id, task_id, int(bid_axm * ONE_AXM))


def _settle(agent_id, task_id):
    """Simulate successful settlement: lock released, task settled."""
    stake_ledger().release(agent_id, task_id)
    get_fabric().tasks[task_id]["state"] = "settled"


def test_cap_value_is_50_axm():
    assert MAX_AGENT_OPEN_ESCROW_AXM == 50.0


def test_at_cap_agent_skipped_next_wins(env):
    _mk_agent("vet")
    _mk_agent("runner")
    _open_win("vet", "t-cap-1", 50.0)  # vet at exactly the cap
    assert open_escrow_exposure_axm("vet") == 50.0

    _auction("t-new", [("vet", 5.0, 99.0), ("runner", 6.0, 90.0)])
    snap = close_auction("t-new")
    # vet is the better bid but over cap -> skipped; runner wins.
    assert snap["state"] == "assigned"
    assert snap["assigned_to"] == "runner"
    assert snap.get("close_blocked_reason") is None


def test_all_over_cap_blocks_close_cleanly(env):
    _mk_agent("vet")
    _open_win("vet", "t-cap-2", 50.0)

    _auction("t-solo", [("vet", 5.0, 99.0)])
    snap = close_auction("t-solo")
    # Fail closed: no assignment, task stays open, reason recorded.
    assert snap["state"] == "auction"
    assert snap.get("assigned_to") is None
    assert snap["close_blocked_reason"] == "escrow_cap"
    # Nothing was settled on the blocked close: the prior escrow is intact.
    assert open_escrow_exposure_axm("vet") == 50.0
    assert get_fabric().tasks["t-cap-2"]["state"] == "assigned"


def test_just_under_cap_wins_1_axm_not_2(env):
    _mk_agent("vet")
    _mk_agent("runner")
    _open_win("vet", "t-cap-3", 49.0)
    assert open_escrow_exposure_axm("vet") == 49.0

    # 49 + 1 = 50 <= cap -> vet wins.
    _auction("t-a", [("vet", 1.0, 99.0), ("runner", 2.0, 90.0)])
    snap = close_auction("t-a")
    assert snap["assigned_to"] == "vet"
    assert open_escrow_exposure_axm("vet") == 50.0

    # Now at cap: a 2 AXM win would exceed -> runner wins instead.
    _auction("t-b", [("vet", 2.0, 99.0), ("runner", 3.0, 90.0)])
    snap = close_auction("t-b")
    assert snap["assigned_to"] == "runner"


def test_settlement_frees_capacity(env):
    _mk_agent("vet")
    _open_win("vet", "t-cap-4", 50.0)
    _auction("t-wait", [("vet", 5.0, 99.0)])
    blocked = close_auction("t-wait")
    assert blocked["close_blocked_reason"] == "escrow_cap"

    # Settlement releases the escrow -> capacity frees -> close succeeds.
    _settle("vet", "t-cap-4")
    assert open_escrow_exposure_axm("vet") == 0.0
    snap = close_auction("t-wait")
    assert snap["state"] == "assigned"
    assert snap["assigned_to"] == "vet"
    assert snap.get("close_blocked_reason") is None
    assert open_escrow_exposure_axm("vet") == 5.0


def test_cap_ignores_committed_but_unwon_bids(env):
    # Commits lock stake but are not wins: they must not count as exposure.
    _mk_agent("vet")
    for i in range(4):
        stake_ledger().lock_for_commit("vet", f"t-commit-{i}", int(25 * ONE_AXM))
    assert open_escrow_exposure_axm("vet") == 0.0

    _auction("t-free", [("vet", 5.0, 99.0)])
    snap = close_auction("t-free")
    assert snap["assigned_to"] == "vet"


def test_failed_task_counts_while_lock_held(env):
    _mk_agent("vet")
    _open_win("vet", "t-failed", 10.0, state="failed")
    assert open_escrow_exposure_axm("vet") == 10.0

    # Adjudication consumes the lock -> the dead escrow drops out.
    stake_ledger().release("vet", "t-failed")
    assert open_escrow_exposure_axm("vet") == 0.0


def test_settled_and_expired_tasks_do_not_count(env):
    _mk_agent("vet")
    _open_win("vet", "t-settled", 20.0)
    _settle("vet", "t-settled")
    _open_win("vet", "t-expired", 20.0, state="expired")
    assert open_escrow_exposure_axm("vet") == 0.0


def test_plaintext_win_counts_without_ledger_lock(env):
    # Legacy plaintext path never locks stake; the assignment is the escrow.
    _mk_agent("vet")
    _open_win("vet", "t-plain", 30.0, sealed=False)
    assert open_escrow_exposure_axm("vet") == 30.0


def test_exposure_monitoring_endpoint(env):
    _mk_agent("vet")
    _open_win("vet", "t-mon-1", 20.0)
    _open_win("vet", "t-mon-2", 15.0)

    r = env.get("/v1/a2a/escrow-exposure/vet")
    assert r.status_code == 200
    body = r.get_json()
    assert body["agent_id"] == "vet"
    assert body["open_exposure_axm"] == 35.0
    assert body["cap_axm"] == 50.0
    assert body["headroom_axm"] == 15.0
    assert {e["task_id"] for e in body["open_escrows"]} == {"t-mon-1", "t-mon-2"}

    r = env.get("/v1/a2a/escrow-exposure/nobody")
    assert r.status_code == 404
