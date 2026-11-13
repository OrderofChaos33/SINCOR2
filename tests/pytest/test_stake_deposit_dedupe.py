"""W-36: stake deposit dedupe keyed on the tx hash (money path).

The deposit route and StakeLedger.record_onchain_deposit both funnel into
StakeLedger.deposit().  Before the fix, retrying the same deposit with an
identical tx hash credited the stake twice because nothing tracked which
tx hashes had already been reconciled.

These tests prove:
  (a) the same tx hash deposited twice credits exactly once;
  (b) distinct tx hashes credit independently;
  (c) dedupe state survives a store reload (durable, not in-memory);
  (d) same hash + different agent is refused (no cross-agent credit theft);
  (e) same hash + different amount is refused (no key-reuse replays);
  (f) record_onchain_deposit (the on-chain reconciliation entry point) is
      covered by the same guard;
  (g) deposits without a tx hash are unaffected (no false dedupe);
  (h) concurrent identical deposits credit exactly once;
  (i) the HTTP route returns 201 for a fresh deposit, 200 + duplicate for
      an honest retry, and 400 for a conflicting reuse.
"""

from __future__ import annotations

import threading
import time

import pytest
from conftest import hb_headers
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from bid_testkit import register_wallet_agent

from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound import reset_fabric
from sincor2.a2a_inbound_market import stake_deposit_message
from sincor2.onchain.stake_ledger import (
    DuplicateTxHashError,
    StakeLedger,
    reset_stake_ledger,
    stake_ledger,
)

ONE_AXM = 10**18
TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32


@pytest.fixture
def ledger(tmp_path):
    return StakeLedger(path=str(tmp_path / "stake.json"))


def _balance(ledger, agent_id):
    return int(ledger.balance_of(agent_id)["deposited_wei"])


# -- (a) same tx hash twice credits exactly once ------------------------------


def test_same_tx_hash_twice_credits_once(ledger):
    first = ledger.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)
    assert first["duplicate"] is False
    assert first["credited_wei"] == str(2 * ONE_AXM)

    second = ledger.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)
    assert second["duplicate"] is True
    assert second["credited_wei"] == "0"
    # Idempotent success: same balance shape returned, no exception.
    assert second["agent_id"] == "agent-1"

    assert _balance(ledger, "agent-1") == 2 * ONE_AXM


# -- (b) distinct tx hashes credit independently -------------------------------


def test_distinct_tx_hashes_credit_independently(ledger):
    ledger.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)
    ledger.deposit("agent-1", 3 * ONE_AXM, reference=TX_B)
    assert _balance(ledger, "agent-1") == 5 * ONE_AXM


# -- (c) dedupe survives a store reload ----------------------------------------


def test_dedupe_survives_store_reload(tmp_path):
    path = str(tmp_path / "stake.json")
    first = StakeLedger(path=path)
    first.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)

    # Fresh instance = "restart": no in-memory state carried over.
    reloaded = StakeLedger(path=path)
    assert reloaded.balance_of("agent-1")["deposited_wei"] == str(2 * ONE_AXM)

    retry = reloaded.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)
    assert retry["duplicate"] is True
    assert _balance(reloaded, "agent-1") == 2 * ONE_AXM


# -- (d) same hash, different agent: refused ------------------------------------


def test_same_hash_different_agent_refused(ledger):
    ledger.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)
    with pytest.raises(DuplicateTxHashError):
        ledger.deposit("agent-2", 2 * ONE_AXM, reference=TX_A)
    assert _balance(ledger, "agent-2") == 0
    assert _balance(ledger, "agent-1") == 2 * ONE_AXM


# -- (e) same hash, different amount: refused ------------------------------------


def test_same_hash_different_amount_refused(ledger):
    ledger.deposit("agent-1", 2 * ONE_AXM, reference=TX_A)
    with pytest.raises(DuplicateTxHashError):
        ledger.deposit("agent-1", 5 * ONE_AXM, reference=TX_A)
    assert _balance(ledger, "agent-1") == 2 * ONE_AXM


def test_malformed_0x_reference_rejected_loudly(ledger):
    # A near-hash must not silently dodge the duplicate guard.
    with pytest.raises(ValueError):
        ledger.deposit("agent-1", ONE_AXM, reference="0xnotahash")


# -- (f) record_onchain_deposit uses the same guard ------------------------------


def test_record_onchain_deposit_idempotent(ledger):
    ledger.record_onchain_deposit("agent-1", 2 * ONE_AXM, TX_A)
    retry = ledger.record_onchain_deposit("agent-1", 2 * ONE_AXM, TX_A)
    assert retry["duplicate"] is True
    assert _balance(ledger, "agent-1") == 2 * ONE_AXM


# -- (g) no tx hash: no dedupe, both credit --------------------------------------


def test_no_reference_deposits_still_credit(ledger):
    ledger.deposit("agent-1", 2 * ONE_AXM)
    ledger.deposit("agent-1", 3 * ONE_AXM)
    assert _balance(ledger, "agent-1") == 5 * ONE_AXM


