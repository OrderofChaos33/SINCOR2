"""Phase 1 — persistent task board + launch bounty pool.

Covers: JSON task persistence round-trip, GET /v1/a2a/tasks filters +
pagination, seed-on-empty-boot (no duplicate seeding), listing TTL +
stale-window refresh (the ~10-minute listing death fix), launch bounty pool
ledger (fund/allocate/release, default-zero, admin gate), and rate-limit
policy coverage for the new routes.
"""
from __future__ import annotations

import json
import os

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import (
    COMMIT_WINDOW_MS,
    REVEAL_WINDOW_MS,
    create_task,
    refresh_stale_listings,
    seed_probation_tasks,
)
from sincor2.a2a_rate_limits import policy_for


@pytest.fixture
def board_client(tmp_path, monkeypatch):
    """Bare Flask app with the A2A blueprint mounted and file-backed
    persistence redirected to tmp (persistence enabled via override)."""
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-key")
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _reboot():
    """Simulate a process restart: drop the fabric singleton so the next
    get_fabric() rebuilds from the persisted files."""
    import sincor2.a2a_inbound as inbound

    inbound._FABRIC = None
    return inbound.get_fabric()


def _seed_task_id(client):
    body = client.get("/v1/a2a/tasks", query_string={"per_page": 1}).get_json()
    assert body["total"] >= 1
    return body["tasks"][0]["task_id"]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def test_persistence_round_trip(board_client, tmp_path):
    """write -> reboot -> listed: a created task survives a fabric rebuild."""
    r = board_client.post(
        "/v1/a2a/tasks",
        json={"skill": "deal-scoring", "tags": ["deal-scoring"], "bounty_axm": 2.0},
    )
    assert r.status_code == 201, r.get_json()
    task_id = r.get_json()["task_id"]

    tasks_path = tmp_path / "tasks.json"
    assert tasks_path.is_file()
    raw = json.loads(tasks_path.read_text(encoding="utf-8"))
    assert "tasks" in raw
    assert any(t["task_id"] == task_id for t in raw["tasks"])

    fabric = _reboot()
    assert task_id in fabric.tasks

    body = board_client.get("/v1/a2a/tasks").get_json()
    assert body["total"] == 13  # 12 seeds + 1
    assert task_id in {t["task_id"] for t in body["tasks"]}


def test_persistence_disabled_without_override(monkeypatch):
    """Without SINCOR_A2A_TASKS_PATH in a test env, nothing is written."""
    from sincor2.a2a_inbound import _tasks_persist_enabled

    monkeypatch.delenv("SINCOR_A2A_TASKS_PATH", raising=False)
    assert _tasks_persist_enabled() is False


# ---------------------------------------------------------------------------
# List endpoint: filters + pagination
# ---------------------------------------------------------------------------

def test_list_filters_and_pagination(board_client):
    for skill, bounty, tags in [
        ("deal-scoring", 3.0, ["deal-scoring", "finance"]),
        ("lead-enrichment", 1.0, ["lead-enrichment"]),
        ("cashflow-recovery", 9.0, ["cashflow-recovery", "finance"]),
    ]:
        r = board_client.post(
            "/v1/a2a/tasks",
            json={"skill": skill, "tags": tags, "bounty_axm": bounty},
        )
        assert r.status_code == 201, r.get_json()

    # tag filter (any-match)
    body = board_client.get(
        "/v1/a2a/tasks", query_string={"tags": "finance"}).get_json()
    assert body["total"] == 2
    assert {t["skill"] for t in body["tasks"]} == {"deal-scoring", "cashflow-recovery"}

    # min_bounty filter: seeds >= 2.5 are (2.5, 3.0, 2.6); added (3.0, 9.0)
    body = board_client.get(
        "/v1/a2a/tasks", query_string={"min_bounty": 2.5}).get_json()
    assert all(t["bounty_axm"] >= 2.5 for t in body["tasks"])
    assert body["total"] == 5

    # state filter
    body = board_client.get(
        "/v1/a2a/tasks", query_string={"state": "open"}).get_json()
    assert body["total"] == 15
    assert all(t["state"] == "open" for t in body["tasks"])

    body = board_client.get(
        "/v1/a2a/tasks", query_string={"state": "settled"}).get_json()
    assert body["total"] == 0

    # pagination
    p1 = board_client.get(
        "/v1/a2a/tasks", query_string={"page": 1, "per_page": 5}).get_json()
    p2 = board_client.get(
        "/v1/a2a/tasks", query_string={"page": 2, "per_page": 5}).get_json()
    p3 = board_client.get(
        "/v1/a2a/tasks", query_string={"page": 3, "per_page": 5}).get_json()
    assert (p1["page"], p1["per_page"], p1["total"], p1["pages"]) == (1, 5, 15, 3)
    assert len(p1["tasks"]) == 5 and len(p2["tasks"]) == 5 and len(p3["tasks"]) == 5
    ids1 = {t["task_id"] for t in p1["tasks"]}
    ids2 = {t["task_id"] for t in p2["tasks"]}
    ids3 = {t["task_id"] for t in p3["tasks"]}
    assert not (ids1 & ids2) and not (ids1 & ids3) and not (ids2 & ids3)
    # newest first
    created = [t["created_at"] for t in p1["tasks"]]
    assert created == sorted(created, reverse=True)

    # per_page clamp
    body = board_client.get(
        "/v1/a2a/tasks", query_string={"per_page": 500}).get_json()
    assert body["per_page"] == 100

    # bad params
    assert board_client.get(
        "/v1/a2a/tasks", query_string={"min_bounty": "abc"}).status_code == 400
    assert board_client.get(
        "/v1/a2a/tasks", query_string={"page": "x"}).status_code == 400


