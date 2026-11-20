"""Operator relist of expired marketplace tasks (Option A, 2026-11-20).

The expired pile (5,848 tasks at census) could not be revived: the only
revival path (refresh_stale_listings) covers standing seed listings, and
every expired task's pool allocation was already released at expiry —
flipping state in place would resurrect a task claiming pool backing
that no longer exists (settlement would fail or pay unbacked).

relist_task() is the safe inverse of expiry:
* state == "expired" only, with NO live pool allocation (verified
  against the pool ledger, never the task's own claim);
* anonymous-poster tasks refused (bids on them fail closed);
* P24 test listings refused; onchain-anchored tasks refused;
* lifecycle state reset to creation shape, prior bids/commits purged,
  sealed windows re-anchored, 24h listing TTL restarted;
* pool backing re-run through the create path — a FRESH allocation,
  poster-standing gate included; MAX_OPEN_TASKS enforced.

Route: POST /v1/a2a/tasks/<task_id>/relist, gated by the unified
admin credential (_require_pool_admin), same as the task delete.
"""
from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import MAX_OPEN_TASKS, _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import (
    COMMIT_WINDOW_MS,
    REVEAL_WINDOW_MS,
    RelistError,
    create_task,
    listing_ttl_ms,
    relist_task,
)
from sincor2.a2a_bounty_pool import bounty_pool, reset_bounty_pool
from sincor2.a2a_rate_limits import policy_for

ADMIN = {"X-Admin-Key": "test-admin-key"}
POSTER = "engine-poster"


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Bare Flask app + funded pool + an internal-origin (pool-eligible)
    poster, mirroring test_task_board's board_client."""
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-key")
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=20000)
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    fabric = get_fabric()
    with fabric.lock:
        fabric.agents[POSTER] = {"agent_id": POSTER, "origin": "internal"}
    return app.test_client()


def _expire(task_id, *, reason="auction_timeout", release=True, stale=True):
    """Drive a task into the expired state the way the lifecycle does:
    state + reason stamped, and (normally) its pool allocation released
    inline by the expiry path."""
    fabric = get_fabric()
    with fabric.lock:
        task = fabric.tasks[task_id]
        task["state"] = "expired"
        task["expired_reason"] = reason
        task["expired_at"] = _now_ms()
        if stale:
            # Residue of a completed round: winner, proof, payout, ghosts.
            task["assigned_to"] = "some-winner"
            task["assigned_at"] = _now_ms()
            task["winner_score"] = 0.91
            task["winning_bid_axm"] = 2.5
            task["time_est_ms"] = 60_000
            task["proof_id"] = "proof_stale"
            task["payout_axm"] = 2.5
            task["ghosted_commits"] = 2
    if release:
        pool = bounty_pool()
        for alloc in pool.allocations_for_task(task_id):
            pool.release(alloc["allocation_id"])


def _make_expired(*, skill="defi-metrics-sweep", bounty=3.0, poster=POSTER,
                  sealed=True, seed_key=None, release=True, stale=True):
    snap = create_task(skill, tags=[skill], bounty_axm=bounty, sealed=sealed,
                       poster_id=poster, seed_key=seed_key)
    _expire(snap["task_id"], release=release, stale=stale)
    return snap


def _open_count():
    fabric = get_fabric()
    with fabric.lock:
        return sum(1 for t in fabric.tasks.values()
                   if t.get("state") in ("open", "auction"))


# ---------------------------------------------------------------------------
# Happy path: state reset + re-anchor + TTL
# ---------------------------------------------------------------------------

def test_relist_resets_state_and_reanchors(board):
    snap = _make_expired()
    task_id = snap["task_id"]
    before = _now_ms()
    out = relist_task(task_id)
    after = _now_ms()

    assert out["state"] == "open"
    task = get_fabric().tasks[task_id]
    # Winner / assignment / proof / payout residue is gone.
    assert task["assigned_to"] is None
    assert task["winner_score"] is None
    assert task["winning_bid_axm"] is None
    assert task["time_est_ms"] is None
    assert task["proof_id"] is None
    assert task["payout_axm"] is None
    assert task["auction_closes_at"] is None
    assert "assigned_at" not in task
    assert "expired_reason" not in task
    assert "expired_at" not in task
    assert "ghosted_commits" not in task
    # Sealed windows re-anchored at now.
    assert before + COMMIT_WINDOW_MS <= task["commit_deadline"] <= after + COMMIT_WINDOW_MS
    assert task["reveal_deadline"] - task["commit_deadline"] == REVEAL_WINDOW_MS
    # Listing TTL restarted (24h default).
    assert before + listing_ttl_ms() <= task["listing_expires_at"] <= after + listing_ttl_ms()
    # Audit trail on the record.
    assert task["relist_count"] == 1
    assert before <= task["relisted_at"] <= after
    # Identity fields untouched.
    assert task["skill"] == "defi-metrics-sweep"
    assert task["bounty_axm"] == 3.0
    assert task["poster_id"] == POSTER
    assert task["sealed"] is True


