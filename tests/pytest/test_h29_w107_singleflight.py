"""W-107 — dispatch-before-consume race: per-token single-flight guard.

Regression tests for the hunt-29 item-4 finding in
``sincor2.mvp_blueprints.billing.x402_paid_resource``: concurrent
requests bearing the SAME single-use token all passed the P1-1 peek,
all ran the provider dispatch (spending provider money), and only the
atomic-consume winner was served (verified pre-fix, synchronised:
N=2 -> provider_calls=2, N=4 -> provider_calls=4).

The fix is a per-token single-flight guard around the
peek -> dispatch -> consume pipeline: exactly one leader dispatches;
concurrent duplicates are refused 409 access_token_already_consumed
WITHOUT dispatching. It must NOT become consume-before-dispatch:
a leader dispatch that fails (503 here) leaves the token unconsumed
and the flight released, so a later retry dispatches fresh and can
serve 200 (P1-1 preservation, PR #533).

Synchronisation design (genuine overlap, not a naive two-thread race):
  * a fresh start-gate Event per trial parks every worker thread,
    then releases them simultaneously;
  * a FRESH barrier per trial, sized N, installed INSIDE the provider
    stub: pre-fix, all N workers enter dispatch and the barrier
    releases at once (provider_calls == N, the bug); post-fix, only
    the leader ever enters the stub, so its barrier wait times out
    (BrokenBarrierError, swallowed by the stub) while the duplicates
    are refused at the flight guard — provider_calls == 1;
  * one pre-created Flask test client per thread (never shared).

Resource under test: web-search-read (the metered Exa seam —
``sincor2.web_search_read.search_provider``), the real money leak
in the W-107 severity map.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing
import sincor2.web_search_read as ws
import sincor2.x402_payments as x402

RESOURCE = "web-search-read"
WALLET = "0x" + "11" * 20
BARRIER_TIMEOUT = 3.0  # leader's stub wait when duplicates never enter

FIXTURE_ROW = {"title": "t", "url": "https://example.com/x",
               "text": "hello"}


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def app():
    flask_app = Flask("h29_w107_singleflight")
    flask_app.register_blueprint(billing.bp)
    flask_app.config.update(TESTING=True)
    return flask_app


def _mint_token() -> str:
    """Mint a fulfilled single-use token directly in the x402 store
    (same pattern as the W-107 verification probe)."""
    ch = x402.create_challenge(RESOURCE)
    assert ch.get("challenge_id"), ch
    token = "w107-test-" + uuid.uuid4().hex
    exp = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    with x402._conn() as conn:
        conn.execute(
            "UPDATE x402_challenges SET status='fulfilled', "
            "access_token=?, token_expires_at=?, payer_wallet=? "
            "WHERE challenge_id=?",
            (token, exp, WALLET, ch["challenge_id"]))
        conn.commit()
    return token


class _ProviderStub:
    """Synchronised search_provider stub.

    Counts calls; when a barrier is installed it blocks inside the
    dispatch until the barrier releases or times out (post-fix the
    leader is the ONLY entrant, so the timeout is the expected path
    and BrokenBarrierError is recorded, not raised).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0
        self.barrier = None
        self.mode = "ok"  # "ok" | "fail503"
        self.barrier_broken = False

    def __call__(self, query):
        with self._lock:
            self.calls += 1
            barrier = self.barrier
            mode = self.mode
        if barrier is not None:
            try:
                barrier.wait(timeout=BARRIER_TIMEOUT)
            except threading.BrokenBarrierError:
                with self._lock:
                    self.barrier_broken = True
        if mode == "fail503":
            raise ws.ProviderNotConfigured("EXA_API_KEY is not set")
        return [dict(FIXTURE_ROW)]


@pytest.fixture()
def stub(monkeypatch):
    provider = _ProviderStub()
    monkeypatch.setattr(ws, "search_provider", provider)
    return provider


def _run_concurrent(app, tokens):
    """Fire one POST /api/paid/<resource> per token, all at once.

    Fresh start-gate per call; one pre-created test client per
    thread. Returns a list of (status, body) in token order.
    """
    n = len(tokens)
    clients = [app.test_client() for _ in range(n)]
    start_gate = threading.Event()
    results = {}

    def worker(i):
        start_gate.wait(timeout=10)
        resp = clients[i].post(
            f"/api/paid/{RESOURCE}",
            headers={"X-Payment-Token": tokens[i]},
            json={"mode": "search", "query": "x"})
        results[i] = (resp.status_code, resp.get_json(silent=True) or {})

    threads = [threading.Thread(target=worker, args=(i,),
                                name=f"w107-worker-{i}")
               for i in range(n)]
    for t in threads:
        t.start()
    start_gate.set()  # release all workers simultaneously
    for t in threads:
        t.join(timeout=30)
    assert not [t.name for t in threads if t.is_alive()], \
        "worker thread(s) hung — possible single-flight deadlock"
    assert len(results) == n
    return [results[i] for i in range(n)]


# ---------------------------------------------------------------------------
# W-107 regressions: concurrent duplicates dispatch exactly once
# ---------------------------------------------------------------------------

