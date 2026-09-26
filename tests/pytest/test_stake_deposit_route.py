"""Self-service stake deposit routes.

POST /v1/a2a/stake/deposit unblocks external agents: register -> deposit
(via HTTP, no operator) -> commit. GET /v1/a2a/stake/<agent_id> reads the
balance back. Both operate on the offchain AXM stake ledger (Pool 1).
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import create_task, sealed_commitment
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


def _register(client, agent_id):
    r = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": agent_id,
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc",
            "wallet": "0x" + "11" * 20,
        },
    )
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id})
    assert r.status_code == 200, r.get_json()


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
    _register(client, "agent-1")
    task_id = _sealed_task(client)

    # No stake yet -> commit is rejected exactly as the gate requires.
    r = _commit(client, task_id, "agent-1")
    assert r.status_code == 403, r.get_json()

    # Deposit via HTTP, then commit in the same process.
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "agent-1", "amount_axm": 10})
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
    _register(client, "agent-2")
    task_id = _sealed_task(client)
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "agent-2", "amount_axm": 0.1})
    assert r.status_code == 201
    r = _commit(client, task_id, "agent-2")
    assert r.status_code == 403  # 0.1 AXM < 0.75 AXM required


# -- validation ------------------------------------------------------------------

def test_deposit_unknown_agent_404(client):
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "ghost", "amount_axm": 5})
    assert r.status_code == 404, r.get_json()


def test_deposit_negative_zero_and_missing_amount_400(client):
    _register(client, "agent-3")
    for bad in (-5, 0, "not-a-number", None):
        payload = {"agent_id": "agent-3"}
        if bad is not None:
            payload["amount_axm"] = bad
        r = client.post("/v1/a2a/stake/deposit", json=payload)
        assert r.status_code == 400, (bad, r.get_json())


def test_deposit_missing_agent_id_400(client):
    r = client.post("/v1/a2a/stake/deposit", json={"amount_axm": 5})
    assert r.status_code == 400


def test_deposit_exceeds_max_400(client):
    _register(client, "agent-4")
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "agent-4", "amount_axm": 1e12})
    assert r.status_code == 400, r.get_json()


def test_deposit_bad_tx_hash_400(client):
    _register(client, "agent-5")
    for bad in ("nope", "0x1234", "0x" + "zz" * 32, "11" * 32):
        r = client.post("/v1/a2a/stake/deposit",
                        json={"agent_id": "agent-5", "amount_axm": 1,
                              "tx_hash": bad})
        assert r.status_code == 400, (bad, r.get_json())


# -- accumulation & reads ---------------------------------------------------------

def test_double_deposit_accumulates(client):
    _register(client, "agent-6")
    for amt in (5, 7):
        r = client.post("/v1/a2a/stake/deposit",
                        json={"agent_id": "agent-6", "amount_axm": amt})
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
    _register(client, "agent-8")
    txh = "0x" + "ab" * 32
    r = client.post("/v1/a2a/stake/deposit",
                    json={"agent_id": "agent-8", "amount_axm": 2,
                          "tx_hash": txh})
    assert r.status_code == 201
    assert r.get_json()["tx_hash"] == txh
    events = stake_ledger()._data["events"]
    deposits = [e for e in events
                if e["kind"] == "deposit" and e["agent_id"] == "agent-8"]
    assert deposits and deposits[-1]["reference"] == txh
