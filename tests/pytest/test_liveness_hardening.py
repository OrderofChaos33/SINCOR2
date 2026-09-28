"""Liveness runner hardening tests (2026-09-28 production incidents).

(a) a failed pass exits nonzero (never 0)
(b) net.py retries IncompleteRead / connection-reset failures with
    backoff; truncated JSON bodies are retried; /v1/a2a/tasks is
    fetched page-by-page and a truncated page is NEVER treated as the
    complete inventory
(c) one agent's heartbeat transport failure is isolated per-agent: the
    rest of the pass (other heartbeats, commits, reveals, closes,
    performance) still runs, and the pass reports partial failure

All HTTP is mocked — no live network calls.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIVENESS = os.path.join(REPO, "liveness")
sys.path.insert(0, LIVENESS)
sys.path.insert(0, os.path.join(REPO, "src"))

import net  # noqa: E402
import runner as liv_runner  # noqa: E402


# ---------------------------------------------------------------------------
# mock transport
# ---------------------------------------------------------------------------

class FakeResp:
    def __init__(self, status, body: bytes):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)


@pytest.fixture
def tmp_state(monkeypatch, tmp_path):
    monkeypatch.setattr(liv_runner, "COMMITS_PATH",
                        str(tmp_path / "commits.json"))
    monkeypatch.setattr(liv_runner, "QUEUE_PATH",
                        str(tmp_path / "queue.json"))


def _cohort(*aids):
    return [{"agent_id": aid, "capability_tags": ["defi"]} for aid in aids]


# ---------------------------------------------------------------------------
# (b) retry hardening
# ---------------------------------------------------------------------------

def test_incomplete_read_retried_then_succeeds(monkeypatch, no_sleep):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise http.client.IncompleteRead(partial=b'{"tasks":', expected=64)
        return FakeResp(200, b'{"tasks": [], "page": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert net.get_json("http://x", "/v1/a2a/tasks") == {"tasks": [], "page": 1}
    assert calls["n"] == 3  # two truncated reads, then success


def test_silently_truncated_json_body_retried(monkeypatch, no_sleep):
    """A 200 with a cut-off body raises nothing at the socket layer — the
    corrupt payload itself must trigger the retry."""
    bodies = [b'{"tasks": [{"task_id": "t1",',  # truncated mid-body, no error
              b'{"tasks": [], "page": 1, "pages": 1}']
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return FakeResp(200, bodies[min(calls["n"], 2) - 1])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert net.get_json("http://x", "/v1/a2a/tasks") == {
        "tasks": [], "page": 1, "pages": 1}
    assert calls["n"] == 2


def test_empty_body_retried(monkeypatch, no_sleep):
    bodies = [b"", b"   ", b'{"ok": true}']
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return FakeResp(200, bodies[calls["n"] - 1])

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert net.get_json("http://x", "/v1/a2a/pool") == {"ok": True}
    assert calls["n"] == 3


@pytest.mark.parametrize("boom", [
    http.client.RemoteDisconnected(
        "Remote end closed connection without response"),
    http.client.BadStatusLine(""),
    ConnectionResetError("Connection reset by peer"),
    BrokenPipeError("Broken pipe"),
    TimeoutError("timed out"),
])
def test_connection_style_failures_retried(monkeypatch, no_sleep, boom):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise boom
        return FakeResp(200, b'{"ok": true}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert net.post_json("http://x", "/v1/a2a/heartbeat",
                         {"agent_id": "a1"}) == {"ok": True}
    assert calls["n"] == 2


def test_http_error_never_retried(monkeypatch, no_sleep):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", {}, io.BytesIO(b"unknown agent"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(net.HttpError) as ei:
        net.get_json("http://x", "/v1/a2a/heartbeat")
    assert ei.value.status == 404
    assert calls["n"] == 1  # 4xx: no retry


def test_persistent_transport_failure_raises_after_tries(monkeypatch, no_sleep):
    def fake_urlopen(req, timeout=None):
        raise http.client.IncompleteRead(partial=b"", expected=10)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="request failed after 3 tries"):
        net.get_json("http://x", "/v1/a2a/tasks", tries=3)


# ---------------------------------------------------------------------------
# (b) paginated /v1/a2a/tasks — never a partial inventory
# ---------------------------------------------------------------------------

def _paged_server(monkeypatch, no_sleep, total=137, per_page=100,
                  truncate_page2_once=True):
    """Fake urlopen serving page=1..N; optionally truncates page 2 once."""
    calls = []

    def fake_urlopen(req, timeout=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        page = int(q["page"][0])
        assert int(q["per_page"][0]) == per_page
        calls.append(page)
        if truncate_page2_once and page == 2 and calls.count(2) == 1:
            raise http.client.IncompleteRead(partial=b'{"tasks": [',
                                             expected=9999)
        start = (page - 1) * per_page
        batch = [{"task_id": f"t{i}"} for i in range(start, min(start + per_page, total))]
        payload = {"tasks": batch, "page": page, "per_page": per_page,
                   "total": total,
                   "pages": (total + per_page - 1) // per_page}
        return FakeResp(200, json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_get_all_pages_paginates_and_retries_truncated_page(
        monkeypatch, no_sleep):
    calls = _paged_server(monkeypatch, no_sleep, total=137)
    items = net.get_all_pages("http://x", "/v1/a2a/tasks", per_page=100)
    assert len(items) == 137
    assert items[0]["task_id"] == "t0"
    assert items[-1]["task_id"] == "t136"
    # page 1 once, page 2 twice (truncated attempt retried, not accepted)
    assert calls == [1, 2, 2]


def test_get_all_pages_never_returns_partial_inventory(
        monkeypatch, no_sleep):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        page = int(q["page"][0])
        if page == 1:
            batch = [{"task_id": f"t{i}"} for i in range(100)]
            payload = {"tasks": batch, "page": 1, "per_page": 100,
                       "total": 150, "pages": 2}
            return FakeResp(200, json.dumps(payload).encode())
        calls["n"] += 1
        raise http.client.IncompleteRead(partial=b"", expected=10)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match="request failed after 1 tries"):
        net.get_all_pages("http://x", "/v1/a2a/tasks", per_page=100, tries=1)
    assert calls["n"] == 1  # the failure surfaced; no silent 100-item list


def test_get_all_pages_rejects_non_object_payload(monkeypatch, no_sleep):
    def fake_urlopen(req, timeout=None):
        return FakeResp(200, b'["not", "an", "object"]')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(net.TruncatedResponse):
        net.get_all_pages("http://x", "/v1/a2a/tasks")


# ---------------------------------------------------------------------------
# (a) nonzero exit on failed pass
# ---------------------------------------------------------------------------

def test_one_pass_false_when_run_once_raises(monkeypatch, tmp_state, capsys):
    def boom(self):
        raise RuntimeError("boom")
    monkeypatch.setattr(liv_runner.Runner, "run_once", boom)
    assert liv_runner.one_pass("http://x", _cohort("a1")) is False
    assert "pass failed: boom" in capsys.readouterr().out  # log line kept


def test_one_pass_false_on_partial_failure(monkeypatch, tmp_state):
    def degraded(self):
        self.pass_ok = False
    monkeypatch.setattr(liv_runner.Runner, "run_once", degraded)
    assert liv_runner.one_pass("http://x", _cohort("a1")) is False


def test_one_pass_true_on_clean_pass(monkeypatch, tmp_state):
    monkeypatch.setattr(liv_runner.Runner, "run_once", lambda self: None)
    assert liv_runner.one_pass("http://x", _cohort("a1")) is True


def test_main_exit_code_wires_one_pass_result(monkeypatch, tmp_state):
    monkeypatch.setattr(sys, "argv",
                        ["runner.py", "--once", "--base-url", "http://x"])
    monkeypatch.setattr(liv_runner, "one_pass", lambda *a: False)
    assert liv_runner.main() == 1
    monkeypatch.setattr(liv_runner, "one_pass", lambda *a: True)
    assert liv_runner.main() == 0


# ---------------------------------------------------------------------------
# (c) per-agent heartbeat isolation
# ---------------------------------------------------------------------------

def test_heartbeat_transport_failure_isolated_per_agent(
        monkeypatch, tmp_state):
    heartbeats = []

    def fake_post(base_url, path, body, **kw):
        assert path == "/v1/a2a/heartbeat"
        if body["agent_id"] == "lx-bad":
            raise RuntimeError("Remote end closed connection without response")
        heartbeats.append(body["agent_id"])
        return {"ok": True}

    monkeypatch.setattr(liv_runner, "post_json", fake_post)
    r = liv_runner.Runner("http://x", _cohort("lx-0", "lx-bad", "lx-2"))
    r.heartbeat_all()  # must not raise
    assert heartbeats == ["lx-0", "lx-2"]  # others still heartbeat
    assert r.pass_ok is False  # partial failure recorded


def test_heartbeat_404_self_heal_still_works(monkeypatch, tmp_state):
    calls = []
    first = {"n": 0}

    def fake_post(base_url, path, body, **kw):
        calls.append((path, body.get("agent_id")))
        if path == "/v1/a2a/heartbeat" and first["n"] == 0:
            first["n"] += 1
            raise net.HttpError(404, "unknown agent")
        return {"ok": True}

    monkeypatch.setattr(liv_runner, "post_json", fake_post)
    agent = {"agent_id": "lx-0", "name": "LX0", "description": "d",
             "capability_tags": ["defi"], "wallet": "0x0000"}
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()
    paths = [c[0] for c in calls if isinstance(c, tuple)]
    assert paths == ["/v1/a2a/heartbeat", "/v1/a2a/register",
                     "/v1/a2a/heartbeat"]
    assert r.pass_ok is True


def test_heartbeat_re_register_transport_failure_marks_pass(
        monkeypatch, tmp_state):
    def fake_post(base_url, path, body, **kw):
        if path == "/v1/a2a/heartbeat":
            raise net.HttpError(404, "unknown agent")
        raise ConnectionResetError("reset during re-register")

    monkeypatch.setattr(liv_runner, "post_json", fake_post)
    agent = {"agent_id": "lx-0", "name": "LX0", "description": "d",
             "capability_tags": ["defi"], "wallet": "0x0000"}
    r = liv_runner.Runner("http://x", [agent])
    r.heartbeat_all()
    assert r.pass_ok is False


def test_pass_completes_other_work_after_heartbeat_failure(
        monkeypatch, tmp_state):
    """One dead agent must not abort commits/reveals/closes/performance."""
    heartbeats, commits = [], []
    now_ms = 1_700_000_000_000

    def fake_post(base_url, path, body, **kw):
        if path == "/v1/a2a/heartbeat":
            if body["agent_id"] == "lx-bad":
                raise RuntimeError(
                    "Remote end closed connection without response")
            heartbeats.append(body["agent_id"])
            return {"ok": True}
        if path == "/v1/a2a/bids/commit":
            commits.append(body["agent_id"])
            return {"committed": True}
        raise AssertionError(f"unexpected POST {path}")

    task = {"task_id": "t1", "state": "open", "sealed": True,
            "tags": ["defi"], "bounty_axm": 1.0,
            "seed_key": "fountain-defi-metrics-sweep-2026-09-28",
            "title": "T",
            "commit_deadline": now_ms + 5 * 60_000,
            "reveal_deadline": now_ms + 10 * 60_000}
    monkeypatch.setattr(liv_runner, "post_json", fake_post)
    monkeypatch.setattr(liv_runner, "get_all_pages",
                        lambda base_url, path, **kw: [dict(task)])
    monkeypatch.setattr(liv_runner.fountain, "ensure_supply",
                        lambda *a, **k: {})

    r = liv_runner.Runner("http://x", _cohort("lx-0", "lx-bad", "lx-1"))
    r.now_ms = now_ms
    r.run_once()  # must not raise

    assert heartbeats == ["lx-0", "lx-1"]
    # the sweep still runs for ALL eligible agents: a heartbeat transport
    # failure must not change bidding semantics or abort the pass
    assert sorted(commits) == ["lx-0", "lx-1", "lx-bad"]
    assert r.pass_ok is False  # ...but the pass reports partial failure