def test_relist_unsealed_task_has_no_windows(board):
    snap = _make_expired(skill="lead-enrichment", sealed=False)
    out = relist_task(snap["task_id"])
    assert out["state"] == "open"
    assert out["commit_deadline"] is None
    assert out["reveal_deadline"] is None
    assert out["listing_expires_at"] > _now_ms()


def test_relist_clears_prior_round_bids_and_commits(board):
    snap = _make_expired()
    task_id = snap["task_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.bids["bid_stale"] = {
            "bid_id": "bid_stale", "task_id": task_id,
            "agent_id": "some-winner", "bid_axm": 2.5, "revealed": True,
            "received_at": 1, "score": 0.91,
        }
        fabric.commits[f"{task_id}\x00ghost-agent"] = {
            "agent_id": "ghost-agent", "revealed": False,
        }
    out = relist_task(task_id)
    with fabric.lock:
        assert "bid_stale" not in fabric.bids
        assert not any(k.startswith(f"{task_id}\x00") for k in fabric.commits)
    assert out["relist_cleared"] == {"bids": 1, "commits": 1}


# ---------------------------------------------------------------------------
# Pool backing: fresh allocation, never the released one
# ---------------------------------------------------------------------------

def test_relist_creates_fresh_allocation(board):
    snap = _make_expired(bounty=3.0)
    task_id = snap["task_id"]
    old_alloc = snap["pool_allocation_id"]
    assert old_alloc, "fixture task must start pool-backed"
    pool = bounty_pool()
    # _expire released the old allocation, as the real expiry paths do.
    assert pool._allocations[old_alloc]["state"] == "released"
    assert pool.allocations_for_task(task_id) == []
    assert pool.status()["allocated_axm"] == 0

    out = relist_task(task_id)

    assert out["pool_backed"] is True
    new_alloc = out["pool_allocation_id"]
    assert new_alloc and new_alloc != old_alloc
    live = pool.allocations_for_task(task_id)
    assert [a["allocation_id"] for a in live] == [new_alloc]
    assert live[0]["state"] == "allocated"
    assert live[0]["amount_axm"] == 3.0
    # The released allocation stays released — never resurrected.
    assert pool._allocations[old_alloc]["state"] == "released"
    assert pool.status()["allocated_axm"] == 3.0
    # Persisted record carries the fresh allocation (W-92 discipline).
    assert get_fabric().tasks[task_id]["pool_allocation_id"] == new_alloc


