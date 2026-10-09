"""WP3 tests: capability manifest + dispatch gate.

Fail-closed guarantees:
(a) unknown task kind -> rejected before any handler runs
(b) value-moving handler without operator originator -> rejected
(c) on-chain anchor/fund flags default OFF (D4)
(d) retry preserves the manifest check (gate runs on every dispatch)
"""

import os

import pytest

from sincor2.workers.capability_manifest import (
    MANIFESTS,
    OperatorRequiredError,
    UnknownTaskKindError,
    check_dispatch,
    get_manifest,
)
from sincor2.workers.dispatch_gate import gated_enqueue, gated_run_job, originator_for_job
from sincor2.shadow_monitor.contract import EFFECT_VOCABULARY, VALUE_MOVING_EFFECTS


# ---------------------------------------------------------------------------
# Manifest integrity
# ---------------------------------------------------------------------------

def test_all_manifests_use_contract_vocabulary():
    for kind, manifest in MANIFESTS.items():
        unknown = manifest.effect_types - EFFECT_VOCABULARY
        assert not unknown, f"{kind} declares {sorted(unknown)}"


def test_manifests_cover_all_registered_handlers():
    # Every handler registered in async_tasks must have a manifest.
    import sincor2.async_tasks  # noqa: F401 — registers handlers
    from sincor2 import task_queue

    for kind in ("a2a.execute", "content.generate", "webbuilder.run", "webbuilder.rebuild"):
        assert task_queue.get_handler(kind) is not None, f"handler {kind} missing"
        assert kind in MANIFESTS, f"no manifest for {kind}"


def test_manifests_are_immutable():
    with pytest.raises(TypeError):
        MANIFESTS["a2a.execute"] = None  # type: ignore[index]


# ---------------------------------------------------------------------------
# (a) Unknown task kind fails closed
# ---------------------------------------------------------------------------

def test_unknown_kind_raises():
    with pytest.raises(UnknownTaskKindError, match="no capability manifest"):
        get_manifest("evil.backdoor")


def test_check_dispatch_unknown_kind_raises():
    with pytest.raises(UnknownTaskKindError):
        check_dispatch("evil.backdoor", "operator")


def test_gated_run_job_unknown_kind_marks_failed(monkeypatch):
    # Non-eager backend so the job stays QUEUED until gated_run_job.
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "thread")
    from sincor2 import task_queue
    task_queue.reset_backend_cache()

    job = task_queue.enqueue("evil.backdoor", {"x": 1})
    # The thread pool may grab the job first (async); either way the gate
    # blocks it. Wait for terminal state, then verify the gate denied it.
    import time
    for _ in range(50):
        j = task_queue.get_job(job.id)
        if j.state in task_queue._TERMINAL:
            break
        time.sleep(0.05)
    failed = task_queue.get_job(job.id)
    assert failed.state == task_queue.FAILED
    assert "no capability manifest" in (failed.error or "")
    # Explicit gated_run_job on a fresh job also raises.
    job2 = task_queue.enqueue("evil.backdoor2", {"x": 2})
    task_queue.update_job(job2.id, state=task_queue.QUEUED, error=None)
    with pytest.raises(UnknownTaskKindError):
        gated_run_job(job2.id, originator="operator")


# ---------------------------------------------------------------------------
# (b) Value-moving handler requires operator originator (D5)
# ---------------------------------------------------------------------------

def test_a2a_execute_declares_value_moving_effects():
    manifest = get_manifest("a2a.execute")
    assert manifest.requires_operator
    assert manifest.effect_types & VALUE_MOVING_EFFECTS


def test_value_moving_rejected_for_background_worker():
    with pytest.raises(OperatorRequiredError, match="must be 'operator'"):
        check_dispatch("a2a.execute", "background_worker")


def test_value_moving_rejected_for_agent():
    with pytest.raises(OperatorRequiredError):
        check_dispatch("a2a.execute", "agent")


def test_value_moving_allowed_for_operator():
    manifest = check_dispatch("a2a.execute", "operator")
    assert manifest.kind == "a2a.execute"


def test_non_value_moving_allowed_for_background_worker():
    # content.generate and webbuilder.run declare social.post (not value-moving)
    for kind in ("content.generate", "webbuilder.run", "webbuilder.rebuild"):
        manifest = check_dispatch(kind, "background_worker")
        assert manifest.kind == kind
        assert not manifest.requires_operator


def test_gated_enqueue_rejects_value_moving_at_enqueue_time():
    with pytest.raises(OperatorRequiredError):
        gated_enqueue("a2a.execute", {}, originator="background_worker")


# ---------------------------------------------------------------------------
# (c) On-chain anchor/fund flags default OFF (D4)
# ---------------------------------------------------------------------------

def test_anchor_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("AUCTION_ONCHAIN_ANCHOR", raising=False)
    from sincor2.onchain.auction_relayer import anchor_enabled
    assert anchor_enabled() is False


def test_fund_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("AUCTION_ONCHAIN_FUND", raising=False)
    from sincor2.onchain.auction_relayer import fund_enabled
    assert fund_enabled() is False


def test_anchor_flag_requires_explicit_one(monkeypatch):
    from sincor2.onchain.auction_relayer import anchor_enabled, fund_enabled
    for val in ("", "0", "true", "yes", "2"):
        monkeypatch.setenv("AUCTION_ONCHAIN_ANCHOR", val)
        monkeypatch.setenv("AUCTION_ONCHAIN_FUND", val)
        assert anchor_enabled() is False, val
        assert fund_enabled() is False, val
    monkeypatch.setenv("AUCTION_ONCHAIN_ANCHOR", "1")
    monkeypatch.setenv("AUCTION_ONCHAIN_FUND", "1")
    assert anchor_enabled() is True
    assert fund_enabled() is True


# ---------------------------------------------------------------------------
# (d) Retry preserves the manifest check
# ---------------------------------------------------------------------------

def test_retry_reruns_gate(monkeypatch):
    """Simulate a retry: the gate must run on EVERY dispatch, not just the first."""
    monkeypatch.setenv("SINCOR_TASK_QUEUE", "thread")
    from sincor2 import task_queue
    task_queue.reset_backend_cache()

    calls = []

    def fake_handler(payload, job_id):
        calls.append(job_id)
        raise RuntimeError("transient failure")

    # Register a test-only kind WITHOUT a manifest — must fail even on retry.
    task_queue.register_handler("test.no_manifest", fake_handler)
    job = task_queue.enqueue("test.no_manifest", {})

    # The gate lives in run_job (the choke point), so even the thread-pool
    # path is blocked. Wait for the background thread to settle.
    import time
    for _ in range(50):
        j = task_queue.get_job(job.id)
        if j.state in task_queue._TERMINAL:
            break
        time.sleep(0.05)
    # Handler was never invoked.
    assert calls == []
    failed = task_queue.get_job(job.id)
    assert failed.state == task_queue.FAILED
    assert "no capability manifest" in (failed.error or "")
    # Retry hits the same gate (reset to QUEUED first to simulate retry).
    task_queue.update_job(job.id, state=task_queue.QUEUED, error=None)
    with pytest.raises(UnknownTaskKindError):
        task_queue.run_job(job.id)
    assert calls == []


def test_originator_stamped_at_enqueue():
    from sincor2 import task_queue

    job = task_queue.enqueue("webbuilder.rebuild", {"project_id": "p1"})
    # Raw enqueue has no originator stamp -> default background_worker.
    assert originator_for_job(job) == "background_worker"

    job2 = gated_enqueue("webbuilder.rebuild", {"project_id": "p2"}, originator="operator")
    assert originator_for_job(job2) == "operator"
