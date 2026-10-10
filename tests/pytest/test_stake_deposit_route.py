"""Self-service stake deposit routes.

POST /v1/a2a/stake/deposit unblocks external agents: register -> deposit
(via HTTP, no operator) -> commit. GET /v1/a2a/stake/<agent_id> reads the
balance back. Both operate on the offchain AXM stake ledger (Pool 1).

Identity binding (G2.16, fail-closed): a deposit must carry an EIP-191
signature over the canonical message
    SINCOR-STAKE-DEPOSIT|<agent_id>|<amount_wei>|<wallet>|<expires_at_ms>
produced by the wallet registered on the agent record. Unsigned deposits
are rejected (403); signatures are single-use inside a 15-minute window.
"""

from __future__ import annotations

from conftest import hb_headers

import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import (
    create_task,
    sealed_commitment,
    stake_deposit_message,
)
from sincor2.a2a_identity import register_message
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

ONE_AXM = 10**18
BOUNTY = 1.5
COMMIT_LOCK_WEI = int(BOUNTY * ONE_AXM * 5000 // 10000)  # 0.75 AXM


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id, account=None):
    """Register an agent; returns the eth_account that owns its wallet.

    Attaches the EIP-191 registration proof (C3 fail-closed): a wallet
    claim without it is rejected outright by the register route.
    """
    account = account or Account.create()
    reg_ts = int(time.time() * 1000)
    reg_sig = "0x" + account.sign_message(
        encode_defunct(text=register_message(agent_id, reg_ts))
    ).signature.hex()
    r = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": agent_id,
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc",
            "wallet": account.address,
            "registration_wallet": account.address,
            "registration_ts": reg_ts,
            "registration_signature": reg_sig,
        },
    )
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id}, headers=hb_headers())
    assert r.status_code == 200, r.get_json()
    return account


def _deposit_payload(agent_id, account, amount_axm, tx_hash=None,
                     expires_at_ms=None, signature_override=None):
    amount_wei = int(round(float(amount_axm) * 1e18))
    if expires_at_ms is None:
        expires_at_ms = int(time.time() * 1000) + 5 * 60 * 1000
    message = stake_deposit_message(agent_id, amount_wei, account.address,
                                    expires_at_ms)
    if signature_override is not None:
        signature = signature_override
    else:
        signature = account.sign_message(
            encode_defunct(text=message)).signature.hex()
    payload = {"agent_id": agent_id, "amount_axm": amount_axm,
               "expires_at_ms": expires_at_ms, "signature": signature}
    if tx_hash is not None:
        payload["tx_hash"] = tx_hash
    return payload


def _deposit(client, agent_id, account, amount_axm, **kw):
    return client.post("/v1/a2a/stake/deposit",
                       json=_deposit_payload(agent_id, account, amount_axm,
                                             **kw))


def _sealed_task(client, poster_id="poster-p"):
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "tags": ["lead-enrichment"],
              "bounty_axm": BOUNTY, "sealed": True, "poster_id": poster_id},
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()["task_id"]


def _commit(client, task_id, agent_id, bid_axm=1.0, nonce_hex="aa" * 32):
    price_wei = int(round(float(bid_axm) * 1e18))
    salt = bytes.fromhex(nonce_hex)
    commitment = "0x" + sealed_commitment(price_wei, salt, agent_id).hex()
    return client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": task_id, "agent_id": agent_id,
              "commitment": commitment},
    )


# -- the unblock ----------------------------------------------------------------

def test_deposit_then_commit_succeeds(client):
    """The full external-agent loop with zero operator assistance."""
    acct = _register(client, "agent-1")
    task_id = _sealed_task(client)

    # No stake yet -> commit is rejected exactly as the gate requires.
    r = _commit(client, task_id, "agent-1")
    assert r.status_code == 403, r.get_json()

    # Signed deposit via HTTP, then commit in the same process.
    r = _deposit(client, "agent-1", acct, 10)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["agent_id"] == "agent-1"
    assert body["ledger"] == "offchain-axm"
    assert int(body["available_wei"]) == 10 * ONE_AXM
    assert int(body["deposit_wei"]) == 10 * ONE_AXM
    assert body["tx_hash"] is None

    r = _commit(client, task_id, "agent-1")
    assert r.status_code == 201, r.get_json()

    # The commit locked bounty*50% against the ledger.
    r = client.get("/v1/a2a/stake/agent-1")
    assert r.status_code == 200
    bal = r.get_json()
    assert int(bal["locked_wei"]) == COMMIT_LOCK_WEI
    assert int(bal["available_wei"]) == 10 * ONE_AXM - COMMIT_LOCK_WEI


