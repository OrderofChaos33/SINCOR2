"""Red-team hunt #15 — W-91: unauthenticated KYA mutations (CRITICAL).

CONFIRMED finding: POST /v1/kya/verify, /v1/kya/heartbeat and /v1/kya/sla
mutated registry state with NO auth guard — anyone could flip any agent to
"verified", fabricate heartbeats, or rewrite SLA/uptime with one POST.

Fix: _guard_kya_mutation (owner-or-admin) now gates /verify (base call AND
stake path) and /sla. /heartbeat accepts EITHER the owner/admin principal,
the operator heartbeat token, OR a valid EIP-191 agent self-report
signature by the record's registered wallet — the same proof format as
production /v1/a2a/heartbeat (shared message domain SINCOR-HEARTBEAT,
5-min freshness, low-s enforced).
"""

from __future__ import annotations

import os
import tempfile

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2.a2a_inbound_ext import build_heartbeat_message

STRANGER_WALLET = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture()
def kya_client():
    """Minimal Flask app with only the KYA blueprint + JWT configured.

    The victim agent owns a real throwaway key so EIP-191 self-report
    signatures can be produced in-test.
    """
    fd, store_path = tempfile.mkstemp(prefix="kya_hunt15w91_", suffix=".json")
    os.close(fd)
    prior = os.environ.get("KYA_STORE_PATH")
    os.environ["KYA_STORE_PATH"] = store_path
    try:
        from flask import Flask
        from flask_jwt_extended import JWTManager

        from sincor2 import kya_registry as kya
        from sincor2.kya_blueprint import kya_bp

        kya.reset()
        if os.path.exists(store_path):
            os.unlink(store_path)

        victim_acct = Account.create()
        victim_wallet = victim_acct.address

        app = Flask(__name__)
        app.config["JWT_SECRET_KEY"] = "hunt15w91-test-secret-32-chars-min!"
        app.secret_key = "hunt15w91-session-secret"
        JWTManager(app)
        app.register_blueprint(kya_bp)

        rec = kya.list_from_inbound(
            {
                "agent_id": "victim-agent-01",
                "name": "Victim Agent",
                "version": "1.0.0",
                "skills": [],
                "wallet": victim_wallet,
            }
        )
        with app.app_context():
            from flask_jwt_extended import create_access_token

            def tok(identity, role, wallet=None):
                claims = {"role": role}
                if wallet:
                    claims["wallet"] = wallet
                return create_access_token(identity=identity, additional_claims=claims)

            tokens = {
                "admin": tok("admin", "admin"),
                "owner": tok("victim-user", "customer", victim_wallet),
                "stranger": tok("mallory", "customer", STRANGER_WALLET),
            }
        yield app.test_client(), tokens, rec, kya, victim_acct
    finally:
        if os.path.exists(store_path):
            os.unlink(store_path)
        if prior is None:
            os.environ.pop("KYA_STORE_PATH", None)
        else:
            os.environ["KYA_STORE_PATH"] = prior


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _sign(acct, message: str) -> str:
    return acct.sign_message(encode_defunct(text=message)).signature.hex()


def _heartbeat_sig_payload(kya_client_fixture, acct, ts=None, agent_id=None):
    _, _, rec, kya, _ = kya_client_fixture
    agent_id = agent_id or rec["agent_id"]
    ts = ts if ts is not None else kya._now_ms()
    return {
        "agent_id": agent_id,
        "ok": True,
        "ts": ts,
        "signature": _sign(acct, build_heartbeat_message(agent_id, ts)),
    }


def _status_of(kya, kya_id):
    rec = kya.get(kya_id)
    return rec["status"], rec["revoked"]


# --- unauthenticated: all three routes deny ---------------------------------


def test_w91_verify_unauthenticated_401_no_mutation(kya_client):
    """The reported attack on /verify: unauthenticated flip-to-verified
    must be rejected with ZERO state mutation."""
    client, _, rec, kya, _ = kya_client
    before = _status_of(kya, rec["kya_id"])
    r = client.post("/v1/kya/verify", json={"kya_id": rec["kya_id"]})
    assert r.status_code == 401
    assert _status_of(kya, rec["kya_id"]) == before


