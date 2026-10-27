"""W-111 — free-path dispatch-before-consume race (token-less).

Regression tests for the hunt-30 finding in
``sincor2.mvp_blueprints.billing._x402_paid_resource_leader``: the
Wave 1 free-ration branch did remaining() check ->
_paid_skill_dispatch() -> try_consume only on a 200, and the W-107
single-flight guard was acquired only ``if token:`` — token-less
requests skipped it entirely. Two concurrent token-less requests for
the same identity+resource could therefore both dispatch (provider
cost paid twice) for one logical free serve slot.

The fix adds a free-path flight guard keyed per (identity,
resource_id), reusing the W-107 flight registry with a namespaced
hashed key ("free\\x00" + resource + "\\x00" + identity): the leader
acquires it BEFORE dispatch; a concurrent duplicate does NOT
dispatch and falls through to the unchanged paid flow (402
challenge with a wallet param, 400 without). Consume-only-on-200 is
preserved, so a failed leader burns no ration and releases the
flight for a fresh retry.

Synchronisation design (genuine overlap, harness-controlled):
  * a fresh start-gate Event per trial parks every worker thread,
    then releases them simultaneously;
  * the provider stub is event-controlled: it signals entry and
    blocks on a release Event, so the leader is provably inside
    dispatch while the duplicates attempt their flight acquisition;
  * the test waits until every duplicate has COMPLETED (its
    fall-through response is recorded) before releasing the leader —
    that is what makes provider_calls == 1 deterministic rather
    than a timing race;
  * one pre-created Flask test client per thread (never shared).

Resource under test: web-search-read (the metered Exa seam —
``sincor2.web_search_read.search_provider``), the same provider
surface as the W-107 suite.
"""
from __future__ import annotations

import threading
import time

import pytest
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint (see test_x402_verify_frontrun.py).
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing
import sincor2.web_search_read as ws
import sincor2.x402_payments as x402
from sincor2.free_ration import daily_ration_tracker

RESOURCE = "web-search-read"
WALLET = "0x" + "11" * 20
WALLET_B = "0x" + "22" * 20

FIXTURE_ROW = {"title": "t", "url": "https://example.com/x",
               "text": "hello"}


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_ration():
    daily_ration_tracker.clear()
    yield
    daily_ration_tracker.clear()
    # No flight state may linger between tests.
    assert billing._PAID_TOKEN_FLIGHTS == {}


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def app():
    flask_app = Flask("h30_w111_free_flight")
    flask_app.register_blueprint(billing.bp)
    flask_app.config.update(TESTING=True)
    return flask_app


