"""Wave 1 agent hook — FULL-LOOP funnel integration test.

The money-path-adjacent loop, end to end on the real routes:

  anonymous peek (200, labelled sample / metadata)
    -> register a new agent via POST /v1/a2a/register (signed wallet)
    -> A2A: registered wallet's quote shows the daily ration and a
       message/send on a non-top-5 skill is served free by the ration
    -> x402: free-ration calls serve free (ration 3 -> 0 across calls),
       consuming NO payment token and touching NO fee/token machinery
    -> the next call gets the normal paid challenge (402 with the
       existing challenge envelope)

Plus: per-IP cap on x402 free serves, and the paid path's byte
behaviour unchanged once the ration is exhausted (a bought token
still delivers exactly once).

Run: PYTHONPATH=src:. <venv>/python -m pytest \
  tests/pytest/test_wave1_hook_funnel.py -q
"""
from __future__ import annotations

import time
import uuid

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

import sincor2.mvp_app  # noqa: F401  (import order: billing binding)
import sincor2.x402_payments as x402
from sincor2.mvp_blueprints import billing

RESOURCE = "toa-micro-decision"
WINNER = {
    "options": [
        {"id": "strong", "label": "strong performer",
         "signals": [0.80, 0.82, 0.81, 0.83, 0.84]},
        {"id": "weak", "label": "weak performer",
         "signals": [0.20, 0.21, 0.19, 0.22, 0.20]},
    ],
    "context": {"horizon": 4},
}


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def app_client(tmp_path, monkeypatch, x402_db):
    monkeypatch.setenv("TOA_DECISION_LOG", str(tmp_path / "toa.jsonl"))
    from sincor2.a2a_inbound import register as register_inbound
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.a2a_integration import A2ARouter
    from sincor2.a2a_rate_limits import reset_a2a_limits
    from sincor2.free_ration import daily_ration_tracker

    reset_fabric()
    reset_a2a_limits()
    daily_ration_tracker.clear()
    app = Flask("wave1_funnel")
    app.register_blueprint(billing.bp)
    app.register_blueprint(A2ARouter().blueprint)
    register_inbound(app)
    app.config.update(TESTING=True)
    yield app.test_client()
    daily_ration_tracker.clear()
    reset_fabric()


