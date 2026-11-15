"""Anonymous-poster / self-bid selection filter in sweep_commits.

Root cause (production, 2026-11-14): the server's anti-gaming guard
(``sincor2.axm_antigaming.check_self_bid``, enforced inside
``check_bid_eligibility`` on BOTH bid paths — ``place_bid`` and
``commit_bid`` — and re-checked at ``reveal_bid`` and winner
selection) rejects, fail-closed with HTTP 403:

* ANY bid on a task whose ``poster_id`` is absent (None / "" /
  missing) while the task carries a bounty (> 0 AXM) —
  ``AnonymousBountyError``: "poster_id is absent so self-dealing
  cannot be ruled out".  The guard cannot prove the bidder is not
  the poster, so it applies to EVERY bidder identically.
* A bid from the poster itself: ``poster_id == agent_id`` —
  ``SelfBidError``.  ``poster_id`` is server-bound at creation
  (EIP-191 signer wallet, or an internal poster's id), so the
  comparison is the exact string comparison the server performs.

The sweep had NO poster filter, so a verified live pass burned all
7 of its commit attempts on anonymous-poster bounty tasks — every
one a guaranteed 403.

Fix under test: ``sweep_commits`` mirrors ``check_self_bid`` at
SELECTION time (never a bypass — the server guard is untouched):

* poster absent + bounty > 0 -> skip the task for the whole cohort
  (the sweep only reaches this check with bounty > 0 already
  established, which is exactly the guard's firing condition; a
  zero-bounty anonymous task is NOT claimed by this filter — the
  sweep's pre-existing bounty rule is what skips those, unchanged).
* poster == the bidding agent -> exclude that agent only; the rest
  of the cohort may still commit, exactly as the server allows.

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


class PosterServer:
    """In-memory A2A surface: agents listing + heartbeat + commit."""

    def __init__(self, reputations=None):
        self.reputations = reputations or {}
        self.commits = {}               # (task_id, aid) -> commitment

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
            return {"commitment": body["commitment"]}
        raise AssertionError(f"unexpected POST {path}")


def _agent(aid):
    return {"agent_id": aid, "name": aid, "description": "poster test",
            "version": "1.0.0", "capability_tags": ["defi"], "skills": [],
            "wallet": "0x0000000000000000000000000000000000000001",
            "chain_id": 8453}


def _task(task_id, poster_id="external-poster", bounty=1.0,
          requires_merit=False, omit_poster=False):
    now = int(time.time() * 1000)
    task = {"task_id": task_id, "state": "open", "sealed": True,
            "tags": ["defi"], "bounty_axm": bounty,
            "seed_key": "", "title": "T",
            "requires_merit": requires_merit,
            "commit_deadline": now + 5 * 60_000,
            "reveal_deadline": now + 10 * 60_000}
    if not omit_poster:
        task["poster_id"] = poster_id
    return task


@pytest.fixture
def make_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))
    # Test agents hold no cohort keys: unsigned/operator-token path.
    monkeypatch.setattr(liv_runner, "load_cohort_keys", lambda path=None: {})
    logs = []
    monkeypatch.setattr(liv_runner, "log", logs.append)

    def _make(agent_ids, reputations=None):
        srv = PosterServer(reputations)
        monkeypatch.setattr(liv_runner, "post_json", srv.post_json)
        monkeypatch.setattr(liv_runner, "get_json", srv.get_json)
        r = liv_runner.Runner("http://x", [_agent(a) for a in agent_ids])
        return srv, r

    _make.logs = logs
    return _make


def _anon_logs(make_runner):
    return [m for m in make_runner.logs if "anonymous-poster" in m]


# -- anonymous poster: the whole cohort is rejected server-side -------------

def test_anonymous_poster_bounty_task_skipped(make_runner):
    srv, r = make_runner(["p-0", "p-1"])
    r.sweep_commits({"t-anon": _task("t-anon", poster_id=None)})
    assert srv.commits == {}, \
        "anonymous-poster bounty task was attempted (guaranteed 403)"
    assert any("t-anon" in m for m in _anon_logs(make_runner)), \
        "anonymous-poster skip was not logged"


def test_missing_poster_key_treated_anonymous(make_runner):
    """Server reads task.get('poster_id') -> None for a missing key:
    same anonymous treatment."""
    srv, r = make_runner(["p-0"])
    r.sweep_commits({"t-nokey": _task("t-nokey", omit_poster=True)})
    assert srv.commits == {}
    assert any("t-nokey" in m for m in _anon_logs(make_runner))


def test_empty_string_poster_treated_anonymous(make_runner):
    """Server checks falsiness, not just None: '' is anonymous too."""
    srv, r = make_runner(["p-0"])
    r.sweep_commits({"t-empty": _task("t-empty", poster_id="")})
    assert srv.commits == {}
    assert any("t-empty" in m for m in _anon_logs(make_runner))


def test_anonymous_merit_task_reports_anonymous_not_merit(make_runner):
    """An anonymous merit task dies on the poster guard first (the
    sweep's task-level check precedes per-agent merit selection):
    the skip must be attributed to the poster guard, and no agent —
    qualified or not — may attempt it."""
    srv, r = make_runner(["p-0"], reputations={"p-0": 0.0})
    r.sweep_commits({"t-anon-merit": _task("t-anon-merit", poster_id=None,
                                           bounty=5.0, requires_merit=True)})
    assert srv.commits == {}
    assert any("t-anon-merit" in m for m in _anon_logs(make_runner))
    assert not any("skipping merit task" in m for m in make_runner.logs), \
        "skip was attributed to the merit filter, not the poster guard"


# -- named poster: biddable, exactly as the server allows -------------------

def test_named_poster_task_attempted(make_runner):
    srv, r = make_runner(["p-0"])
    r.sweep_commits({"t-named": _task("t-named",
                                      poster_id="sincor-fountain")})
    assert ("t-named", "p-0") in srv.commits
    assert _anon_logs(make_runner) == []


# -- self-posted task: only the poster is excluded --------------------------

def test_self_posted_task_excludes_only_the_poster(make_runner):
    srv, r = make_runner(["p-0", "p-1"])
    r.sweep_commits({"t-self": _task("t-self", poster_id="p-0")})
    assert ("t-self", "p-0") not in srv.commits, \
        "poster attempted its own task (server SelfBidError)"
    assert ("t-self", "p-1") in srv.commits, \
        "non-poster agent was wrongly excluded from a named-poster task"


def test_self_posted_task_with_only_the_poster_skipped(make_runner):
    srv, r = make_runner(["p-0"])
    r.sweep_commits({"t-self": _task("t-self", poster_id="p-0")})
    assert srv.commits == {}
    assert any("self" in m and "t-self" in m for m in make_runner.logs), \
        "self-bid exclusion of the whole eligible set was not logged"


# -- no over-filtering -------------------------------------------------------

def test_zero_bounty_anonymous_task_not_claimed_by_poster_filter(
        make_runner):
    """The server ALLOWS bids on anonymous tasks carrying no bounty
    (bounty == 0 disarms the guard).  The sweep skips zero-bounty
    tasks for its own pre-existing reasons — this filter must not
    claim or change that: no anonymous-poster skip may be logged."""
    srv, r = make_runner(["p-0"])
    r.sweep_commits({"t-free": _task("t-free", poster_id=None, bounty=0.0)})
    assert srv.commits == {}
    assert _anon_logs(make_runner) == [], \
        "poster filter over-claimed a zero-bounty task the server allows"
