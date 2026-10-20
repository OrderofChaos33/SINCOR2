"""Adversarial guardrail tests (Part C) — every test attempts a REAL attack
and asserts the guard FAILS CLOSED.  These test existing guards; no guard
was weakened to make any test pass.

Attack surface covered:
  1. anti-self-dealing  — bid on an anonymous (poster-less) bounty task
  2. stake enforcement  — commit with < 50%-of-bounty stake
  3. sealed-bid         — reveal without commit; commit/reveal mismatch
  4. sybil guards       — 4th live identity on one wallet; unsigned commit
  5. KYA auth gates     — unauthenticated /verify, /heartbeat, /sla -> 401
"""
from __future__ import annotations

import secrets
import tempfile
import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from conftest import hb_headers, signed_task_auth
from sincor2.a2a_identity import register_message
from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import sealed_commitment
from sincor2.a2a_integration import _auth_bid_message


@pytest.fixture()
def client(tmp_path):
    from sincor2.a2a_rate_limits import reset_a2a_limits
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    reset_a2a_limits()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register_keyed(client, agent_id, key, stake_axm=10.0):
    """Register with an EXPLICIT key (lets the test bind identities)."""
    from sincor2.onchain.stake_ledger import stake_ledger

    ts = _now_ms()
    message = register_message(agent_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    r = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": agent_id,
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc",
            "wallet": key.address,
            "registration_wallet": key.address,
            "registration_ts": ts,
            "registration_signature": "0x" + bytes(sig).hex(),
        },
    )
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers=hb_headers())
    assert r.status_code == 200
    stake_ledger().deposit(agent_id, int(stake_axm * 10**18))
    return key


def _register(client, agent_id, stake_axm=10.0):
    return _register_keyed(client, agent_id, Account.create(), stake_axm)