def test_deposit_insufficient_still_rejected(client):
    acct = _register(client, "agent-2")
    task_id = _sealed_task(client)
    r = _deposit(client, "agent-2", acct, 0.1)
    assert r.status_code == 201
    r = _commit(client, task_id, "agent-2")
    assert r.status_code == 403  # 0.1 AXM < 0.75 AXM required


# -- validation ------------------------------------------------------------------

def test_deposit_unknown_agent_404(client):
    acct = Account.create()
    r = _deposit(client, "ghost", acct, 5)
    assert r.status_code == 404, r.get_json()


def test_deposit_unsigned_unknown_agent_404(client):
    # Even a fully unsigned deposit for an unknown agent stays a 404:
    # agent lookup precedes the auth check.
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "ghost", "amount_axm": 5})
    assert r.status_code == 404, r.get_json()


def test_deposit_negative_zero_and_missing_amount_400(client):
    acct = _register(client, "agent-3")
    for bad in (-5, 0, "not-a-number", None):
        payload = {"agent_id": "agent-3"}
        if bad is not None:
            payload["amount_axm"] = bad
        r = client.post("/v1/a2a/stake/deposit", json=payload)
        assert r.status_code == 400, (bad, r.get_json())


def test_deposit_missing_agent_id_400(client):
    payload = {"amount_axm": 5, "signature": "0x" + "00" * 65,
               "expires_at_ms": int(time.time() * 1000) + 60000}
    r = client.post("/v1/a2a/stake/deposit", json=payload)
    assert r.status_code == 400


def test_deposit_exceeds_max_400(client):
    acct = _register(client, "agent-4")
    r = _deposit(client, "agent-4", acct, 1e12)
    assert r.status_code == 400, r.get_json()


def test_deposit_bad_tx_hash_400(client):
    acct = _register(client, "agent-5")
    for bad in ("nope", "0x1234", "0x" + "zz" * 32, "11" * 32):
        r = _deposit(client, "agent-5", acct, 1, tx_hash=bad)
        assert r.status_code == 400, (bad, r.get_json())


# -- identity binding (G2.16, fail-closed) ----------------------------------------

def test_deposit_unsigned_rejected_403(client):
    """No signature at all -> 403, even for a registered agent."""
    _register(client, "agent-9")
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "agent-9", "amount_axm": 5})
    assert r.status_code == 403, r.get_json()
    r = client.get("/v1/a2a/stake/agent-9")
    assert int(r.get_json()["deposited_wei"]) == 0


def test_deposit_for_other_agents_id_rejected_403(client):
    """Attacker cannot top up someone else's stake: a signature by the
    attacker's own wallet does not authorize a deposit for the victim's
    agent_id."""
    victim_acct = _register(client, "victim")
    attacker_acct = Account.create()
    # Attacker crafts a fully valid signed payload — but the message binds
    # agent_id, and the signature comes from a wallet that is NOT the
    # victim's registered wallet.
    r = _deposit(client, "victim", attacker_acct, 100)
    assert r.status_code == 403, r.get_json()
    r = client.get("/v1/a2a/stake/victim")
    assert int(r.get_json()["deposited_wei"]) == 0
    assert victim_acct.address != attacker_acct.address


def test_deposit_tampered_signature_rejected_403(client):
    acct = _register(client, "agent-10")
    payload = _deposit_payload("agent-10", acct, 5)
    sig = payload["signature"]
    # Flip the last nibble (v byte): recovery fails or yields a different
    # address either way.
    payload["signature"] = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    r = client.post("/v1/a2a/stake/deposit", json=payload)
    assert r.status_code == 403, r.get_json()


def test_deposit_signature_for_other_agent_rejected_403(client):
    """A valid signature minted for agent A's message cannot be replayed
    for agent B: the message binds agent_id and the wallets differ."""
    acct_a = _register(client, "agent-a")
    acct_b = _register(client, "agent-b")
    payload = _deposit_payload("agent-a", acct_a, 5)
    payload["agent_id"] = "agent-b"  # malleate: keep A's signature
    r = client.post("/v1/a2a/stake/deposit", json=payload)
    assert r.status_code == 403, r.get_json()
    assert acct_a.address != acct_b.address


