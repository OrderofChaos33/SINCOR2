"""Bankruptcy recovery track: deterministic eligibility, fixed ladder, durability.

Covers: default-off, failure classification matrix, strike-1 sponsorship,
idempotent replays (same bankruptcy_event_id never consumes two strikes),
ghost ineligibility, tombstoned-wallet gate, strike-2 halved cap + 7-day
cooldown, strike-3 permanent ineligibility, active-sponsorship block,
ledger persistence, and the admin HTTP surface.
"""
from __future__ import annotations

import os

import pytest
from flask import Flask

from sincor2.a2a_inbound import register as register_inbound
from sincor2.recovery import (
    ENABLE_ENV,
    RecoveryDisabled,
    RecoveryIneligible,
    check_eligibility,
    classify_failure,
    recovery_ledger,
    recovery_status,
    reset_recovery_ledger,
    sponsor_recovery,
)
from sincor2.sponsored_stake import ENABLE_ENV as SPONSOR_ENABLE_ENV

AGENT = "recovery-agent-1"
AGENT2 = "recovery-agent-2"
WALLET = "0x" + "44" * 20
AXM = 10**18

HONEST = {"committed": True, "revealed": True, "submitted_on_time": True}
GHOST = {"committed": True, "revealed": False, "submitted_on_time": False}
LATE = {"committed": True, "revealed": True, "submitted_on_time": False}


@pytest.fixture
def ledgers(tmp_path, monkeypatch):
    from sincor2.onchain.stake_ledger import reset_stake_ledger
    from sincor2.sponsored_stake import reset_sponsored_ledger
    from sincor2 import kya_registry

    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    reset_sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    reset_recovery_ledger(path=str(tmp_path / "recovery.json"))
    kya_registry.reset()
    monkeypatch.delenv(ENABLE_ENV, raising=False)
    monkeypatch.delenv(SPONSOR_ENABLE_ENV, raising=False)
    return tmp_path


@pytest.fixture
def client(ledgers):
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _enable(monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "1")
    monkeypatch.setenv(SPONSOR_ENABLE_ENV, "1")


def _admin(monkeypatch):
    return {"X-Admin-Key": os.environ["ADMIN_PASSWORD"]}


# -- classification -----------------------------------------------------------
def test_classify_matrix():
    assert classify_failure(**HONEST)["verdict"] == "honest_fail"
    assert classify_failure(**GHOST)["verdict"] == "ghost"
    assert classify_failure(**LATE)["verdict"] == "ghost"
    assert classify_failure(False, False, False)["verdict"] == "no_stake"


# -- default-off ------------------------------------------------------------------
def test_disabled_by_default(ledgers):
    with pytest.raises(RecoveryDisabled):
        sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)


def test_http_403_while_disabled(client, monkeypatch):
    r = client.post(
        "/v1/a2a/admin/recovery/sponsor",
        headers={"X-Admin-Key": os.environ["ADMIN_PASSWORD"]},
        json={"agent_id": AGENT, "wallet": WALLET,
              "bankruptcy_event_id": "evt-1", "evidence": HONEST})
    assert r.status_code == 403


def test_http_401_without_admin_key(client, monkeypatch):
    _enable(monkeypatch)
    r = client.post("/v1/a2a/admin/recovery/sponsor", json={})
    assert r.status_code == 401


# -- strike 1 -----------------------------------------------------------------------
def test_strike1_sponsor_ok(ledgers, monkeypatch):
    _enable(monkeypatch)
    rec = sponsor_recovery(AGENT, WALLET, "evt-1", HONEST,
                           approved_by="test-op")
    assert rec["dedupe"] is False
    assert rec["strike"] == 1
    assert rec["cap_axm"] == 25.0
    assert rec["cap_wei"] == str(25 * AXM)
    assert rec["approved_by"] == "test-op"
    # Fronted through the sponsored-stake ledger: immediately usable,
    # automatically recouped from earnings.
    from sincor2.sponsored_stake import sponsored_ledger
    srec = sponsored_ledger().status_of(AGENT)
    assert srec["status"] == "fronted"
    assert srec["fronted_wei"] == str(25 * AXM)


def test_idempotent_replay_consumes_no_extra_strike(ledgers, monkeypatch):
    _enable(monkeypatch)
    first = sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    second = sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    assert second["dedupe"] is True
    assert second["strike"] == 1
    assert recovery_ledger().wallet_state(WALLET)["strikes"] == 1


# -- gates ------------------------------------------------------------------------------
def test_ghost_never_eligible(ledgers, monkeypatch):
    _enable(monkeypatch)
    with pytest.raises(RecoveryIneligible) as ei:
        sponsor_recovery(AGENT, WALLET, "evt-ghost", GHOST)
    assert ei.value.reason_code == "ghost_ineligible"
    assert recovery_ledger().wallet_state(WALLET)["strikes"] == 0