class _ProviderStub:
    """Event-controlled search_provider stub.

    Counts calls; signals first/second entry via Events; when
    ``block`` is set, waits inside dispatch on ``release`` (with a
    timeout so a broken test fails instead of hanging forever).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = 0
        self.mode = "ok"  # "ok" | "fail503"
        self.block = False
        self.release = threading.Event()
        self.entered_first = threading.Event()
        self.entered_second = threading.Event()

    def __call__(self, query):
        with self._lock:
            self.calls += 1
            n = self.calls
            mode = self.mode
            block = self.block
        if n == 1:
            self.entered_first.set()
        if n >= 2:
            self.entered_second.set()
        if block:
            self.release.wait(timeout=10)
        if mode == "fail503":
            raise ws.ProviderNotConfigured("EXA_API_KEY is not set")
        return [dict(FIXTURE_ROW)]


@pytest.fixture()
def stub(monkeypatch):
    provider = _ProviderStub()
    monkeypatch.setattr(ws, "search_provider", provider)
    return provider


def _spawn(app, wallets):
    """Fire one token-less POST per wallet, all at once.

    Returns (threads, results) where results maps index ->
    (status, body). The caller coordinates the stub release and
    then joins via _join.
    """
    n = len(wallets)
    clients = [app.test_client() for _ in range(n)]
    start_gate = threading.Event()
    results = {}

    def worker(i):
        start_gate.wait(timeout=10)
        resp = clients[i].post(
            f"/api/paid/{RESOURCE}?wallet={wallets[i]}",
            json={"mode": "search", "query": "x"})
        results[i] = (resp.status_code, resp.get_json(silent=True) or {})

    threads = [threading.Thread(target=worker, args=(i,),
                                name=f"w111-worker-{i}")
               for i in range(n)]
    for t in threads:
        t.start()
    start_gate.set()
    return threads, results


def _wait_until(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _join(threads):
    for t in threads:
        t.join(timeout=30)
    assert not [t.name for t in threads if t.is_alive()], \
        "worker thread(s) hung — possible free-flight deadlock"


def _is_free_200(result):
    status, body = result
    return status == 200 and body.get("x402_free_ration") is True


# ---------------------------------------------------------------------------
# Key namespacing: the free key can never collide with a token key
# and never stores the raw identity
# ---------------------------------------------------------------------------

class TestFreeFlightKey:
    def test_free_key_namespaced_and_hashed(self):
        free_key = billing._free_flight_key(WALLET.lower(), RESOURCE)
        token_key = billing._paid_flight_key(WALLET.lower(), RESOURCE)
        assert free_key != token_key
        assert WALLET.lower() not in free_key
        assert RESOURCE not in free_key
        # Deterministic, and distinct per identity / resource.
        assert free_key == billing._free_flight_key(
            WALLET.lower(), RESOURCE)
        assert free_key != billing._free_flight_key(
            WALLET_B.lower(), RESOURCE)
        assert free_key != billing._free_flight_key(
            WALLET.lower(), "market-data-read")


# ---------------------------------------------------------------------------
# W-111 regression: concurrent token-less duplicates dispatch once
# ---------------------------------------------------------------------------

class TestConcurrentSameIdentityDispatchOnce:
    def test_n4_same_identity_provider_called_once(
            self, app, x402_db, stub):
        stub.block = True
        threads, results = _spawn(app, [WALLET] * 4)
        try:
            # Leader is provably inside dispatch...
            assert stub.entered_first.wait(timeout=10)
            # ...while all 3 duplicates complete their fall-through
            # (they must NOT dispatch and must NOT wait for release).
            assert _wait_until(lambda: len(results) == 3), results
            assert stub.calls == 1, (
                f"provider dispatched {stub.calls} times for one "
                "identity+resource — W-111 race is back")
        finally:
            stub.release.set()
            _join(threads)

        assert len(results) == 4
        assert stub.calls == 1
        ordered = [results[i] for i in range(4)]
        free_winners = [r for r in ordered if _is_free_200(r)]
        assert len(free_winners) == 1, ordered
        assert free_winners[0][1]["count"] == 1
        # Recorded actual statuses: the three duplicates fell through
        # to the paid flow and, with a wallet param present, got the
        # existing 402 challenge — none is a free 200.
        other_statuses = sorted(
            s for r in ordered if not _is_free_200(r) for s in [r[0]])
        assert other_statuses == [402, 402, 402], ordered
        for status, body in ordered:
            if not _is_free_200((status, body)):
                assert body.get("x402_free_ration") is not True
        # Exactly one ration unit was consumed, by the winner only.
        assert daily_ration_tracker.remaining(
            WALLET.lower(), RESOURCE) == 2
        assert billing._PAID_TOKEN_FLIGHTS == {}


# ---------------------------------------------------------------------------
# The guard is per (identity, resource), not a global free lock
# ---------------------------------------------------------------------------

class TestGuardIsPerIdentity:
    def test_distinct_identities_dispatch_concurrently(
            self, app, x402_db, stub):
        stub.block = True
        threads, results = _spawn(app, [WALLET, WALLET_B])
        try:
            # Both leaders must be inside dispatch at the same time;
            # a global lock would strand one outside (calls stays 1).
            assert stub.entered_second.wait(timeout=10), (
                "distinct identities did not dispatch concurrently — "
                "the free guard is sharing across identities")
            assert stub.calls == 2
        finally:
            stub.release.set()
            _join(threads)

        ordered = [results[i] for i in range(2)]
        assert all(_is_free_200(r) for r in ordered), ordered
        assert stub.calls == 2
        assert billing._PAID_TOKEN_FLIGHTS == {}


# ---------------------------------------------------------------------------
# Leader failure: flight released, ration NOT consumed (P1-1 shape)
# ---------------------------------------------------------------------------

class TestLeaderFailureReleasesFlight:
    def test_503_leader_burns_no_ration_and_retry_serves_free(
            self, app, x402_db, stub):
        stub.mode = "fail503"  # no blocking: solo sequential request
        client = app.test_client()
        r1 = client.post(
            f"/api/paid/{RESOURCE}?wallet={WALLET}",
            json={"mode": "search", "query": "x"})
        # The free dispatch failed (503 from the handler), so the
        # request fell through to the paid flow: with a wallet param
        # it gets the 402 challenge, NOT a free serve.
        assert r1.status_code == 402, r1.get_json(silent=True)
        assert (r1.get_json(silent=True) or {}).get(
            "x402_free_ration") is not True
        assert stub.calls == 1
        # Ration NOT consumed by the failed dispatch...
        assert daily_ration_tracker.remaining(
            WALLET.lower(), RESOURCE) == 3
        # ...and the flight was released on the failure exit path.
        assert billing._PAID_TOKEN_FLIGHTS == {}

        # An immediate retry dispatches fresh and can serve free 200.
        stub.mode = "ok"
        r2 = client.post(
            f"/api/paid/{RESOURCE}?wallet={WALLET}",
            json={"mode": "search", "query": "x"})
        assert r2.status_code == 200, r2.get_json(silent=True)
        assert r2.get_json()["x402_free_ration"] is True
        assert stub.calls == 2  # exactly one fresh dispatch
        assert daily_ration_tracker.remaining(
            WALLET.lower(), RESOURCE) == 2
        assert billing._PAID_TOKEN_FLIGHTS == {}


# ---------------------------------------------------------------------------
# Sequential behaviour unchanged
# ---------------------------------------------------------------------------

class TestSequentialSanity:
    def test_sequential_free_serves_up_to_ration_then_402(
            self, app, x402_db, stub):
        client = app.test_client()
        for expected_remaining in (2, 1, 0):
            resp = client.post(
                f"/api/paid/{RESOURCE}?wallet={WALLET}",
                json={"mode": "search", "query": "x"})
            assert resp.status_code == 200, resp.get_json(silent=True)
            body = resp.get_json()
            assert body["x402_free_ration"] is True
            assert body["free_ration_remaining"] == expected_remaining
            assert billing._PAID_TOKEN_FLIGHTS == {}
        assert stub.calls == 3

        # Ration exhausted: the next call gets the normal paid
        # challenge and does not dispatch at all.
        resp = client.post(
            f"/api/paid/{RESOURCE}?wallet={WALLET}",
            json={"mode": "search", "query": "x"})
        assert resp.status_code == 402, resp.get_json(silent=True)
        assert stub.calls == 3
        assert billing._PAID_TOKEN_FLIGHTS == {}