def test_deposit_signature_for_other_amount_rejected_403(client):
    """A signature for 1 AXM cannot be transplanted onto a 100 AXM body."""
    acct = _register(client, "agent-11")
    payload = _deposit_payload("agent-11", acct, 1)
    payload["amount_axm"] = 100
    r = client.post("/v1/a2a/stake/deposit", json=payload)
    assert r.status_code == 403, r.get_json()
    r = client.get("/v1/a2a/stake/agent-11")
    assert int(r.get_json()["deposited_wei"]) == 0


def test_deposit_expired_authorization_403(client):
    acct = _register(client, "agent-12")
    expired = int(time.time() * 1000) - 1000
    r = _deposit(client, "agent-12", acct, 5, expires_at_ms=expired)
    assert r.status_code == 403, r.get_json()


def test_deposit_expires_too_far_in_future_400(client):
    acct = _register(client, "agent-13")
    far = int(time.time() * 1000) + 60 * 60 * 1000  # 1h > 15m window
    r = _deposit(client, "agent-13", acct, 5, expires_at_ms=far)
    assert r.status_code == 400, r.get_json()


def test_deposit_replay_rejected_403(client):
    """The same signed authorization cannot credit stake twice."""
    acct = _register(client, "agent-14")
    payload = _deposit_payload("agent-14", acct, 5)
    r = client.post("/v1/a2a/stake/deposit", json=payload)
    assert r.status_code == 201, r.get_json()
    r = client.post("/v1/a2a/stake/deposit", json=payload)
    assert r.status_code == 403, r.get_json()
    r = client.get("/v1/a2a/stake/agent-14")
    assert int(r.get_json()["deposited_wei"]) == 5 * ONE_AXM
    # The consumed signature is recorded on the deposit event (audit).
    events = stake_ledger()._data["events"]
    deposits = [e for e in events
                if e["kind"] == "deposit" and e["agent_id"] == "agent-14"]
    assert len(deposits) == 1 and deposits[0].get("signature_hash")


def test_deposit_agent_without_wallet_403(client):
    """An agent registered without a wallet has no verifiable identity:
    fail-closed rejection."""
    r = client.post(
        "/v1/a2a/register",
        json={"agent_id": "walletless", "capability_tags": ["x"],
              "rpc_callback": "https://agent.example/rpc"},
    )
    assert r.status_code in (200, 201)
    acct = Account.create()
    r = _deposit(client, "walletless", acct, 5)
    assert r.status_code == 403, r.get_json()


# -- accumulation & reads ---------------------------------------------------------

def test_double_deposit_accumulates(client):
    acct = _register(client, "agent-6")
    for amt in (5, 7):
        r = _deposit(client, "agent-6", acct, amt)
        assert r.status_code == 201
    r = client.get("/v1/a2a/stake/agent-6")
    assert r.status_code == 200
    bal = r.get_json()
    assert int(bal["deposited_wei"]) == 12 * ONE_AXM
    assert int(bal["available_wei"]) == 12 * ONE_AXM
    assert bal["ledger"] == "offchain-axm"


def test_get_balance_fresh_agent_zeros(client):
    _register(client, "agent-7")
    r = client.get("/v1/a2a/stake/agent-7")
    assert r.status_code == 200
    bal = r.get_json()
    assert bal["agent_id"] == "agent-7"
    assert int(bal["deposited_wei"]) == 0
    assert int(bal["available_wei"]) == 0


def test_get_balance_unknown_agent_404(client):
    r = client.get("/v1/a2a/stake/ghost")
    assert r.status_code == 404


def test_deposit_tx_hash_stored_and_echoed(client):
    acct = _register(client, "agent-8")
    txh = "0x" + "ab" * 32
    r = _deposit(client, "agent-8", acct, 2, tx_hash=txh)
    assert r.status_code == 201
    assert r.get_json()["tx_hash"] == txh
    events = stake_ledger()._data["events"]
    deposits = [e for e in events
                if e["kind"] == "deposit" and e["agent_id"] == "agent-8"]
    assert deposits and deposits[-1]["reference"] == txh