def _register_agent(client, key, agent_id):
    """First registration with a signed wallet claim (C3 fail-closed)."""
    from sincor2.a2a_identity import register_message
    from sincor2.a2a_inbound import _now_ms

    ts = _now_ms()
    sig = key.sign_message(
        encode_defunct(text=register_message(agent_id, ts))).signature
    body = {
        "agent_id": agent_id,
        "name": "Wave1 Funnel Agent",
        "description": "funnel test agent",
        "version": "1.0.0",
        "capability_tags": ["toa-decision"],
        "skills": [{"id": "toa-decision", "name": "TOA Decision"}],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": key.address,
        "registration_wallet": key.address,
        "registration_ts": ts,
        "registration_signature": "0x" + bytes(sig).hex(),
    }
    resp = client.post("/v1/a2a/register", json=body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _a2a_quote(client, key, skill_id):
    from sincor2 import a2a_integration as ai

    ts = int(time.time() * 1000)
    message = ai.quota_message_for_quote(skill_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature.hex()
    resp = client.post("/api/a2a/quote", json={
        "skill_id": skill_id, "signature": "0x" + sig,
        "quota_ts": str(ts), "wallet": key.address,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _a2a_send(client, key, skill_id, text, caller_id):
    from sincor2 import a2a_integration as ai

    ts_ms = int(time.time() * 1000)
    qmsg = ai.quota_message_for_send(skill_id, text, ts_ms)
    qsig = key.sign_message(encode_defunct(text=qmsg)).signature.hex()
    ts_s = int(time.time())
    nonce = uuid.uuid4().hex
    amsg = (
        "SINCOR-A2A task-create\n"
        f"agent_id:{caller_id}\n"
        f"skill_id:{skill_id}\n"
        f"timestamp:{ts_s}\n"
        f"nonce:{nonce}"
    )
    asig = key.sign_message(encode_defunct(text=amsg)).signature.hex()
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {
            "skillId": skill_id,
            "callerId": caller_id,
            "message": {
                "role": "user",
                "parts": [{"text": text}],
                "metadata": {
                    "signature": "0x" + qsig,
                    "quota_ts": str(ts_ms),
                    "wallet": key.address,
                },
            },
            "authSignature": "0x" + asig,
            "authTimestamp": ts_s,
            "authNonce": nonce,
            "ownerWallet": key.address,
        },
    }
    resp = client.post("/api/a2a", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# ---------------------------------------------------------------------------
# The funnel
# ---------------------------------------------------------------------------

def test_hook_funnel_peek_register_free_ration_then_402(app_client):
    client = app_client
    key = Account.create()
    wallet = key.address

    # 1. Anonymous peek — no wallet, no auth: a real labelled sample on
    #    x402, and labelled metadata on the A2A surface.
    resp = client.get(f"/api/x402/peek/{RESOURCE}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["sample"] is True and body["peek_kind"] == "sample"
    resp = client.get("/api/a2a/peek/lead-enrichment")
    assert resp.status_code == 200
    assert resp.get_json()["peek_kind"] == "metadata"

    # 2. Register a new agent via the existing registration path.
    reg = _register_agent(client, key, "wave1-funnel-agent")
    assert reg["agent_id"] == "wave1-funnel-agent"
    assert reg["status"] == "registered"

    # 3. A2A leg: the registered wallet's quote carries the daily
    #    ration on a non-top-5 skill, and a send is served free by it.
    quote = _a2a_quote(client, key, "compliance-sbom")
    assert quote["free_daily_ration_remaining"] == 3
    assert quote["is_free"] is True
    sent = _a2a_send(client, key, "compliance-sbom",
                     "compliance check", "wave1-funnel-agent")
    result = sent["result"]
    assert result["metadata"]["free_call"] is True
    assert result["metadata"]["free_daily_ration"] is True
    quote = _a2a_quote(client, key, "compliance-sbom")
    assert quote["free_daily_ration_remaining"] == 2

    # 4. x402 leg: three free-ration serves, ration 3 -> 0, and the
    #    token machinery is never touched (no access_granted calls, so
    #    no token consumption / fee / settlement side effects).
    calls = {"n": 0}
    real_access_granted = x402.access_granted

    def _spy(*a, **k):
        calls["n"] += 1
        return real_access_granted(*a, **k)

    import sincor2.x402_payments as _x402_mod
    orig = _x402_mod.access_granted
    _x402_mod.access_granted = _spy
    try:
        for expected_remaining in (2, 1, 0):
            resp = client.post(
                f"/api/paid/{RESOURCE}?wallet={wallet}", json=WINNER)
            assert resp.status_code == 200, resp.get_json()
            assert resp.headers.get("X-Free-Ration") == "true"
            body = resp.get_json()
            assert body["x402_free_ration"] is True
            assert body["free_ration_remaining"] == expected_remaining
            assert body["action"] == "recommend"
    finally:
        _x402_mod.access_granted = orig
    assert calls["n"] == 0, "free serves must not touch token state"

    # 5. Ration exhausted: the next call gets the normal paid
    #    challenge — HTTP 402 with the existing challenge envelope.
    resp = client.post(f"/api/paid/{RESOURCE}?wallet={wallet}", json=WINNER)
    assert resp.status_code == 402, resp.get_json()
    body = resp.get_json()
    assert body.get("challenge_id"), body
    assert body.get("sign_message"), body
    assert "accepts" in body, body
    assert resp.headers.get("PAYMENT-REQUIRED"), resp.headers


def test_x402_per_ip_cap_blocks_identity_rotation(app_client, monkeypatch):
    from sincor2.free_ration import daily_ration_tracker

    monkeypatch.setattr(daily_ration_tracker, "per_ip_daily_cap", 2)
    client = app_client
    wallets = [Account.create().address for _ in range(3)]
    for w in wallets[:2]:
        resp = client.post(f"/api/paid/{RESOURCE}?wallet={w}", json=WINNER)
        assert resp.status_code == 200, resp.get_json()
        assert resp.get_json()["x402_free_ration"] is True
    # Third distinct wallet, same source IP: per-IP cap reached, so the
    # request falls through to the paid challenge.
    resp = client.post(
        f"/api/paid/{RESOURCE}?wallet={wallets[2]}", json=WINNER)
    assert resp.status_code == 402, resp.get_json()


def test_paid_path_unchanged_when_ration_exhausted(
        app_client, x402_db, monkeypatch):
    """With the ration pre-exhausted, a bought token delivers exactly
    once through the exact pre-Wave-1 paid flow."""
    from sincor2.free_ration import daily_ration_tracker
    from sincor2.sig_canonical import is_low_s

    client = app_client
    key = None
    for _ in range(50):
        probe = Account.create()
        sig = probe.sign_message(encode_defunct(text="probe")).signature
        if is_low_s(sig):
            key = probe
            break
    assert key is not None
    buyer = key.address
    # Pre-exhaust this wallet's ration for the resource.
    for _ in range(3):
        assert daily_ration_tracker.try_consume(
            buyer.lower(), RESOURCE, ip="203.0.113.9") is True

    def _verify(tx_hash, **kwargs):
        return {"ok": True, "payer_wallet": buyer}

    monkeypatch.setattr(x402, "verify_treasury_transfer", _verify)
    monkeypatch.setattr(
        x402, "record_platform_payment",
        lambda **kw: {"payment_id": kw.get("payment_id"), "ok": True})
    monkeypatch.setattr(x402, "record_inflow", lambda *a, **k: {"ok": True})

    # 402 challenge -> bind -> verify/settle -> token -> one delivery.
    resp = client.get(f"/x402/{RESOURCE}?wallet={buyer}")
    assert resp.status_code == 402, resp.get_json()
    ch = resp.get_json()
    sig = key.sign_message(
        encode_defunct(text=ch["sign_message"])).signature.hex()
    resp = client.post("/api/x402/challenge-bind", json={
        "challenge_id": ch["challenge_id"], "payer_wallet": buyer,
        "signature": sig})
    assert resp.status_code == 402, resp.get_json()
    resp = client.post("/api/x402/verify", json={
        "challenge_id": ch["challenge_id"], "tx_hash": "0x" + "ab" * 32,
        "payer_wallet": buyer})
    assert resp.status_code == 200, resp.get_json()
    token = resp.get_json()["access_token"]
    assert token

    r1 = client.post(f"/api/paid/{RESOURCE}?wallet={buyer}",
                     headers={"X-Payment-Token": token}, json=WINNER)
    assert r1.status_code == 200, r1.get_json()
    assert r1.get_json().get("x402_free_ration") is not True
    r2 = client.post(f"/api/paid/{RESOURCE}?wallet={buyer}",
                     headers={"X-Payment-Token": token}, json=WINNER)
    assert r2.status_code != 200
