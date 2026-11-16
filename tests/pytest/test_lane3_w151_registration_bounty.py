"""W-151 regression — Lane 3 POST /ap2/register intro-bounty exhaustion
+ anonymous name-squat (red-team hunt 47, PoC poc47_lane3_register.py).

The fix, in three parts:
* Gate the money (lane3.apply_intro_bounty_flag): only a VERIFIED
  external record can be flagged/allocated; slots and ordinals are
  counted among verified external records only. An anonymous record
  gets a non-allocating ``pending_verification`` marker.
* Provisional slugs (a2a_inbound_ext.register_agent_record): an
  anonymous (unverified, wallet-less) record can be CLAIMED by the
  first valid wallet proof — first-claim form or re-registration
  form — which binds the owner wallet and verifies the identity.
  Without a valid proof it stays frozen to mutation (fail closed).
* Allocate at proof completion: a proof-completing registration
  allocates exactly once (record-level idempotency + a ledger-level
  guard in the flag path), whichever route completes the proof.

These tests assert the post-fix behavior the PoC proved absent.
"""
from __future__ import annotations

import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import a2a_identity as ident
from sincor2 import a2a_inbound_ext as ext
from sincor2.a2a_inbound import (
    RegistrationAuthError,
    build_reregistration_message,
)


@pytest.fixture(scope="module")
def mvp_client():
    from sincor2.mvp_app import app as flask_app

    flask_app.config.update(TESTING=True)
    return flask_app.test_client()


@pytest.fixture()
def fabric_isolated():
    from sincor2.a2a_inbound import _save_agents, get_fabric

    fabric = get_fabric()
    with fabric.lock:
        snapshot = {k: dict(v) for k, v in fabric.agents.items()}
    try:
        yield fabric
    finally:
        with fabric.lock:
            fabric.agents.clear()
            fabric.agents.update(snapshot)
        _save_agents(fabric)


@pytest.fixture()
def tmp_pool(tmp_path, monkeypatch):
    from sincor2.a2a_bounty_pool import BountyPool

    pool = BountyPool(path=str(tmp_path / "pool.json"), reserve_axm=1000.0)
    pool.fund()
    monkeypatch.setattr(
        "sincor2.a2a_bounty_pool.bounty_pool", lambda: pool)
    return pool


def _clear_externals(fabric):
    from sincor2.a2a_inbound import _save_agents

    with fabric.lock:
        for agent_id in [k for k, v in fabric.agents.items()
                         if v.get("origin") == "external"]:
            del fabric.agents[agent_id]
    _save_agents(fabric)


def _payload(name):
    return {
        "name": name,
        "endpoint": "https://agents.example.com/lane3/a2a",
        "contact": "ops@example.com",
    }


def _sign(text, signer):
    sig = signer.sign_message(encode_defunct(text=text)).signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


def _proof_fields(agent_id, signer):
    ts = int(time.time() * 1000)
    return {
        "wallet": signer.address,
        "registration_wallet": signer.address,
        "registration_signature": _sign(
            ident.register_message(agent_id, ts), signer),
        "registration_ts": ts,
    }


def _allocations(pool, agent_id):
    return pool.allocations_for_task(f"intro-bounty:{agent_id}")


# ── (A) exhaustion is closed ────────────────────────────────────────────────