def test_tombstoned_wallet_ineligible(ledgers, monkeypatch):
    from sincor2 import kya_registry
    _enable(monkeypatch)
    kya_registry._write_tombstone(WALLET, AGENT, "cardhash", "ghosting")
    with pytest.raises(RecoveryIneligible) as ei:
        sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    assert ei.value.reason_code == "tombstoned_wallet"


def test_active_sponsorship_blocked(ledgers, monkeypatch):
    from sincor2.sponsored_stake import front_sponsored_stake
    _enable(monkeypatch)
    front_sponsored_stake(AGENT, 2 * AXM)  # genesis-kind sponsorship
    with pytest.raises(RecoveryIneligible) as ei:
        sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    assert ei.value.reason_code == "active_sponsorship"


# -- escalation ladder ---------------------------------------------------------------------
def test_strike2_cooldown_then_halved_cap(ledgers, monkeypatch):
    import sincor2.recovery as rec
    _enable(monkeypatch)
    sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    # Strike 2 immediately: cooldown blocks.
    with pytest.raises(RecoveryIneligible) as ei:
        sponsor_recovery(AGENT2, WALLET, "evt-2", HONEST)
    assert ei.value.reason_code == "cooldown_active"
    assert ei.value.retry_after_ms and ei.value.retry_after_ms > 0
    # Eight days later: strike 2 fronts at half cap.
    base = rec._now_ms()
    monkeypatch.setattr(rec, "_now_ms", lambda: base + 8 * 24 * 3600 * 1000)
    second = sponsor_recovery(AGENT2, WALLET, "evt-2", HONEST)
    assert second["strike"] == 2
    assert second["cap_axm"] == 12.5
    assert recovery_ledger().wallet_state(WALLET)["strikes"] == 2


def test_strike3_permanently_ineligible(ledgers, monkeypatch):
    import sincor2.recovery as rec
    _enable(monkeypatch)
    base = rec._now_ms()
    sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    monkeypatch.setattr(rec, "_now_ms", lambda: base + 8 * 24 * 3600 * 1000)
    sponsor_recovery(AGENT2, WALLET, "evt-2", HONEST)
    monkeypatch.setattr(rec, "_now_ms", lambda: base + 30 * 24 * 3600 * 1000)
    with pytest.raises(RecoveryIneligible) as ei:
        sponsor_recovery("recovery-agent-3", WALLET, "evt-3", HONEST)
    assert ei.value.reason_code == "strike_limit_reached"
    status = recovery_status(WALLET)
    assert status["strikes"] == 2
    assert status["next"]["eligible"] is False


# -- durability -------------------------------------------------------------------------------
def test_ledger_survives_reload(ledgers, monkeypatch, tmp_path):
    _enable(monkeypatch)
    sponsor_recovery(AGENT, WALLET, "evt-1", HONEST)
    path = str(tmp_path / "recovery.json")
    fresh = recovery_ledger(path=path)
    assert fresh.wallet_state(WALLET)["strikes"] == 1
    assert fresh.find_by_event("evt-1")["agent_id"] == AGENT


# -- HTTP ------------------------------------------------------------------------------------------
def test_http_sponsor_and_status(client, monkeypatch):
    _enable(monkeypatch)
    admin = {"X-Admin-Key": os.environ["ADMIN_PASSWORD"]}
    r = client.post("/v1/a2a/admin/recovery/sponsor", headers=admin,
                    json={"agent_id": AGENT, "wallet": WALLET,
                          "bankruptcy_event_id": "evt-http-1",
                          "evidence": HONEST})
    assert r.status_code == 201
    body = r.get_json()
    assert body["strike"] == 1 and body["dedupe"] is False
    # Replay: 200 dedupe.
    r = client.post("/v1/a2a/admin/recovery/sponsor", headers=admin,
                    json={"agent_id": AGENT, "wallet": WALLET,
                          "bankruptcy_event_id": "evt-http-1",
                          "evidence": HONEST})
    assert r.status_code == 200
    assert r.get_json()["dedupe"] is True
    # Ghost: 409 with reason code.
    r = client.post("/v1/a2a/admin/recovery/sponsor", headers=admin,
                    json={"agent_id": AGENT2, "wallet": "0x" + "55" * 20,
                          "bankruptcy_event_id": "evt-http-ghost",
                          "evidence": GHOST})
    assert r.status_code == 409
    assert r.get_json()["reason_code"] == "ghost_ineligible"
    # Status endpoint.
    r = client.get(f"/v1/a2a/admin/recovery/status?wallet={WALLET}",
                   headers=admin)
    assert r.status_code == 200
    status = r.get_json()
    assert status["strikes"] == 1
    assert status["next"]["terms"]["strike"] == 2
    assert status["next"]["terms"]["cap_axm"] == 12.5
