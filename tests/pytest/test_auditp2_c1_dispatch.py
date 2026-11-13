"""P2 audit Batch C — C1: vertical policy dispatch fails CLOSED.

A policy-executor exception must never fall through to an unenforced
``agent.run()``. The dispatch is recorded as indeterminate, the caller gets
an explicit operator-review error, and re-dispatch without an idempotency
key is rejected.
"""
from __future__ import annotations

import json
import logging

import pytest


class _FakeAgent:
    def __init__(self, name="test_skill_agent"):
        self.name = name
        self.tags = ["test-skill"]
        self.runs = []

    def run(self, task_payload):
        self.runs.append(task_payload)
        return {"status": "success", "ran": True}


@pytest.fixture
def platform():
    from sincor2.vertical_dispatch import _reset_dispatch_registry

    _reset_dispatch_registry()
    agent = _FakeAgent()
    yield {"vertical_agents": {"a1": agent}, "agent": agent}
    _reset_dispatch_registry()


def _payload(task_id=None, idempotency_key=None):
    p = {"task_type": "x", "payload": {}}
    if task_id:
        p["task_id"] = task_id
    if idempotency_key:
        p["idempotency_key"] = idempotency_key
    return json.dumps(p)


def test_executor_failure_never_runs_agent(platform, monkeypatch, caplog):
    """Policy executor raises -> agent.run NOT called, error returned, audit log."""
    from sincor2 import vertical_dispatch

    calls = []

    class BoomExecutor:
        def execute(self, handler, task):
            calls.append(task)
            raise RuntimeError("policy store exploded")

    monkeypatch.setattr(
        "core.policy.get_default_executor", lambda: BoomExecutor()
    )
    with caplog.at_level(logging.ERROR, logger="sincor.audit.vertical_dispatch"):
        out = vertical_dispatch.dispatch_vertical_task(
            "test-skill", _payload(task_id="t-fail-1"), platform,
            task_id="t-fail-1", caller_id="c1",
        )
    assert out is not None
    output, error = out
    assert output is None
    assert "policy_enforcement_failure" in error
    assert "operator review" in error
    # The handler (agent.run) was never invoked — executor raised first.
    assert platform["agent"].runs == []
    # Full-context audit log written.
    audit_records = [
        r for r in caplog.records
        if r.name == "sincor.audit.vertical_dispatch" and "policy_enforcement_failure" in r.message
    ]
    assert audit_records, "expected audit log entry for enforcement failure"
    rec = audit_records[0].message
    assert "test-skill" in rec and "t-fail-1" in rec and "c1" in rec
    assert "RuntimeError" in rec
    # Dispatch recorded as indeterminate.
    prior = vertical_dispatch._prior_dispatch("t-fail-1")
    assert prior["status"] == vertical_dispatch._DISPATCH_INDETERMINATE


def test_executor_success_normal_path(platform, monkeypatch):
    """Policy executor succeeds -> agent.run called exactly once via executor."""
    from sincor2 import vertical_dispatch

    class OkExecutor:
        def execute(self, handler, task):
            return handler(task)

    monkeypatch.setattr("core.policy.get_default_executor", lambda: OkExecutor())
    out = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-ok-1"), platform, task_id="t-ok-1"
    )
    assert out is not None
    output, error = out
    assert error is None
    assert json.loads(output)["status"] == "success"
    assert len(platform["agent"].runs) == 1
    prior = vertical_dispatch._prior_dispatch("t-ok-1")
    assert prior["status"] == vertical_dispatch._DISPATCH_COMPLETED


def test_double_dispatch_without_idempotency_key_rejected(platform, monkeypatch):
    """Second dispatch of the same task_id without a key -> rejected, no re-run."""
    from sincor2 import vertical_dispatch

    class OkExecutor:
        def execute(self, handler, task):
            return handler(task)

    monkeypatch.setattr("core.policy.get_default_executor", lambda: OkExecutor())
    first = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-dup-1"), platform, task_id="t-dup-1"
    )
    assert first[1] is None
    second = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-dup-1"), platform, task_id="t-dup-1"
    )
    assert second is not None
    output, error = second
    assert output is None
    assert "duplicate_dispatch_rejected" in error
    assert len(platform["agent"].runs) == 1  # never re-executed


def test_idempotent_replay_with_matching_key(platform, monkeypatch):
    """Matching idempotency key on a completed dispatch replays stored result."""
    from sincor2 import vertical_dispatch

    class OkExecutor:
        def execute(self, handler, task):
            return handler(task)

    monkeypatch.setattr("core.policy.get_default_executor", lambda: OkExecutor())
    first = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-idem-1", idempotency_key="k-1"),
        platform, task_id="t-idem-1",
    )
    second = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-idem-1", idempotency_key="k-1"),
        platform, task_id="t-idem-1",
    )
    assert second == first
    assert len(platform["agent"].runs) == 1

    # Wrong key -> rejected, not replayed.
    third = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-idem-1", idempotency_key="k-WRONG"),
        platform, task_id="t-idem-1",
    )
    assert "duplicate_dispatch_rejected" in third[1]
    assert len(platform["agent"].runs) == 1


def test_indeterminate_never_retried_blindly(platform, monkeypatch):
    """After an enforcement failure the effect may have run: no blind retry."""
    from sincor2 import vertical_dispatch

    calls = {"n": 0}

    class FlakyExecutor:
        def execute(self, handler, task):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("executor died mid-flight")
            return handler(task)

    monkeypatch.setattr("core.policy.get_default_executor", lambda: FlakyExecutor())
    first = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-ind-1"), platform, task_id="t-ind-1"
    )
    assert "policy_enforcement_failure" in first[1]
    # Even though the executor would succeed now, the retry is rejected —
    # the effect may already have run; operator review is required.
    second = vertical_dispatch.dispatch_vertical_task(
        "test-skill", _payload(task_id="t-ind-1"), platform, task_id="t-ind-1"
    )
    assert "duplicate_dispatch_rejected" in second[1]
    assert platform["agent"].runs == []
    assert calls["n"] == 1


def test_no_task_id_no_registry_tracking(platform, monkeypatch):
    """Dispatches without a task_id keep working and are not registry-gated."""
    from sincor2 import vertical_dispatch

    class OkExecutor:
        def execute(self, handler, task):
            return handler(task)

    monkeypatch.setattr("core.policy.get_default_executor", lambda: OkExecutor())
    for _ in range(2):
        out = vertical_dispatch.dispatch_vertical_task("test-skill", _payload(), platform)
        assert out[1] is None
    assert len(platform["agent"].runs) == 2