def test_anonymous_posts_allocate_nothing_and_consume_no_slots(
        mvp_client, fabric_isolated, tmp_pool):
    _clear_externals(fabric_isolated)
    agent_ids = []
    for i in range(20):
        resp = mvp_client.post(
            "/ap2/register", json=_payload(f"attacker-farm-{i:02d}"))
        assert resp.status_code == 201, resp.get_data(as_text=True)
        data = resp.get_json()
        assert data["ok"] is True
        assert data["agent"]["identity"] == "unverified"
        assert data["agent"]["origin"] == "external"
        flag = data["intro_bounty"]
        assert flag["eligible"] is False, flag
        assert flag["status"] == "pending_verification"
        assert flag["allocation_id"] is None
        agent_ids.append(data["agent"]["agent_id"])

    # Zero ledger reservations after 20 anonymous POSTs; ledger intact.
    for agent_id in agent_ids:
        assert _allocations(tmp_pool, agent_id) == []
    verdict = tmp_pool.verify_ledger()
    assert verdict["ok"], verdict

    # The budget is untouched: the first agent to complete proof takes
    # slot #1 and exactly one 1.0 AXM reservation.
    signer = Account.create()
    payload = _payload("real-agent-first")
    payload.update(_proof_fields("real-agent-first", signer))
    resp = mvp_client.post("/ap2/register", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["agent"]["identity"] == "verified"
    flag = data["intro_bounty"]
    assert flag["eligible"] is True
    assert flag["ordinal"] == 1
    assert flag["status"] == "reserved"
    allocs = _allocations(tmp_pool, data["agent"]["agent_id"])
    assert len(allocs) == 1
    assert allocs[0].get("amount_axm") == 1.0


# ── (B) the squat no longer burns the slug ──────────────────────────────────

def test_anonymous_squat_claimable_by_first_claim_proof(
        mvp_client, fabric_isolated, tmp_pool):
    _clear_externals(fabric_isolated)
    # 1. Attacker squats the victim's brand name anonymously.
    resp = mvp_client.post("/ap2/register", json=_payload("victim-agent"))
    assert resp.status_code == 201
    agent_id = resp.get_json()["agent"]["agent_id"]
    assert agent_id == "victim-agent"
    record = fabric_isolated.agents[agent_id]
    assert record["identity"] == "unverified"
    assert not (record.get("wallet") or record.get("owner_wallet"))
    assert _allocations(tmp_pool, agent_id) == []

    # 2. An anonymous re-registration (no proof) is still refused —
    #    the provisional record stays frozen to unauthenticated mutation.
    resp = mvp_client.post("/ap2/register", json=_payload("victim-agent"))
    assert resp.status_code == 403

    # 3. The real owner claims the slug with a first-claim wallet proof.
    victim = Account.create()
    ts = int(time.time() * 1000)
    snap = ext.register_agent_record({
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["ap2", "external-onboarding"],
        "wallet": victim.address,
        "registration_wallet": victim.address,
        "registration_signature": _sign(
            ident.register_message(agent_id, ts), victim),
        "registration_ts": ts,
    })
    assert snap["identity"] == "verified"
    assert snap["owner_wallet"] == victim.address.lower()
    # The proof-completing registration allocated exactly once.
    allocs = _allocations(tmp_pool, agent_id)
    assert len(allocs) == 1
    assert allocs[0].get("amount_axm") == 1.0

    # 4. A different key cannot take it over now that it is owned:
    #    re-registration-form proof from a non-owner is refused.
    squatter = Account.create()
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["ap2", "external-onboarding"],
        "wallet": squatter.address,
    }
    ts = int(time.time() * 1000)
    body["registration_ts"] = ts
    body["registration_signature"] = _sign(
        build_reregistration_message(body, ts), squatter)
    with pytest.raises(RegistrationAuthError):
        ext.register_agent_record(body)

    # 5. The owner's own re-registration (valid proof of control)
    #    succeeds and does NOT allocate a second time.
    body2 = {
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["ap2", "external-onboarding"],
        "wallet": victim.address,
    }
    ts2 = int(time.time() * 1000)
    body2["registration_ts"] = ts2
    body2["registration_signature"] = _sign(
        build_reregistration_message(body2, ts2), victim)
    snap2 = ext.register_agent_record(body2)
    assert snap2["identity"] == "verified"
    assert len(_allocations(tmp_pool, agent_id)) == 1


def test_anonymous_squat_claimable_by_reregistration_form_proof(
        mvp_client, fabric_isolated, tmp_pool):
    """The PoC's victim used the re-registration signature form; for a
    wallet-less provisional record that form also proves key control
    and must claim the slug."""
    _clear_externals(fabric_isolated)
    resp = mvp_client.post("/ap2/register", json=_payload("victim-two"))
    assert resp.status_code == 201
    agent_id = resp.get_json()["agent"]["agent_id"]

    victim = Account.create()
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["ap2", "external-onboarding"],
        "wallet": victim.address,
    }
    ts = int(time.time() * 1000)
    body["registration_ts"] = ts
    body["registration_signature"] = _sign(
        build_reregistration_message(body, ts), victim)
    snap = ext.register_agent_record(body)
    assert snap["identity"] == "verified"
    assert snap["owner_wallet"] == victim.address.lower()
    assert len(_allocations(tmp_pool, agent_id)) == 1


def test_claim_via_ap2_register_with_proof_fields(
        mvp_client, fabric_isolated, tmp_pool):
    """End-to-end over HTTP: squat anonymously, then claim the same
    slug through /ap2/register itself by presenting proof fields."""
    _clear_externals(fabric_isolated)
    resp = mvp_client.post("/ap2/register", json=_payload("claimable-agent"))
    assert resp.status_code == 201
    assert resp.get_json()["intro_bounty"]["eligible"] is False

    owner = Account.create()
    payload = _payload("claimable-agent")
    payload.update(_proof_fields("claimable-agent", owner))
    resp = mvp_client.post("/ap2/register", json=payload)
    assert resp.status_code == 201, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["agent"]["identity"] == "verified"
    flag = data["intro_bounty"]
    assert flag["eligible"] is True
    assert flag["ordinal"] == 1
    assert flag["status"] == "reserved"
    assert len(_allocations(tmp_pool, "claimable-agent")) == 1
    record = fabric_isolated.agents["claimable-agent"]
    assert record["owner_wallet"] == owner.address.lower()


def test_proof_with_mismatched_wallet_does_not_claim(
        mvp_client, fabric_isolated, tmp_pool):
    """A valid signature from key A presented with key B's wallet
    declared is not a claim — the record stays provisional/frozen."""
    _clear_externals(fabric_isolated)
    resp = mvp_client.post("/ap2/register", json=_payload("victim-three"))
    assert resp.status_code == 201
    agent_id = resp.get_json()["agent"]["agent_id"]

    signer = Account.create()
    other = Account.create()
    ts = int(time.time() * 1000)
    body = {
        "agent_id": agent_id,
        "name": agent_id,
        "capability_tags": ["ap2", "external-onboarding"],
        "wallet": other.address,
        "registration_wallet": other.address,
        "registration_signature": _sign(
            ident.register_message(agent_id, ts), signer),
        "registration_ts": ts,
    }
    with pytest.raises((RegistrationAuthError, PermissionError)):
        ext.register_agent_record(body)
    record = fabric_isolated.agents[agent_id]
    assert record["identity"] == "unverified"
    assert _allocations(tmp_pool, agent_id) == []
