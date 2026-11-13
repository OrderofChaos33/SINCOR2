"""Command Center APIs on the production app (sincor2.mvp_app).

The command_center blueprint was historically mounted ONLY by the legacy
app because its killswitch POST was unauthenticated. On the production app
every /api/command-center/* route — reads and writes — must be gated by the
existing admin pattern (_require_admin: admin session / admin token /
admin key):

  * anonymous            -> 401, no data, no state change
  * non-admin session    -> 401, no data, no state change
  * admin session        -> 200 with real-shaped payloads, writes round-trip

The production app has no legacy TaskMarket in ``extensions`` so market
payloads must be the honest empty shape — never fabricated rows.
"""
from __future__ import annotations

import os

import pytest

CC = "/api/command-center"
PROBE_AGENT = "E-cctest-probe-01"


@pytest.fixture
def mvp_client():
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    return mvp_app.test_client()


@pytest.fixture
def isolated_controller(tmp_path, monkeypatch):
    """Swap the token-budget singleton for one backed by a throwaway ledger
    so killswitch state assertions never touch the repo's data dir."""
    import sincor2.token_budget_controller as tbc

    ctrl = tbc.TokenBudgetController(ledger_path=tmp_path / "tb_ledger.json")
    monkeypatch.setattr(tbc, "_controller", ctrl)
    return ctrl


def _admin(client):
    with client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = os.environ.get("ADMIN_USERNAME", "a")


def _customer(client):
    with client.session_transaction() as sess:
        sess["user_email"] = "customer@example.com"
        sess["is_admin"] = False


# ---------------------------------------------------------------------------
# Anonymous: every route refused, nothing leaks, nothing changes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        f"{CC}/market-feed",
        f"{CC}/agents",
        f"{CC}/agents/{PROBE_AGENT}/memory",
        f"{CC}/killswitch",
        f"{CC}/market-snapshot",
    ],
)
def test_anon_gets_refused(mvp_client, isolated_controller, path):
    r = mvp_client.get(path)
    assert r.status_code == 401, path
    assert "text/event-stream" not in (r.content_type or "")
    body = r.get_data(as_text=True)
    assert "agents" not in body and "active_tasks" not in body


def test_anon_killswitch_post_refused_and_no_state_change(
    mvp_client, isolated_controller
):
    isolated_controller.register_agent(PROBE_AGENT, 1000)
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False

    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "anon-attack"},
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False


def test_anon_grade_task_refused(mvp_client, isolated_controller):
    r = mvp_client.post(
        f"{CC}/grade-task",
        json={"task_id": "t-1", "agent_id": PROBE_AGENT, "grade": 9.0},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Non-admin (customer) session: refused the same way
# ---------------------------------------------------------------------------

def test_customer_session_refused(mvp_client, isolated_controller):
    _customer(mvp_client)
    isolated_controller.register_agent(PROBE_AGENT, 1000)

    assert mvp_client.get(f"{CC}/agents").status_code == 401
    assert mvp_client.get(f"{CC}/killswitch").status_code == 401
    assert mvp_client.get(f"{CC}/market-snapshot").status_code == 401
    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}", json={"action": "kill"}
    )
    assert r.status_code == 401
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False
    r = mvp_client.post(
        f"{CC}/grade-task",
        json={"task_id": "t-1", "agent_id": PROBE_AGENT, "grade": 1.0},
    )
    assert r.status_code == 401


def test_forged_admin_session_wrong_username_refused(
    mvp_client, isolated_controller
):
    # is_admin flag alone must not suffice: the session username must match
    # the configured admin identity (the _is_admin_session contract).
    with mvp_client.session_transaction() as sess:
        sess["is_admin"] = True
        sess["admin_username"] = "not-the-admin"
    assert mvp_client.get(f"{CC}/agents").status_code == 401
    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}", json={"action": "kill"}
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Admin session: reads return real-shaped payloads
# ---------------------------------------------------------------------------

def test_admin_agents_roster_shape(mvp_client, isolated_controller):
    _admin(mvp_client)
    r = mvp_client.get(f"{CC}/agents")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data["agents"], list)
    assert data["count"] == len(data["agents"])
    assert data["count"] > 0  # roster is read from the repo's agents/E-*.yaml
    first = data["agents"][0]
    for key in ("agent_id", "name", "personality", "career", "daily_tokens"):
        assert key in first, key