def test_w91_verify_unknown_id_unauthenticated_401_not_404(kya_client):
    """No existence oracle for unauthenticated scanners."""
    client, _, _, _, _ = kya_client
    r = client.post("/v1/kya/verify", json={"kya_id": "kya_doesnotexist"})
    assert r.status_code == 401


def test_w91_heartbeat_unauthenticated_401_no_mutation(kya_client):
    client, _, rec, kya, _ = kya_client
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post("/v1/kya/heartbeat", json={"agent_id": rec["agent_id"], "ok": True})
    assert r.status_code == 401
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_heartbeat_unknown_agent_unauthenticated_401_not_404(kya_client):
    client, _, _, _, _ = kya_client
    r = client.post("/v1/kya/heartbeat", json={"agent_id": "ghost-agent-99"})
    assert r.status_code == 401


def test_w91_sla_unauthenticated_401_no_mutation(kya_client):
    client, _, rec, kya, _ = kya_client
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post(
        "/v1/kya/sla", json={"kya_id": rec["kya_id"], "ok": False, "latency_ms": 1}
    )
    assert r.status_code == 401
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_sla_unknown_id_unauthenticated_401_not_404(kya_client):
    client, _, _, _, _ = kya_client
    r = client.post("/v1/kya/sla", json={"kya_id": "kya_doesnotexist", "latency_ms": 1})
    assert r.status_code == 401


# --- cross-account -----------------------------------------------------------


def test_w91_verify_cross_principal_403_no_mutation(kya_client):
    client, tokens, rec, kya, _ = kya_client
    before = _status_of(kya, rec["kya_id"])
    r = client.post(
        "/v1/kya/verify", json={"kya_id": rec["kya_id"]}, headers=_auth(tokens["stranger"])
    )
    assert r.status_code == 403
    assert _status_of(kya, rec["kya_id"]) == before


def test_w91_sla_cross_principal_403_no_mutation(kya_client):
    client, tokens, rec, kya, _ = kya_client
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post(
        "/v1/kya/sla",
        json={"kya_id": rec["kya_id"], "ok": False, "latency_ms": 1},
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 403
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_verify_body_role_injection_ignored(kya_client):
    """Body-injected role/wallet must not confer ownership on /verify."""
    client, tokens, rec, kya, _ = kya_client
    before = _status_of(kya, rec["kya_id"])
    r = client.post(
        "/v1/kya/verify",
        json={"kya_id": rec["kya_id"], "role": "admin", "is_admin": True,
              "wallet": rec["agent_wallet"]},
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 403
    assert _status_of(kya, rec["kya_id"]) == before


# --- heartbeat proof forgeries -----------------------------------------------


def test_w91_heartbeat_garbage_signature_401(kya_client):
    client, _, rec, kya, _ = kya_client
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post(
        "/v1/kya/heartbeat",
        json={"agent_id": rec["agent_id"], "ok": True,
              "ts": kya._now_ms(), "signature": "0x" + "ab" * 65},
    )
    assert r.status_code == 401
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_heartbeat_wrong_wallet_signature_401(kya_client):
    """Signature by a DIFFERENT wallet (not the record's registered
    wallet) must not authenticate."""
    client, _, rec, kya, _ = kya_client
    stranger_acct = Account.create()
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post(
        "/v1/kya/heartbeat",
        json=_heartbeat_sig_payload(kya_client, stranger_acct),
    )
    assert r.status_code == 401
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_heartbeat_stale_timestamp_401(kya_client):
    """A valid signature replayed outside the freshness window is rejected."""
    client, _, rec, kya, victim_acct = kya_client
    stale_ts = kya._now_ms() - (10 * 60 * 1000)
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post(
        "/v1/kya/heartbeat",
        json=_heartbeat_sig_payload(kya_client, victim_acct, ts=stale_ts),
    )
    assert r.status_code == 401
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_heartbeat_cross_agent_replay_401(kya_client):
    """A proof signed for agent A cannot heartbeat agent B."""
    client, tokens, rec, kya, victim_acct = kya_client
    # register a second agent owned by the same wallet
    rec_b = kya.list_from_inbound(
        {"agent_id": "victim-agent-02", "name": "Victim 2",
         "version": "1.0.0", "skills": [], "wallet": victim_acct.address}
    )
    before_b = dict(kya.get(rec_b["kya_id"])["sla"])
    payload = _heartbeat_sig_payload(kya_client, victim_acct, agent_id=rec_b["agent_id"])
    # tamper: keep agent B's id but sign agent A's message
    ts = payload["ts"]
    payload["signature"] = _sign(victim_acct, build_heartbeat_message(rec["agent_id"], ts))
    r = client.post("/v1/kya/heartbeat", json=payload)
    assert r.status_code == 401
    assert kya.get(rec_b["kya_id"])["sla"] == before_b


def test_w91_heartbeat_high_s_malleation_401(kya_client):
    """(r, n-s) malleation twins are rejected by the low-s rule."""
    client, _, rec, kya, victim_acct = kya_client
    from sincor2.sig_canonical import SECP256K1_N

    ts = kya._now_ms()
    sig_hex = _sign(victim_acct, build_heartbeat_message(rec["agent_id"], ts))
    sig = bytes.fromhex(sig_hex[2:] if sig_hex.startswith("0x") else sig_hex)
    r_int, s_int = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:64], "big")
    high_s = SECP256K1_N - s_int
    assert high_s != s_int  # sanity: actually a twin
    evil = (r_int.to_bytes(32, "big") + high_s.to_bytes(32, "big") + sig[64:]).hex()
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.post(
        "/v1/kya/heartbeat",
        json={"agent_id": rec["agent_id"], "ok": True, "ts": ts,
              "signature": "0x" + evil},
    )
    assert r.status_code == 401
    assert kya.get(rec["kya_id"])["sla"] == before


