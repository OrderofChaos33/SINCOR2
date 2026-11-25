"""Perf fix for GET /v1/a2a/tasks (2026-11-24).

Production diagnosis (Railway logs): the list endpoint took 3-9 s per
call at board scale (~7.4k tasks). Two compounding causes:

* ``refresh_stale_listings`` — which runs on every list read — decided
  "has commits" per task with ``any(k.startswith(prefix) for k in
  fabric.commits)``: O(tasks x commits) string scans per call.
* The handler copied + sorted the whole board per request, so the
  liveness runner's page-through-the-board polling pattern paid
  full-board cost for every page, and the single sync gunicorn worker
  queued every other route behind those reads.

Fixes pinned here: the commit set is computed once per sweep; the
sorted board is served from a short-TTL snapshot cached on the fabric
(write requests and sweep changes invalidate it); ``limit``/``offset``
are accepted as slice aliases alongside ``page``/``per_page``.
"""
from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import refresh_stale_listings


@pytest.fixture
def board(tmp_path, monkeypatch):
    """Bare Flask app with an EMPTY board (boot seeds cleared) so
    totals are exact."""
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-key")
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks.clear()
        fabric.commits.clear()
    return app.test_client()


def _inject(n, *, state="open", skill="deal-scoring", tags=("deal-scoring",),
            bounty=2.5, base_created=None, sealed=False):
    """Insert n tasks directly into the fabric; returns their ids in
    insertion order (created_at ascending)."""
    now = _now_ms()
    base = base_created if base_created is not None else now - n * 1000
    fabric = get_fabric()
    ids = []
    with fabric.lock:
        for i in range(n):
            tid = f"tsk_perf{state}{i:04d}"
            fabric.tasks[tid] = {
                "task_id": tid,
                "skill": skill,
                "tags": list(tags),
                "bounty_axm": bounty,
                "title": f"perf {state} {i}",
                "description": "perf fixture",
                "state": state,
                "sealed": sealed,
                "created_at": base + i * 1000,
                "commit_deadline": now + 300_000,
                "reveal_deadline": now + 600_000,
                "listing_expires_at": None,
                "poster_id": "perf-poster",
            }
            ids.append(tid)
    return ids


# ---------------------------------------------------------------------------
# Pagination + filtering (behavior preserved, now snapshot-served)
# ---------------------------------------------------------------------------

def test_empty_board_reports_zero(board):
    body = board.get("/v1/a2a/tasks").get_json()
    assert body == {"tasks": [], "page": 1, "per_page": 20,
                    "total": 0, "pages": 1}


def test_pagination_slices_exact_disjoint_newest_first(board):
    ids = _inject(15)
    seen = []
    for page in (1, 2, 3):
        body = board.get("/v1/a2a/tasks",
                         query_string={"page": page, "per_page": 5}).get_json()
        assert (body["page"], body["per_page"], body["total"],
                body["pages"]) == (page, 5, 15, 3)
        assert len(body["tasks"]) == 5
        seen.extend(t["task_id"] for t in body["tasks"])
    # disjoint + complete + newest first (reverse insertion order)
    assert len(set(seen)) == 15
    assert seen == list(reversed(ids))


def test_state_and_tag_filters(board):
    _inject(4, state="open")
    _inject(3, state="settled", skill="cashflow-recovery",
            tags=("cashflow-recovery", "finance"))
    _inject(2, state="expired")
    body = board.get("/v1/a2a/tasks",
                     query_string={"state": "open"}).get_json()
    assert body["total"] == 4
    assert all(t["state"] == "open" for t in body["tasks"])
    body = board.get("/v1/a2a/tasks",
                     query_string={"state": "open,expired"}).get_json()
    assert body["total"] == 6
    body = board.get("/v1/a2a/tasks",
                     query_string={"tags": "finance"}).get_json()
    assert body["total"] == 3
    body = board.get("/v1/a2a/tasks",
                     query_string={"min_bounty": 3.0}).get_json()
    assert body["total"] == 0


def test_limit_offset_alias_matches_page_slice(board):
    ids = _inject(15)
    by_page = board.get(
        "/v1/a2a/tasks",
        query_string={"page": 2, "per_page": 5}).get_json()
    by_offset = board.get(
        "/v1/a2a/tasks",
        query_string={"offset": 5, "limit": 5}).get_json()
    assert [t["task_id"] for t in by_offset["tasks"]] == \
        [t["task_id"] for t in by_page["tasks"]]
    assert by_offset["page"] == 2 and by_offset["per_page"] == 5
    assert by_offset["total"] == 15 and by_offset["pages"] == 3
    # limit clamps to the same max as per_page
    body = board.get("/v1/a2a/tasks",
                     query_string={"limit": 500}).get_json()
    assert body["per_page"] == 100
    assert len(body["tasks"]) == 15
    # bad alias values are a 400, same as bad page/per_page
    assert board.get("/v1/a2a/tasks",
                     query_string={"offset": "x"}).status_code == 400
    assert ids  # silence unused warning


