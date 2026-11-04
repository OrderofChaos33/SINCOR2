"""Wren PR 5 — retry-bind UX fix (challenge_already_bound).

Bug: a legitimate buyer's FIRST bind could succeed server-side while
the 200 response was lost in transit (network drop, page reload,
wallet popup race). Every retry then hit the already-bound guard and
the pay page showed "Wallet verification failed: challenge_already_bound"
— a dead end for a buyer who had done everything right and whose
wallet provably owned the binding.

Fix under test:
  1. Server (x402_payments.bind_challenge_payer): when the challenge
     is already bound to the SAME wallet (case-insensitive) and is not
     yet fulfilled, the retry RE-VERIFIES the EIP-191 ownership
     signature and, on success, returns the standard successful-bind
     envelope — the /api/x402/challenge-bind route translates it into
     the standard 200 success payload (ok, ownership_proven, bound).
     A retry whose signature does NOT verify for that wallet is still
     refused (ownership_proof_failed) — the idempotent path is not a
     signature bypass.
  2. Different-wallet retries and already-fulfilled challenges keep
     the 409 challenge_already_bound refusal; when a fulfilled
     challenge is bound to the REQUESTER's wallet the 409 body carries
     bound_to_you: true so the client can tell "yours, already done"
     apart from "someone else's".
  3. Client (templates/rail_pay.html): postJSON keeps the parsed
     response body on the thrown error, and the bind handler treats
     challenge_already_bound + bound_to_you === true as the success
     state instead of "Wallet verification failed".

Wallets are freshly generated keys probed for low-s signatures (the
codebase bind standard, mirrored from test_w102r3_challenge_ownership)
— test-only, never funded.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

# Production import order matters: mvp_app must be imported before the
# billing blueprint so _bind_mvp() binds against the initializing module.
import sincor2.mvp_app  # noqa: F401  (imported for binding side effects)
from sincor2.mvp_blueprints import billing

import sincor2.x402_payments as x402
from sincor2.sig_canonical import is_low_s

RESOURCE = "agent_task"


def _make_key() -> Account:
    while True:
        acct = Account.create()
        if is_low_s(acct.sign_message(
                encode_defunct(text="sincor-wren-pr5-probe")).signature):
            return acct


BUYER_KEY = _make_key()
OTHER_KEY = _make_key()
BUYER = BUYER_KEY.address
OTHER = OTHER_KEY.address


@pytest.fixture()
def x402_db(tmp_path, monkeypatch):
    db = tmp_path / "orders.db"
    monkeypatch.setenv("ORDERS_DB_PATH", str(db))
    x402.init_x402_db()
    return db


@pytest.fixture()
def client():
    app = Flask(__name__)
    app.register_blueprint(billing.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _sig(acct: Account, challenge_id: str,
         resource_id: str = RESOURCE) -> str:
    msg = x402.challenge_bind_message(challenge_id, resource_id, acct.address)
    return acct.sign_message(encode_defunct(text=msg)).signature.hex()


def _draft() -> str:
    draft = x402.create_challenge(RESOURCE)
    assert draft["challenge_id"]
    return draft["challenge_id"]


def _bind_http(client, challenge_id: str, acct: Account,
               signature: str | None = None):
    return client.post("/api/x402/challenge-bind", json={
        "challenge_id": challenge_id,
        "payer_wallet": acct.address,
        "signature": signature if signature is not None
        else _sig(acct, challenge_id),
    })


def _row(challenge_id: str) -> dict:
    with x402._conn() as conn:
        row = conn.execute(
            "SELECT status, payer_wallet FROM x402_challenges "
            "WHERE challenge_id=?", (challenge_id,)).fetchone()
    return dict(row)


def _mark_fulfilled(challenge_id: str) -> None:
    with x402._conn() as conn:
        conn.execute(
            "UPDATE x402_challenges SET status='fulfilled' "
            "WHERE challenge_id=?", (challenge_id,))
        conn.commit()


# ---------------------------------------------------------------------------
# Same wallet + valid signature: the retry is a success, not a 409
# ---------------------------------------------------------------------------

def test_bind_twice_same_wallet_same_signature_200_both(client, x402_db):
    cid = _draft()
    first = _bind_http(client, cid, BUYER_KEY)
    assert first.status_code == 200, first.get_json()
    body = first.get_json()
    assert body["ok"] is True
    assert body["ownership_proven"] is True
    assert body["bound"] is True
    assert body["payer_wallet"] == BUYER.lower()

    # The retry: identical request, the case that used to dead-end the
    # buyer with "Wallet verification failed: challenge_already_bound".
    second = _bind_http(client, cid, BUYER_KEY)
    assert second.status_code == 200, second.get_json()
    body2 = second.get_json()
    assert body2["ok"] is True
    assert body2["ownership_proven"] is True
    assert body2["bound"] is True
    assert body2["payer_wallet"] == BUYER.lower()
    assert body2["payment"]["payTo"]

    row = _row(cid)
    assert row["status"] == "pending"
    assert row["payer_wallet"] == BUYER.lower()


def test_retry_bind_helper_level_returns_success_envelope(x402_db):
    cid = _draft()
    first = x402.bind_challenge_payer(cid, BUYER, _sig(BUYER_KEY, cid))
    assert first.get("ownership_proven") is True, first
    second = x402.bind_challenge_payer(cid, BUYER, _sig(BUYER_KEY, cid))
    assert second.get("ownership_proven") is True, second
    assert second.get("error") == "payment_required"
    assert second.get("payer_wallet") == BUYER.lower()


def test_retry_bind_is_case_insensitive_on_wallet(client, x402_db):
    cid = _draft()
    assert _bind_http(client, cid, BUYER_KEY).status_code == 200
    # Same wallet presented in checksum case on the retry.
    res = client.post("/api/x402/challenge-bind", json={
        "challenge_id": cid,
        "payer_wallet": BUYER_KEY.address,
        "signature": _sig(BUYER_KEY, cid),
    })
    assert res.status_code == 200, res.get_json()


# ---------------------------------------------------------------------------
# The idempotent path RE-VERIFIES: a bad retry signature is still refused
# ---------------------------------------------------------------------------

def test_retry_bind_with_wrong_signer_signature_refused(client, x402_db):
    cid = _draft()
    assert _bind_http(client, cid, BUYER_KEY).status_code == 200
    # Retry claims the buyer's wallet but the signature recovers to a
    # DIFFERENT signer (signed over the buyer's bind message by OTHER).
    msg = x402.challenge_bind_message(cid, RESOURCE, BUYER)
    forged = OTHER_KEY.sign_message(
        encode_defunct(text=msg)).signature.hex()
    res = _bind_http(client, cid, BUYER_KEY, signature=forged)
    assert res.status_code == 403, res.get_json()
    assert res.get_json()["error"] == "ownership_proof_failed"
    # The original binding is untouched.
    assert _row(cid)["payer_wallet"] == BUYER.lower()


def test_retry_bind_with_garbage_signature_refused(client, x402_db):
    cid = _draft()
    assert _bind_http(client, cid, BUYER_KEY).status_code == 200
    res = _bind_http(client, cid, BUYER_KEY, signature="0xdeadbeef")
    assert res.status_code == 403, res.get_json()
    assert res.get_json()["error"] == "ownership_proof_failed"


# ---------------------------------------------------------------------------
# 409s retained: different wallet, and already-fulfilled challenges
# ---------------------------------------------------------------------------

def test_different_wallet_retry_returns_409(client, x402_db):
    cid = _draft()
    assert _bind_http(client, cid, BUYER_KEY).status_code == 200
    # A different wallet, signing correctly for ITSELF, still cannot
    # take over or re-bind the challenge.
    res = _bind_http(client, cid, OTHER_KEY)
    assert res.status_code == 409, res.get_json()
    body = res.get_json()
    assert body["error"] == "challenge_already_bound"
    assert body.get("bound_to_you") is not True
    row = _row(cid)
    assert row["status"] == "pending"
    assert row["payer_wallet"] == BUYER.lower()


def test_fulfilled_challenge_bind_returns_409_bound_to_you(client, x402_db):
    cid = _draft()
    assert _bind_http(client, cid, BUYER_KEY).status_code == 200
    _mark_fulfilled(cid)
    res = _bind_http(client, cid, BUYER_KEY)
    assert res.status_code == 409, res.get_json()
    body = res.get_json()
    assert body["error"] == "challenge_already_bound"
    # The requester IS the bound wallet: the client may treat this as
    # "yours, already done" instead of a verification failure.
    assert body["bound_to_you"] is True


def test_fulfilled_challenge_bind_other_wallet_409_not_bound_to_you(
        client, x402_db):
    cid = _draft()
    assert _bind_http(client, cid, BUYER_KEY).status_code == 200
    _mark_fulfilled(cid)
    res = _bind_http(client, cid, OTHER_KEY)
    assert res.status_code == 409, res.get_json()
    body = res.get_json()
    assert body["error"] == "challenge_already_bound"
    assert body.get("bound_to_you") is not True


# ---------------------------------------------------------------------------
# Client: the shipped rail_pay.html handler treats bound_to_you as success
# ---------------------------------------------------------------------------

def _bind_handler_block(html: str) -> str:
    start = html.index('$("btnBind").addEventListener')
    end = html.index("refreshButtons();", start)
    return html[start:end]


def test_client_bind_handler_treats_bound_to_you_as_success():
    repo_root = Path(__file__).resolve().parent.parent.parent
    html = (repo_root / "templates" / "rail_pay.html").read_text(
        encoding="utf-8")
    # postJSON must keep the parsed body on the thrown error, otherwise
    # the handler can never see bound_to_you on a 409.
    assert "err.data = data" in html
    block = _bind_handler_block(html)
    assert "challenge_already_bound" in block
    assert "bound_to_you" in block
    # The success state is applied from that branch (wallet marked
    # verified), not the "Wallet verification failed" error path.
    assert "bound = true" in block
