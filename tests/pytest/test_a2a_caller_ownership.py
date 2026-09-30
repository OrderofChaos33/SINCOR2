"""P3 item 14 — A2A caller ownership.

Bypass-focused tests:
  * task creation requires a fresh EIP-191 caller signature (401 otherwise)
  * cancel / read are restricted to the creating (signing) wallet (403 cross-caller)
  * tasks/list is isolated per caller
  * marketplace poster_id is server-bound: unsigned / spoofed poster_id ignored
  * signature replay, stale timestamps, and cross-skill signatures are rejected
"""
from __future__ import annotations

import os
import time
import uuid

os.environ.setdefault("FLASK_ENV", "test")
os.environ.setdefault("ENVIRONMENT", "test")

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_inbound import register as register_inbound, reset_fabric
from sincor2.a2a_integration import (
    TaskState,
    _auth_create_message,
    _auth_market_create_message,
    _auth_task_message,
    _get_task,
    _handle_cancel,
    _handle_get_rpc,
    _handle_list,
    _handle_push_config_delete,
    _handle_push_config_get,
    _handle_push_config_list,
    _handle_push_config_set,
    _handle_resubscribe,
    _handle_send,
    _recover_eip191_signer,
    _update_task,
)

KEY_A = "0x" + "aa" * 32
KEY_B = "0x" + "bb" * 32
ACCT_A = Account.from_key(KEY_A)
ACCT_B = Account.from_key(KEY_B)
WALLET_A = ACCT_A.address.lower()
WALLET_B = ACCT_B.address.lower()

SKILL = "lead-enrichment"


def _sig(text: str, acct) -> str:
    return "0x" + acct.sign_message(encode_defunct(text=text)).signature.hex()


def _create_params(acct=ACCT_A, label="owner-a", skill: str = SKILL,
                   ts: int | None = None, sign_skill: str | None = None,
                   nonce: str | None = None, claim_wallet: str | None = None) -> dict:
    """Signed message/send params. sign_skill != skill simulates a signature
    bound to a different skill (cross-context replay)."""
    ts = int(ts if ts is not None else time.time())
    nonce = nonce or uuid.uuid4().hex
    message = _auth_create_message(label, sign_skill or skill, ts, nonce)
    return {
        "skillId": skill,
        "callerId": label,
        "message": {"role": "user", "parts": [{"text": "Enrich Acme"}]},
        "ownerWallet": claim_wallet or acct.address,
        "authSignature": _sig(message, acct),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def _send(acct=ACCT_A, label="owner-a", **kw) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": _create_params(acct, label, **kw)}
    return _handle_send(body)


def _task_auth_params(task_id: str, purpose: str, acct, ts: int | None = None,
                      nonce: str | None = None) -> dict:
    ts = int(ts if ts is not None else time.time())
    nonce = nonce or uuid.uuid4().hex
    return {
        "id": task_id,
        "authSignature": _sig(_auth_task_message(purpose, task_id, ts, nonce), acct),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


@pytest.fixture
def market_client():
    reset_fabric()
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


# --- creation auth ----------------------------------------------------------

def test_send_rejects_missing_signature():
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"skillId": SKILL, "callerId": "x",
                       "message": {"role": "user", "parts": [{"text": "hi"}]}}}
    err = _handle_send(body)["error"]
    assert err["code"] == -32010


def test_send_rejects_garbage_signature():
    params = _create_params()
    params["authSignature"] = "0xdeadbeef"
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": params}
    err = _handle_send(body)["error"]
    assert err["code"] == -32010


def test_send_rejects_stale_timestamp():
    err = _send(ts=int(time.time()) - 3600)["error"]
    assert err["code"] == -32010


def test_send_rejects_cross_skill_signature():
    # Signature bound to another skill recovers to a random address, which
    # cannot match the ownerWallet claim.
    err = _send(sign_skill="sbom-analysis")["error"]
    assert err["code"] == -32010


def test_send_rejects_owner_wallet_claim_mismatch():
    # Signed by A but claims B's wallet: the signature is valid, the claim
    # is not — rejected.
    err = _send(claim_wallet=WALLET_B)["error"]
    assert err["code"] == -32010


