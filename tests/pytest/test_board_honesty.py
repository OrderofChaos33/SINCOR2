"""Board-honesty fixes from the 2026-11-20 outsider sweep.

N4 — the open board presented sealed tasks whose commit window had
     already closed as biddable (commit -> 403 "commit window closed").
     The board now carries a derived ``commit_window_open`` marker on
     the task list and detail payloads. Display-only: no timing rule,
     state transition, or anchoring behavior changes.
N11 — GET /v1/a2a/pool dumped the entire allocation ledger (~1.75 MB).
     The public route now returns the summary only; the in-process
     ``BountyPool.status()`` keeps the allocations list for its
     reconciliation/test consumers.
N12 — unknown /v1/* (and /api/*) routes returned the HTML 404 page.
     API namespaces now get the JSON error envelope; public pages keep
     the HTML 404.
"""
from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import commit_bid, create_task


@pytest.fixture
def board_client(tmp_path, monkeypatch):
    """Bare Flask app with the A2A blueprint mounted, file-backed
    persistence redirected to tmp (mirrors test_task_board)."""
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


def _admin_headers(key="test-admin-key"):
    return {"X-Admin-Key": key}


def _listed_task(client, task_id):
    body = client.get(
        "/v1/a2a/tasks", query_string={"per_page": 100}).get_json()
    for task in body["tasks"]:
        if task["task_id"] == task_id:
            return task
    raise AssertionError(f"task {task_id} not in board listing")


def _force_commit_window_closed(task_id):
    """Move a sealed task into the dead zone the sweep measured live:
    commit deadline passed, reveal deadline still ahead. The listing
    refresh only re-anchors zero-commit tasks past their REVEAL
    deadline, so this task survives refresh untouched."""
    fabric = get_fabric()
    now = _now_ms()
    with fabric.lock:
        task = fabric.tasks[task_id]
        task["commit_deadline"] = now - 1_000
        task["reveal_deadline"] = now + 60_000


# ---------------------------------------------------------------------------
# N4 — commit-window marker
# ---------------------------------------------------------------------------

def test_list_marks_commit_window_closed_task(board_client):
    task = create_task("deal-scoring", tags=["deal-scoring"],
                       bounty_axm=2.0, sealed=True)
    _force_commit_window_closed(task["task_id"])

    listed = _listed_task(board_client, task["task_id"])
    # Display-only fix: the row is still listed, still state "open" —
    # but it no longer presents as biddable.
    assert listed["state"] == "open"
    assert listed["commit_window_open"] is False


def test_list_marks_fresh_sealed_task_window_open(board_client):
    task = create_task("deal-scoring", tags=["deal-scoring"],
                       bounty_axm=2.0, sealed=True)
    listed = _listed_task(board_client, task["task_id"])
    assert listed["commit_window_open"] is True


def test_list_unsealed_task_has_no_commit_window(board_client):
    task = create_task("deal-scoring", tags=["deal-scoring"],
                       bounty_axm=2.0, sealed=False)
    listed = _listed_task(board_client, task["task_id"])
    assert listed["commit_window_open"] is None


def test_detail_marks_commit_window_closed(board_client):
    task = create_task("deal-scoring", tags=["deal-scoring"],
                       bounty_axm=2.0, sealed=True)
    _force_commit_window_closed(task["task_id"])

    r = board_client.get(f"/v1/a2a/tasks/{task['task_id']}")
    assert r.status_code == 200
    assert r.get_json()["commit_window_open"] is False


def test_marker_matches_commit_rejection(board_client):
    """The marker is honest: a task marked closed really does reject
    commits with the window error, and a fresh task gets past the
    window check (failing later, on agent checks, instead)."""
    task = create_task("deal-scoring", tags=["deal-scoring"],
                       bounty_axm=2.0, sealed=True)
    _force_commit_window_closed(task["task_id"])
    commitment = "0x" + "11" * 32
    with pytest.raises(PermissionError, match="commit window closed"):
        commit_bid(task["task_id"], "agent-nobody", commitment)

    fresh = create_task("deal-scoring", tags=["deal-scoring"],
                        bounty_axm=2.0, sealed=True)
    with pytest.raises(Exception) as excinfo:
        commit_bid(fresh["task_id"], "agent-nobody", commitment)
    assert "commit window closed" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# N11 — /pool summary payload
# ---------------------------------------------------------------------------

def test_pool_route_returns_summary_without_ledger_dump(board_client,
                                                         tmp_path,
                                                         monkeypatch):
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "100")
    from sincor2.a2a_bounty_pool import reset_bounty_pool
    # Rebuild the singleton now that the reserve env is set (the fixture
    # built it before the env existed).
    reset_bounty_pool(path=str(tmp_path / "pool.json"))

    r = board_client.post("/v1/a2a/pool/fund", json={},
                          headers=_admin_headers())
    assert r.status_code == 200, r.get_json()

    task = create_task("deal-scoring", tags=["deal-scoring"],
                       bounty_axm=2.0, sealed=False)
    r = board_client.post(
        "/v1/a2a/pool/allocate",
        json={"task_id": task["task_id"], "amount_axm": 30,
              "reason": "honesty test"},
        headers=_admin_headers())
    assert r.status_code == 201, r.get_json()

    r = board_client.get("/v1/a2a/pool")
    assert r.status_code == 200
    body = r.get_json()
    # The full allocation ledger no longer rides the public payload...
    assert "allocations" not in body
    # ...while every summary field existing consumers read survives.
    for field in ("reserve_axm", "funded_axm", "allocated_axm",
                  "available_axm", "spent_axm", "allocation_count",
                  "ledger"):
        assert field in body, field
    assert body["funded_axm"] == 100
    assert body["allocated_axm"] == 30
    assert body["available_axm"] == 70
    assert body["allocation_count"] == 1
    # Conservation: funded = allocated + available + spent.
    assert body["funded_axm"] == pytest.approx(
        body["allocated_axm"] + body["available_axm"] + body["spent_axm"])
    # Ledger verification (accountability probes read .ok) stays.
    assert body["ledger_verification"]["ok"] is True


def test_pool_status_in_process_keeps_allocations():
    """a2a_pool_reconciliation iterates status()["allocations"]; the
    slimming is HTTP-layer only and must not starve it."""
    from sincor2.a2a_bounty_pool import bounty_pool
    status = bounty_pool().status()
    assert "allocations" in status
    assert isinstance(status["allocations"], list)


# ---------------------------------------------------------------------------
# N12 — JSON 404s for API namespaces
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def prod_client():
    pytest.importorskip("flask")
    from sincor2.mvp_app import app

    app.config["TESTING"] = True
    return app.test_client()


def test_v1_unknown_route_returns_json_404(prod_client):
    r = prod_client.get("/v1/a2a/definitely-not-a-route")
    assert r.status_code == 404
    assert r.content_type.startswith("application/json")
    body = r.get_json()
    assert body["error"] == "Not found"
    assert body["status"] == 404


def test_api_unknown_route_returns_json_404(prod_client):
    r = prod_client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert r.content_type.startswith("application/json")
    assert r.get_json()["status"] == 404


def test_public_page_404_stays_html(prod_client):
    r = prod_client.get("/definitely-not-a-page-honesty")
    assert r.status_code == 404
    assert "text/html" in r.content_type