def _bid_auth(key, purpose, task_id, agent_id, payload):
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    message = _auth_bid_message(purpose, task_id, agent_id, payload, ts, nonce)
    sig = key.sign_message(encode_defunct(text=message)).signature
    return {
        "authSignature": "0x" + bytes(sig).hex(),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def _sealed_task(client, bounty=1.5, signed=True, skill="lead-enrichment"):
    body = {"skill": skill, "tags": ["lead-enrichment"],
            "bounty_axm": bounty, "sealed": True}
    if signed:
        body.update(signed_task_auth(skill))
    r = client.post("/v1/a2a/tasks", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["task_id"]


def _commitment(bid_axm, nonce_hex, agent_id):
    price_wei = int(round(float(bid_axm) * 1e18))
    salt = bytes.fromhex(nonce_hex)
    return "0x" + sealed_commitment(price_wei, salt, agent_id).hex()


def _commit(client, key, task_id, agent_id, bid_axm, nonce_hex, auth=True):
    body = {"task_id": task_id, "agent_id": agent_id,
            "commitment": _commitment(bid_axm, nonce_hex, agent_id)}
    if auth:
        body.update(_bid_auth(key, "commit", task_id, agent_id,
                              body["commitment"]))
    return client.post("/v1/a2a/bids/commit", json=body)


def _reveal(client, key, task_id, agent_id, bid_axm, nonce_hex, est=300):
    payload = f"{float(bid_axm)}:{nonce_hex}"
    body = {"task_id": task_id, "agent_id": agent_id, "bid_axm": bid_axm,
            "nonce": nonce_hex, "estimated_seconds": est}
    body.update(_bid_auth(key, "reveal", task_id, agent_id, payload))
    return client.post("/v1/a2a/bids/reveal", json=body)


def _force_reveal_window(task_id):
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000


# ---------------------------------------------------------------------------
# 1. anti-self-dealing: anonymous bounty tasks are un-biddable (403)
# ---------------------------------------------------------------------------


def test_attack_anonymous_bounty_bid_rejected_403(client):
    """ATTACK: a task posted with NO poster identity but carrying a bounty —
    the bidder could be the poster (self-dealing).  The bid must fail closed
    and leave no commit record behind."""
    key = _register(client, "atk-selfdeal")
    task_id = _sealed_task(client, signed=False)  # anonymous: no poster bound
    r = _commit(client, key, task_id, "atk-selfdeal", 1.0, "aa" * 32)
    assert r.status_code == 403, r.get_json()
    assert "poster" in r.get_json()["error"].lower() or \
        "anonymous" in r.get_json()["error"].lower()
    # fail-closed: nothing was written to the commit store
    fabric = get_fabric()
    assert not any(k[1] == "atk-selfdeal" for k in fabric.commits)


def test_attack_self_bid_taxonomy_fail_closed():
    """The guard taxonomy itself: self-bid and anonymous-bounty are both
    PermissionError subclasses -> both surface as HTTP 403, never 5xx."""
    from sincor2.axm_antigaming import (AnonymousBountyError, SelfBidError,
                                        check_self_bid)

    assert issubclass(AnonymousBountyError, SelfBidError)
    assert issubclass(SelfBidError, PermissionError)
    # own task: poster_id == agent_id
    with pytest.raises(SelfBidError):
        check_self_bid({"task_id": "t", "poster_id": "agent-x",
                        "bounty_axm": 1.0}, "agent-x")
    # anonymous bounty task: no poster at all
    with pytest.raises(AnonymousBountyError):
        check_self_bid({"task_id": "t", "poster_id": None,
                        "bounty_axm": 1.0}, "agent-y")
    # zero-bounty anonymous tasks stay biddable (no money at stake)
    check_self_bid({"task_id": "t", "poster_id": None,
                    "bounty_axm": 0.0}, "agent-y")


# ---------------------------------------------------------------------------
# 2. stake enforcement: 50%-of-bounty collateral, under-staked commits die
# ---------------------------------------------------------------------------


def test_attack_understaked_commit_rejected_403(client):
    """ATTACK: an agent with 0.1 AXM staked tries to commit on a 1.5 AXM
    bounty (needs 0.75 AXM = 50%).  The commit must be rejected and the
    ledger must show NO lock was created."""
    from sincor2.onchain.stake_ledger import stake_ledger

    key = _register(client, "atk-poor", stake_axm=0.1)
    task_id = _sealed_task(client, bounty=1.5)
    r = _commit(client, key, task_id, "atk-poor", 1.0, "bb" * 32)
    assert r.status_code == 403, r.get_json()
    locks = stake_ledger().balance_of("atk-poor").get("locks", {})
    assert not locks, f"fail-closed violated: locks created {locks}"
    # fail-closed: no commit record was persisted either
    fabric = get_fabric()
    assert all(not k.endswith("\x00atk-poor") for k in fabric.commits)


def test_attack_stake_sized_to_bounty_not_bid(client):
    """Commit-time lock is 50% of the PUBLIC bounty.  A fully-staked agent
    commits fine; the guard math (required_stake_wei) pins the 50% ratio."""
    from sincor2.onchain.stake_ledger import required_stake_wei

    assert required_stake_wei(int(1.5 * 10**18)) == int(0.75 * 10**18)
    key = _register(client, "atk-rich", stake_axm=10.0)
    task_id = _sealed_task(client, bounty=1.5)
    r = _commit(client, key, task_id, "atk-rich", 1.0, "cc" * 32)
    assert r.status_code == 201, r.get_json()


# ---------------------------------------------------------------------------
# 3. sealed-bid commit/reveal integrity
# ---------------------------------------------------------------------------


def test_attack_reveal_without_commit_rejected(client):
    """ATTACK: reveal a bid that was never committed — must not materialise
    a bid record."""
    key = _register(client, "atk-nocommit")
    task_id = _sealed_task(client)
    _force_reveal_window(task_id)
    r = _reveal(client, key, task_id, "atk-nocommit", 1.0, "dd" * 32)
    assert r.status_code == 404, r.get_json()
    assert "commit" in r.get_json()["error"].lower()
    fabric = get_fabric()
    assert not fabric.bids or all(
        b.get("agent_id") != "atk-nocommit" for b in fabric.bids.values())


def test_attack_commit_reveal_amount_mismatch_rejected(client):
    """ATTACK: commit at 1.0 AXM, reveal at 0.5 AXM (cheaper to win) — the
    constant-time commitment check must reject it."""
    key = _register(client, "atk-swap")
    task_id = _sealed_task(client)
    assert _commit(client, key, task_id, "atk-swap", 1.0, "ee" * 32
                   ).status_code == 201
    _force_reveal_window(task_id)
    r = _reveal(client, key, task_id, "atk-swap", 0.5, "ee" * 32)
    assert r.status_code == 400, r.get_json()
    assert "mismatch" in r.get_json()["error"].lower()


def test_attack_commit_reveal_nonce_mismatch_rejected(client):
    """ATTACK: commit with one salt, reveal with another — rejected."""
    key = _register(client, "atk-nonceswap")
    task_id = _sealed_task(client)
    assert _commit(client, key, task_id, "atk-nonceswap", 1.0, "aa" * 32
                   ).status_code == 201
    _force_reveal_window(task_id)
    r = _reveal(client, key, task_id, "atk-nonceswap", 1.0, "ff" * 32)
    assert r.status_code == 400, r.get_json()
    assert "mismatch" in r.get_json()["error"].lower()


def test_attack_reveal_during_commit_window_rejected(client):
    """ATTACK: reveal while the commit phase is still open — 403."""
    key = _register(client, "atk-early")
    task_id = _sealed_task(client)
    assert _commit(client, key, task_id, "atk-early", 1.0, "11" * 32
                   ).status_code == 201
    r = _reveal(client, key, task_id, "atk-early", 1.0, "11" * 32)
    assert r.status_code == 403, r.get_json()


# ---------------------------------------------------------------------------
# 4. sybil guards
# ---------------------------------------------------------------------------


def test_attack_fourth_identity_same_wallet_rejected_403(client):
    """ATTACK: one operator mints a sock-puppet fleet on a single wallet.
    The first 3 live identities register; the 4th is refused (403) and no
    record is created."""
    key = Account.create()
    for i in range(3):
        aid = f"atk-sybil-{i}"
        ts = _now_ms()
        r = client.post(
            "/v1/a2a/register",
            json={
                "agent_id": aid,
                "capability_tags": ["lead-enrichment"],
                "rpc_callback": "https://agent.example/rpc",
                "wallet": key.address,
                "registration_wallet": key.address,
                "registration_ts": ts,
                "registration_signature": "0x" + bytes(
                    key.sign_message(encode_defunct(
                        text=register_message(aid, ts))).signature
                ).hex(),
            },
        )
        # 200/201 expected for the first three.
        assert r.status_code in (200, 201), (i, r.get_json())

    ts = _now_ms()
    r = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": "atk-sybil-3",
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc",
            "wallet": key.address,
            "registration_wallet": key.address,
            "registration_ts": ts,
            "registration_signature": "0x" + bytes(
                key.sign_message(encode_defunct(
                    text=register_message("atk-sybil-3", ts))).signature
            ).hex(),
        },
    )
    assert r.status_code == 403, r.get_json()
    assert "identity cap" in r.get_json()["error"].lower()
    assert "atk-sybil-3" not in get_fabric().agents


