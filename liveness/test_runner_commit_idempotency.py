#!/usr/bin/env python3
"""Hermetic tests for the runner's commit idempotency + single-instance guard.

Two bugs fixed here (see runner.py):
  1. A commit POST that returns a DIFFERENT commitment than the one just
     sent means the server kept a pre-existing record for (task, agent);
     keeping the fresh local entry guarantees a later "commitment
     mismatch" reveal rejection. The runner must drop the fresh entry.
  2. Overlapping passes (cron every 2 min + manual runs) last-writer-win
     on commits.json: _save_json is atomic but there is no lock and no
     merge. The runner must hold an exclusive non-blocking lock for the
     whole pass and skip (exit 0) when another pass holds it.

No network: all server calls are stubbed. Run from
~/workspace/sincor2-liveness-wt:

    ~/.venvs/sincor2/bin/python -m pytest \
        liveness/test_runner_commit_idempotency.py -q
"""

import fcntl
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

import runner  # noqa: E402
from net import HttpError  # noqa: E402

AGENT = {"agent_id": "a1", "capability_tags": ["x"]}
COHORT = [AGENT]


def _task(now_ms, *, commit_deadline=None, reveal_deadline=None):
    return {
        "task_id": "t1",
        "state": "open",
        "sealed": True,
        "bounty_axm": 1.0,
        "tags": ["x"],
        "seed_key": "",
        "commit_deadline": commit_deadline if commit_deadline is not None else now_ms + 600_000,
        "reveal_deadline": reveal_deadline if reveal_deadline is not None else now_ms + 1_200_000,
    }


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Point all runner state at a temp dir; stub keys + network."""
    monkeypatch.setattr(runner, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "COMMITS_PATH", str(tmp_path / "commits.json"))
    monkeypatch.setattr(runner, "QUEUE_PATH", str(tmp_path / "performance_queue.json"))
    monkeypatch.setattr(runner, "load_cohort_keys", lambda *a, **k: {})
    return tmp_path


def _make_runner(env, monkeypatch):
    r = runner.Runner("http://stub", COHORT)
    monkeypatch.setattr(r, "_bid_auth", lambda *a, **k: None)
    return r


def test_commit_different_commitment_drops_fresh_entry(env, monkeypatch, capsys):
    r = _make_runner(env, monkeypatch)
    calls = []

    def fake_post(base, path, body, headers=None):
        calls.append((path, body))
        return {"task_id": body["task_id"], "agent_id": body["agent_id"],
                "commitment": "0x" + "ff" * 32, "committed_at": 1}

    monkeypatch.setattr(runner, "post_json", fake_post)
    task = _task(r.now_ms)
    r._commit(task, AGENT, 1.0)
    # The server kept a pre-existing record: the fresh entry must go.
    assert r.commits.get("t1", {}).get("a1") is None
    saved = json.loads((env / "commits.json").read_text())
    assert saved.get("t1", {}).get("a1") is None
    # ... and no reveal is ever attempted for the dropped pair.
    calls.clear()
    r.reveal_due({"t1": _task(r.now_ms, commit_deadline=r.now_ms - 1000,
                               reveal_deadline=r.now_ms + 100_000)})
    assert not [c for c in calls if "reveal" in c[0]]


def test_commit_same_commitment_keeps_entry_and_reveals(env, monkeypatch):
    r = _make_runner(env, monkeypatch)

    def fake_post(base, path, body, headers=None):
        if path.endswith("/bids/commit"):
            # Server echoes back exactly the commitment we just sent.
            return {"task_id": body["task_id"], "agent_id": body["agent_id"],
                    "commitment": body["commitment"], "committed_at": 1}
        return {}

    monkeypatch.setattr(runner, "post_json", fake_post)
    task = _task(r.now_ms)
    r._commit(task, AGENT, 1.0)
    entry = r.commits.get("t1", {}).get("a1")
    assert entry is not None and entry["status"] == "committed"
    # Window open: reveal must succeed (stubbed server recomputes honestly).
    r.now_ms = r.now_ms + 700_000
    r.reveal_due({"t1": _task(r.now_ms, commit_deadline=r.now_ms - 1000,
                               reveal_deadline=r.now_ms + 100_000)})
    assert r.commits["t1"]["a1"]["status"] == "revealed"


def test_commit_no_commitment_field_keeps_entry_and_warns(env, monkeypatch, capsys):
    r = _make_runner(env, monkeypatch)

    def fake_post(base, path, body, headers=None):
        return {"ok": True}  # no commitment field at all

    monkeypatch.setattr(runner, "post_json", fake_post)
    r._commit(_task(r.now_ms), AGENT, 1.0)
    assert r.commits.get("t1", {}).get("a1") is not None
    out = capsys.readouterr().out
    assert "no commitment" in out.lower()


def test_commit_already_committed_error_still_drops(env, monkeypatch):
    r = _make_runner(env, monkeypatch)

    def fake_post(base, path, body, headers=None):
        raise HttpError(409, "already committed")

    monkeypatch.setattr(runner, "post_json", fake_post)
    r._commit(_task(r.now_ms), AGENT, 1.0)
    assert r.commits.get("t1", {}).get("a1") is None


def test_second_pass_with_lock_held_skips_without_touching_state(env, monkeypatch, capsys):
    (env / "commits.json").write_text(json.dumps({"t1": {"a1": {"status": "committed"}}}))
    before = (env / "commits.json").read_bytes()
    lock_path = env / ".runner.lock"
    with open(lock_path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # one_pass must NOT construct a Runner or touch state while held.
        assert runner.one_pass("http://stub", COHORT) is True
    after = (env / "commits.json").read_bytes()
    assert after == before
    assert "another" in capsys.readouterr().out.lower()


def test_pass_proceeds_once_lock_released(env, monkeypatch):
    (env / "commits.json").write_text(json.dumps({}))
    monkeypatch.setattr(runner, "post_json", lambda *a, **k: {})
    monkeypatch.setattr(runner, "get_all_pages", lambda *a, **k: [])
    monkeypatch.setattr(runner.fountain, "ensure_supply", lambda url: {})
    assert runner.one_pass("http://stub", COHORT) is True
    # State file still valid JSON after a full (stubbed) pass.
    json.loads((env / "commits.json").read_text())
