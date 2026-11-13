"""Red-team hunt #15 — W-76: POST /v1/kya/verify stake-write authorization.

CONFIRMED finding: POST /v1/kya/verify called kya.apply_stake(kya_id,
stake_wei, stake_tx) from the JSON body with NO auth and ZERO onchain
verification. An unauthenticated caller could persist stake_axm_wei to
another principal's record (flipping status to "staked" at >= MIN_STAKE_WEI).

Fix: the stake-application branch of /verify is now gated behind the same
owner-or-admin guard the W-59 batch-B fixes applied to revoke/unstake —
guard runs BEFORE apply_stake, so no partial mutation is possible.
"""

from __future__ import annotations

import os
import tempfile

import pytest

VICTIM_WALLET = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
STRANGER_WALLET = "0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture()
def kya_client():
    """Minimal Flask app with only the KYA blueprint + JWT configured."""
    fd, store_path = tempfile.mkstemp(prefix="kya_hunt15w76_", suffix=".json")
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

        app = Flask(__name__)
        app.config["JWT_SECRET_KEY"] = "hunt15w76-test-secret-32-chars-min!"
        app.secret_key = "hunt15w76-session-secret"
        JWTManager(app)
        app.register_blueprint(kya_bp)

        rec = kya.list_from_inbound(
            {
                "agent_id": "victim-agent-01",
                "name": "Victim Agent",
                "version": "1.0.0",
                "skills": [],
                "wallet": VICTIM_WALLET,
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
                "owner": tok("victim-user", "customer", VICTIM_WALLET),
                "stranger": tok("mallory", "customer", STRANGER_WALLET),
            }
        yield app.test_client(), tokens, rec["kya_id"], kya
    finally:
        if os.path.exists(store_path):
            os.unlink(store_path)
        if prior is None:
            os.environ.pop("KYA_STORE_PATH", None)
        else:
            os.environ["KYA_STORE_PATH"] = prior


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _stake_payload(kya_id, wei="999999"):
    return {
        "kya_id": kya_id,
        "stake_wei": wei,
        "stake_tx": "0x" + "ab" * 32,
    }


def _stake_of(kya, kya_id):
    rec = kya.get(kya_id)
    return rec["stake_axm_wei"], rec["status"], rec["stake_tx"]


# --- unauthenticated -------------------------------------------------------


def test_w76_verify_stake_unauthenticated_401_no_mutation(kya_client):
    """The reported attack: unauthenticated stake write must be rejected
    with ZERO state mutation (FAILS on the unfixed code — 400 + persisted)."""
    client, _, kya_id, kya = kya_client
    before = _stake_of(kya, kya_id)
    assert before[0] == "0"
    r = client.post("/v1/kya/verify", json=_stake_payload(kya_id))
    assert r.status_code == 401
    assert _stake_of(kya, kya_id) == before


def test_w76_verify_stake_unknown_id_unauthenticated_401_not_404(kya_client):
    """No existence oracle for unauthenticated scanners."""
    client, _, _, _ = kya_client
    r = client.post("/v1/kya/verify", json=_stake_payload("kya_doesnotexist"))
    assert r.status_code == 401


# --- cross-account ----------------------------------------------------------


def test_w76_verify_stake_cross_principal_403_no_mutation(kya_client):
    """Principal A authenticated, targeting principal B's kya_id → 403,
    zero mutation."""
    client, tokens, kya_id, kya = kya_client
    before = _stake_of(kya, kya_id)
    r = client.post(
        "/v1/kya/verify",
        json=_stake_payload(kya_id),
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 403
    assert _stake_of(kya, kya_id) == before


def test_w76_verify_stake_body_role_injection_ignored(kya_client):
    """Body-injected {"role": "admin"} / wallet must not confer ownership."""
    client, tokens, kya_id, kya = kya_client
    before = _stake_of(kya, kya_id)
    payload = _stake_payload(kya_id)
    payload.update(
        {"role": "admin", "is_admin": True, "wallet": VICTIM_WALLET,
         "principal": VICTIM_WALLET}
    )
    r = client.post(
        "/v1/kya/verify",
        json=payload,
        headers=_auth(tokens["stranger"]),
    )
    assert r.status_code == 403
    assert _stake_of(kya, kya_id) == before


# --- owner / admin legitimate path ------------------------------------------


def test_w76_verify_stake_owner_200_mutates(kya_client):
    """Record owner can still apply their own stake."""
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/verify",
        json=_stake_payload(kya_id),
        headers=_auth(tokens["owner"]),
    )
    # verify() itself fails downstream (no attestation bound) — but auth
    # passed and the stake WAS applied, which is the legitimate path.
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad request"
    assert _stake_of(kya, kya_id)[0] == "999999"


def test_w76_verify_stake_admin_200_mutates(kya_client):
    """Admin can still apply stake."""
    client, tokens, kya_id, kya = kya_client
    r = client.post(
        "/v1/kya/verify",
        json=_stake_payload(kya_id),
        headers=_auth(tokens["admin"]),
    )
    assert r.status_code == 400  # verify() business logic reached, auth passed
    assert _stake_of(kya, kya_id)[0] == "999999"


def test_w76_verify_without_stake_fields_now_requires_auth(kya_client):
    """Plain /verify (no stake fields) is now auth-gated (W-91 fix):
    unauthenticated → 401, zero mutation. (Supersedes the pre-W-91
    expectation of 400 from the unguarded base verify() call.)"""
    client, _, kya_id, kya = kya_client
    before = _stake_of(kya, kya_id)
    r = client.post("/v1/kya/verify", json={"kya_id": kya_id})
    assert r.status_code == 401
    assert _stake_of(kya, kya_id) == before
