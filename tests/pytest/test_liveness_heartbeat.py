"""Liveness heartbeat freshness tests (2026-09-28 production incident).

Root cause: the server enforces a 60s heartbeat TTL on every bid action
(commit/reveal), but the runner heartbeated once at pass start while a
pass can run for many minutes (paginated task fetches under truncation
retries).  By the time the commit/reveal sweep ran, every heartbeat was
expired -> 403 "agent heartbeat expired" on every bid, and 404s whenever
the registry had been wiped by a restart forced re-registration.

Fix under test: Runner.ensure_heartbeat() refreshes the heartbeat
immediately before each commit/reveal (best-effort; a failed refresh
never changes bidding semantics), with a 45s in-pass dedupe guard and
404 -> idempotent re-register -> retry.

All HTTP is mocked — no live network calls.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVENESS = os.path.join(REPO, "liveness")
sys.path.insert(0, LIVENESS)
sys.path.insert(0, os.path.join(REPO, "src"))

import net  # noqa: E402
import runner as liv_runner  # noqa: E402


# ---------------------------------------------------------------------------
# TTL-enforcing fake market
# ---------------------------------------------------------------------------

class TtlServer:
    """In-memory A2A surface that enforces the 60s heartbeat TTL exactly
    like production: commit/reveal 403 when the agent's heartbeat is
    older than TTL_MS, heartbeat 404s for unknown agents."""

    TTL_MS = 60_000

    def __init__(self):
        self.now_ms = int(time.time() * 1000)
        self.agents = {}      # aid -> {"last_heartbeat": ms, "tags": [...]}
        self.tasks = {}
        self.commits = {}     # (task_id, aid) -> True / "revealed"
        self.register_calls = []
        self.heartbeat_calls = []
        self.events = []      # ordered ("heartbeat"|"register"|"commit"|"reveal", ...)

    def advance(self, ms):
        self.now_ms += ms

    # -- HTTP surface ------------------------------------------------------
    def get_json(self, base_url, path, **kw):
        if path.startswith("/v1/a2a/tasks"):
            return {"tasks": list(self.tasks.values())}
        raise AssertionError(f"unexpected GET {path}")

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
            self.agents[aid] = {"last_heartbeat": self.now_ms,
                                "tags": list(body.get("capability_tags") or [])}
            return {"agent_id": aid, "status": "registered"}
        if path == "/v1/a2a/bids/commit":
            aid, tid = body["agent_id"], body["task_id"]
            self.events.append(("commit", tid, aid))
            self._check_bidder(aid)
            key = (tid, aid)
            if key in self.commits:
                raise net.HttpError(409, "already committed")
            self.commits[key] = True
            return {"committed": True}
        if path == "/v1/a2a/bids/reveal":
            aid, tid = body["agent_id"], body["task_id"]
            self.events.append(("reveal", tid, aid))
            self._check_bidder(aid)
            assert (tid, aid) in self.commits, "reveal without commit"
            self.commits[(tid, aid)] = "revealed"
            return {"revealed": True}
        raise AssertionError(f"unexpected POST {path}")

    def _check_bidder(self, aid):
        ag = self.agents.get(aid)
        if ag is None:
            raise net.HttpError(404, "unknown agent")
        if self.now_ms - ag["last_heartbeat"] > self.TTL_MS:
            raise net.HttpError(403, "agent heartbeat expired")

    # -- fixture builders ----------------------------------------------------
    def add_task(self, task_id="t-hb-1", bounty=1.0, tags=("defi",)):
        now = int(time.time() * 1000)
        self.tasks[task_id] = {
            "task_id": task_id, "state": "open", "sealed": True,
            "tags": list(tags), "bounty_axm": bounty, "seed_key": "",
            "title": "T", "commit_deadline": now + 5 * 60_000,
            "reveal_deadline": now + 10 * 60_000,
        }
        return self.tasks[task_id]


def _agent(aid="hb-0"):
    return {"agent_id": aid, "name": "HB", "description": "heartbeat test",
            "version": "1.0.0", "capability_tags": ["defi"], "skills": [],
            "wallet": "0x0000000000000000000000000000000000000001",
            "chain_id": 8453}


@pytest.fixture
def tsrv(monkeypatch, tmp_path):
    srv = TtlServer()
    monkeypatch.setattr(liv_runner, "get_json", srv.get_json)
    monkeypatch.setattr(liv_runner, "post_json", srv.post_json)
    monkeypatch.setattr(liv_runner, "get_all_pages",
                        lambda base_url, path, **kw: list(srv.tasks.values()))
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    monkeypatch.setattr(liv_runner.fountain, "ensure_supply",
                        lambda *a, **k: {"lane_a": [], "lane_b": [],
                                         "lane_c": []})
    return srv


def _stale(srv, runner, aid, ms=120_000):
    """Simulate ms passing on both the server clock and the runner's
    wall clock: the agent's last heartbeat is now older than the TTL."""
    srv.agents[aid]["last_heartbeat"] -= ms
    runner._hb_at[aid] = runner._hb_at.get(aid, 0) - ms


