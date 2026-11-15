"""Performance-queue hygiene: prune entries that can never be performed.

Root cause (production, 2026-11-14): ``perform_assigned`` only ever
APPENDED to performance_queue.json.  Nothing pruned it, so the queue
grew past 5,000 entries — nearly all for tasks long settled, expired,
or forgotten by the server (oldest entries 404 on read).  DEPLOY.md
flags exactly this ("alert if it grows unbounded"): the real worker
reads this queue as its work list, and the live entries were buried.

Fix under test: ``Runner._prune_queue`` runs at the top of
``perform_assigned`` against the pass's complete task index.  An
entry survives only while its task is still ``assigned`` server-side
(submit_proof rejects anything else) or — when the task is absent
from the index — while it is within a grace window (an entry queued
moments ago for a task the snapshot predates).  Pruning loses nothing
performable: a pruned task could not accept a proof anyway.

All state is tmp files — no live network calls.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner as liv_runner  # noqa: E402

AID = "q-0"
HOUR_MS = 3_600_000


def _agent(aid=AID):
    return {"agent_id": aid, "name": aid, "description": "queue test",
            "version": "1.0.0", "capability_tags": ["defi"], "skills": [],
            "wallet": "0x0000000000000000000000000000000000000001",
            "chain_id": 8453}


def _entry(task_id, assigned_at):
    return {"task_id": task_id, "agent_id": AID,
            "seed_key": "activity-engine:p01-x", "lane": None,
            "title": "T", "bounty_axm": 1.0, "assigned_at": assigned_at,
            "note": "won by liveness agent; needs real LLM worker"}


def _task(task_id, state, assigned_to=AID):
    return {"task_id": task_id, "state": state,
            "assigned_to": assigned_to,
            "seed_key": "activity-engine:p01-x", "title": "T",
            "bounty_axm": 1.0}


@pytest.fixture
def queue_path(monkeypatch, tmp_path):
    qp = str(tmp_path / "queue.json")
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH", qp)
    monkeypatch.setattr(liv_runner, "load_cohort_keys", lambda path=None: {})
    return qp


def _runner_with_queue(queue_path, entries):
    with open(queue_path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh)
    return liv_runner.Runner("http://x", [_agent()])


def _saved(queue_path):
    with open(queue_path, encoding="utf-8") as fh:
        return json.load(fh)


def test_terminal_state_entries_are_pruned(queue_path):
    now = int(time.time() * 1000)
    r = _runner_with_queue(queue_path, [
        _entry("t-settled", now - HOUR_MS),
        _entry("t-expired", now - HOUR_MS),
        _entry("t-live", now - HOUR_MS),
    ])
    r.perform_assigned({
        "t-settled": _task("t-settled", "settled"),
        "t-expired": _task("t-expired", "expired"),
        "t-live": _task("t-live", "assigned"),
    })
    ids = [e["task_id"] for e in _saved(queue_path)]
    assert ids == ["t-live"], f"dead entries survived pruning: {ids}"


def test_server_forgotten_old_entry_is_pruned(queue_path):
    now = int(time.time() * 1000)
    r = _runner_with_queue(queue_path, [
        _entry("t-gone", now - 2 * HOUR_MS),  # absent from index, old
    ])
    r.perform_assigned({})
    assert _saved(queue_path) == []


def test_fresh_entry_absent_from_index_survives_grace(queue_path):
    now = int(time.time() * 1000)
    r = _runner_with_queue(queue_path, [_entry("t-new", now)])
    r.perform_assigned({})
    ids = [e["task_id"] for e in _saved(queue_path)]
    assert ids == ["t-new"], "fresh entry pruned inside the grace window"


def test_new_win_is_still_queued_after_pruning(queue_path):
    now = int(time.time() * 1000)
    r = _runner_with_queue(queue_path, [_entry("t-dead", now - HOUR_MS)])
    r.perform_assigned({
        "t-dead": _task("t-dead", "expired"),
        "t-won": _task("t-won", "assigned"),
    })
    ids = [e["task_id"] for e in _saved(queue_path)]
    assert ids == ["t-won"], f"queue after prune+append wrong: {ids}"
