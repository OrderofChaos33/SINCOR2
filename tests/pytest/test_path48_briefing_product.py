"""Path 48 — Governance & Treasury Risk Briefings product tests.

Covers rendering from real treasury_dao/governance outputs, the
subscription model + status transitions, endpoint auth/ownership,
tier gating, and the no-billing-duplication rule (tiers reference
PLATFORM_PLANS; this product defines no prices of its own).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

NOW = 1_700_000_000.0
VENUES = ("aave", "compound", "morpho", "spark")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDERS_DB_PATH", str(tmp_path / "orders.db"))
    from sincor2 import briefing_product

    return briefing_product


def _swarm_with_holds():
    from src.sincor2.defi.treasury_dao import AllocationBand, TreasurySwarm

    bands = [AllocationBand(v, 1000, 3000) for v in VENUES]
    swarm = TreasurySwarm(bands, reviewers=["rev-1"])
    alloc = {v: 2500 for v in VENUES}
    old = swarm.propose_hold(alloc, "ops", now=NOW - 100 * 3600)  # unreviewed, stale
    swarm.propose_hold(alloc, "ops", now=NOW - 3600)
    swarm.reviewer.review(swarm.holds[1], True, "rev-1", now=NOW - 3500)
    swarm.yield_ledger.record_yield(10_000)
    assert old.hold_id != swarm.holds[1].hold_id
    return swarm


def _snapshot(store):
    from sincor2.governance import check_action, decision_log

    # One real governance decision (denied: no evidence) in the log.
    try:
        from sincor2.governance.action_catalog import ACTIONS

        check_action("agent-test", next(iter(ACTIONS)), {})
    except Exception:
        pass
    assert decision_log(), "governance gate should have logged a decision"
    return store.snapshot_from_swarm(_swarm_with_holds(), now=NOW)


# ---------------------------------------------------------------- rendering
def test_briefing_renders_from_real_modules(store):
    snap = _snapshot(store)
    out = store.render_briefing(snap, "intel")
    assert out["metrics"]["holds_total"] == 2
    assert out["metrics"]["holds_reviewed"] == 1
    assert out["metrics"]["holds_backlog_over_72h"] == 1
    assert out["metrics"]["band_violations"] == 0
    assert out["metrics"]["max_venue_bps_latest_hold"] == 2500
    # 8 bps on 10_000 wei realized yield = 8 wei fee (treasury_dao rule).
    assert out["metrics"]["fee_owed_wei"] == 8
    gov = out["sections"]["governance_activity"]
    assert gov["decisions_total"] >= 1
    assert "## governance_activity" in out["markdown"]
    assert "risk_flags" in out["sections"]


def test_briefing_unknown_when_data_stale(store):
    snap = _snapshot(store)
    snap["data_fresh"] = False
    snap["treasury_status"] = {"status": "unknown"}
    out = store.render_briefing(snap, "report")
    assert out["metrics"]["treasury_status"] == "unknown"
    assert out["sections"]["executive_summary"]["treasury_status"] == "unknown"


def test_tier_gating_full_vs_standard(store):
    snap = _snapshot(store)
    standard = store.render_briefing(snap, "report")
    full = store.render_briefing(snap, "intel")
    assert "governance_activity" not in standard["sections"]
    assert "hold_detail" not in standard["sections"]
    assert set(full["sections"]) > set(standard["sections"])
    with pytest.raises(ValueError):
        store.render_briefing(snap, "enterprise")


def test_no_billing_rail_duplication(store):
    from sincor2.platform_payments import PLATFORM_PLANS

    plans = store.briefing_plans()
    assert set(plans) <= set(PLATFORM_PLANS)
    for plan_id, view in plans.items():
        assert view["usd_reference"] == PLATFORM_PLANS[plan_id]["usd_reference"]
        assert view["billing"] == PLATFORM_PLANS[plan_id]["billing"]
    src = Path(store.__file__).read_text(encoding="utf-8")
    assert "PLATFORM_PLANS" in src
    # No locally restated prices for these tiers anywhere in the module.
    for literal in ('"usd_reference": 49', '"usd_reference": 149',
                    "usd_reference=49", "usd_reference=149"):
        assert literal not in src


# ------------------------------------------------------------- subscriptions
def test_subscription_create_lookup_and_transitions(store):
    created = store.create_subscriber(
        wallet="0xABC", plan_id="intel", email="a@example.com")
    assert created["ok"] and created["status"] == "active"
    assert created["token"]
    row = store.get_subscriber(created["subscriber_id"])
    assert row["wallet"] == "0xabc" and row["plan_id"] == "intel"
    assert "token_hash" not in row
    assert store.authenticate(created["token"])["id"] == created["subscriber_id"]
    assert store.authenticate("bogus") is None

    assert store.set_subscriber_status(created["subscriber_id"], "paused")["ok"]
    bad = store.set_subscriber_status(created["subscriber_id"], "bogus")
    assert not bad["ok"]
    assert store.set_subscriber_status(created["subscriber_id"], "cancelled")["ok"]
    # cancelled is terminal
    assert not store.set_subscriber_status(created["subscriber_id"], "active")["ok"]
    assert not store.create_subscriber(wallet="0x1", plan_id="nope")["ok"]
    assert not store.create_subscriber(wallet="", plan_id="intel")["ok"]


def test_paused_subscriber_cannot_generate(store):
    created = store.create_subscriber(wallet="0xdef", plan_id="report")
    sub = store.authenticate(created["token"])
    store.set_subscriber_status(created["subscriber_id"], "paused")
    sub = store.authenticate(created["token"])
    result = store.generate_delivery(sub, _snapshot(store))
    assert not result["ok"] and result["error"] == "subscriber_not_active"


# ---------------------------------------------------------------- endpoints
@pytest.fixture()
def client(store):
    from flask import Flask

    from sincor2.blueprints.briefings import briefings_bp

    app = Flask(__name__)
    app.register_blueprint(briefings_bp)
    return app.test_client()


def _subscribe(client, plan_id="intel", wallet="0x111"):
    resp = client.post("/api/briefings/subscribe",
                       json={"wallet": wallet, "plan_id": plan_id})
    assert resp.status_code == 201, resp.data
    return resp.get_json()


def test_endpoint_plans_come_from_platform_plans(client):
    body = client.get("/api/briefings/plans").get_json()
    from sincor2.platform_payments import PLATFORM_PLANS

    assert body["plans"]["intel"]["usd_reference"] == \
        PLATFORM_PLANS["intel"]["usd_reference"] == 149
    assert body["plans"]["report"]["usd_reference"] == \
        PLATFORM_PLANS["report"]["usd_reference"] == 49


def test_endpoint_generate_and_retrieve_roundtrip(client, store):
    sub = _subscribe(client)
    headers = {"X-Briefing-Token": sub["token"]}
    snap = _snapshot(store)
    gen = client.post("/api/briefings/generate", json=snap, headers=headers)
    assert gen.status_code == 201, gen.data
    delivery_id = gen.get_json()["delivery_id"]

    latest = client.get("/api/briefings/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.get_json()["delivery"]["id"] == delivery_id
    assert "governance_activity" in \
        latest.get_json()["delivery"]["briefing"]["sections"]

    one = client.get(f"/api/briefings/deliveries/{delivery_id}", headers=headers)
    assert one.status_code == 200
    assert "Governance & Treasury Risk Briefing" in one.get_json()["delivery"]["markdown"]


def test_endpoint_auth_and_ownership(client, store):
    snap = _snapshot(store)
    # No token / bad token -> 401 everywhere protected.
    assert client.post("/api/briefings/generate", json=snap).status_code == 401
    assert client.get("/api/briefings/latest").status_code == 401
    assert client.get("/api/briefings/latest",
                      headers={"X-Briefing-Token": "nope"}).status_code == 401

    owner = _subscribe(client, wallet="0x222")
    other = _subscribe(client, plan_id="report", wallet="0x333")
    gen = client.post("/api/briefings/generate", json=snap,
                      headers={"X-Briefing-Token": owner["token"]})
    delivery_id = gen.get_json()["delivery_id"]
    # Non-owner cannot fetch the delivery; non-subscriber tier content differs.
    assert client.get(f"/api/briefings/deliveries/{delivery_id}",
                      headers={"X-Briefing-Token": other["token"]}).status_code == 404
    other_gen = client.post("/api/briefings/generate", json=snap,
                            headers={"X-Briefing-Token": other["token"]})
    assert other_gen.status_code == 201
    sections = other_gen.get_json()["briefing"]["sections"]
    assert "governance_activity" not in sections  # report tier gating over HTTP


def test_endpoint_subscribe_validation_and_status(client):
    assert client.post("/api/briefings/subscribe",
                       json={"wallet": "0x1", "plan_id": "nope"}).status_code == 400
    sub = _subscribe(client, wallet="0x444")
    headers = {"X-Briefing-Token": sub["token"]}
    resp = client.post("/api/briefings/status", json={"status": "cancelled"},
                       headers=headers)
    assert resp.status_code == 200 and resp.get_json()["status"] == "cancelled"
    # Cancelled subscriber can no longer generate.
    assert client.post("/api/briefings/generate", json={"holds": []},
                       headers=headers).status_code == 403


def test_wiring_registered_in_production_app():
    src = (Path(__file__).resolve().parents[2] / "src" / "sincor2" / "mvp_app.py")
    text = src.read_text(encoding="utf-8")
    assert "briefings_bp" in text and "register_blueprint(_briefings_bp)" in text