def test_admin_market_snapshot_honest_empty(mvp_client, isolated_controller):
    # The production app carries no legacy TaskMarket, so the snapshot must
    # be the honest empty shape — keys present, zero fabricated tasks.
    _admin(mvp_client)
    r = mvp_client.get(f"{CC}/market-snapshot")
    assert r.status_code == 200
    data = r.get_json()
    assert data["active_tasks"] == []
    assert data["recent_transactions"] == []
    assert data["stats"] == {}


def test_admin_killswitch_status_shape(mvp_client, isolated_controller):
    _admin(mvp_client)
    isolated_controller.register_agent(PROBE_AGENT, 1234)
    r = mvp_client.get(f"{CC}/killswitch")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 1
    entry = data["agents"][PROBE_AGENT]
    assert entry["daily_ceiling"] == 1234
    assert entry["killed"] is False
    assert entry["name"]  # merged from roster or id fallback


def test_admin_agent_memory_shape(mvp_client, isolated_controller, monkeypatch, tmp_path):
    _admin(mvp_client)
    monkeypatch.chdir(tmp_path)  # keep any episodic store out of the repo
    r = mvp_client.get(f"{CC}/agents/{PROBE_AGENT}/memory")
    assert r.status_code == 200
    data = r.get_json()
    assert data["agent_id"] == PROBE_AGENT
    assert isinstance(data["episodic_events"], list)
    assert isinstance(data["task_history"], list)


def test_admin_market_feed_streams_snapshot(mvp_client, isolated_controller):
    _admin(mvp_client)
    r = mvp_client.get(f"{CC}/market-feed", buffered=False)
    try:
        assert r.status_code == 200
        assert "text/event-stream" in (r.content_type or "")
        first = next(iter(r.response))
        text = first.decode("utf-8", "replace") if isinstance(first, bytes) else first
        assert "event: snapshot" in text
        assert "active_tasks" in text
    finally:
        r.close()


# ---------------------------------------------------------------------------
# Admin session: writes round-trip
# ---------------------------------------------------------------------------

def test_admin_killswitch_round_trip(mvp_client, isolated_controller):
    _admin(mvp_client)
    isolated_controller.register_agent(PROBE_AGENT, 1000)

    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}",
        json={"action": "kill", "reason": "test-drill"},
    )
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert r.get_json()["action"] == "killed"
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is True

    r = mvp_client.get(f"{CC}/killswitch")
    assert r.get_json()["agents"][PROBE_AGENT]["killed"] is True

    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}", json={"action": "reinstate"}
    )
    assert r.status_code == 200
    assert r.get_json()["action"] == "reinstated"
    assert isolated_controller.get_status(PROBE_AGENT)["killed"] is False

    r = mvp_client.post(
        f"{CC}/killswitch/{PROBE_AGENT}", json={"action": "explode"}
    )
    assert r.status_code == 400


def test_admin_grade_task(mvp_client, isolated_controller):
    _admin(mvp_client)
    r = mvp_client.post(
        f"{CC}/grade-task",
        json={"task_id": "t-42", "agent_id": PROBE_AGENT, "grade": 8.0},
    )
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["task_id"] == "t-42"
    assert data["grade"] == 8.0
    # No legacy TaskMarket on the production app: nothing to adjust, and the
    # endpoint must say so with a zero delta rather than inventing merit.
    assert data["merit_delta"] == 0

    r = mvp_client.post(f"{CC}/grade-task", json={"grade": 5.0})
    assert r.status_code == 400
    r = mvp_client.post(
        f"{CC}/grade-task",
        json={"task_id": "t-1", "agent_id": PROBE_AGENT, "grade": 11.0},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# The gate is scoped: unrelated public routes stay public
# ---------------------------------------------------------------------------

def test_gate_scoped_to_command_center(mvp_client, isolated_controller):
    r = mvp_client.get("/health")
    assert r.status_code == 200
    # And the operator page itself keeps its existing login-redirect gate.
    r = mvp_client.get("/command-center", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login")