def test_min_bounty_count_precise(board_client):
    """Pin the exact min_bounty result set: 12 seeds + 3 added tasks."""
    board_client.post("/v1/a2a/tasks",
                      json={"skill": "deal-scoring", "bounty_axm": 3.0})
    body = board_client.get(
        "/v1/a2a/tasks", query_string={"min_bounty": 2.5}).get_json()
    # seeds >= 2.5: 2.5, 3.0, 2.6 (local-business-site-builder x2? no:
    # 2.5, 3.0), detailing none, lead/outreach none, healthcare 2.6
    # -> (local-business-site-builder,2.5), (local-business-site-builder,3.0),
    #    (cashflow-recovery,2.6) = 3 seeds + 1 added = 4
    assert body["total"] == 4, [t["bounty_axm"] for t in body["tasks"]]


# ---------------------------------------------------------------------------
# Seeding: empty-only, no duplicates
# ---------------------------------------------------------------------------

def test_seed_on_empty_boot(tmp_path, monkeypatch):
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "empty.json"))
    seeded = seed_probation_tasks()
    assert len(seeded) == 12
    assert all(t["sealed"] is True for t in seeded)
    assert all(t["auto_refresh"] is True for t in seeded)
    assert all(t["state"] == "open" for t in seeded)
    assert len({t["seed_key"] for t in seeded}) == 12
    assert all(t["listing_expires_at"] and t["commit_deadline"]
               and t["reveal_deadline"] for t in seeded)


def test_no_duplicate_seeding_when_store_nonempty(board_client):
    before = len(get_fabric().tasks)
    assert before == 12
    seed_probation_tasks()
    assert len(get_fabric().tasks) == 12
    # operator-created tasks also suppress reseeding
    create_task("deal-scoring", bounty_axm=1.0)
    seed_probation_tasks()
    assert len(get_fabric().tasks) == 13


def test_seed_skips_when_restored_from_disk(board_client, tmp_path):
    """A reboot with a persisted non-empty store does not reseed."""
    assert len(get_fabric().tasks) == 12
    _reboot()
    assert len(get_fabric().tasks) == 12
    seed_probation_tasks()
    assert len(get_fabric().tasks) == 12


# ---------------------------------------------------------------------------
# TTL + refresh (the ~10-minute listing death fix)
# ---------------------------------------------------------------------------

def test_stale_sealed_listing_refreshes(tmp_path, monkeypatch):
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "t.json"))
    task = create_task("deal-scoring", tags=["deal-scoring"], bounty_axm=1.0,
                       sealed=True)
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task["task_id"]]["reveal_deadline"] = 1
        fabric.tasks[task["task_id"]]["commit_deadline"] = 1
    refreshed = refresh_stale_listings()
    assert any(t["task_id"] == task["task_id"] for t in refreshed)
    t = get_fabric().tasks[task["task_id"]]
    assert t["state"] == "open"
    assert t["commit_deadline"] > _now_ms()
    assert t["reveal_deadline"] - t["commit_deadline"] == REVEAL_WINDOW_MS


