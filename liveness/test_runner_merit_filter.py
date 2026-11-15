"""Merit-aware task selection in sweep_commits.

Root cause (production, 2026-11-14): the server merit-gates tasks
stamped ``requires_merit`` (bounty >= 5.0 AXM) — commit/reveal from an
agent whose server-side reputation is below 0.15 is rejected with
403 "merit required".  The sweep had NO merit/reputation filter, so
with all six cohort agents at reputation 0.0 and 41% of the live
board merit-gated, every pass burned 3-4 commit attempts per merit
task into guaranteed 403s (35x in a single pass).

Fix under test: ``Runner.reputations()`` fetches ``GET /v1/a2a/agents``
ONCE per pass (cached) and ``sweep_commits`` selects merit tasks only
for agents at/above ``REPUTATION_MERIT_THRESHOLD``.  This is task
SELECTION, not a gate bypass: reputation is still earned solely by
settling proofs server-side.  A failed reputation fetch FAILS SAFE —
every reputation reads as 0.0, merit tasks are skipped, non-merit
tasks proceed — never fail open into merit tasks.

Reveals of existing commits are deliberately NOT filtered: an
unrevealed commit is ghosting, which resets reputation to 0.0.

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


class MeritServer:
    """In-memory A2A surface: agents listing + heartbeat + commit."""

    def __init__(self, reputations):
        self.reputations = reputations  # aid -> float
        self.commits = {}               # (task_id, aid) -> commitment
        self.agents_calls = 0

    def get_json(self, base_url, path, **kw):
        assert path == "/v1/a2a/agents", f"unexpected GET {path}"
        self.agents_calls += 1
        return {"agents": [{"agent_id": aid, "reputation": rep}
                           for aid, rep in self.reputations.items()]}

    def post_json(self, base_url, path, body, **kw):
        if path == "/v1/a2a/heartbeat":
            return {"ok": True, "agent_id": body["agent_id"]}
        if path == "/v1/a2a/bids/commit":
            self.commits[(body["task_id"], body["agent_id"])] = \
                body["commitment"]
            return {"commitment": body["commitment"]}
        raise AssertionError(f"unexpected POST {path}")


def _agent(aid):
    return {"agent_id": aid, "name": aid, "description": "merit test",
            "version": "1.0.0", "capability_tags": ["defi"], "skills": [],
            "wallet": "0x0000000000000000000000000000000000000001",
            "chain_id": 8453}


def _task(task_id, requires_merit):
    now = int(time.time() * 1000)
    return {"task_id": task_id, "state": "open", "sealed": True,
            "tags": ["defi"], "bounty_axm": 5.0 if requires_merit else 1.0,
            "seed_key": "", "title": "T",
            # Server task records always carry poster_id (bound at
            # creation); a named external poster keeps these fixtures
            # clear of the anonymous-poster/self-bid selection filter.
            "poster_id": "external-poster",
            "requires_merit": requires_merit,
            "commit_deadline": now + 5 * 60_000,
            "reveal_deadline": now + 10 * 60_000}


@pytest.fixture
def make_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    # Test agents hold no cohort keys: unsigned/operator-token path.
    monkeypatch.setattr(liv_runner, "load_cohort_keys", lambda path=None: {})

    def _make(reputations, agent_ids):
        srv = MeritServer(reputations)
        monkeypatch.setattr(liv_runner, "post_json", srv.post_json)
        monkeypatch.setattr(liv_runner, "get_json", srv.get_json)
        r = liv_runner.Runner("http://x", [_agent(a) for a in agent_ids])
        return srv, r

    return _make


def test_merit_task_skipped_at_zero_reputation(make_runner):
    srv, r = make_runner({"m-0": 0.0}, ["m-0"])
    r.sweep_commits({"t-merit": _task("t-merit", True)})
    assert srv.commits == {}, "sub-floor agent attempted a merit task"


def test_merit_task_allowed_at_reputation_above_floor(make_runner):
    srv, r = make_runner({"m-0": 0.2}, ["m-0"])
    r.sweep_commits({"t-merit": _task("t-merit", True)})
    assert ("t-merit", "m-0") in srv.commits


def test_merit_task_selects_only_qualified_agents(make_runner):
    srv, r = make_runner({"m-low": 0.0, "m-high": 0.35},
                         ["m-low", "m-high"])
    r.sweep_commits({"t-merit": _task("t-merit", True)})
    assert ("t-merit", "m-low") not in srv.commits
    assert ("t-merit", "m-high") in srv.commits


def test_nonmerit_task_unaffected_by_low_reputation(make_runner):
    srv, r = make_runner({"m-0": 0.0}, ["m-0"])
    r.sweep_commits({"t-open": _task("t-open", False)})
    assert ("t-open", "m-0") in srv.commits


def test_reputation_fetch_failure_fails_safe(make_runner, monkeypatch):
    srv, r = make_runner({"m-0": 0.0}, ["m-0"])

    def boom(base_url, path, **kw):
        raise RuntimeError("transport down")

    monkeypatch.setattr(liv_runner, "get_json", boom)
    r.sweep_commits({"t-merit": _task("t-merit", True),
                     "t-open": _task("t-open", False)})
    assert ("t-merit", "m-0") not in srv.commits, \
        "fetch failure failed OPEN into a merit task"
    assert ("t-open", "m-0") in srv.commits, \
        "fetch failure wrongly blocked a non-merit task"


def test_reputation_fetched_once_per_pass(make_runner):
    srv, r = make_runner({"m-0": 0.0}, ["m-0"])
    r.sweep_commits({"t-m1": _task("t-m1", True),
                     "t-m2": _task("t-m2", True),
                     "t-m3": _task("t-m3", True)})
    assert srv.agents_calls == 1, \
        f"reputation fetched {srv.agents_calls} times in one pass"