def test_response_shape_small_board(board):
    """The payload shape is byte-identical in structure to the pre-fix
    endpoint: exactly the five envelope keys, and task entries are the
    stored task dicts verbatim, newest first."""
    ids = _inject(3)
    fabric = get_fabric()
    with fabric.lock:
        stored = [dict(fabric.tasks[t]) for t in ids]
    body = board.get("/v1/a2a/tasks").get_json()
    assert set(body.keys()) == {"tasks", "page", "per_page", "total", "pages"}
    assert body["tasks"] == list(reversed(stored))


# ---------------------------------------------------------------------------
# Snapshot cache: hit, write invalidation, TTL=0 bypass
# ---------------------------------------------------------------------------

def test_snapshot_cache_hit_then_write_invalidation(board, monkeypatch):
    monkeypatch.setenv("SINCOR_A2A_TASKS_CACHE_TTL_S", "60")
    _inject(3)
    body = board.get("/v1/a2a/tasks").get_json()
    assert body["total"] == 3
    fabric = get_fabric()
    assert getattr(fabric, "_tasks_list_snapshot", None) is not None

    # Direct in-process mutation (no HTTP write, no sweep change):
    # the cached snapshot keeps serving until TTL/invalidation.
    _inject(2, state="settled")
    body = board.get("/v1/a2a/tasks").get_json()
    assert body["total"] == 3

    # An HTTP write through the blueprint invalidates immediately.
    r = board.post("/v1/a2a/tasks",
                   json={"skill": "deal-scoring", "bounty_axm": 1.0})
    assert r.status_code == 201, r.get_json()
    body = board.get("/v1/a2a/tasks").get_json()
    assert body["total"] == 6  # 3 + 2 injected + 1 posted


def test_snapshot_cache_disabled_at_ttl_zero(board, monkeypatch):
    monkeypatch.setenv("SINCOR_A2A_TASKS_CACHE_TTL_S", "0")
    _inject(3)
    assert board.get("/v1/a2a/tasks").get_json()["total"] == 3
    _inject(2, state="settled")
    assert board.get("/v1/a2a/tasks").get_json()["total"] == 5


def test_sweep_change_invalidates_snapshot(board, monkeypatch):
    """A listing revived by refresh_stale_listings must show its new
    deadlines on the very next list read, cache or no cache."""
    monkeypatch.setenv("SINCOR_A2A_TASKS_CACHE_TTL_S", "60")
    r = board.post("/v1/a2a/tasks", json={
        "skill": "deal-scoring", "tags": ["deal-scoring"],
        "bounty_axm": 1.0, "sealed": True})
    assert r.status_code == 201, r.get_json()
    task_id = r.get_json()["task_id"]
    assert board.get("/v1/a2a/tasks").get_json()["total"] == 1  # snapshot built

    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["reveal_deadline"] = 1
        fabric.tasks[task_id]["commit_deadline"] = 1
    body = board.get("/v1/a2a/tasks").get_json()
    (task,) = body["tasks"]
    assert task["task_id"] == task_id
    assert task["commit_deadline"] > _now_ms()  # re-anchored by the sweep


# ---------------------------------------------------------------------------
# Commit-set sweep semantics (the O(tasks x commits) fix)
# ---------------------------------------------------------------------------

def test_refresh_commit_set_semantics(board):
    """With a large commit keyspace: a stale sealed task WITH a commit
    is left on the close path; one WITHOUT is re-anchored."""
    fabric = get_fabric()
    # 2,000 commit keys belonging to other tasks — the old code scanned
    # all of them once per task on every sweep.
    with fabric.lock:
        for j in range(2000):
            fabric.commits[f"tsk_other{j % 50:03d}\x00agent-{j}"] = \
                {"commitment": "0x" + "ab" * 32}

    r = board.post("/v1/a2a/tasks", json={
        "skill": "deal-scoring", "bounty_axm": 1.0, "sealed": True})
    with_commit = r.get_json()["task_id"]
    r = board.post("/v1/a2a/tasks", json={
        "skill": "deal-scoring", "bounty_axm": 1.0, "sealed": True})
    without_commit = r.get_json()["task_id"]

    with fabric.lock:
        fabric.commits[f"{with_commit}\x00agent-x"] = \
            {"commitment": "0x" + "cd" * 32}
        for tid in (with_commit, without_commit):
            fabric.tasks[tid]["reveal_deadline"] = 1
            fabric.tasks[tid]["commit_deadline"] = 1

    changed = {t["task_id"] for t in refresh_stale_listings()}
    assert without_commit in changed
    assert with_commit not in changed
    assert fabric.tasks[without_commit]["commit_deadline"] > _now_ms()
    assert fabric.tasks[with_commit]["commit_deadline"] == 1
