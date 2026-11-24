"""W-199 — activity_engine cycle lock (cross-instance / cross-process).

The bug: ``run_cycle()`` did ``load_state()`` at cycle start, held the
snapshot for the ENTIRE cycle (posting, advancing, settling, fee
accounting), then ``save_state()`` rewrote the whole file unlocked. A
second concurrent cycle (scheduler overlap, a second process on the
same volume) loaded the same snapshot and whichever cycle saved last
silently erased the other's cursor advance, settled/expired counts,
fee totals, and in-flight records — the widest race window in the
lost-update family.

The fix shape (persistence research 2026-11-23, W-199 row): a CYCLE
LOCK, not a merge — an exclusive ``flock`` on a sidecar lock file held
from load to save; a second concurrent cycle FAILS CLOSED with
``ActivityCycleInProgressError`` instead of racing.

These tests fail pre-fix (no ``cycle_lock`` /
``ActivityCycleInProgressError`` exists) and pass post-fix.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from sincor2 import activity_engine

REPO_ROOT = Path(__file__).resolve().parents[2]

HOLDER_SCRIPT = r"""
import os, sys, time
from pathlib import Path
from sincor2 import activity_engine

ready = Path(os.environ["AE_LOCK_READY"])
release = Path(os.environ["AE_LOCK_RELEASE"])
with activity_engine.cycle_lock():
    state = activity_engine.load_state()
    state["cycles"] = int(state.get("cycles") or 0) + 1
    activity_engine.save_state(state)
    ready.write_text("held", encoding="utf-8")
    deadline = time.time() + 30
    while not release.exists() and time.time() < deadline:
        time.sleep(0.05)
"""


@pytest.fixture
def state_env(tmp_path, monkeypatch):
    state_file = tmp_path / "ae_state.json"
    monkeypatch.setenv("SINCOR_ACTIVITY_ENGINE_STATE", str(state_file))
    return state_file


def _spawn_holder(tmp_path):
    ready = tmp_path / "lock_ready"
    release = tmp_path / "lock_release"
    env = dict(os.environ)
    env["SINCOR_ACTIVITY_ENGINE_STATE"] = str(tmp_path / "ae_state.json")
    env["AE_LOCK_READY"] = str(ready)
    env["AE_LOCK_RELEASE"] = str(release)
    env["PYTHONPATH"] = str(REPO_ROOT / "src") + os.pathsep + str(REPO_ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-c", HOLDER_SCRIPT],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        if ready.exists():
            return proc, release
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"lock holder exited early:\n{out}")
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("lock holder never signalled readiness")


def test_second_process_cycle_fails_closed_while_lock_held(state_env, tmp_path):
    """Cross-process: while another process holds the cycle lock (and has
    already committed its state increment), this process's cycle must
    fail closed — never load-mutate-save over the holder's work."""
    proc, release = _spawn_holder(tmp_path)
    try:
        with pytest.raises(activity_engine.ActivityCycleInProgressError):
            with activity_engine.cycle_lock():
                pass  # pragma: no cover - must never be reached
        with pytest.raises(activity_engine.ActivityCycleInProgressError):
            activity_engine.run_cycle(post=False)
    finally:
        release.write_text("go", encoding="utf-8")
        proc.wait(timeout=30)
    # The holder's committed increment survived: no lost update.
    state = activity_engine.load_state()
    assert state["cycles"] == 1
    # And once the holder released, the lock is free again and a new
    # locked increment lands on top of the holder's (both survive).
    with activity_engine.cycle_lock():
        state = activity_engine.load_state()
        state["cycles"] = int(state.get("cycles") or 0) + 1
        activity_engine.save_state(state)
    assert activity_engine.load_state()["cycles"] == 2


def test_second_thread_cycle_fails_closed_while_lock_held(state_env):
    """Same-process: a concurrent cycle on another thread fails closed
    instead of racing the in-flight cycle's snapshot."""
    errors = []

    def contender():
        try:
            activity_engine.run_cycle(post=False)
        except activity_engine.ActivityCycleInProgressError as err:
            errors.append(err)

    with activity_engine.cycle_lock():
        t = threading.Thread(target=contender)
        t.start()
        t.join(timeout=30)
    assert not t.is_alive()
    assert len(errors) == 1


def test_cycle_lock_released_after_exception(state_env):
    """A crashed cycle must not wedge the engine: the lock is released
    when the holder's cycle body raises."""
    with pytest.raises(RuntimeError, match="boom"):
        with activity_engine.cycle_lock():
            raise RuntimeError("boom")
    # Re-acquisition succeeds immediately after the failed cycle.
    with activity_engine.cycle_lock():
        pass


def test_run_cycle_releases_lock_on_success(state_env, tmp_path, monkeypatch):
    """After a normal (empty) cycle the lock is free for the next one,
    and the cycle counter was persisted exactly once."""
    monkeypatch.setenv("SINCOR_ACTIVITY_COHORT_IDS", "")
    # No cohort and nothing in flight: the cycle body is just
    # load -> increment -> save, fully under the lock.
    monkeypatch.setattr(activity_engine, "resolve_cohort", lambda: [])
    monkeypatch.setattr(activity_engine, "ensure_cohort_agents",
                        lambda ids: [])
    monkeypatch.setattr(activity_engine, "ensure_engine_poster",
                        lambda: True)
    summary = activity_engine.run_cycle(post=False)
    assert summary["in_flight"] == 0
    assert activity_engine.load_state()["cycles"] == 1
    with activity_engine.cycle_lock():
        pass  # lock was released


def test_save_state_uses_pid_unique_tmp_and_round_trips(state_env, tmp_path):
    """The whole-file rewrite no longer shares one fixed ``.tmp`` name
    across processes (the family's shared-tmp crash hazard): no tmp
    litter remains and the payload round-trips byte-for-byte."""
    state = activity_engine._default_state()
    state["cycles"] = 7
    state["fees_axm"] = 1.25
    activity_engine.save_state(state)
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert leftovers == []
    raw = json.loads(state_env.read_text(encoding="utf-8"))
    assert raw["cycles"] == 7
    assert raw["fees_axm"] == 1.25
    assert activity_engine.load_state()["cycles"] == 7