class TestConcurrentDuplicatesDispatchOnce:
    def test_n2_same_token_provider_called_once(self, app, x402_db, stub):
        stub.barrier = threading.Barrier(2)  # fresh barrier, in the stub
        token = _mint_token()
        results = _run_concurrent(app, [token, token])

        statuses = sorted(s for s, _ in results)
        assert statuses == [200, 409], results
        assert stub.calls == 1, (
            f"provider dispatched {stub.calls} times for one token — "
            "W-107 race is back")
        loser_bodies = [b for s, b in results if s == 409]
        assert loser_bodies[0]["error"] == "access_token_already_consumed"
        winner_bodies = [b for s, b in results if s == 200]
        assert winner_bodies[0]["ok"] is True
        assert winner_bodies[0]["count"] == 1
        # No flight state may linger after the pipeline finishes.
        assert billing._PAID_TOKEN_FLIGHTS == {}

    def test_n4_same_token_provider_called_once(self, app, x402_db, stub):
        stub.barrier = threading.Barrier(4)  # fresh barrier, in the stub
        token = _mint_token()
        results = _run_concurrent(app, [token, token, token, token])

        statuses = sorted(s for s, _ in results)
        assert statuses == [200, 409, 409, 409], results
        assert stub.calls == 1, (
            f"provider dispatched {stub.calls} times for one token — "
            "W-107 race is back")
        for status, body in results:
            if status == 409:
                assert body["error"] == "access_token_already_consumed"
        assert billing._PAID_TOKEN_FLIGHTS == {}


# ---------------------------------------------------------------------------
# Token preservation under concurrency (P1-1 must survive the fix)
# ---------------------------------------------------------------------------

class TestTokenPreservationUnderConcurrency:
    def test_leader_503_duplicate_refused_token_still_consumable(
            self, app, x402_db, stub):
        stub.mode = "fail503"
        stub.barrier = threading.Barrier(2)  # fresh barrier, in the stub
        token = _mint_token()

        results = _run_concurrent(app, [token, token])
        statuses = sorted(s for s, _ in results)
        # Leader's dispatch fails 503; the duplicate is refused 409
        # WITHOUT a second dispatch.
        assert statuses == [409, 503], results
        assert stub.calls == 1
        loser = [b for s, b in results if s == 503][0]
        assert loser["error"] == "provider_not_configured"

        # P1-1: the failed dispatch did NOT burn the token...
        assert x402.access_granted(token, RESOURCE, consume=False) is True
        # ...and the flight was released on the failure exit path.
        assert billing._PAID_TOKEN_FLIGHTS == {}

        # A subsequent retry dispatches fresh and serves 200.
        stub.mode = "ok"
        stub.barrier = threading.Barrier(1)  # fresh barrier; releases at once
        client = app.test_client()
        retry = client.post(
            f"/api/paid/{RESOURCE}",
            headers={"X-Payment-Token": token},
            json={"mode": "search", "query": "x"})
        assert retry.status_code == 200, retry.get_json(silent=True)
        assert retry.get_json()["count"] == 1
        assert stub.calls == 2  # exactly one fresh dispatch for the retry
        assert billing._PAID_TOKEN_FLIGHTS == {}

    def test_sequential_503_then_retry_still_works(self, app, x402_db, stub):
        """Sequential P1-1 baseline through the guarded path: a solo
        503 preserves the token and leaves no flight behind."""
        stub.mode = "fail503"  # no barrier: solo request
        token = _mint_token()
        client = app.test_client()
        r1 = client.post(
            f"/api/paid/{RESOURCE}",
            headers={"X-Payment-Token": token},
            json={"mode": "search", "query": "x"})
        assert r1.status_code == 503, r1.get_json(silent=True)
        assert billing._PAID_TOKEN_FLIGHTS == {}
        stub.mode = "ok"
        r2 = client.post(
            f"/api/paid/{RESOURCE}",
            headers={"X-Payment-Token": token},
            json={"mode": "search", "query": "x"})
        assert r2.status_code == 200, r2.get_json(silent=True)
        assert stub.calls == 2
        assert billing._PAID_TOKEN_FLIGHTS == {}


# ---------------------------------------------------------------------------
# The guard is per-token, not a global paid-request lock
# ---------------------------------------------------------------------------

class TestGuardIsPerToken:
    def test_different_tokens_dispatch_concurrently(self, app, x402_db, stub):
        # Barrier(2) inside the stub releases ONLY if both leaders are
        # inside dispatch at the same time; a global lock would strand
        # one leader outside and break the barrier by timeout.
        stub.barrier = threading.Barrier(2)
        token_a = _mint_token()
        token_b = _mint_token()
        results = _run_concurrent(app, [token_a, token_b])

        statuses = sorted(s for s, _ in results)
        assert statuses == [200, 200], results
        assert stub.calls == 2
        assert stub.barrier_broken is False, (
            "different tokens did not dispatch concurrently — the "
            "guard is serialising globally instead of per-token")
        assert billing._PAID_TOKEN_FLIGHTS == {}