def test_send_requires_nonce():
    params = _create_params()
    del params["authNonce"]
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": params}
    err = _handle_send(body)["error"]
    assert err["code"] == -32010


def test_same_second_creates_both_succeed_with_distinct_nonces():
    # eth_account signs deterministically: without a per-call nonce the two
    # calls would collide and the second would trip the replay guard.
    first = _send()
    second = _send()
    assert "result" in first, first
    assert "result" in second, second
    assert first["result"]["id"] != second["result"]["id"]


def test_send_rejects_replayed_signature():
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": _create_params()}
    first = _handle_send(body)
    assert "result" in first, first
    second = _handle_send(body)
    assert second["error"]["code"] == -32010


def test_replay_guard_catches_signature_encoding_variants():
    # "0xAB..", "ab..", " 0xab.. " are the same signature: the replay guard
    # must canonicalize before digesting, or single-use is trivially
    # bypassed (different digest, same recovered address).
    task_id = _owned_task_id()
    base = _task_auth_params(task_id, "get", ACCT_A)
    sig = base["authSignature"]
    body0 = {"jsonrpc": "2.0", "id": 9, "method": "tasks/get", "params": base}
    assert _handle_get_rpc(body0)["result"]["id"] == task_id
    variants = [
        sig[2:] if sig.startswith("0x") else "0x" + sig,  # prefix toggled
        "  " + sig + " ",                                 # whitespace padded
        sig.upper(),                                      # case flipped
    ]
    for v in variants:
        p = dict(base)
        p["authSignature"] = v
        res = _handle_get_rpc({"jsonrpc": "2.0", "id": 9, "method": "tasks/get",
                               "params": p})
        assert res["error"]["code"] == -32010, v  # replay rejected


def test_send_binds_owner_wallet_not_caller_id():
    # callerId claims "victim" but the SIGNER owns the task.
    res = _send(label="victim")
    assert "result" in res, res
    assert res["result"]["ownerWallet"] == WALLET_A
    assert res["result"]["ownerWallet"] != "victim"


# --- cancel ownership (G2.1) -------------------------------------------------

def _owned_task_id(acct=ACCT_A, label="owner-a") -> str:
    res = _send(acct=acct, label=label)
    assert "result" in res, res
    return res["result"]["id"]


def test_cancel_by_non_owner_is_forbidden():
    task_id = _owned_task_id()
    body = {"jsonrpc": "2.0", "id": 2, "method": "tasks/cancel",
            "params": _task_auth_params(task_id, "cancel", ACCT_B)}
    err = _handle_cancel(body)["error"]
    assert err["code"] == -32011


def test_cancel_by_owner_succeeds():
    task_id = _owned_task_id()
    # Eager test env may already have completed the task; reset to a live
    # state so the cancel path itself is exercised.
    _update_task(_get_task(task_id), state=TaskState.SUBMITTED)
    body = {"jsonrpc": "2.0", "id": 2, "method": "tasks/cancel",
            "params": _task_auth_params(task_id, "cancel", ACCT_A)}
    res = _handle_cancel(body)
    assert "result" in res, res
    assert res["result"]["status"]["state"] == "canceled"


def test_cancel_without_signature_is_unauthorized():
    task_id = _owned_task_id()
    body = {"jsonrpc": "2.0", "id": 2, "method": "tasks/cancel",
            "params": {"id": task_id}}
    err = _handle_cancel(body)["error"]
    assert err["code"] == -32010


def test_cancel_unknown_task_stays_not_found():
    body = {"jsonrpc": "2.0", "id": 2, "method": "tasks/cancel",
            "params": _task_auth_params("nope", "cancel", ACCT_A)}
    err = _handle_cancel(body)["error"]
    assert err["code"] == -32602


# --- read ownership (G2.19) ---------------------------------------------------

def test_get_by_non_owner_is_forbidden():
    task_id = _owned_task_id()
    body = {"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
            "params": _task_auth_params(task_id, "get", ACCT_B)}
    err = _handle_get_rpc(body)["error"]
    assert err["code"] == -32011