def test_opaque_reference_not_deduplicated(ledger):
    ledger.deposit("agent-1", 2 * ONE_AXM, reference="sponsored-stake:agent-1")
    ledger.deposit("agent-1", 2 * ONE_AXM, reference="sponsored-stake:agent-1")
    assert _balance(ledger, "agent-1") == 4 * ONE_AXM


# -- (h) concurrent identical deposits credit exactly once ------------------------


def test_concurrent_identical_deposits_credit_once(tmp_path):
    ledger = StakeLedger(path=str(tmp_path / "stake.json"))
    barrier = threading.Barrier(8)
    results = []

    def worker():
        barrier.wait()
        results.append(ledger.deposit("agent-1", ONE_AXM, reference=TX_A))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert _balance(ledger, "agent-1") == ONE_AXM
    assert sum(1 for r in results if not r["duplicate"]) == 1
    assert sum(1 for r in results if r["duplicate"]) == 7


# -- (i) HTTP route semantics ------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id):
    # Fail-closed C3: registration carries a real EIP-191 wallet proof.
    # Returns the signing key so deposits can be authorized.
    account = register_wallet_agent(client, agent_id)
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers=hb_headers())
    assert r.status_code == 200, r.get_json()
    return account


def _deposit_payload(agent_id, account, amount_axm, tx_hash=None,
                     expires_at_ms=None):
    # Fail-closed G2.16: every deposit carries an EIP-191 signature over
    # the canonical server-side message. Signatures are single-use, so
    # each POST builds a fresh one.
    amount_wei = int(round(float(amount_axm) * 1e18))
    if expires_at_ms is None:
        expires_at_ms = int(time.time() * 1000) + 5 * 60 * 1000
    message = stake_deposit_message(agent_id, amount_wei, account.address,
                                    expires_at_ms)
    signature = account.sign_message(
        encode_defunct(text=message)).signature.hex()
    payload = {"agent_id": agent_id, "amount_axm": amount_axm,
               "expires_at_ms": expires_at_ms, "signature": signature}
    if tx_hash is not None:
        payload["tx_hash"] = tx_hash
    return payload


def test_route_retry_returns_200_duplicate(client):
    account = _register(client, "agent-1")

    first = client.post("/v1/a2a/stake/deposit",
                        json=_deposit_payload("agent-1", account, 2,
                                              tx_hash=TX_A))
    assert first.status_code == 201
    assert first.get_json()["duplicate"] is False
    assert first.get_json()["deposit_wei"] == str(2 * ONE_AXM)

    # Honest retry: same tx_hash, fresh signature (signatures are single-use).
    retry = client.post("/v1/a2a/stake/deposit",
                        json=_deposit_payload("agent-1", account, 2,
                                              tx_hash=TX_A))
    assert retry.status_code == 200
    payload = retry.get_json()
    assert payload["duplicate"] is True
    assert payload["deposit_wei"] == "0"  # nothing credited on the retry

    bal = client.get("/v1/a2a/stake/agent-1").get_json()
    assert bal["deposited_wei"] == str(2 * ONE_AXM)


def test_route_conflicting_reuse_is_400(client):
    account_1 = _register(client, "agent-1")
    account_2 = _register(client, "agent-2")
    r = client.post("/v1/a2a/stake/deposit",
                    json=_deposit_payload("agent-1", account_1, 2,
                                          tx_hash=TX_A))
    assert r.status_code == 201

    # Same tx hash claimed by another agent: refused, no credit.
    r = client.post("/v1/a2a/stake/deposit",
                    json=_deposit_payload("agent-2", account_2, 2,
                                          tx_hash=TX_A))
    assert r.status_code == 400
    bal = client.get("/v1/a2a/stake/agent-2").get_json()
    assert bal["deposited_wei"] == "0"

    # Same tx hash with a different amount: refused as well.
    r = client.post("/v1/a2a/stake/deposit",
                    json=_deposit_payload("agent-1", account_1, 9,
                                          tx_hash=TX_A))
    assert r.status_code == 400
    bal = client.get("/v1/a2a/stake/agent-1").get_json()
    assert bal["deposited_wei"] == str(2 * ONE_AXM)


def test_route_tx_hash_case_insensitive(client):
    account = _register(client, "agent-1")
    mixed = "0x" + "aA" * 32  # same hash as TX_A ("aa"*32), different case
    r = client.post("/v1/a2a/stake/deposit",
                    json=_deposit_payload("agent-1", account, 1,
                                          tx_hash=mixed))
    assert r.status_code == 201
    # Same hash, different case: recognized as the same deposit.
    r = client.post("/v1/a2a/stake/deposit",
                    json=_deposit_payload("agent-1", account, 1,
                                          tx_hash=TX_A))
    assert r.status_code == 200
    assert r.get_json()["duplicate"] is True
    bal = client.get("/v1/a2a/stake/agent-1").get_json()
    assert bal["deposited_wei"] == str(ONE_AXM)
