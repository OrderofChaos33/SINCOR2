"""Async task queue — 202 + poll, eager/thread backends, no Redis required."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def queue_mod(monkeypatch):
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "eager")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    from sincor2 import task_queue

    task_queue.reset_backend_cache()
    yield task_queue
    task_queue.reset_backend_cache()


def test_eager_enqueue_runs_inline(queue_mod):
    def handler(payload, job_id):
        return {"echo": payload.get("n"), "job": job_id}

    queue_mod.register_handler("test.echo", handler)
    job = queue_mod.enqueue("test.echo", {"n": 7})
    assert job.state == queue_mod.COMPLETED
    assert job.result["echo"] == 7
    assert job.result["job"] == job.id


def test_thread_enqueue_returns_before_finish(queue_mod, monkeypatch):
    import threading

    started = threading.Event()
    release = threading.Event()

    def handler(payload, job_id):
        started.set()
        release.wait(timeout=2)
        return {"ok": True}

    monkeypatch.setenv("SINCOR_TASK_QUEUE", "thread")
    queue_mod.reset_backend_cache()
    queue_mod.register_handler("test.block", handler)
    job = queue_mod.enqueue("test.block", {})
    assert job.state in (queue_mod.QUEUED, queue_mod.RUNNING)
    assert started.wait(timeout=2)
    still = queue_mod.get_job(job.id)
    assert still.state == queue_mod.RUNNING
    release.set()
    for _ in range(50):
        done = queue_mod.get_job(job.id)
        if done and done.state == queue_mod.COMPLETED:
            break
        import time
        time.sleep(0.02)
    assert queue_mod.get_job(job.id).state == queue_mod.COMPLETED


def test_accepted_payload_shape(queue_mod):
    queue_mod.register_handler("test.noop", lambda p, j: {"ok": True})
    job = queue_mod.enqueue("test.noop", {})
    body = queue_mod.accepted_payload(job)
    assert body["accepted"] is True
    assert body["task_id"] == job.id
    assert body["poll_url"] == f"/api/tasks/{job.id}"
    assert body["status"] in (queue_mod.COMPLETED, queue_mod.QUEUED, queue_mod.RUNNING)


def test_poll_routes(client, queue_mod):
    queue_mod.register_handler("test.poll", lambda p, j: {"v": 1})
    job = queue_mod.enqueue("test.poll", {})
    resp = client.get(f"/api/tasks/{job.id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task_id"] == job.id
    assert data["status"] == "completed"
    assert data["result"]["v"] == 1

    missing = client.get("/api/tasks/does-not-exist")
    assert missing.status_code == 404


def test_rest_a2a_send_returns_202(client):
    resp = client.post(
        "/api/a2a/tasks/send",
        json={
            "method": "message/send",
            "params": {
                "skillId": "lead-enrichment",
                "callerId": "queue-test-caller",
                "message": {
                    "role": "user",
                    "parts": [{"text": "Enrich Globex"}],
                },
            },
        },
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert data.get("accepted") is True
    assert data.get("task_id")
    task = data.get("result") or {}
    assert task.get("id") == data["task_id"]
    # Eager test env still settles the A2A task before return.
    state = (task.get("status") or {}).get("state")
    assert state in ("completed", "working", "submitted")


def test_jsonrpc_send_stays_http_200(client):
    resp = client.post(
        "/api/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "message/send",
            "params": {
                "skillId": "lead-enrichment",
                "callerId": "queue-jsonrpc-caller",
                "message": {"role": "user", "parts": [{"text": "ping"}]},
            },
        },
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("result", {}).get("id")


def test_chunk_text_yields_multiple_pieces():
    from sincor2.llm_stream import chunk_text

    text = "alpha bravo charlie delta echo foxtrot golf hotel india"
    pieces = list(chunk_text(text, size=12))
    assert len(pieces) >= 3
    assert " ".join(pieces) == text


def test_claude_client_exposes_stream_sync():
    from sincor2.cortecs_core import ClaudeClient

    assert hasattr(ClaudeClient, "stream_sync")


def test_queue_health_reports_backend(queue_mod):
    health = queue_mod.queue_health()
    assert health["backend"] in ("eager", "thread", "celery")
    assert "handlers" in health