def test_get_by_owner_succeeds():
    task_id = _owned_task_id()
    body = {"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
            "params": _task_auth_params(task_id, "get", ACCT_A)}
    res = _handle_get_rpc(body)
    assert "result" in res, res
    assert res["result"]["id"] == task_id


def test_cancel_signature_cannot_read():
    # Purpose binding: a cancel signature presented to tasks/get recovers to
    # a random address (never the owner), so it is rejected as forbidden.
    task_id = _owned_task_id()
    params = _task_auth_params(task_id, "cancel", ACCT_A)
    body = {"jsonrpc": "2.0", "id": 3, "method": "tasks/get", "params": params}
    err = _handle_get_rpc(body)["error"]
    assert err["code"] == -32011


# --- list isolation ----------------------------------------------------------

def _list_params(acct, ts: int | None = None, nonce: str | None = None) -> dict:
    ts = int(ts if ts is not None else time.time())
    nonce = nonce or uuid.uuid4().hex
    return {
        "ownerWallet": acct.address,
        "authSignature": _sig(_auth_task_message("list", "", ts, nonce), acct),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def test_list_isolated_per_caller():
    id_a = _owned_task_id(ACCT_A, "owner-a")
    id_b = _owned_task_id(ACCT_B, "owner-b")

    res_a = _handle_list({"jsonrpc": "2.0", "id": 4, "method": "tasks/list",
                          "params": _list_params(ACCT_A)})
    ids_a = {t["id"] for t in res_a["result"]["tasks"]}
    assert id_a in ids_a
    assert id_b not in ids_a
    assert all(t["ownerWallet"] == WALLET_A for t in res_a["result"]["tasks"])

    res_b = _handle_list({"jsonrpc": "2.0", "id": 4, "method": "tasks/list",
                          "params": _list_params(ACCT_B)})
    ids_b = {t["id"] for t in res_b["result"]["tasks"]}
    assert id_b in ids_b
    assert id_a not in ids_b


def test_list_rejects_missing_signature():
    err = _handle_list({"jsonrpc": "2.0", "id": 4, "method": "tasks/list",
                        "params": {}})["error"]
    assert err["code"] == -32010


# --- REST + dispatcher HTTP status mapping -----------------------------------

def test_rest_send_unauthenticated_is_401(client):
    resp = client.post("/api/a2a/tasks/send", json={
        "method": "message/send",
        "params": {"skillId": SKILL, "callerId": "x",
                   "message": {"role": "user", "parts": [{"text": "hi"}]}}})
    assert resp.status_code == 401
    assert resp.get_json()["error"]["code"] == -32010


def test_rest_get_cross_caller_is_403(client):
    send = client.post("/api/a2a/tasks/send", json={
        "method": "message/send", "params": _create_params()})
    assert send.status_code == 202, send.get_json()
    task_id = send.get_json()["task_id"]

    # no auth at all -> 401
    assert client.get(f"/api/a2a/tasks/{task_id}").status_code == 401

    # wrong owner -> 403
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    bad_qs = {"authSignature": _sig(_auth_task_message("get", task_id, ts, nonce), ACCT_B),
              "authTimestamp": ts, "authNonce": nonce}
    resp = client.get(f"/api/a2a/tasks/{task_id}", query_string=bad_qs)
    assert resp.status_code == 403
    assert resp.get_json()["error"]["code"] == -32011

    # owner -> 200
    ts2 = int(time.time())
    nonce2 = uuid.uuid4().hex
    good_qs = {"authSignature": _sig(_auth_task_message("get", task_id, ts2, nonce2), ACCT_A),
               "authTimestamp": ts2, "authNonce": nonce2}
    resp = client.get(f"/api/a2a/tasks/{task_id}", query_string=good_qs)
    assert resp.status_code == 200
    assert resp.get_json()["result"]["id"] == task_id


def test_rest_cancel_cross_caller_is_403(client):
    send = client.post("/api/a2a/tasks/send", json={
        "method": "message/send", "params": _create_params()})
    task_id = send.get_json()["task_id"]
    resp = client.post("/api/a2a/tasks/cancel", json={
        "id": task_id, **_task_auth_params(task_id, "cancel", ACCT_B)})
    assert resp.status_code == 403


def test_dispatcher_auth_failures_map_to_http_status(client):
    # unauthenticated JSON-RPC send -> HTTP 401 (not 200)
    resp = client.post("/api/a2a", json={
        "jsonrpc": "2.0", "id": 1, "method": "message/send",
        "params": {"skillId": SKILL, "callerId": "x",
                   "message": {"role": "user", "parts": [{"text": "hi"}]}}})
    assert resp.status_code == 401


# --- marketplace poster binding (G2.15) ---------------------------------------

def _market_auth(acct, skill: str, ts: int | None = None,
                 nonce: str | None = None) -> dict:
    ts = int(ts if ts is not None else time.time())
    nonce = nonce or uuid.uuid4().hex
    return {
        "authSignature": _sig(_auth_market_create_message(skill, ts, nonce), acct),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def test_market_spoofed_poster_id_ignored(market_client):
    resp = market_client.post("/v1/a2a/tasks", json={
        "skill": "lead-enrichment", "bounty_axm": 1.0,
        "poster_id": "victim-poster", "agent_id": "mallory"})
    assert resp.status_code == 201
    assert resp.get_json()["poster_id"] is None


def test_market_signed_poster_bound_to_signer_wallet(market_client):
    skill = "lead-enrichment"
    resp = market_client.post("/v1/a2a/tasks", json={
        "skill": skill, "bounty_axm": 1.0,
        "poster_id": "victim-poster", "agent_id": "mallory-label",
        **_market_auth(ACCT_A, skill)})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["poster_id"] == WALLET_A
    assert body["poster_id"] != "victim-poster"
    assert body["poster_id"] != "mallory-label"


def test_market_signature_bound_to_skill(market_client):
    # Signature over a different skill recovers to a random address: the
    # post must NOT be attributed to the signer.
    resp = market_client.post("/v1/a2a/tasks", json={
        "skill": "lead-enrichment", "bounty_axm": 1.0,
        **_market_auth(ACCT_A, "sbom-analysis")})
    assert resp.status_code == 201
    assert resp.get_json()["poster_id"] != WALLET_A


def test_market_replayed_signature_not_attributed(market_client):
    payload = {"skill": "lead-enrichment", "bounty_axm": 1.0,
               **_market_auth(ACCT_A, "lead-enrichment")}
    first = market_client.post("/v1/a2a/tasks", json=payload)
    assert first.status_code == 201
    assert first.get_json()["poster_id"] == WALLET_A
    second = market_client.post("/v1/a2a/tasks", json=payload)
    assert second.status_code == 201
    assert second.get_json()["poster_id"] is None


# --- resubscribe + push-config gating (remaining task-read paths) -------------

def _push_auth_params(task_id: str, acct, extra: dict | None = None) -> dict:
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    params = {
        "taskId": task_id,
        "authSignature": _sig(_auth_task_message("push", task_id, ts, nonce), acct),
        "authTimestamp": ts,
        "authNonce": nonce,
    }
    if extra:
        params.update(extra)
    return params


def _resub_events(task_id: str, acct) -> list:
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    body = {"jsonrpc": "2.0", "id": 5, "method": "tasks/resubscribe",
            "params": {"id": task_id,
                       "authSignature": _sig(_auth_task_message("get", task_id, ts, nonce), acct),
                       "authTimestamp": ts, "authNonce": nonce}}
    import json as _json
    return [_json.loads(e[len("data: "):]) for e in _handle_resubscribe(body)]


def test_resubscribe_by_non_owner_is_forbidden():
    task_id = _owned_task_id()
    events = _resub_events(task_id, ACCT_B)
    assert events[0]["error"]["code"] == -32011


def test_resubscribe_by_owner_emits_status():
    task_id = _owned_task_id()
    events = _resub_events(task_id, ACCT_A)
    assert "result" in events[0], events[0]
    assert events[0]["result"]["taskStatus"]["taskId"] == task_id


def test_resubscribe_without_signature_is_unauthorized():
    task_id = _owned_task_id()
    import json as _json
    body = {"jsonrpc": "2.0", "id": 5, "method": "tasks/resubscribe",
            "params": {"id": task_id}}
    events = [_json.loads(e[len("data: "):]) for e in _handle_resubscribe(body)]
    assert events[0]["error"]["code"] == -32010


def test_push_config_set_by_non_owner_is_forbidden():
    task_id = _owned_task_id()
    body = {"jsonrpc": "2.0", "id": 6, "method": "tasks/pushNotificationConfig/set",
            "params": _push_auth_params(task_id, ACCT_B,
                                        {"url": "https://evil.example/hook"})}
    assert _handle_push_config_set(body)["error"]["code"] == -32011


def test_push_config_get_set_delete_owner_only():
    task_id = _owned_task_id()
    # set + get by the owner
    set_body = {"jsonrpc": "2.0", "id": 6, "method": "tasks/pushNotificationConfig/set",
                "params": _push_auth_params(task_id, ACCT_A,
                                            {"url": "https://owner.example/hook",
                                             "token": "secret-token"})}
    assert "result" in _handle_push_config_set(set_body)
    get_body = {"jsonrpc": "2.0", "id": 6, "method": "tasks/pushNotificationConfig/get",
                "params": _push_auth_params(task_id, ACCT_A)}
    res = _handle_push_config_get(get_body)
    assert res["result"]["token"] == "secret-token"
    # non-owner cannot read the token
    get_bad = {"jsonrpc": "2.0", "id": 6, "method": "tasks/pushNotificationConfig/get",
               "params": _push_auth_params(task_id, ACCT_B)}
    assert _handle_push_config_get(get_bad)["error"]["code"] == -32011
    # non-owner cannot delete
    del_bad = {"jsonrpc": "2.0", "id": 6, "method": "tasks/pushNotificationConfig/delete",
               "params": _push_auth_params(task_id, ACCT_B)}
    assert _handle_push_config_delete(del_bad)["error"]["code"] == -32011
    # owner can delete
    del_ok = {"jsonrpc": "2.0", "id": 6, "method": "tasks/pushNotificationConfig/delete",
              "params": _push_auth_params(task_id, ACCT_A)}
    assert "result" in _handle_push_config_delete(del_ok)


def test_push_config_list_isolated_per_caller():
    id_a = _owned_task_id(ACCT_A, "owner-a")
    id_b = _owned_task_id(ACCT_B, "owner-b")
    _handle_push_config_set(
        {"jsonrpc": "2.0", "id": 6, "params": _push_auth_params(id_a, ACCT_A, {"url": "https://a.example"})})
    _handle_push_config_set(
        {"jsonrpc": "2.0", "id": 6, "params": _push_auth_params(id_b, ACCT_B, {"url": "https://b.example"})})
    res = _handle_push_config_list(
        {"jsonrpc": "2.0", "id": 6, "params": _list_params(ACCT_A)})
    urls = {c["url"] for c in res["result"]["configs"]}
    assert "https://a.example" in urls
    assert "https://b.example" not in urls


# --- crypto sanity ------------------------------------------------------------

def test_recover_eip191_roundtrip():
    message = _auth_create_message("agent-x", SKILL, 1234567890, "nonce-1")
    sig = _sig(message, ACCT_A)
    assert _recover_eip191_signer(message, sig).lower() == WALLET_A
    assert _recover_eip191_signer(message, "0x" + "00" * 65) is None
    assert _recover_eip191_signer(message, "not-hex") is None
    assert _recover_eip191_signer(message, "") is None


def test_recover_rejects_high_s_malleation():
    # (r, n-s) malleation recovers to the same address with different bytes;
    # without low-s enforcement it would mint a fresh replay-cache digest and
    # defeat single-use. It must be rejected.
    from sincor2.a2a_integration import _SECP256K1_N
    message = _auth_create_message("agent-x", SKILL, 1234567890, "nonce-2")
    sig = _sig(message, ACCT_A)
    raw = bytes.fromhex(sig[2:])
    r, s, v = raw[:32], int.from_bytes(raw[32:64], "big"), raw[64:]
    assert s <= _SECP256K1_N // 2  # eth_account signs canonical low-s
    malleated = "0x" + (r + (_SECP256K1_N - s).to_bytes(32, "big") + v).hex()
    assert malleated != sig
    assert _recover_eip191_signer(message, malleated) is None