def test_commit_after_stale_window_refreshes_not_kills(board_client):
    """The bug: a commit arriving after the 10-min window used to expire the
    listing via close_auction. Now the listing refreshes and the commit lands."""
    from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

    reset_stake_ledger(path="/tmp/sincor-test-board-stake.json")
    r = board_client.post("/v1/a2a/register", json={
        "agent_id": "board-bidder",
        "capability_tags": ["deal-scoring"],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": "0x" + "ab" * 20,
    })
    assert r.status_code in (200, 201), r.get_json()
    assert board_client.post(
        "/v1/a2a/heartbeat", json={"agent_id": "board-bidder"}).status_code == 200
    stake_ledger().deposit("board-bidder", 10 * 10**18)

    r = board_client.post("/v1/a2a/tasks", json={
        "skill": "deal-scoring", "tags": ["deal-scoring"],
        "bounty_axm": 1.0, "sealed": True,
    })
    task_id = r.get_json()["task_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["reveal_deadline"] = 1
        fabric.tasks[task_id]["commit_deadline"] = 1

    r = board_client.post("/v1/a2a/bids/commit", json={
        "task_id": task_id,
        "agent_id": "board-bidder",
        "commitment": "0x" + "cd" * 32,
    })
    assert r.status_code == 201, r.get_json()
    assert get_fabric().tasks[task_id]["state"] in ("open", "auction")


def test_listing_ttl_expires_nonseed_task(tmp_path, monkeypatch):
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "t.json"))
    task = create_task("deal-scoring", bounty_axm=1.0)
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task["task_id"]]["listing_expires_at"] = 1
    refresh_stale_listings()
    t = get_fabric().tasks[task["task_id"]]
    assert t["state"] == "expired"
    assert t["expired_reason"] == "listing_ttl"


