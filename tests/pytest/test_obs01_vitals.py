"""OBS-01 Agent Vitals (BETA): snapshot math, alerts, webhook signing, routes.

Isolation: every test rebuilds the in-memory A2A fabric, points the token
budget controller at a tmp ledger, and stubs the KYA registry — no repo
data dirs are touched. Webhook tests always inject a stub transport; no
real network traffic ever leaves the test process.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from sincor2.obs_skus import vitals as V
from sincor2.obs_skus.vitals import (
    AlertEvent,
    AlertRule,
    ToolCallBudget,
    WebhookDispatcher,
    build_agent_snapshot,
    build_fleet_snapshot,
    canonical_json,
    derive_health,
    evaluate_rules,
    load_alert_rules_from_env,
    metric_value,
    sign_payload,
    verify_signature,
)

NOW_MS = 1_780_000_000_000  # fixed clock for determinism
TTL_S = 60


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fabric():
    from sincor2.a2a_inbound import get_fabric, reset_fabric

    reset_fabric()
    return get_fabric()


def _agent(agent_id: str, hb_age_s: float | None, **extra) -> dict:
    rec = {
        "agent_id": agent_id,
        "name": f"Agent {agent_id}",
        "origin": "external",
        "registered_at": NOW_MS - 86_400_000,
        "reputation": 1.5,
    }
    rec["last_heartbeat"] = None if hb_age_s is None else int(NOW_MS - hb_age_s * 1000)
    rec.update(extra)
    return rec


@pytest.fixture
def budget_controller(tmp_path):
    from sincor2.token_budget_controller import TokenBudgetController

    return TokenBudgetController(ledger_path=tmp_path / "token_budget.json")


@pytest.fixture(autouse=True)
def _stub_kya(monkeypatch):
    import sincor2.kya_registry as kya

    monkeypatch.setattr(kya, "get_by_agent", lambda agent_id: None)


@pytest.fixture(autouse=True)
def _stub_budget_singleton(monkeypatch, budget_controller):
    import sincor2.token_budget_controller as tbc

    monkeypatch.setattr(tbc, "get_controller", lambda: budget_controller)


# ---------------------------------------------------------------------------
# Snapshot computation
# ---------------------------------------------------------------------------


def test_heartbeat_health_derivation():
    assert derive_health(10, TTL_S, None, False) == ("live", "live")
    assert derive_health(TTL_S, TTL_S, None, False) == ("live", "live")
    assert derive_health(TTL_S + 1, TTL_S, None, False) == ("stale", "stale")
    assert derive_health(2 * TTL_S, TTL_S, None, False) == ("stale", "stale")
    assert derive_health(2 * TTL_S + 1, TTL_S, None, False) == ("silent", "silent")
    assert derive_health(None, TTL_S, None, False) == ("unknown", "unknown")


def test_fail_closed_ordering():
    # A revoked KYA or killed budget is NEVER reported live, even with a fresh heartbeat.
    assert derive_health(5, TTL_S, "revoked", False)[0] == "kya_suspended"
    assert derive_health(5, TTL_S, "tombstoned", False)[0] == "kya_suspended"
    assert derive_health(5, TTL_S, None, True)[0] == "budget_blocked"
    assert derive_health(5, TTL_S, "verified", True)[0] == "budget_blocked"


def test_fleet_snapshot_health_and_ages(fabric, budget_controller):
    fabric.agents["E-live-01"] = _agent("E-live-01", 5)
    fabric.agents["E-stale-01"] = _agent("E-stale-01", 90)
    fabric.agents["E-silent-01"] = _agent("E-silent-01", 5000)
    fabric.agents["E-none-01"] = _agent("E-none-01", None)

    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    by_id = {a["agent_id"]: a for a in fleet["agents"]}

    assert fleet["fleet_size"] == 4
    assert by_id["E-live-01"]["health"] == "live"
    assert by_id["E-stale-01"]["health"] == "stale"
    assert by_id["E-silent-01"]["health"] == "silent"
    assert by_id["E-none-01"]["health"] == "unknown"
    assert by_id["E-live-01"]["heartbeat_age_s"] == pytest.approx(5.0)
    assert fleet["health_counts"] == {"live": 1, "stale": 1, "silent": 1, "unknown": 1}
    assert fleet["heartbeat_ttl_s"] == TTL_S
    assert fleet["beta"] is True


def test_cost_per_task_measured_from_settled_only(fabric, budget_controller):
    fabric.agents["E-cost-01"] = _agent("E-cost-01", 5)
    fabric.tasks["t1"] = {"task_id": "t1", "state": "settled", "assigned_to": "E-cost-01",
                          "payout_axm": 2.5, "settled_at": NOW_MS}
    fabric.tasks["t2"] = {"task_id": "t2", "state": "settled", "assigned_to": "E-cost-01",
                          "payout_axm": 1.5, "settled_at": NOW_MS}
    fabric.tasks["t3"] = {"task_id": "t3", "state": "open", "assigned_to": "E-cost-01",
                          "bounty_axm": 99.0}  # must NOT count
    fabric.tasks["t4"] = {"task_id": "t4", "state": "failed", "assigned_to": "E-cost-01",
                          "payout_axm": 7.0}  # must NOT count

    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    snap = {a["agent_id"]: a for a in fleet["agents"]}["E-cost-01"]
    assert snap["tasks_completed"] == 2
    assert snap["total_earned_axm"] == pytest.approx(4.0)
    assert snap["cost_per_task_axm"] == pytest.approx(2.0)


def test_cost_per_task_none_without_settled(fabric, budget_controller):
    fabric.agents["E-new-01"] = _agent("E-new-01", 5)
    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    snap = {a["agent_id"]: a for a in fleet["agents"]}["E-new-01"]
    assert snap["tasks_completed"] == 0
    assert snap["cost_per_task_axm"] is None  # honest "—", not 0.0


def test_token_budget_math(fabric, budget_controller):
    fabric.agents["E-bud-01"] = _agent("E-bud-01", 5)
    budget_controller.register_agent("E-bud-01", 1000)
    budget_controller.record_usage("E-bud-01", 250)

    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    tb = {a["agent_id"]: a for a in fleet["agents"]}["E-bud-01"]["token_budget"]
    assert tb["daily_ceiling"] == 1000
    assert tb["used_today"] == 250
    assert tb["remaining"] == 750
    assert tb["utilisation_pct"] == pytest.approx(25.0)
    assert tb["allowed"] is True
    assert tb["killed"] is False


def test_token_budget_unavailable_is_honest(fabric, budget_controller, monkeypatch):
    # A controller failure must render "Budget unavailable", never "ceiling reached".
    import sincor2.token_budget_controller as tbc

    def boom(agent_id):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(tbc, "get_controller",
                        lambda: type("C", (), {"get_status": staticmethod(boom)})())
    fabric.agents["E-down-01"] = _agent("E-down-01", 5)
    fleet = build_fleet_snapshot(now_ms=NOW_MS)
    tb = {a["agent_id"]: a for a in fleet["agents"]}["E-down-01"]["token_budget"]
    assert tb["source_ok"] is False


def test_killed_budget_marks_snapshot(fabric, budget_controller):
    fabric.agents["E-kill-01"] = _agent("E-kill-01", 5)
    budget_controller.kill_agent("E-kill-01", reason="operator_override")

    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    snap = {a["agent_id"]: a for a in fleet["agents"]}["E-kill-01"]
    assert snap["health"] == "budget_blocked"
    assert snap["token_budget"]["killed"] is True
    assert snap["token_budget"]["allowed"] is False


def test_kya_uptime_and_suspension(fabric, budget_controller, monkeypatch):
    import sincor2.kya_registry as kya

    def _rec(agent_id):
        if agent_id == "E-kya-01":
            return {"status": "verified",
                    "sla": {"uptime_30d": 0.987, "latency_ms": 42}}
        if agent_id == "E-rev-01":
            return {"status": "revoked", "sla": {"uptime_30d": 0.5}}
        return None

    monkeypatch.setattr(kya, "get_by_agent", _rec)
    fabric.agents["E-kya-01"] = _agent("E-kya-01", 5)
    fabric.agents["E-rev-01"] = _agent("E-rev-01", 5)
    fabric.agents["E-plain-01"] = _agent("E-plain-01", 5)

    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    by_id = {a["agent_id"]: a for a in fleet["agents"]}
    assert by_id["E-kya-01"]["uptime_pct"] == pytest.approx(98.7)
    assert by_id["E-kya-01"]["latency_ms"] == 42
    assert by_id["E-kya-01"]["kya_status"] == "verified"
    assert by_id["E-plain-01"]["uptime_pct"] is None  # honest "—"
    assert by_id["E-rev-01"]["health"] == "kya_suspended"


def test_tool_calls_uninstrumented_by_default(fabric, budget_controller):
    fabric.agents["E-tc-01"] = _agent("E-tc-01", 5)
    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller)
    tc = {a["agent_id"]: a for a in fleet["agents"]}["E-tc-01"]["tool_calls"]
    assert tc["instrumented"] is False


def test_tool_call_provider_plumbs_through(fabric, budget_controller):
    fabric.agents["E-tc-02"] = _agent("E-tc-02", 5)

    def provider(agent_id):
        return ToolCallBudget(instrumented=True, used=37, limit=100)

    fleet = build_fleet_snapshot(now_ms=NOW_MS, controller=budget_controller,
                                 tool_call_provider=provider)
    tc = {a["agent_id"]: a for a in fleet["agents"]}["E-tc-02"]["tool_calls"]
    assert tc == {"instrumented": True, "used": 37, "limit": 100, "window": "daily"}


# ---------------------------------------------------------------------------
# Alert rules
# ---------------------------------------------------------------------------


def _snap(agent_id, **kw):
    base = {
        "heartbeat_age_s": 10.0, "uptime_pct": 99.0, "cost_per_task_axm": 1.0,
        "tasks_completed": 3,
    }
    base.update(kw)
    return V.AgentVitals(
        agent_id=agent_id, name=agent_id, origin="external",
        heartbeat_age_s=base["heartbeat_age_s"], heartbeat_status="live",
        health="live", uptime_pct=base["uptime_pct"], latency_ms=20,
        kya_status="verified", tasks_completed=int(base["tasks_completed"]),
        total_earned_axm=3.0, cost_per_task_axm=base["cost_per_task_axm"],
        token_budget=V.TokenBudget(1000, 100, 900, 10.0, True, False),
        tool_calls=V.ToolCallBudget(),
        as_of="2026-09-29T00:00:00+00:00",
    )


def test_rule_fires_on_breach_only():
    snaps = [_snap("E-a", heartbeat_age_s=400.0), _snap("E-b", heartbeat_age_s=10.0)]
    rule = AlertRule("hb-stale", V.METRIC_HEARTBEAT_AGE_S, ">", 300.0)
    events, _state = evaluate_rules([rule], snaps, now_s=1000.0)
    assert len(events) == 1
    assert events[0].agent_id == "E-a"
    assert events[0].observed_value == pytest.approx(400.0)


def test_cooldown_dedupes():
    snaps = [_snap("E-a", heartbeat_age_s=400.0)]
    rule = AlertRule("hb-stale", V.METRIC_HEARTBEAT_AGE_S, ">", 300.0, cooldown_s=3600)
    events1, state = evaluate_rules([rule], snaps, now_s=1000.0)
    assert len(events1) == 1
    events2, _state = evaluate_rules([rule], snaps, now_s=1000.0 + 3599, cooldown_state=state)
    assert events2 == []  # still in cooldown
    events3, _state = evaluate_rules([rule], snaps, now_s=1000.0 + 3601, cooldown_state=state)
    assert len(events3) == 1  # cooldown expired -> fires again


def test_no_data_never_fires():
    snaps = [_snap("E-a", uptime_pct=None, cost_per_task_axm=None)]
    rules = [
        AlertRule("up-low", V.METRIC_UPTIME_PCT, "<", 90.0),
        AlertRule("cost-high", V.METRIC_COST_PER_TASK_AXM, ">", 0.5),
        AlertRule("tc-high", V.METRIC_TOOL_CALLS_USED, ">", 10),  # uninstrumented
    ]
    events, _state = evaluate_rules(rules, snaps, now_s=1000.0)
    assert events == []


def test_rule_scoped_to_agent():
    snaps = [_snap("E-a", heartbeat_age_s=400.0), _snap("E-b", heartbeat_age_s=400.0)]
    rule = AlertRule("hb-b", V.METRIC_HEARTBEAT_AGE_S, ">", 300.0, agent_id="E-b")
    events, _state = evaluate_rules([rule], snaps, now_s=1000.0)
    assert [e.agent_id for e in events] == ["E-b"]


def test_invalid_rules_rejected():
    with pytest.raises(ValueError):
        AlertRule("bad", "no_such_metric", ">", 1.0).validate()
    with pytest.raises(ValueError):
        AlertRule("bad", V.METRIC_HEARTBEAT_AGE_S, "!=", 1.0).validate()
    # invalid rules in a batch are skipped, not fatal
    good = AlertRule("ok", V.METRIC_HEARTBEAT_AGE_S, ">", 300.0)
    bad = AlertRule("bad", "nope", ">", 1.0)
    events, _state = evaluate_rules([bad, good], [_snap("E-a", heartbeat_age_s=400.0)], now_s=1.0)
    assert len(events) == 1 and events[0].rule_id == "ok"


def test_load_rules_from_env(monkeypatch):
    monkeypatch.setenv("SINCOR_VITALS_ALERTS", json.dumps([
        {"rule_id": "r1", "metric": "heartbeat_age_s", "op": ">", "threshold": 300,
         "webhook_url": "https://example.com/hook"},
        {"rule_id": "broken", "metric": "bogus", "op": ">", "threshold": 1},
    ]))
    out = load_alert_rules_from_env()
    assert [r.rule_id for r in out] == ["r1"]
    monkeypatch.delenv("SINCOR_VITALS_ALERTS")
    assert load_alert_rules_from_env() == []


# ---------------------------------------------------------------------------
# Webhook dispatch: schema, signing, retry
# ---------------------------------------------------------------------------


def _event() -> AlertEvent:
    return AlertEvent(
        event_id="evt_abc123", rule_id="hb-stale", agent_id="E-a",
        metric="heartbeat_age_s", operator=">", threshold=300.0,
        observed_value=400.0, observed_at="2026-09-29T15:30:00+00:00", fleet_size=4,
    )


def test_payload_schema_documented():
    from sincor2.obs_skus.vitals import build_alert_payload

    payload = build_alert_payload(_event())
    assert payload["schema"] == "sincor.vitals.alert/1"
    assert set(payload) == {
        "schema", "event_id", "rule_id", "agent_id", "metric", "operator",
        "threshold", "observed_value", "observed_at", "fleet_size",
    }
    # canonical form is stable regardless of dict insertion order
    assert canonical_json(payload) == canonical_json(dict(reversed(list(payload.items()))))


def test_signature_roundtrip():
    body = canonical_json({"a": 1})
    sig = sign_payload("s3cret", body, "12345")
    assert sig.startswith("sha256=")
    assert verify_signature("s3cret", body, "12345", sig)
    assert not verify_signature("wrong", body, "12345", sig)
    assert not verify_signature("s3cret", b'{"a":2}', "12345", sig)


def test_dispatch_uses_injected_transport_only():
    calls = []

    def stub(url, body, headers):
        calls.append({"url": url, "body": body, "headers": headers})
        return 200, "ok"

    disp = WebhookDispatcher("s3cret", transport=stub, sleeper=lambda s: None)
    result = disp.dispatch(_event(), "https://example.com/hook")

    assert result.ok is True and result.attempts == 1 and result.status_code == 200
    assert len(calls) == 1
    sent = calls[0]
    assert sent["url"] == "https://example.com/hook"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert "X-Sincor-Timestamp" in sent["headers"]
    sig = sent["headers"]["X-Sincor-Signature"]
    assert verify_signature("s3cret", sent["body"], sent["headers"]["X-Sincor-Timestamp"], sig)


def test_dispatch_retries_with_backoff_then_succeeds():
    sleeps = []
    attempts = {"n": 0}

    def flaky(url, body, headers):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise IOError("connection refused")
        return 200, "ok"

    disp = WebhookDispatcher("s3cret", transport=flaky, max_attempts=3,
                             base_backoff_s=1.0, sleeper=sleeps.append)
    result = disp.dispatch(_event(), "https://example.com/hook")
    assert result.ok is True and result.attempts == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff, no jitter surprises


def test_dispatch_gives_up_after_max_attempts():
    def dead(url, body, headers):
        raise IOError("down")

    disp = WebhookDispatcher("s3cret", transport=dead, max_attempts=2,
                             base_backoff_s=0.5, sleeper=lambda s: None)
    result = disp.dispatch(_event(), "https://example.com/hook")
    assert result.ok is False and result.attempts == 2
    assert result.error


def test_dispatch_requires_secret():
    with pytest.raises(ValueError):
        WebhookDispatcher("")


def test_dispatch_no_url_no_attempt():
    disp = WebhookDispatcher("s3cret", transport=lambda *a: (200, "ok"))
    result = disp.dispatch(_event(), "")
    assert result.ok is False and result.attempts == 0


# ---------------------------------------------------------------------------
# Routes: /obs/vitals + /api/obs/vitals (customer scope, BETA, unlisted)
# ---------------------------------------------------------------------------


@pytest.fixture
def mvp_client(fabric):
    os.environ.setdefault("FLASK_ENV", "test")
    os.environ.setdefault("ENVIRONMENT", "test")
    from sincor2.mvp_app import app as mvp_app

    mvp_app.config["TESTING"] = True
    mvp_app.config["SERVER_NAME"] = None
    # Route-level snapshots use the real clock, so stamp a live heartbeat now.
    fabric.agents["E-route-01"] = _agent("E-route-01", 0)
    fabric.agents["E-route-01"]["last_heartbeat"] = int(time.time() * 1000)
    return mvp_app.test_client()


def _login_customer(client):
    with client.session_transaction() as sess:
        sess["user_email"] = "customer@example.com"


def test_dashboard_requires_login(mvp_client):
    r = mvp_client.get("/obs/vitals")
    assert r.status_code == 302
    assert r.headers["Location"].startswith("/login?next=/obs/vitals")


def test_api_requires_login(mvp_client):
    r = mvp_client.get("/api/obs/vitals")
    assert r.status_code == 401
    assert r.get_json()["error"] == "login required"


def test_dashboard_renders_200_beta_for_customer(mvp_client):
    _login_customer(mvp_client)
    r = mvp_client.get("/obs/vitals")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "BETA" in html
    assert "Agent Vitals" in html
    assert "E-route-01" in html  # real agent id rendered from the live fabric
    assert "noindex" in html  # never indexed
    assert "Not instrumented" in html  # honest tool-call gap


def test_api_returns_live_fleet_json(mvp_client):
    _login_customer(mvp_client)
    r = mvp_client.get("/api/obs/vitals")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok" and body["beta"] is True
    agents = body["fleet"]["agents"]
    assert any(a["agent_id"] == "E-route-01" and a["health"] == "live" for a in agents)
    assert "alerts" in body


def test_vitals_not_linked_from_public_surface(mvp_client):
    # Founder standing rule: the beta dashboard must not be linked from any
    # public page. Spot-check the highest-traffic surfaces.
    for path in ("/", "/pricing", "/products"):
        r = mvp_client.get(path)
        if r.status_code == 200:
            assert "/obs/vitals" not in r.get_data(as_text=True), path
