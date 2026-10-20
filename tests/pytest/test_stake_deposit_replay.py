"""W-40/W-41: stake-deposit replay guards (money path).

W-40: the single-use replay key must be computed over the CANONICAL
signature bytes, not the raw caller-supplied string. eth_account recovery
accepts the same 65 signature bytes in many encodings (lowercase hex,
UPPERCASE hex, with/without the 0x prefix); a hash of the raw string let
one signing operation credit stake once per encoding. The route now
normalizes via sincor2.sig_canonical.require_low_s before hashing.

W-41: the single-use check-and-record must be atomic across processes.
The old route-level pre-check (in-memory scan under a process-local
threading.Lock) raced between workers; the guard now lives inside
StakeLedger.deposit(), under the instance lock plus the POSIX exclusive
file lock, after a fresh reload.

These tests prove:
  (a) UPPERCASE-hex re-encoding of a consumed signature -> 403,
      nothing credited;
  (b) 0x-prefix flip of a consumed signature -> 403, nothing credited;
  (c) byte-identical replay -> 403 (unchanged behavior);
  (d) fresh signatures credit correctly (201 each, accumulating);
  (e) high-s twins (raw and re-encoded) are blocked by require_low_s;
  (f) honest tx_hash retry with a FRESH signature -> 200 duplicate
      (W-36 idempotency path untouched by the moved check);
  (g) ledger level: a stale second StakeLedger instance on the same file
      cannot double-credit the same signature hash (cross-process race
      closed);
  (h) ledger level: threads racing the same signature hash credit
      exactly once;
  (i) round 2: v-variant replay, native-first order (v=27/28 deposit
      -> 201, then same (r, s) re-encoded as v-27 -> 403);
  (j) round 2: v-variant replay, flipped-first order (v=0/1 deposit
      -> 201, then native v=27/28 -> 403).

eth_account accepts v in {0, 1, 27, 28} with 0==27 and 1==28 recovering
to the SAME signer, so the same signing operation could previously
double-credit by re-encoding the v byte. require_low_s now maps 0->27
and 1->28 (rejecting any other v), closing the bypass.
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
from sincor2.onchain.stake_ledger import StakeLedger, reset_stake_ledger
from sincor2.sig_canonical import SECP256K1_N

ONE_AXM = 10**18
TX_A = "0x" + "aa" * 32


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id):
    account = register_wallet_agent(client, agent_id)
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id},
                    headers=hb_headers())
    assert r.status_code == 200, r.get_json()
    return account


def _payload(agent_id, account, amount_axm, tx_hash=None,
             signature_override=None, expires_at_ms=None):
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
    body = {"agent_id": agent_id, "amount_axm": amount_axm,
            "expires_at_ms": expires_at_ms, "signature": signature}
    if tx_hash is not None:
        body["tx_hash"] = tx_hash
    return body


def _post(client, payload):
    return client.post("/v1/a2a/stake/deposit", json=payload)


def _deposited(client, agent_id):
    r = client.get(f"/v1/a2a/stake/{agent_id}")
    assert r.status_code == 200
    return int(r.get_json()["deposited_wei"])


# -- W-40: encoding replays -----------------------------------------------------

def test_uppercase_hex_reencoding_of_used_signature_rejected(client):
    """PoC scenario 1: the same 65 bytes as UPPERCASE hex must not
    credit a second time."""
    acct = _register(client, "w40-upper")
    payload = _payload("w40-upper", acct, 5)
    raw = payload["signature"]  # lowercase hex, no 0x prefix
    r = _post(client, payload)
    assert r.status_code == 201, r.get_json()

    replay = dict(payload)
    replay["signature"] = "0x" + raw.upper()  # same bytes, new encoding
    r = _post(client, replay)
    assert r.status_code == 403, r.get_json()
    assert _deposited(client, "w40-upper") == 5 * ONE_AXM


def test_0x_prefix_flip_of_used_signature_rejected(client):
    """PoC scenario 2: the same 65 bytes with a 0x prefix must not
    credit a second time."""
    acct = _register(client, "w40-prefix")
    payload = _payload("w40-prefix", acct, 3)
    raw = payload["signature"]
    r = _post(client, payload)
    assert r.status_code == 201, r.get_json()

    for variant in ("0x" + raw, "0X" + raw.upper(), raw.upper()):
        replay = dict(payload)
        replay["signature"] = variant
        r = _post(client, replay)
        assert r.status_code == 403, (variant, r.get_json())
    assert _deposited(client, "w40-prefix") == 3 * ONE_AXM


def test_byte_identical_replay_still_403(client):
    acct = _register(client, "w40-identical")
    payload = _payload("w40-identical", acct, 5)
    assert _post(client, payload).status_code == 201
    r = _post(client, dict(payload))
    assert r.status_code == 403, r.get_json()
    assert _deposited(client, "w40-identical") == 5 * ONE_AXM


def test_fresh_signatures_credit_independently(client):
    """Legitimate distinct deposits (one fresh signature each) still
    credit 201 and accumulate."""
    acct = _register(client, "w40-legit")
    for amt in (5, 7):
        r = _post(client, _payload("w40-legit", acct, amt))
        assert r.status_code == 201, r.get_json()
    assert _deposited(client, "w40-legit") == 12 * ONE_AXM


# -- high-s twins stay blocked ---------------------------------------------------

def _high_s_twin(signature_hex):
    raw = bytes.fromhex(signature_hex[2:]
                        if signature_hex[:2].lower() == "0x"
                        else signature_hex)
    s = int.from_bytes(raw[32:64], "big")
    twin_s = (SECP256K1_N - s).to_bytes(32, "big")
    return (raw[:32] + twin_s + raw[64:65]).hex()


def test_high_s_variants_rejected_403(client):
    """The high-s malleability twin recovers to the same signer but is
    non-canonical: require_low_s must reject it in every encoding."""
    acct = _register(client, "w40-highs")
    payload = _payload("w40-highs", acct, 5)
    twin = _high_s_twin(payload["signature"])
    for variant in (twin, "0x" + twin, twin.upper(), "0x" + twin.upper()):
        r = _post(client, _payload("w40-highs", acct, 5,
                                   signature_override=variant))
        assert r.status_code == 403, (variant, r.get_json())
    assert _deposited(client, "w40-highs") == 0


# -- W-36 interaction: fresh-signature retry still 200 duplicate -----------------

def test_tx_hash_retry_with_fresh_signature_still_200_duplicate(client):
    """The moved atomic check must not swallow the W-36 idempotency
    path: same tx_hash + a FRESH signature retries as 200 duplicate."""
    acct = _register(client, "w40-retry")
    r = _post(client, _payload("w40-retry", acct, 2, tx_hash=TX_A))
    assert r.status_code == 201, r.get_json()
    r = _post(client, _payload("w40-retry", acct, 2, tx_hash=TX_A))
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["duplicate"] is True
    assert body["deposit_wei"] == "0"
    assert _deposited(client, "w40-retry") == 2 * ONE_AXM


# -- W-41: ledger-level atomicity -------------------------------------------------

SIG_HASH = "bb" * 32


def test_stale_instance_cannot_double_credit_signature_hash(tmp_path):
    """PoC scenario 3: two StakeLedger instances on the same file
    (two worker processes' views). The second deposit with an
    already-consumed signature hash must raise, even though the second
    instance's in-memory view predates the first write."""
    path = str(tmp_path / "stake.json")
    led_a = StakeLedger(path=path)
    led_b = StakeLedger(path=path)  # stale view, like another worker

    first = led_a.deposit("w41-agent", ONE_AXM, signature_hash=SIG_HASH)
    assert first["duplicate"] is False

    # led_b never reloaded; the atomic check inside deposit() must catch
    # the consumed hash on its own reload under the exclusive file lock.
    try:
        led_b.deposit("w41-agent", ONE_AXM, signature_hash=SIG_HASH)
    except PermissionError as exc:
        assert "already used" in str(exc)
    else:
        raise AssertionError("stale instance double-credited the "
                             "consumed signature hash")
    assert int(led_b.balance_of("w41-agent")["deposited_wei"]) == ONE_AXM


def test_concurrent_same_signature_hash_credits_once(tmp_path):
    """Threads racing the same consumed-authorization: exactly one
    credits, the rest get the opaque 403-equivalent error."""
    ledger = StakeLedger(path=str(tmp_path / "stake.json"))
    barrier = threading.Barrier(8)
    outcomes = []

    def worker():
        barrier.wait()
        try:
            ledger.deposit("w41-race", ONE_AXM, signature_hash=SIG_HASH)
            outcomes.append("credited")
        except PermissionError:
            outcomes.append("rejected")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("credited") == 1, outcomes
    assert outcomes.count("rejected") == 7, outcomes
    assert int(ledger.balance_of("w41-race")["deposited_wei"]) == ONE_AXM


# -- W-40 round 2: v-byte variants -----------------------------------------------

def _flip_v(signature_hex):
    """Re-encode the same (r, s) with the alternate v encoding:
    27 -> 0, 28 -> 1. eth_account recovers both to the same signer."""
    raw = bytes.fromhex(signature_hex[2:]
                        if signature_hex[:2].lower() == "0x"
                        else signature_hex)
    v = raw[64]
    flipped = (v - 27) if v in (27, 28) else (v + 27)
    return (raw[:64] + bytes([flipped])).hex()


def test_v_flip_replay_after_native_deposit_rejected(client):
    """Round-2 bypass PoC (native-first): deposit with the native
    v=27/28 signature -> 201; the same (r, s) re-encoded with v-27
    must 403, crediting nothing further."""
    acct = _register(client, "w40-vflip")
    payload = _payload("w40-vflip", acct, 5)
    assert _post(client, payload).status_code == 201

    replay = dict(payload)
    replay["signature"] = _flip_v(payload["signature"])
    r = _post(client, replay)
    assert r.status_code == 403, r.get_json()
    assert _deposited(client, "w40-vflip") == 5 * ONE_AXM


def test_native_replay_after_v_flip_deposit_rejected(client):
    """Round-2 bypass PoC (flipped-first): deposit with the v=0/1
    encoding -> 201; the native v=27/28 encoding of the same (r, s)
    must 403, crediting nothing further."""
    acct = _register(client, "w40-vflip-rev")
    payload = _payload("w40-vflip-rev", acct, 5)
    flipped = dict(payload)
    flipped["signature"] = _flip_v(payload["signature"])
    assert _post(client, flipped).status_code == 201

    r = _post(client, dict(payload))
    assert r.status_code == 403, r.get_json()
    assert _deposited(client, "w40-vflip-rev") == 5 * ONE_AXM