def test_seed_listing_ttl_extends_instead_of_expiring(tmp_path, monkeypatch):
    """Standing seed listings never die of listing TTL; it is extended."""
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "t.json"))
    seeded = seed_probation_tasks()
    task_id = seeded[0]["task_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["listing_expires_at"] = 1
    refresh_stale_listings()
    t = get_fabric().tasks[task_id]
    assert t["state"] == "open"
    assert t["listing_expires_at"] > _now_ms()


def test_seed_revived_after_permissionless_close(tmp_path, monkeypatch):
    """A permissionless close() that kills a zero-commit seed listing is
    undone by refresh: the standing listing comes back biddable."""
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "t.json"))
    seeded = seed_probation_tasks()
    task_id = seeded[0]["task_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["reveal_deadline"] = 1
        fabric.tasks[task_id]["commit_deadline"] = 1
    from sincor2.a2a_inbound_market import close_auction

    closed = close_auction(task_id)
    assert closed["state"] == "expired"
    refreshed = refresh_stale_listings()
    assert any(t["task_id"] == task_id for t in refreshed)
    assert get_fabric().tasks[task_id]["state"] == "open"


# ---------------------------------------------------------------------------
# Bounty pool
# ---------------------------------------------------------------------------

def _admin_headers(key="test-admin-key"):
    return {"X-Admin-Key": key}


def test_pool_default_zero(board_client):
    """No SINCOR_LAUNCH_BOUNTY_AXM configured: pool unconfigured, fund 503."""
    status = board_client.get("/v1/a2a/pool").get_json()
    assert status["reserve_axm"] == 0
    assert status["funded_axm"] == 0
    assert status["available_axm"] == 0
    assert status["ledger"] == "offchain-axm"

    r = board_client.post("/v1/a2a/pool/fund", json={},
                          headers=_admin_headers())
    assert r.status_code == 503, r.get_json()


def test_pool_admin_gate(board_client, monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    # deny-by-default: admin surface disabled without a key
    assert board_client.post("/v1/a2a/pool/fund", json={}).status_code == 503
    assert board_client.post(
        "/v1/a2a/pool/allocate",
        json={"task_id": "x", "amount_axm": 1}).status_code == 503
    assert board_client.post(
        "/v1/a2a/pool/release",
        json={"allocation_id": "x"}).status_code == 503
    # public status stays readable
    assert board_client.get("/v1/a2a/pool").status_code == 200

    monkeypatch.setenv("ADMIN_PASSWORD", "right-key")
    assert board_client.post(
        "/v1/a2a/pool/fund", json={}).status_code == 401  # no key sent
    assert board_client.post(
        "/v1/a2a/pool/fund", json={},
        headers=_admin_headers("wrong-key")).status_code == 401


def test_pool_fund_allocate_release(board_client, tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "100")
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_bounty_pool(path=str(tmp_path / "pool.json"))

    # fund the full reserve
    r = board_client.post("/v1/a2a/pool/fund", json={},
                          headers=_admin_headers())
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["funded_axm"] == 100
    assert r.get_json()["available_axm"] == 100

    # partial fund beyond remaining reserve is rejected
    r = board_client.post("/v1/a2a/pool/fund", json={"amount_axm": 1},
                          headers=_admin_headers())
    assert r.status_code == 400

    task_id = _seed_task_id(board_client)

    # allocate
    r = board_client.post("/v1/a2a/pool/allocate",
                          json={"task_id": task_id, "amount_axm": 30,
                                "reason": "launch bounty"},
                          headers=_admin_headers())
    assert r.status_code == 201, r.get_json()
    alloc = r.get_json()
    assert alloc["state"] == "allocated"
    assert alloc["amount_axm"] == 30
    alloc_id = alloc["allocation_id"]

    status = board_client.get("/v1/a2a/pool").get_json()
    assert status["allocated_axm"] == 30
    assert status["available_axm"] == 70

    # over-allocation rejected
    r = board_client.post("/v1/a2a/pool/allocate",
                          json={"task_id": task_id, "amount_axm": 80},
                          headers=_admin_headers())
    assert r.status_code == 400, r.get_json()

    # unknown task rejected
    r = board_client.post("/v1/a2a/pool/allocate",
                          json={"task_id": "tsk_nope", "amount_axm": 1},
                          headers=_admin_headers())
    assert r.status_code == 404

    # release returns funds
    r = board_client.post("/v1/a2a/pool/release",
                          json={"allocation_id": alloc_id},
                          headers=_admin_headers())
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["state"] == "released"
    assert board_client.get("/v1/a2a/pool").get_json()["available_axm"] == 100

    # double release rejected; unknown allocation 404
    assert board_client.post(
        "/v1/a2a/pool/release", json={"allocation_id": alloc_id},
        headers=_admin_headers()).status_code == 400
    assert board_client.post(
        "/v1/a2a/pool/release", json={"allocation_id": "alloc_nope"},
        headers=_admin_headers()).status_code == 404


def test_pool_persists_across_reboot(board_client, tmp_path, monkeypatch):
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "50")
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    pool_path = str(tmp_path / "pool.json")
    reset_bounty_pool(path=pool_path)  # fresh disk: auto-funds the reserve
    assert board_client.get("/v1/a2a/pool").get_json()["funded_axm"] == 50

    task_id = _seed_task_id(board_client)
    r = board_client.post("/v1/a2a/pool/allocate",
                          json={"task_id": task_id, "amount_axm": 20,
                                "reason": "reboot test"},
                          headers=_admin_headers())
    assert r.status_code == 201, r.get_json()

    reset_bounty_pool(path=pool_path)  # simulate restart: rebuild from file
    status = board_client.get("/v1/a2a/pool").get_json()
    assert status["funded_axm"] == 50
    assert status["allocated_axm"] == 20
    assert status["available_axm"] == 30


def test_pool_rehydrates_on_fresh_disk(board_client, tmp_path, monkeypatch):
    """A redeploy wiping the ephemeral ledger must not strand the pool:
    with a reserve configured and no ledger file, boot auto-funds."""
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "75")
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    pool_path = str(tmp_path / "pool.json")
    assert not os.path.exists(pool_path)
    reset_bounty_pool(path=pool_path)
    status = board_client.get("/v1/a2a/pool").get_json()
    assert status["reserve_axm"] == 75
    assert status["funded_axm"] == 75
    assert status["available_axm"] == 75

    # explicit fund with nothing remaining is idempotent, not an error
    r = board_client.post("/v1/a2a/pool/fund", json={},
                          headers=_admin_headers())
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["funded_axm"] == 75


def test_pool_no_rehydrate_without_reserve(board_client, tmp_path, monkeypatch):
    """Deny-by-default holds: no reserve configured, no auto-fund."""
    monkeypatch.delenv("SINCOR_LAUNCH_BOUNTY_AXM", raising=False)
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    status = board_client.get("/v1/a2a/pool").get_json()
    assert status["reserve_axm"] == 0
    assert status["funded_axm"] == 0


# ---------------------------------------------------------------------------
# Rate-limit policy coverage
# ---------------------------------------------------------------------------

def test_rate_limit_policy_covers_new_routes():
    assert policy_for("GET", "/v1/a2a/tasks") == "read"
    assert policy_for("GET", "/v1/a2a/pool") == "read"
    assert policy_for("POST", "/v1/a2a/pool/fund") == "bid"
    assert policy_for("POST", "/v1/a2a/pool/allocate") == "bid"
    assert policy_for("POST", "/v1/a2a/pool/release") == "bid"