def test_attack_unsigned_commit_rejected_403(client):
    """ATTACK (W-77): a bare client-supplied agent_id with NO EIP-191 proof
    and NO operator token tries to lock a victim's stake in their name —
    the gate must refuse (403) without touching the ledger."""
    key = _register(client, "atk-victim")
    task_id = _sealed_task(client)
    r = _commit(client, key, task_id, "atk-victim", 1.0, "22" * 32, auth=False)
    assert r.status_code == 403, r.get_json()
    fabric = get_fabric()
    assert not any("atk-victim" in str(k) for k in fabric.commits)


# ---------------------------------------------------------------------------
# 5. KYA auth gates — unauthenticated mutations are 401
# ---------------------------------------------------------------------------


@pytest.fixture()
def kya_client():
    """Minimal Flask app with only the KYA blueprint (W-91 pattern)."""
    fd, store_path = tempfile.mkstemp(prefix="kya_guardrail_", suffix=".json")
    import os

    os.close(fd)
    prior = os.environ.get("KYA_STORE_PATH")
    os.environ["KYA_STORE_PATH"] = store_path
    try:
        from flask_jwt_extended import JWTManager

        from sincor2 import kya_registry as kya
        from sincor2.kya_blueprint import kya_bp

        kya.reset()
        if os.path.exists(store_path):
            os.unlink(store_path)
        app = Flask(__name__)
        app.config["JWT_SECRET_KEY"] = "guardrail-test-secret-32-chars-min!"
        app.secret_key = "guardrail-session-secret"
        JWTManager(app)
        app.register_blueprint(kya_bp)
        rec = kya.list_from_inbound({
            "agent_id": "kya-victim-01",
            "name": "Victim",
            "version": "1.0.0",
            "skills": [],
            "wallet": "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        })
        yield app.test_client(), rec["kya_id"]
    finally:
        if prior is None:
            os.environ.pop("KYA_STORE_PATH", None)
        else:
            os.environ["KYA_STORE_PATH"] = prior


def test_attack_kya_verify_unauthenticated_401(kya_client):
    """ATTACK: flip an agent to 'verified' with no credentials at all."""
    c, kya_id = kya_client
    before = c.get(f"/v1/kya/{kya_id}").get_json()
    r = c.post("/v1/kya/verify", json={"kya_id": kya_id})
    assert r.status_code == 401, r.get_json()
    after = c.get(f"/v1/kya/{kya_id}").get_json()
    assert after["status"] == before["status"]  # no mutation


def test_attack_kya_heartbeat_unauthenticated_401(kya_client):
    """ATTACK: fabricate a liveness heartbeat with no credentials."""
    c, _kya_id = kya_client
    r = c.post("/v1/kya/heartbeat",
               json={"agent_id": "kya-victim-01", "ok": True})
    assert r.status_code == 401, r.get_json()


def test_attack_kya_sla_unauthenticated_401(kya_client):
    """ATTACK: rewrite SLA/uptime state with no credentials."""
    c, kya_id = kya_client
    r = c.post("/v1/kya/sla",
               json={"kya_id": kya_id, "ok": True, "latency_ms": 1})
    assert r.status_code == 401, r.get_json()
