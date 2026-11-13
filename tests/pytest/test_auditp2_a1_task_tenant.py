"""P2-A1: task list/poll/result endpoints require authenticated tenant ownership.

- unauthenticated request -> 401
- authenticated but wrong tenant -> 403 on item, item excluded from list
- owner -> 200 with redacted diagnostics (no tracebacks, no internal paths)
- internal/legacy jobs with empty tenant -> never served (fail closed)
"""
from __future__ import annotations

import pytest


@pytest.fixture
def queue_mod(monkeypatch):
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "eager")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    from sincor2 import task_queue

    task_queue.reset_backend_cache()
    task_queue.register_handler("a1.noop", lambda p, j: {"ok": True})
    yield task_queue
    task_queue.reset_backend_cache()


def _bearer(app, identity: str) -> dict:
    with app.app_context():
        from flask_jwt_extended import create_access_token

        return {"Authorization": "Bearer " + create_access_token(identity=identity)}


def test_unauthenticated_poll_rejected(client, app, queue_mod):
    job = queue_mod.enqueue("a1.noop", {}, tenant_id="tenant-a")
    assert client.get("/api/tasks").status_code == 401
    assert client.get(f"/api/tasks/{job.id}").status_code == 401


def test_wrong_tenant_forbidden(client, app, queue_mod):
    job = queue_mod.enqueue("a1.noop", {}, tenant_id="tenant-a")
    other = _bearer(app, "tenant-b")

    item = client.get(f"/api/tasks/{job.id}", headers=other)
    assert item.status_code == 403

    listing = client.get("/api/tasks", headers=other)
    assert listing.status_code == 200
    ids = [t["task_id"] for t in listing.get_json()["tasks"]]
    assert job.id not in ids


def test_owner_sees_own_job(client, app, queue_mod):
    job = queue_mod.enqueue("a1.noop", {}, tenant_id="tenant-a")
    owner = _bearer(app, "tenant-a")

    listing = client.get("/api/tasks", headers=owner)
    assert listing.status_code == 200
    ids = [t["task_id"] for t in listing.get_json()["tasks"]]
    assert job.id in ids

    item = client.get(f"/api/tasks/{job.id}", headers=owner)
    assert item.status_code == 200
    body = item.get_json()
    assert body["task_id"] == job.id
    # ownership internals never leak into the public payload
    assert "tenant_id" not in body
    assert "payload" not in body
    assert "celery_id" not in body


def test_error_diagnostics_redacted(client, app, queue_mod):
    def _boom(payload, job_id):
        raise RuntimeError(
            "boom\nTraceback (most recent call last):\n"
            '  File "/app/secrets/config.py", line 42, in run\n'
            "ValueError: bad provider payload {'key': 'sk-live-123'}"
        )

    queue_mod.register_handler("a1.boom", _boom)
    with pytest.raises(RuntimeError):
        queue_mod.enqueue("a1.boom", {}, tenant_id="tenant-a")
    job = next(j for j in queue_mod.list_jobs(50) if j.kind == "a1.boom")

    owner = _bearer(app, "tenant-a")
    body = client.get(f"/api/tasks/{job.id}", headers=owner).get_json()
    err = body["error"]
    assert err == "boom"
    assert "Traceback" not in err
    assert "/app/" not in err
    assert "sk-live" not in err
    assert len(err) <= 280


def test_long_single_line_error_truncated(client, app, queue_mod):
    def _long(payload, job_id):
        raise RuntimeError("x" * 1000)

    queue_mod.register_handler("a1.long", _long)
    with pytest.raises(RuntimeError):
        queue_mod.enqueue("a1.long", {}, tenant_id="tenant-a")
    job = next(j for j in queue_mod.list_jobs(50) if j.kind == "a1.long")

    owner = _bearer(app, "tenant-a")
    body = client.get(f"/api/tasks/{job.id}", headers=owner).get_json()
    assert len(body["error"]) <= 280


def test_unstamped_job_fail_closed(client, app, queue_mod):
    # Internal/legacy jobs (empty tenant) are never served over the API.
    job = queue_mod.enqueue("a1.noop", {}, tenant_id="")
    owner = _bearer(app, "tenant-a")
    assert client.get(f"/api/tasks/{job.id}", headers=owner).status_code == 403
    listing = client.get("/api/tasks", headers=owner).get_json()["tasks"]
    assert job.id not in [t["task_id"] for t in listing]


def test_enqueue_derives_tenant_from_request_jwt(client, app, queue_mod):
    # Jobs enqueued inside an authenticated request are stamped automatically.
    from sincor2 import task_queue

    owner = _bearer(app, "tenant-auto")
    with app.test_request_context(
        "/api/tasks", headers=owner
    ):
        # Simulate what a JWT-authenticated enqueue call site sees.
        from flask_jwt_extended import verify_jwt_in_request

        verify_jwt_in_request()
        job = task_queue.enqueue("a1.noop", {})
    assert job.tenant_id == "tenant-auto"
