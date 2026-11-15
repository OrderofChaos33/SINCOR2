"""Regression tests: heartbeat must be refreshed before commit/reveal.

Root cause (production, recurring through 2026-11-05): the server
enforces a 60s heartbeat TTL on bid actions
(``ts - last_heartbeat > 60_000`` -> 403 "agent heartbeat expired"),
but the runner heartbeated only once at pass start while a pass takes
~84-90s (paginated task fetch + a long sequential commit sweep).
Commits late in the sweep were systematically rejected.

Fix under test: ``Runner.ensure_heartbeat()`` refreshes an agent's
heartbeat immediately before each commit/reveal, with a 45s in-pass
dedupe guard, best-effort (a failed refresh never changes bidding
semantics). A Sept-2026 version of this fix existed in a side
worktree (wt-heartbeat) but never landed in this live worktree.

All HTTP is mocked — no live network calls.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import net  # noqa: E402
import runner as liv_runner  # noqa: E402


class TtlServer:
    """In-memory A2A surface enforcing the 60s heartbeat TTL exactly
    like production: commit/reveal 403 when the agent's heartbeat is
    older than TTL_MS; heartbeat 404s for unknown agents."""

    TTL_MS = 60_000

    def __init__(self):
        self.now_ms = int(time.time() * 1000)
        self.agents = {}       # aid -> {"last_heartbeat": ms}
        self.commits = {}      # (task_id, aid) -> commitment hex
        self.revealed = []
        self.heartbeat_calls = []
        self.register_calls = []
        self.events = []       # ordered event log

    def post_json(self, base_url, path, body, **kw):
        if path == "/v1/a2a/heartbeat":
            aid = body["agent_id"]
            self.heartbeat_calls.append(aid)
            self.events.append(("heartbeat", aid))
            ag = self.agents.get(aid)
            if ag is None:
                raise net.HttpError(404, "unknown agent")
            ag["last_heartbeat"] = self.now_ms
            return {"ok": True, "agent_id": aid,
                    "expires_at": self.now_ms + self.TTL_MS}
        if path == "/v1/a2a/register":
            aid = body["agent_id"]
            self.register_calls.append(aid)
            self.events.append(("register", aid))
            self.agents[aid] = {"last_heartbeat": self.now_ms}
            return {"agent_id": aid, "status": "registered"}
        if path == "/v1/a2a/bids/commit":
            aid, tid = body["agent_id"], body["task_id"]
            self.events.append(("commit", tid, aid))
            self._check_bidder(aid)
            self.commits[(tid, aid)] = body["commitment"]
            return {"commitment": body["commitment"]}
        if path == "/v1/a2a/bids/reveal":
            aid, tid = body["agent_id"], body["task_id"]
            self.events.append(("reveal", tid, aid))
            self._check_bidder(aid)
            assert (tid, aid) in self.commits, "reveal without commit"
            self.revealed.append((tid, aid))
            return {"revealed": True}
        raise AssertionError(f"unexpected POST {path}")

    def _check_bidder(self, aid):
        ag = self.agents.get(aid)
        if ag is None:
            raise net.HttpError(404, "unknown agent")
        if self.now_ms - ag["last_heartbeat"] > self.TTL_MS:
            raise net.HttpError(403, "agent heartbeat expired")


def _agent(aid="hb-0"):
    return {"agent_id": aid, "name": "HB", "description": "heartbeat test",
            "version": "1.0.0", "capability_tags": ["defi"], "skills": [],
            "wallet": "0x0000000000000000000000000000000000000001",
            "chain_id": 8453}


def _task(task_id="t-hb-1", bounty=1.0):
    now = int(time.time() * 1000)
    return {"task_id": task_id, "state": "open", "sealed": True,
            "tags": ["defi"], "bounty_axm": bounty, "seed_key": "",
            "title": "T", "commit_deadline": now + 5 * 60_000,
            "reveal_deadline": now + 10 * 60_000}


@pytest.fixture
def srv(monkeypatch, tmp_path):
    s = TtlServer()
    monkeypatch.setattr(liv_runner, "post_json", s.post_json)
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    # Test agents hold no cohort keys: heartbeat/commit use the
    # unsigned/operator-token fallback path, which the fake accepts.
    monkeypatch.setattr(liv_runner, "load_cohort_keys", lambda path=None: {})
    return s


def _stale(srv, runner, aid, ms=120_000):
    """Make the agent's heartbeat older than the TTL on both the
    server's record and the runner's in-pass dedupe clock."""
    srv.agents[aid]["last_heartbeat"] -= ms
    runner._hb_at[aid] = runner._hb_at.get(aid, 0) - ms


def test_commit_refreshes_stale_heartbeat_before_post(srv):
    agent, aid = _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms}
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()
    _stale(srv, r, aid)  # pass runs long; heartbeat now expired
    r._commit(_task(), agent, 1.0)
    assert (("t-hb-1", aid) in srv.commits), "commit was rejected"
    kinds = [e[0] for e in srv.events]
    assert kinds.index("commit") > max(
        i for i, k in enumerate(kinds) if k == "heartbeat"), \
        "no heartbeat refresh immediately before the commit"


def test_reveal_refreshes_stale_heartbeat_before_post(srv):
    agent, aid = _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms}
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()
    r._commit(_task(), agent, 1.0)
    assert ("t-hb-1", aid) in srv.commits
    entry = r.commits["t-hb-1"][aid]
    _stale(srv, r, aid)  # reveal phase starts long after the commit
    r._reveal("t-hb-1", aid, entry)
    assert ("t-hb-1", aid) in srv.revealed, "reveal was rejected"
    assert entry["status"] == "revealed"


def test_ensure_heartbeat_dedupes_within_fresh_window(srv):
    agent, aid = _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms}
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()
    n = len(srv.heartbeat_calls)
    assert r.ensure_heartbeat(agent) is True
    assert len(srv.heartbeat_calls) == n, "fresh heartbeat was re-posted"


def test_ensure_heartbeat_reregisters_on_404(srv):
    agent, aid = _agent(), "hb-0"
    r = liv_runner.Runner("http://x", [agent])  # agent unknown to server
    assert r.ensure_heartbeat(agent) is True
    assert aid in srv.register_calls
    assert aid in srv.agents