def test_w91_heartbeat_missing_signature_401(kya_client):
    client, _, rec, kya, _ = kya_client
    r = client.post("/v1/kya/heartbeat", json={"agent_id": rec["agent_id"]})
    assert r.status_code == 401


def test_w91_heartbeat_token_in_body_rejected_401(kya_client):
    """The operator token is header-only; a body field must not work."""
    client, _, rec, kya, _ = kya_client
    token = "w91-operator-token"
    os.environ["AGENT_HEARTBEAT_TOKEN"] = token
    try:
        r = client.post(
            "/v1/kya/heartbeat",
            json={"agent_id": rec["agent_id"], "heartbeat_token": token,
                  "token": token},
        )
    finally:
        os.environ.pop("AGENT_HEARTBEAT_TOKEN", None)
    assert r.status_code == 401


def test_w91_heartbeat_wrong_method_no_mutation(kya_client):
    """GET on the heartbeat path falls through to the read-only
    GET /<kya_id> catch (pre-existing Flask routing) — 404, no mutation."""
    client, _, rec, kya, _ = kya_client
    before = dict(kya.get(rec["kya_id"])["sla"])
    r = client.get("/v1/kya/heartbeat", query_string={"agent_id": rec["agent_id"]})
    assert r.status_code == 404
    assert kya.get(rec["kya_id"])["sla"] == before


# --- legitimate flows still work ----------------------------------------------


def _bind_and_stake(kya, rec, victim_acct):
    """Bring the victim record to bound + staked + live (verify() preconditions)."""
    msg = "kya-bind victim-agent-01"
    kya.bind(rec["agent_id"], victim_acct.address, _sign(victim_acct, msg), msg)
    kya.apply_stake(rec["kya_id"], str(10 * 10**18), "0x" + "cd" * 32)
    kya.heartbeat(rec["agent_id"])  # registry-level: heartbeat precondition


def test_w91_verify_owner_end_to_end_200_flips_verified(kya_client):
    """Legitimate owner verification: bound + staked + live → 'verified'."""
    client, tokens, rec, kya, victim_acct = kya_client
    _bind_and_stake(kya, rec, victim_acct)
    r = client.post(
        "/v1/kya/verify", json={"kya_id": rec["kya_id"]}, headers=_auth(tokens["owner"])
    )
    assert r.status_code == 200
    assert r.get_json()["status"] == "verified"


def test_w91_verify_admin_end_to_end_200_flips_verified(kya_client):
    client, tokens, rec, kya, victim_acct = kya_client
    _bind_and_stake(kya, rec, victim_acct)
    r = client.post(
        "/v1/kya/verify", json={"kya_id": rec["kya_id"]}, headers=_auth(tokens["admin"])
    )
    assert r.status_code == 200
    assert r.get_json()["status"] == "verified"