def test_relist_unbacked_when_poster_loses_standing(board):
    """Create-path semantics: backing is an upgrade, never a gate. A
    poster whose record vanished relists self-funded, with the reason
    recorded — and the dead allocation id is not left on the task."""
    snap = _make_expired()
    task_id = snap["task_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.agents.pop(POSTER, None)
    out = relist_task(task_id)
    assert out["state"] == "open"
    assert out["pool_backed"] is False
    assert out["pool_allocation_id"] is None
    assert out["pool_backing_skipped_reason"]
    assert bounty_pool().allocations_for_task(task_id) == []


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_double_relist_refused(board):
    snap = _make_expired()
    relist_task(snap["task_id"])
    with pytest.raises(RelistError):
        relist_task(snap["task_id"])
    assert get_fabric().tasks[snap["task_id"]]["state"] == "open"


def test_non_expired_task_refused(board):
    snap = create_task("deal-scoring", bounty_axm=1.0, poster_id=POSTER)
    with pytest.raises(RelistError):
        relist_task(snap["task_id"])


def test_unknown_task_raises_key_error(board):
    with pytest.raises(KeyError):
        relist_task("tsk_doesnotexist")


def test_live_allocation_refused(board):
    """An expired task still holding a live allocation means the expiry
    release failed — relisting would double-book the bounty."""
    snap = _make_expired(release=False)
    task_id = snap["task_id"]
    pool = bounty_pool()
    assert pool.allocations_for_task(task_id), "fixture must hold a live allocation"
    with pytest.raises(RelistError) as excinfo:
        relist_task(task_id)
    assert "allocation" in str(excinfo.value)
    task = get_fabric().tasks[task_id]
    assert task["state"] == "expired"
    # The live allocation is untouched.
    assert [a["allocation_id"] for a in pool.allocations_for_task(task_id)] == [
        snap["pool_allocation_id"]]


def test_anonymous_poster_refused(board):
    snap = _make_expired(poster=None)
    with pytest.raises(RelistError) as excinfo:
        relist_task(snap["task_id"])
    assert "anonymous" in str(excinfo.value)
    assert get_fabric().tasks[snap["task_id"]]["state"] == "expired"


def test_p24_skill_refused(board):
    snap = _make_expired(skill="p24-yield-probe")
    with pytest.raises(RelistError) as excinfo:
        relist_task(snap["task_id"])
    assert "P24" in str(excinfo.value)
    assert get_fabric().tasks[snap["task_id"]]["state"] == "expired"


def test_p24_seed_key_refused(board):
    snap = _make_expired(skill="deal-scoring", seed_key="p24-batch-7")
    with pytest.raises(RelistError):
        relist_task(snap["task_id"])
    assert get_fabric().tasks[snap["task_id"]]["state"] == "expired"


def test_onchain_anchored_refused(board):
    snap = _make_expired()
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[snap["task_id"]]["auction_id"] = 4242
    with pytest.raises(RelistError) as excinfo:
        relist_task(snap["task_id"])
    assert "onchain" in str(excinfo.value)
    assert get_fabric().tasks[snap["task_id"]]["state"] == "expired"


def test_cap_enforced(board):
    snap = _make_expired()
    # Fill the board to the cap with unbacked filler tasks.
    while _open_count() < MAX_OPEN_TASKS:
        create_task("deal-scoring", bounty_axm=1.0)
    assert _open_count() == MAX_OPEN_TASKS
    with pytest.raises(RelistError) as excinfo:
        relist_task(snap["task_id"])
    assert "open" in str(excinfo.value)
    assert get_fabric().tasks[snap["task_id"]]["state"] == "expired"


# ---------------------------------------------------------------------------
# Merit gate survives the relist
# ---------------------------------------------------------------------------

def test_merit_gated_task_relists_with_requires_merit(board):
    snap = _make_expired(skill="cashflow-recovery", bounty=7.5)
    assert snap["requires_merit"] is True
    out = relist_task(snap["task_id"])
    assert out["state"] == "open"
    assert out["requires_merit"] is True
    assert out["bounty_axm"] == 7.5


def test_sub_merit_task_relists_without_merit(board):
    snap = _make_expired(bounty=3.0)
    assert snap["requires_merit"] is False
    out = relist_task(snap["task_id"])
    assert out["requires_merit"] is False


# ---------------------------------------------------------------------------
# Route: admin gate + status mapping
# ---------------------------------------------------------------------------

def test_route_relist_ok(board):
    snap = _make_expired()
    r = board.post(f"/v1/a2a/tasks/{snap['task_id']}/relist", headers=ADMIN)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["task_id"] == snap["task_id"]
    assert body["state"] == "open"
    assert body["pool_backed"] is True


def test_route_admin_gate(board, monkeypatch):
    snap = _make_expired()
    url = f"/v1/a2a/tasks/{snap['task_id']}/relist"
    # No key and wrong key are both 401 while the admin surface is on.
    assert board.post(url).status_code == 401
    assert board.post(url, headers={"X-Admin-Key": "wrong"}).status_code == 401
    # Deny-by-default: unset ADMIN_PASSWORD disables the surface (503).
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    assert board.post(url, headers=ADMIN).status_code == 503
    # Nothing was relisted by any of the denied calls.
    assert get_fabric().tasks[snap["task_id"]]["state"] == "expired"


def test_route_unknown_task_404(board):
    r = board.post("/v1/a2a/tasks/tsk_nope/relist", headers=ADMIN)
    assert r.status_code == 404


def test_route_double_relist_409(board):
    snap = _make_expired()
    url = f"/v1/a2a/tasks/{snap['task_id']}/relist"
    assert board.post(url, headers=ADMIN).status_code == 200
    r = board.post(url, headers=ADMIN)
    assert r.status_code == 409
    assert r.get_json()["error"]


def test_rate_limit_policy_covers_relist_route():
    assert policy_for("POST", "/v1/a2a/tasks/<task_id>/relist") == "task_write"