# ---------------------------------------------------------------------------
# regression tests
# ---------------------------------------------------------------------------

def test_commit_refreshes_stale_heartbeat_before_post(tsrv):
    srv, agent, aid = tsrv, _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms, "tags": ["defi"]}
    task = srv.add_task()
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()

    _stale(srv, r, aid)  # 120s pass: heartbeat older than the 60s TTL

    # negative control: without a refresh the commit is rejected
    with pytest.raises(net.HttpError) as exc:
        srv.post_json("http://x", "/v1/a2a/bids/commit",
                      {"task_id": task["task_id"], "agent_id": aid,
                       "commitment": "0x" + "ab" * 32})
    assert exc.value.status == 403

    r._commit(task, agent, 1.0)  # must refresh first, then commit
    assert (task["task_id"], aid) in srv.commits
    # the refresh heartbeat immediately precedes the commit POST
    assert srv.events[-2:] == [("heartbeat", aid), ("commit", task["task_id"], aid)]


def test_reveal_refreshes_stale_heartbeat_before_post(tsrv):
    srv, agent, aid = tsrv, _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms, "tags": ["defi"]}
    task = srv.add_task()
    r = liv_runner.Runner("http://x", [agent])
    r._commit(task, agent, 1.0)
    entry = r.commits[task["task_id"]][aid]
    assert entry["status"] == "committed"

    _stale(srv, r, aid)

    r._reveal(task["task_id"], aid, entry)  # must refresh first, then reveal
    assert srv.commits[(task["task_id"], aid)] == "revealed"
    assert srv.events[-2:] == [("heartbeat", aid), ("reveal", task["task_id"], aid)]


def test_no_reregistration_storm_when_registry_healthy(tsrv):
    srv, agent, aid = tsrv, _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms, "tags": ["defi"]}
    t1, t2 = srv.add_task("t-hb-1"), srv.add_task("t-hb-2")
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()
    r._commit(t1, agent, 1.0)
    r._commit(t2, agent, 1.0)
    assert (t1["task_id"], aid) in srv.commits
    assert (t2["task_id"], aid) in srv.commits
    assert srv.register_calls == []  # healthy registry: never re-register


def test_commit_self_heals_unknown_agent_via_reregister(tsrv):
    srv, agent, aid = tsrv, _agent("hb-new"), "hb-new"
    # agent NOT in the registry (e.g. wiped by a restart)
    task = srv.add_task()
    r = liv_runner.Runner("http://x", [agent])
    r._commit(task, agent, 1.0)
    assert srv.register_calls == [aid]  # exactly one re-registration
    assert (task["task_id"], aid) in srv.commits  # commit accepted after heal
    assert srv.events == [("heartbeat", aid), ("register", aid),
                          ("heartbeat", aid), ("commit", task["task_id"], aid)]


def test_heartbeat_dedupe_within_fresh_window(tsrv):
    srv, agent, aid = tsrv, _agent(), "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms, "tags": ["defi"]}
    t1, t2 = srv.add_task("t-hb-1"), srv.add_task("t-hb-2")
    r = liv_runner.Runner("http://x", [agent])
    r._commit(t1, agent, 1.0)
    r._commit(t2, agent, 1.0)  # seconds later: must not re-heartbeat
    assert srv.heartbeat_calls == [aid]


def test_full_pass_survives_slow_task_fetches(monkeypatch, tmp_path):
    """End-to-end production scenario: every paginated task fetch costs
    2 minutes (truncation retries), so the pass-start heartbeat is long
    expired by the time the commit sweep runs.  The pass must still get
    its commits accepted."""
    srv = TtlServer()
    aid = "hb-0"
    srv.agents[aid] = {"last_heartbeat": srv.now_ms, "tags": ["defi"]}
    task = srv.add_task("t-slow")
    monkeypatch.setattr(liv_runner, "get_json", srv.get_json)
    monkeypatch.setattr(liv_runner, "post_json", srv.post_json)
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    monkeypatch.setattr(liv_runner.fountain, "ensure_supply",
                        lambda *a, **k: {"lane_a": [], "lane_b": [],
                                         "lane_c": []})

    r = liv_runner.Runner("http://x", [_agent(aid)])

    def slow_pages(base_url, path, **kw):
        # simulate a slow paginated fetch: 2 minutes burn on both clocks
        srv.advance(120_000)
        for a in r.by_id:
            r._hb_at[a] = r._hb_at.get(a, 0) - 120_000
        return list(srv.tasks.values())

    monkeypatch.setattr(liv_runner, "get_all_pages", slow_pages)
    r.run_once()  # must not raise

    assert ("t-slow", aid) in srv.commits  # commit accepted despite stale start
    assert r.pass_ok is True
    assert srv.register_calls == []
