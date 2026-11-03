"""KYA telemetry wiring (recommendation #2, live loop) — integration tests.

Covers the two production seams wired in this branch:

- RECORD: ``a2a_integration._finalize_a2a_task`` — the finalization
  function the async worker (and eager path) calls for every A2A
  skill execution — writes one telemetry row per execution, keyed by
  the caller's reputation identity (verified wallet / caller id),
  with success / error / timeout outcomes.
- APPLY: ``a2a_inbound_market.close_auction`` winner selection ranks
  candidates on ``score * telemetry_multiplier``. A high-error agent
  is scaled per policy (never excluded), a clean agent is untouched,
  and an agent with no telemetry ranks on raw score with the task
  record byte-for-byte unchanged (no audit stamp).

Both seams are fail-open: a telemetry-store failure must not change
any execution or auction outcome. The telemetry store is redirected
to a per-test tmp dir via SINCOR_DATA_DIR (``default_db_path`` reads
``data_dir()``), so these tests exercise the REAL default-store path
the production code uses — no seam internals are patched except in
the explicit failure-injection tests.
"""

from __future__ import annotations

import uuid

import pytest
from flask import Flask

from sincor2 import a2a_integration as ai
from sincor2 import kya_telemetry as kt
from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import close_auction
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

ONE_AXM = 10**18


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fresh fabric + stake ledger + telemetry store per test."""
    monkeypatch.setenv("SINCOR_DATA_DIR", str(tmp_path / "data"))
    kt._default_store = None
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    yield app
    kt._default_store = None


def _seed(agent_id, *, successes=0, errors=0, timeouts=0):
    store = kt.get_default_store()
    for _ in range(successes):
        store.record(agent_id, "success", skill_id="wiring-test")
    for _ in range(errors):
        store.record(agent_id, "error", skill_id="wiring-test")
    for _ in range(timeouts):
        store.record(agent_id, "timeout", skill_id="wiring-test")


def _mk_agent(agent_id, stake_axm=1000):
    get_fabric().agents[agent_id] = {"agent_id": agent_id, "reputation": 1.0}
    if stake_axm:
        stake_ledger().deposit(agent_id, int(stake_axm * ONE_AXM))


def _auction(task_id, bids):
    """Sealed auction with revealed bids: [(agent, bid_axm, score)]."""
    fabric = get_fabric()
    fabric.tasks[task_id] = {
        "task_id": task_id,
        "state": "auction",
        "sealed": True,
        "reveal_deadline": _now_ms() - 1000,
        "tags": [],
    }
    for i, (agent_id, bid_axm, score) in enumerate(bids):
        _mk_agent(agent_id)
        bid_id = f"bid_{task_id}_{i}"
        fabric.bids[bid_id] = {
            "bid_id": bid_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "bid_axm": bid_axm,
            "score": score,
            "received_at": i,
            "revealed": True,
        }
        stake_ledger().lock_for_commit(
            agent_id, task_id, int(bid_axm * ONE_AXM))


# ---------------------------------------------------------------------------
# APPLY seam — close_auction winner selection
# ---------------------------------------------------------------------------

def test_throttled_agent_loses_to_clean_rival(env):
    # 25 attempts, 80% errors → pressure saturates → multiplier 0.1.
    _seed("flaky", successes=5, errors=20)
    _seed("clean", successes=25)
    _auction("t1", [("flaky", 1.0, 100.0), ("clean", 1.0, 50.0)])
    snap = close_auction("t1")
    # Raw scores would crown flaky (100 > 50); scaled 100*0.1=10 < 50.
    assert snap["assigned_to"] == "clean"
    assert snap["winner_score"] == 50.0
    # The winner has telemetry, so the audit stamp is present, neutral.
    stamp = snap["telemetry_throttle"]
    assert stamp["agent_id"] == "clean"
    assert stamp["multiplier"] == 1.0
    assert stamp["evidence"]["attempts"] == 25
    assert stamp["evidence"]["errors"] == 0


def test_throttled_agent_still_wins_when_scaled_score_tops(env):
    # Throttling scales, it never hard-blocks: 100 * 0.1 = 10 > 5.
    _seed("flaky", successes=5, errors=15, timeouts=5)
    _auction("t1", [("flaky", 1.0, 100.0), ("rookie", 1.0, 5.0)])
    snap = close_auction("t1")
    assert snap["assigned_to"] == "flaky"
    assert snap["winner_score"] == 100.0  # raw score never rewritten
    stamp = snap["telemetry_throttle"]
    assert stamp["multiplier"] == 0.1
    assert stamp["status"] == "evaluated"
    assert stamp["raw_score"] == 100.0
    assert stamp["effective_score"] == pytest.approx(10.0)
    assert stamp["evidence"] == {
        "attempts": 25, "errors": 15, "timeouts": 5}


def test_no_telemetry_assignment_is_byte_for_byte_unchanged(env):
    _auction("t1", [("alpha", 1.0, 100.0), ("beta", 1.0, 50.0)])
    snap = close_auction("t1")
    assert snap["assigned_to"] == "alpha"
    assert snap["winner_score"] == 100.0
    assert "telemetry_throttle" not in snap


def test_telemetry_failure_leaves_close_auction_unchanged(env, monkeypatch):
    def _boom():
        raise RuntimeError("telemetry store down")

    monkeypatch.setattr(kt, "get_default_store", _boom)
    _auction("t1", [("alpha", 1.0, 100.0), ("beta", 1.0, 50.0)])
    snap = close_auction("t1")
    assert snap["assigned_to"] == "alpha"
    assert snap["winner_score"] == 100.0
    assert "telemetry_throttle" not in snap


# ---------------------------------------------------------------------------
# RECORD seam — _finalize_a2a_task
# ---------------------------------------------------------------------------

def _a2a_task(caller_id="caller-1", metadata=None,
              skill_id="lead-enrichment"):
    return ai.A2ATask(
        id="task-" + uuid.uuid4().hex[:10],
        context_id="ctx-1",
        skill_id=skill_id,
        input_text="do the thing",
        caller_id=caller_id,
        state=ai.TaskState.WORKING,
        created_at=ai._now(),
        updated_at=ai._now(),
        metadata=metadata or {},
    )


def test_finalize_records_success_under_verified_wallet(env):
    task = _a2a_task(metadata={
        "verified_wallet": "0xAbC0000000000000000000000000000000000001"})
    result = ai._finalize_a2a_task(task, "the output", None)
    assert result["state"] == "completed"
    events = kt.get_default_store().events(
        "wallet:0xabc0000000000000000000000000000000000001")
    assert len(events) == 1
    assert events[0]["outcome"] == "success"
    assert events[0]["skill_id"] == "lead-enrichment"
    assert events[0]["latency_ms"] is not None
    assert events[0]["latency_ms"] >= 0


def test_finalize_records_error_and_timeout_outcomes(env):
    err_task = _a2a_task(caller_id="caller-err")
    result = ai._finalize_a2a_task(err_task, None, "swarm exploded")
    assert result["state"] == "failed"

    timeout_task = _a2a_task(caller_id="caller-err")
    result = ai._finalize_a2a_task(
        timeout_task, None, "execution timeout after 120s")
    assert result["state"] == "failed"

    events = kt.get_default_store().events("id:caller-err")
    assert [e["outcome"] for e in events] == ["error", "timeout"]


def test_telemetry_store_failure_does_not_change_finalization(
        env, monkeypatch):
    def _boom():
        raise RuntimeError("telemetry store down")

    monkeypatch.setattr(kt, "get_default_store", _boom)
    ok_task = _a2a_task(caller_id="caller-failopen")
    result = ai._finalize_a2a_task(ok_task, "ok output", None)
    assert result["state"] == "completed"
    assert result["output"] == "ok output"

    err_task = _a2a_task(caller_id="caller-failopen")
    result = ai._finalize_a2a_task(err_task, None, "boom")
    assert result["state"] == "failed"
    assert result["error"] == "boom"


# ---------------------------------------------------------------------------
# Wiring helpers — direct contract
# ---------------------------------------------------------------------------

def test_wiring_helpers_contract(env):
    # Recording never raises, even on invalid input.
    assert kt.record_execution_outcome("a1", "success") is True
    assert kt.record_execution_outcome("a1", "bogus-outcome") is False
    assert kt.record_execution_outcome("", "success") is False
    assert kt.record_execution_outcome(None, "success") is False
    # No data → None (callers rank on raw score, no audit stamp).
    assert kt.throttle_verdict("nobody") is None
    assert kt.throttle_verdict("") is None
    # Thin data → explicit unknown verdict, neutral multiplier.
    verdict = kt.throttle_verdict("a1")
    assert verdict["status"] == "unknown"
    assert verdict["allocation_multiplier"] == 1.0
    assert verdict["evidence"]["attempts"] == 1
