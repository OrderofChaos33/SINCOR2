"""Commit-window staleness in sweep_commits + one index fetch per pass.

Root cause (production, 2026-11-14): a verified live pass saw 34/34
commit attempts rejected "commit window closed".  Two compounding
defects:

1. ``Runner.now_ms`` is frozen at Runner construction, and the sweep
   judged commit headroom against it with a 30s safety margin.  But a
   pass spends ~42s fetching the task index (measured: 3,107 tasks,
   32 pages, ~1.34s/page) plus heartbeats and earlier sweep work
   before late tasks are attempted — by then the real clock is far
   past ``now_ms + 30s``, so the check waved through commits whose
   windows had already closed.
2. ``run_once`` fetched the full 3,100+ task index THREE times
   (~128s of pure fetching per pass), tripling the staleness.

Fix under test:
* the sweep judges headroom against the LIVE clock at each task's
  own selection moment, with ``COMMIT_WINDOW_SAFETY_MS`` (120s) of
  margin — a commit is never attempted when the runner already
  knows it would land late;
* the sweep works earliest-deadline-first, so the most urgent
  catchable tasks are committed while they are still catchable;
* ``run_once`` fetches the task index ONCE and shares the snapshot
  across recover/sweep/reveal/close/perform (deadlines are absolute
  timestamps, so window math on a shared snapshot stays exact).

No pre-commit single-task re-check: the deadline never moves
earlier server-side, so a fresh GET returns the same (or a later)
deadline the live-clock check already used — zero information gain
at ~1.3s per task across the whole board.

All HTTP is mocked — no live network calls.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner as liv_runner  # noqa: E402


class WindowServer:
    """In-memory A2A surface: agents listing + heartbeat + commit."""

    def __init__(self, reputations=None):
        self.reputations = reputations or {}
        self.commits = {}        # (task_id, aid) -> commitment
        self.commit_order = []   # task_ids in commit-POST order

    def get_json(self, base_url, path, **kw):
        assert path == "/v1/a2a/agents", f"unexpected GET {path}"
        return {"agents": [{"agent_id": aid, "reputation": rep}
                           for aid, rep in self.reputations.items()]}

    def post_json(self, base_url, path, body, **kw):
        if path == "/v1/a2a/heartbeat":
            return {"ok": True, "agent_id": body["agent_id"]}
        if path == "/v1/a2a/bids/commit":
            self.commits[(body["task_id"], body["agent_id"])] = \
                body["commitment"]
            self.commit_order.append(body["task_id"])
            return {"commitment": body["commitment"]}
        if path.endswith("/close"):
            return {"ok": True}
        raise AssertionError(f"unexpected POST {path}")


def _agent(aid):
    return {"agent_id": aid, "name": aid, "description": "window test",
            "version": "1.0.0", "capability_tags": ["defi"], "skills": [],
            "wallet": "0x0000000000000000000000000000000000000001",
            "chain_id": 8453}


def _task(task_id, commit_in_ms, requires_merit=False):
    now = int(time.time() * 1000)
    return {"task_id": task_id, "state": "open", "sealed": True,
            "tags": ["defi"], "bounty_axm": 5.0 if requires_merit else 1.0,
            "seed_key": "", "title": "T",
            # Server task records always carry poster_id (bound at
            # creation); a named external poster keeps these fixtures
            # clear of the anonymous-poster/self-bid selection filter.
            "poster_id": "external-poster",
            "requires_merit": requires_merit,
            "commit_deadline": now + commit_in_ms,
            "reveal_deadline": now + commit_in_ms + 10 * 60_000}


@pytest.fixture
def make_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    # Test agents hold no cohort keys: unsigned/operator-token path.
    monkeypatch.setattr(liv_runner, "load_cohort_keys", lambda path=None: {})

    def _make(reputations=None, agent_ids=("w-0",)):
        srv = WindowServer(reputations)
        monkeypatch.setattr(liv_runner, "post_json", srv.post_json)
        monkeypatch.setattr(liv_runner, "get_json", srv.get_json)
        r = liv_runner.Runner("http://x", [_agent(a) for a in agent_ids])
        return srv, r

    return _make


# -- headroom filter ---------------------------------------------------------

def test_near_deadline_task_skipped(make_runner):
    """60s of headroom is inside the 120s safety margin: never attempt
    a commit the runner already knows would land late (or on the
    edge).  The old 30s-margin check attempted exactly this task."""
    srv, r = make_runner()
    r.sweep_commits({"t-tight": _task("t-tight", 60_000)})
    assert srv.commits == {}, "task inside the safety margin was attempted"


def test_healthy_headroom_task_attempted(make_runner):
    srv, r = make_runner()
    r.sweep_commits({"t-roomy": _task("t-roomy", 10 * 60_000)})
    assert ("t-roomy", "w-0") in srv.commits


def test_stale_pass_clock_does_not_wave_through_late_commit(make_runner):
    """The original bug, encoded: the pass clock (now_ms) froze 10
    minutes ago — after the fetch + earlier sweep work it is deeply
    stale.  A task with 60s of REAL headroom passed the old check
    (stale now vs 30s margin) and was rejected server-side with
    'commit window closed'.  The sweep must judge by the live clock."""
    srv, r = make_runner()
    r.now_ms = int(time.time() * 1000) - 10 * 60_000  # frozen long ago
    r.sweep_commits({"t-tight": _task("t-tight", 60_000)})
    assert srv.commits == {}, \
        "stale pass clock waved through a commit that lands late"


def test_earliest_deadline_committed_first(make_runner):
    """Earliest-deadline-first: the urgent-but-catchable task is
    committed before the roomy one, regardless of index order."""
    srv, r = make_runner()
    tasks = {"t-roomy": _task("t-roomy", 60 * 60_000),
             "t-urgent": _task("t-urgent", 5 * 60_000)}
    r.sweep_commits(tasks)
    assert srv.commit_order[0] == "t-urgent"
    assert ("t-roomy", "w-0") in srv.commits


def test_deadline_filter_applies_to_merit_tasks_too(make_runner):
    """A merit-qualified agent (rep above floor) still may not commit
    to a merit task whose window is inside the safety margin — the
    deadline filter is orthogonal to, and independent of, the merit
    filter."""
    srv, r = make_runner(reputations={"w-0": 0.2})
    r.sweep_commits({"t-merit-tight": _task("t-merit-tight", 60_000,
                                            requires_merit=True)})
    assert srv.commits == {}, \
        "merit task inside the safety margin was attempted"


# -- one index fetch per pass --------------------------------------------------

def test_run_once_fetches_task_index_once(make_runner, monkeypatch):
    """run_once used to fetch the full 3,100+ task index three times
    (~42s each).  It must fetch ONCE and share the snapshot."""
    srv, r = make_runner()
    task = _task("t-roomy", 10 * 60_000)
    calls = []

    def fake_get_all_pages(base_url, path, per_page=100):
        calls.append(path)
        return [task]

    monkeypatch.setattr(liv_runner, "get_all_pages", fake_get_all_pages)
    monkeypatch.setattr(liv_runner.fountain, "ensure_supply",
                        lambda base_url: {})
    r.run_once()
    assert calls == ["/v1/a2a/tasks"], \
        f"task index fetched {len(calls)} times in one pass"
    assert ("t-roomy", "w-0") in srv.commits, \
        "the shared snapshot did not reach the sweep"