def test_w91_verify_owner_unbound_400_auth_passed(kya_client):
    """Auth passes for the owner even when business logic rejects (not bound)."""
    client, tokens, rec, _, _ = kya_client
    r = client.post(
        "/v1/kya/verify", json={"kya_id": rec["kya_id"]}, headers=_auth(tokens["owner"])
    )
    assert r.status_code == 400  # not bound — auth passed, business rule fired


def test_w91_sla_owner_200_mutates(kya_client):
    client, tokens, rec, kya, _ = kya_client
    r = client.post(
        "/v1/kya/sla",
        json={"kya_id": rec["kya_id"], "ok": True, "latency_ms": 42},
        headers=_auth(tokens["owner"]),
    )
    assert r.status_code == 200
    sla = kya.get(rec["kya_id"])["sla"]
    assert sla["latency_ms"] == 42
    assert sla["last_ok"] is True
    assert sla["uptime_30d"] > 0


def test_w91_sla_admin_200_mutates(kya_client):
    client, tokens, rec, kya, _ = kya_client
    r = client.post(
        "/v1/kya/sla",
        json={"kya_id": rec["kya_id"], "ok": True, "latency_ms": 7},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200
    assert kya.get(rec["kya_id"])["sla"]["latency_ms"] == 7


def test_w91_heartbeat_admin_200(kya_client):
    client, tokens, rec, kya, _ = kya_client
    r = client.post(
        "/v1/kya/heartbeat", json={"agent_id": rec["agent_id"], "ok": True},
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 200
    assert kya.get(rec["kya_id"])["sla"]["last_ok"] is True


def test_w91_heartbeat_owner_200(kya_client):
    client, tokens, rec, kya, _ = kya_client
    r = client.post(
        "/v1/kya/heartbeat", json={"agent_id": rec["agent_id"], "ok": True},
        headers=_auth(tokens["owner"]),
    )
    assert r.status_code == 200
    assert kya.get(rec["kya_id"])["sla"]["last_ok"] is True


def test_w91_heartbeat_agent_signature_200(kya_client):
    """The legitimate agent self-report path: EIP-191 signature by the
    record's registered wallet, no admin credentials needed."""
    client, _, rec, kya, victim_acct = kya_client
    before_ms = kya.get(rec["kya_id"])["sla"]["last_heartbeat_ms"]
    r = client.post(
        "/v1/kya/heartbeat",
        json=_heartbeat_sig_payload(kya_client, victim_acct),
    )
    assert r.status_code == 200
    sla = kya.get(rec["kya_id"])["sla"]
    assert sla["last_heartbeat_ms"] >= before_ms
    assert sla["last_ok"] is True


def test_w91_heartbeat_stranger_jwt_with_agent_signature_200(kya_client):
    """An authenticated non-owner carrying the agent's OWN valid signature
    is admitted via the self-report path (signature authenticates the agent)."""
    client, tokens, rec, kya, victim_acct = kya_client
    r = client.post(
        "/v1/kya/heartbeat",
        json=_heartbeat_sig_payload(kya_client, victim_acct),
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 200


def test_w91_heartbeat_operator_token_200(kya_client):
    """First-party/ops path: header-only operator token, same as production
    /v1/a2a/heartbeat."""
    client, _, rec, kya, _ = kya_client
    os.environ["AGENT_HEARTBEAT_TOKEN"] = "w91-operator-token"
    try:
        r = client.post(
            "/v1/kya/heartbeat",
            json={"agent_id": rec["agent_id"], "ok": True},
            headers={"X-Sincor-Heartbeat": "w91-operator-token"},
        )
    finally:
        os.environ.pop("AGENT_HEARTBEAT_TOKEN", None)
    assert r.status_code == 200
    assert kya.get(rec["kya_id"])["sla"]["last_ok"] is True


def test_w91_heartbeat_operator_token_bearer_200(kya_client):
    client, _, rec, kya, _ = kya_client
    os.environ["AGENT_HEARTBEAT_TOKEN"] = "w91-operator-token"
    try:
        r = client.post(
            "/v1/kya/heartbeat",
            json={"agent_id": rec["agent_id"], "ok": True},
            headers={"Authorization": "Bearer w91-operator-token"},
        )
    finally:
        os.environ.pop("AGENT_HEARTBEAT_TOKEN", None)
    assert r.status_code == 200
