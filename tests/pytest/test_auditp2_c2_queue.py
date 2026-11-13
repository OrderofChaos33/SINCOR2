"""P2 audit Batch C — C2: queue config / job state durability.

* Broker unreachable at startup -> boot fails fast with a clear message.
* Enqueue failure -> retriable QueueEnqueueError, NO silent inline/thread
  fallback execution.
* Celery backend -> job state lives in the shared (Redis) store, visible
  from a second process.
* No broker -> explicit, loudly-logged degraded mode (never silent).
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import sys
import time
import uuid

import pytest


@pytest.fixture
def tq(monkeypatch):
    """Fresh task_queue module state per test."""
    for key in ("SINCOR_TASK_QUEUE", "REDIS_URL", "REDIS_PRIVATE_URL",
                "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    from sincor2 import task_queue

    task_queue.reset_backend_cache()
    task_queue.reset_job_store()
    yield task_queue
    task_queue.reset_backend_cache()
    task_queue.reset_job_store()


# ---------------------------------------------------------------------------
# Readiness gate
# ---------------------------------------------------------------------------

def test_gate_fails_fast_when_configured_broker_unreachable(tq, monkeypatch):
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "celery")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:9/0")
    monkeypatch.setattr("sincor2.celery_app.ping_broker", lambda *a, **k: False)
    with pytest.raises(tq.QueueConfigError, match="unreachable"):
        tq.verify_queue_readiness()


def test_gate_fails_fast_when_celery_forced_without_broker_url(tq, monkeypatch):
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "celery")
    with pytest.raises(tq.QueueConfigError, match="no broker URL"):
        tq.verify_queue_readiness()


def test_gate_fails_fast_against_real_unreachable_broker(tq, monkeypatch):
    # No monkeypatched ping: exercises the real connection attempt path.
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "celery")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://127.0.0.1:9/0")
    with pytest.raises(tq.QueueConfigError, match="half-working queue"):
        tq.verify_queue_readiness()


def test_gate_passes_eager(tq, monkeypatch):
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "eager")
    report = tq.verify_queue_readiness()
    assert report["ready"] is True
    assert report["degraded"] is False


def test_no_broker_is_explicit_degraded_mode(tq, monkeypatch, caplog):
    import logging

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    report = tq.describe_backend()
    assert report["backend"] == "thread"
    assert report["degraded"] is True
    assert "no broker" in report["degraded_reason"].lower()
    with caplog.at_level(logging.WARNING, logger="sincor.queue"):
        ready = tq.verify_queue_readiness()
    assert ready["ready"] is True and ready["degraded"] is True
    assert any("DEGRADED" in r.message for r in caplog.records), \
        "degraded mode must be loudly logged, never silent"


def test_queue_health_exposes_degraded(tq, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    health = tq.queue_health()
    assert health["degraded"] is True
    assert health["job_store"] == "memory"


# ---------------------------------------------------------------------------
# Enqueue failure: retriable error, never silent inline execution
# ---------------------------------------------------------------------------

def test_thread_enqueue_failure_raises_retryable_no_inline(tq, monkeypatch):
    ran = []
    tq.register_handler("c2.nope", lambda p, j: ran.append(j) or {"ok": True})
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "thread")

    def _boom_pool():
        raise RuntimeError("pool exploded")

    monkeypatch.setattr(tq, "_thread_pool", _boom_pool)
    with pytest.raises(tq.QueueEnqueueError) as ei:
        tq.enqueue("c2.nope", {})
    assert ei.value.retryable is True
    assert ei.value.retry_after > 0
    assert ran == [], "handler must NOT run when enqueue fails"
    job = tq.list_jobs(1)[0]
    assert job.state == tq.FAILED
    assert "enqueue_failed" in (job.error or "")


def test_celery_enqueue_failure_no_thread_fallback(tq, monkeypatch):
    """Broker accepts the config but .delay() blows up -> no thread fallback."""
    ran = []
    tq.register_handler("c2.nope2", lambda p, j: ran.append(j) or {"ok": True})
    _install_fake_redis(monkeypatch)
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "celery")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://fake-broker:6379/0")
    monkeypatch.setattr("sincor2.celery_app.ping_broker", lambda *a, **k: True)

    import sincor2.async_tasks as async_tasks

    class _FakeDelay:
        @staticmethod
        def delay(job_id):
            raise RuntimeError("broker went away mid-delay")

    monkeypatch.setattr(async_tasks, "execute_job", _FakeDelay)

    def _must_not_run_pool():
        raise AssertionError("thread fallback must not run on celery failure")

    monkeypatch.setattr(tq, "_thread_pool", _must_not_run_pool)
    with pytest.raises(tq.QueueEnqueueError) as ei:
        tq.enqueue("c2.nope2", {})
    assert ei.value.retryable is True
    assert ran == []


def test_celery_backend_requires_shared_store(tq, monkeypatch):
    """Celery backend + unreachable Redis -> QueueConfigError, not memory."""
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "celery")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://fake-broker:6379/0")
    monkeypatch.setattr("sincor2.celery_app.ping_broker", lambda *a, **k: True)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:9/0")  # nothing listening
    with pytest.raises(tq.QueueConfigError, match="[Rr]edis"):
        tq.verify_queue_readiness()


# ---------------------------------------------------------------------------
# Shared storage: job visible from a second process
# ---------------------------------------------------------------------------

class _FakePipeline:
    def __init__(self, client):
        self._c = client
        self._ops = []

    def set(self, k, v, ex=None):
        self._ops.append(("set", k, v))
        return self

    def lpush(self, k, v):
        self._ops.append(("lpush", k, v))
        return self

    def ltrim(self, k, a, b):
        self._ops.append(("ltrim", k, a, b))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "set":
                self._c.set(op[1], op[2])
            elif op[0] == "lpush":
                self._c.lpush(op[1], op[2])
            elif op[0] == "ltrim":
                self._c.ltrim(op[1], op[2], op[3])
        self._ops = []


class _FakeRedisClient:
    """Minimal redis API over multiprocessing-managed containers."""

    def __init__(self, kv, lists):
        self._kv = kv
        self._lists = lists

    @classmethod
    def from_url(cls, url, decode_responses=True):
        return _FAKE_CLIENT[0]

    def get(self, k):
        return self._kv.get(k)

    def set(self, k, v, ex=None):
        self._kv[k] = v
        return True

    def pipeline(self):
        return _FakePipeline(self)

    def lpush(self, k, v):
        lst = self._lists.get(k, [])
        self._lists[k] = [v] + list(lst)
        return len(self._lists[k])

    def ltrim(self, k, a, b):
        self._lists[k] = list(self._lists.get(k, []))[a:b + 1]
        return True

    def lrange(self, k, a, b):
        return list(self._lists.get(k, []))[a:b + 1]


_FAKEREDIS_MODULE = None
_FAKE_CLIENT = [None]


def _install_fake_redis(monkeypatch=None):
    """Share one manager-backed fake redis client across processes (fork)."""
    import types

    global _FAKEREDIS_MODULE
    mgr = mp.Manager()
    _FAKE_CLIENT[0] = _FakeRedisClient(mgr.dict(), mgr.dict())
    mod = types.ModuleType("redis")
    mod.Redis = _FakeRedisClient
    if monkeypatch is not None:
        monkeypatch.setitem(sys.modules, "redis", mod)
    else:
        sys.modules["redis"] = mod
    _FAKEREDIS_MODULE = mod
    return mod


def _child_read_job(job_id, result_queue):
    # Fresh interpreter state for the store (proves the STORE is shared,
    # not just an inherited cached object).
    from sincor2 import task_queue

    task_queue.reset_job_store()
    task_queue.reset_backend_cache()
    job = task_queue.get_job(job_id)
    result_queue.put(job.to_dict() if job else None)


def test_job_state_visible_from_second_process(tq, monkeypatch):
    if not hasattr(mp, "get_context"):
        pytest.skip("multiprocessing unavailable")
    _install_fake_redis(monkeypatch)
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "celery")
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://fake-broker:6379/0")
    monkeypatch.setenv("REDIS_URL", "redis://fake-shared:6379/0")
    monkeypatch.setattr("sincor2.celery_app.ping_broker", lambda *a, **k: True)

    # The store itself must be the shared one on the celery path.
    assert isinstance(tq._job_store(), tq._RedisJobStore)

    # .delay() must not touch a real broker in this hermetic test.
    import sincor2.async_tasks as async_tasks

    class _FakeResult:
        id = "fake-celery-id"

    class _FakeDelay:
        @staticmethod
        def delay(job_id):
            return _FakeResult()

    monkeypatch.setattr(async_tasks, "execute_job", _FakeDelay)

    tq.register_handler("c2.echo", lambda p, j: {"echo": p.get("n")})
    job_id = "c2-shared-" + uuid.uuid4().hex
    job = tq.enqueue("c2.echo", {"n": 3}, job_id=job_id)
    assert job.state == tq.QUEUED

    ctx = mp.get_context("fork")
    rq = ctx.Queue()
    proc = ctx.Process(target=_child_read_job, args=(job_id, rq))
    proc.start()
    proc.join(timeout=30)
    assert proc.exitcode == 0
    seen = rq.get(timeout=10)
    assert seen is not None, "second process could not see the job: store is not shared"
    assert seen["id"] == job_id
    assert seen["state"] == tq.QUEUED
    assert seen["payload"] == {"n": 3}


# ---------------------------------------------------------------------------
# HTTP mapping: 503 + Retry-After, no inline execution
# ---------------------------------------------------------------------------

_TEST_KEY = "0x" + "b2" * 32


def _signed_send_params(caller_label: str):
    import time as _time

    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.from_key(_TEST_KEY)
    from sincor2.a2a_integration import _auth_create_message

    ts = int(_time.time())
    nonce = uuid.uuid4().hex
    message = _auth_create_message(caller_label, "lead-enrichment", ts, nonce)
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    return {
        "skillId": "lead-enrichment",
        "callerId": caller_label,
        "message": {"role": "user", "parts": [{"text": "Enrich Globex"}]},
        "ownerWallet": acct.address,
        "authSignature": "0x" + sig,
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def test_a2a_send_enqueue_failure_returns_503_retry_after(client, tq, monkeypatch):
    """Broker down on tasks/send -> HTTP 503 + Retry-After, nothing executed."""
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "thread")
    tq.reset_backend_cache()

    def _boom_pool():
        raise RuntimeError("broker is gone")

    monkeypatch.setattr(tq, "_thread_pool", _boom_pool)
    resp = client.post(
        "/api/a2a/tasks/send",
        json={"method": "message/send",
              "params": _signed_send_params("c2-503-caller")},
    )
    assert resp.status_code == 503, resp.get_data(as_text=True)[:500]
    assert resp.headers.get("Retry-After"), "Retry-After header required"
    body = resp.get_json()
    err = body.get("error") or {}
    assert err.get("code") == -32012
    assert (err.get("data") or {}).get("retryable") is True


def test_jsonrpc_send_enqueue_failure_surfaces_503(client, tq, monkeypatch):
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "thread")
    tq.reset_backend_cache()

    def _boom_pool():
        raise RuntimeError("broker is gone")

    monkeypatch.setattr(tq, "_thread_pool", _boom_pool)
    resp = client.post(
        "/api/a2a",
        json={"jsonrpc": "2.0", "id": 7, "method": "message/send",
              "params": _signed_send_params("c2-503-jsonrpc")},
    )
    assert resp.status_code == 503
    assert resp.headers.get("Retry-After")
    assert resp.get_json()["error"]["code"] == -32012


def test_queue_unavailable_response_shape():
    from flask import Flask

    from sincor2.a2a_integration import (
        _ERR_QUEUE_UNAVAILABLE,
        _err,
        _queue_unavailable_response,
    )

    app = Flask(__name__)
    payload = _err("queue_unavailable: down", code=_ERR_QUEUE_UNAVAILABLE,
                   data={"retryable": True, "retry_after_seconds": 45})
    with app.test_request_context():
        body, status, headers = _queue_unavailable_response(payload)
    assert status == 503
    assert headers["Retry-After"] == "45"
    assert body.get_json()["error"]["code"] == -32012
