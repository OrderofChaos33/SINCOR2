"""Task seed_key dedupe + admin task deletion.

Covers: create_task() returns the live task on seed_key collision (the
documented dedupe that was missing — the production duplicate-posting
incident), HTTP 201/200 semantics, admin-gated DELETE that releases pool
allocations and refuses tasks with bids.
"""
from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import create_task


@pytest.fixture
def client(tmp_path, monkeypatch):
    reset_fabric()
    monkeypatch.setenv("SINCOR_A2A_TASKS_PATH", str(tmp_path / "tasks.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_PATH", str(tmp_path / "pool.json"))
    monkeypatch.setenv("SINCOR_BOUNTY_POOL_ADMIN_KEY", "test-admin-key")
    monkeypatch.setenv("SINCOR_LAUNCH_BOUNTY_AXM", "100")
    from sincor2.a2a_bounty_pool import reset_bounty_pool

    reset_bounty_pool(path=str(tmp_path / "pool.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _mk(seed_key="p01-dedupe-probe", **kw):
    args = dict(skill="probe", bounty_axm=1.0, seed_key=seed_key,
                title="Probe", description="dedupe probe")
    args.update(kw)
    return create_task(**args)


def test_create_task_dedupes_on_seed_key(client):
    first = _mk()
    second = _mk()
    assert first["task_id"] == second["task_id"]
    assert second.get("_dedupe_hit") is True
    matches = [t for t in get_fabric().tasks.values()
               if t.get("seed_key") == "p01-dedupe-probe"]
    assert len(matches) == 1


def test_create_task_distinct_seed_keys_make_distinct_tasks(client):
    a = _mk(seed_key="p01-one")
    b = _mk(seed_key="p01-two")
    assert a["task_id"] != b["task_id"]


def test_create_task_no_seed_key_never_dedupes(client):
    a = _mk(seed_key=None)
    b = _mk(seed_key=None)
    assert a["task_id"] != b["task_id"]


def test_http_post_seed_key_idempotent(client):
    body = {"skill": "probe", "bounty_axm": 1.0, "seed_key": "p02-http-probe",
            "title": "T", "description": "D"}
    r1 = client.post("/v1/a2a/tasks", json=body)
    r2 = client.post("/v1/a2a/tasks", json=body)
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r1.get_json()["task_id"] == r2.get_json()["task_id"]


def test_delete_requires_admin(client):
    task = _mk()
    assert client.delete(f"/v1/a2a/tasks/{task['task_id']}").status_code == 401
    assert task["task_id"] in get_fabric().tasks


def test_delete_releases_allocation_and_removes_task(client):
    hdr = {"X-Admin-Key": "test-admin-key"}
    fr = client.post("/v1/a2a/pool/fund", json={}, headers=hdr)
    assert fr.status_code == 200
    task = _mk()
    ra = client.post("/v1/a2a/pool/allocate",
                     json={"task_id": task["task_id"], "amount_axm": 5},
                     headers=hdr).get_json()
    assert ra["state"] == "allocated"
    r = client.delete(f"/v1/a2a/tasks/{task['task_id']}", headers=hdr)
    assert r.status_code == 200
    assert r.get_json()["released_allocations"] == [ra["allocation_id"]]
    assert task["task_id"] not in get_fabric().tasks
    pool = client.get("/v1/a2a/pool").get_json()
    assert pool["allocated_axm"] == 0
    assert pool["available_axm"] == 100


def test_delete_refuses_task_with_bids(client):
    hdr = {"X-Admin-Key": "test-admin-key"}
    task = _mk(sealed=False)
    fabric = get_fabric()
    fabric.bids["bid_probe"] = {"bid_id": "bid_probe", "task_id": task["task_id"]}
    r = client.delete(f"/v1/a2a/tasks/{task['task_id']}", headers=hdr)
    assert r.status_code == 409
    assert task["task_id"] in fabric.tasks


def test_delete_unknown_task_404(client):
    hdr = {"X-Admin-Key": "test-admin-key"}
    assert client.delete("/v1/a2a/tasks/tsk_nope", headers=hdr).status_code == 404


def test_allocation_count_excludes_released(client):
    """allocation_count must track live allocations only.

    Regression: a retried allocate POST (the 2026-09-27 wave-1 incident)
    double-allocates the same task; after releasing the duplicate, the
    count, allocated_axm, and available_axm must all agree on the live set.
    """
    hdr = {"X-Admin-Key": "test-admin-key"}
    assert client.post("/v1/a2a/pool/fund", json={}, headers=hdr).status_code == 200
    task = _mk()
    a1 = client.post("/v1/a2a/pool/allocate",
                     json={"task_id": task["task_id"], "amount_axm": 5},
                     headers=hdr).get_json()
    a2 = client.post("/v1/a2a/pool/allocate",
                     json={"task_id": task["task_id"], "amount_axm": 5},
                     headers=hdr).get_json()
    assert a1["allocation_id"] != a2["allocation_id"]
    pool = client.get("/v1/a2a/pool").get_json()
    assert pool["allocation_count"] == 2
    rr = client.post("/v1/a2a/pool/release",
                     json={"allocation_id": a2["allocation_id"]},
                     headers=hdr)
    assert rr.status_code == 200
    pool = client.get("/v1/a2a/pool").get_json()
    assert pool["allocation_count"] == 1
    assert pool["allocated_axm"] == 5
    assert pool["available_axm"] == 95
