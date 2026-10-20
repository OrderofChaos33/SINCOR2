"""W-105-class finding: _auth_digest does not normalize the v byte.

``_auth_digest`` (src/sincor2/a2a_integration.py) canonicalizes hex case /
0x-prefix / whitespace but NOT the v byte. A captured caller-auth signature
re-encoded with v 27->0 produces a different sha256 digest while still
recovering to the same signer via eth_account, defeating the
``_auth_replay_seen`` single-use guard (task create/get/cancel/list and bid
commit/reveal). Same bug class as W-40 (stake-deposit replay), now in the
caller-auth path.

These tests pin the fix:
  * the PoC below FAILS before the fix (v-flipped replay accepted twice)
    and PASSES after (rejected as replay in both submission orders);
  * EIP-155-style v values (37/38) are rejected fail-closed;
  * honest distinct auths, encoding variants, high-s, and the exact error
    surface are all pinned.
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

from sincor2.a2a_integration import (
    _auth_bid_message,
    _auth_create_message,
    _handle_send,
    _server_bound_bidder_wallet,
    _verify_create_auth,
)

KEY_A = "0x" + "aa" * 32
KEY_B = "0x" + "bb" * 32
ACCT_A = Account.from_key(KEY_A)
ACCT_B = Account.from_key(KEY_B)
WALLET_A = ACCT_A.address.lower()

SKILL = "lead-enrichment"

ERR_AUTH_REQUIRED = -32010
REPLAY_MESSAGE = "Signature already used (replay rejected)."
INVALID_SIG_MESSAGE = "Invalid caller signature (does not match ownerWallet)."


def _sign(text: str, acct=ACCT_A) -> str:
    return "0x" + acct.sign_message(encode_defunct(text=text)).signature.hex()


def _flip_v(sig_hex: str, to_v: int) -> str:
    raw = bytes.fromhex(sig_hex[2:] if sig_hex.startswith("0x") else sig_hex)
    assert len(raw) == 65
    return "0x" + (raw[:64] + bytes([to_v])).hex()


def _create_body(signature: str, ts: int, nonce: str, label: str = "owner-a",
                 acct=ACCT_A, skill: str = SKILL) -> dict:
    params = {
        "skillId": skill,
        "callerId": label,
        "message": {"role": "user", "parts": [{"text": "Enrich Acme"}]},
        "ownerWallet": acct.address,
        "authSignature": signature,
        "authTimestamp": ts,
        "authNonce": nonce,
    }
    return {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": params}


def _fresh_create(sig_v: int | None = None, label: str = "owner-a",
                  acct=ACCT_A, skill: str = SKILL):
    """Sign a fresh create-auth message (unique nonce); optionally re-encode v."""
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = _auth_create_message(label, skill, ts, nonce)
    sig = _sign(message, acct)
    if sig_v is not None:
        sig = _flip_v(sig, sig_v)
    return _create_body(sig, ts, nonce, label, acct, skill)


def _alt_v(v: int) -> int:
    """Alternate v encoding of the same recovery id: 27<->0, 28<->1.

    (Cross-pair flips like 27->28 recover a DIFFERENT address — that is not
    a re-encoding, it is a different signature.)"""
    return {27: 0, 0: 27, 28: 1, 1: 28}[v]


def _create_signed_pair(want_native_v: int, label: str = "owner-a",
                        acct=ACCT_A, skill: str = SKILL):
    """Mint (ts, nonce, sig_native, sig_alt) where sig_native has
    v == want_native_v and sig_alt is the same (r, s) in the alternate v
    encoding. The nonce is free, so we retry until the signer produces the
    wanted recovery parity (~50/50 per try)."""
    ts = int(time.time())
    for _ in range(256):
        nonce = uuid.uuid4().hex
        message = _auth_create_message(label, skill, ts, nonce)
        sig = _sign(message, acct)
        if bytes.fromhex(sig[2:])[64] == want_native_v:
            return ts, nonce, sig, _flip_v(sig, _alt_v(want_native_v))
    raise AssertionError(f"could not mint a native v={want_native_v} signature")


# --- PoC: v-flip defeats the replay guard --------------------------------------

def test_poc_v27_to_v0_replay_is_rejected():
    """PoC for the finding.

    Submit an auth signature in its native v=27 encoding (accepted), then
    re-submit the SAME (r, s) with v re-encoded as 0. eth_account recovers
    the identical signer for both, so the second submit must trip the
    replay guard. FAILS before the fix (accepted twice = double-use);
    PASSES after.
    """
    ts, nonce, sig_native, sig_alt = _create_signed_pair(27)

    first = _handle_send(_create_body(sig_native, ts, nonce))
    assert "result" in first, first

    second = _handle_send(_create_body(sig_alt, ts, nonce))  # attacker re-encoding
    err = second.get("error")
    assert err is not None, f"v-flipped replay was ACCEPTED (the bug): {second}"
    assert err["code"] == ERR_AUTH_REQUIRED
    assert err["message"] == REPLAY_MESSAGE


# --- (a) v re-encodings rejected in both submission orders --------------------

@pytest.mark.parametrize("native_v", [27, 28])
def test_v_reencoding_rejected_both_orders(native_v):
    """Both v pairs (27<->0, 28<->1) share one replay digest regardless of
    which encoding is submitted first."""
    alt = _alt_v(native_v)

    # order 1: native encoding first, alt-encoding replay
    ts, nonce, sig_native, sig_alt = _create_signed_pair(native_v)
    assert "result" in _handle_send(_create_body(sig_native, ts, nonce))
    dup = _handle_send(_create_body(sig_alt, ts, nonce))
    assert dup["error"]["code"] == ERR_AUTH_REQUIRED, dup
    assert dup["error"]["message"] == REPLAY_MESSAGE

    # order 2: alt-encoding first (fresh message), native replay
    ts2, nonce2, sig_native2, sig_alt2 = _create_signed_pair(native_v)
    assert "result" in _handle_send(_create_body(sig_alt2, ts2, nonce2))
    dup2 = _handle_send(_create_body(sig_native2, ts2, nonce2))
    assert dup2["error"]["code"] == ERR_AUTH_REQUIRED, dup2
    assert dup2["error"]["message"] == REPLAY_MESSAGE


# --- (b) EIP-155-style v values rejected fail-closed ---------------------------

@pytest.mark.parametrize("bad_v", [37, 38, 2, 29, 255])
def test_eip155_style_v_rejected_fail_closed(bad_v):
    """v values outside {0,1,27,28} recover to nothing: the request is
    refused with the pre-existing invalid-auth payload (401), never a 500,
    never a new error shape."""
    from sincor2.a2a_integration import (
        _auth_http_status,
        _recover_eip191_signer,
    )
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = _auth_create_message("owner-a", SKILL, ts, nonce)
    sig_bad = _flip_v(_sign(message), bad_v)

    assert _recover_eip191_signer(message, sig_bad) is None

    owner, payload = _verify_create_auth(
        _create_body(sig_bad, ts, nonce)["params"], SKILL)
    assert owner is None
    assert payload["error"]["code"] == ERR_AUTH_REQUIRED
    assert payload["error"]["message"] == INVALID_SIG_MESSAGE
    assert _auth_http_status(payload) == 401


# --- (c) honest distinct auths still accepted ----------------------------------

def test_distinct_auths_still_accepted():
    """Different nonces (different signed messages) are distinct uses."""
    for _ in range(3):
        res = _handle_send(_fresh_create())
        assert "result" in res, res


# --- (d) encoding variants still canonicalize ---------------------------------

def test_digest_canonicalizes_encoding_variants():
    """Uppercase hex, 0x prefix toggling, and whitespace padding all digest
    to the same key as the native encoding."""
    from sincor2.a2a_integration import _auth_digest
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = _auth_create_message("owner-a", SKILL, ts, nonce)
    sig = _sign(message)
    canon = _auth_digest(message, sig)
    assert _auth_digest(message, sig.upper()) == canon
    assert _auth_digest(message, sig[2:]) == canon          # prefix stripped
    assert _auth_digest(message, "  " + sig + "\n") == canon  # padded
    # and the v-flipped form now digests identically too:
    native_v = bytes.fromhex(sig[2:])[64]
    assert _auth_digest(message, _flip_v(sig, _alt_v(native_v))) == canon


def test_encoding_variant_replay_rejected_at_route():
    """Route-level pin: native submit, then uppercase-hex resubmit."""
    body = _fresh_create()
    first = _handle_send(body)
    assert "result" in first, first
    params = dict(body["params"])
    params["authSignature"] = params["authSignature"].upper()
    second = _handle_send({"jsonrpc": "2.0", "id": 2, "method": "message/send",
                           "params": params})
    assert second["error"]["code"] == ERR_AUTH_REQUIRED
    assert second["error"]["message"] == REPLAY_MESSAGE


# --- (e) high-s still rejected --------------------------------------------------

def test_high_s_still_rejected():
    """(r, n-s) malleation recovers to the same address with different bytes;
    it must keep failing closed at recovery, and _auth_digest must never
    mint it a fresh replay key."""
    from sincor2.a2a_integration import (
        _SECP256K1_N,
        _auth_digest,
        _AUTH_DIGEST_MALFORMED,
        _auth_replay_seen,
        _recover_eip191_signer,
    )
    message = _auth_create_message("agent-x", SKILL, 1234567890, "nonce-hs")
    sig = _sign(message)
    raw = bytes.fromhex(sig[2:])
    s = int.from_bytes(raw[32:64], "big")
    assert s <= _SECP256K1_N // 2  # eth_account signs canonical low-s
    malleated = "0x" + (raw[:32] + (_SECP256K1_N - s).to_bytes(32, "big")
                        + raw[64:]).hex()

    assert _recover_eip191_signer(message, malleated) is None
    # fail-closed at the digest layer: no fresh key, maps to the poisoned
    # sentinel which the replay guard treats as consumed.
    assert _auth_digest(message, malleated) == _AUTH_DIGEST_MALFORMED
    assert _auth_replay_seen(_AUTH_DIGEST_MALFORMED) is True


# --- (f) error surface unchanged -----------------------------------------------

def test_error_surface_unchanged():
    """Replay and invalid-auth payloads are byte-identical to the pre-fix
    surface: code -32010 (HTTP 401 via _auth_http_status), exact messages."""
    from sincor2.a2a_integration import _auth_http_status

    body = _fresh_create()
    assert "result" in _handle_send(body)
    replay = _handle_send(body)  # identical resubmit
    err = replay["error"]
    assert err["code"] == ERR_AUTH_REQUIRED
    assert err["message"] == REPLAY_MESSAGE
    assert _auth_http_status(replay) == 401

    # wrong-key signature: invalid auth, not a new error kind
    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = _auth_create_message("owner-a", SKILL, ts, nonce)
    bad = _create_body(_sign(message, ACCT_B), ts, nonce)  # signed by B, claims A
    owner, payload = _verify_create_auth(bad["params"], SKILL)
    assert owner is None
    assert payload["error"]["code"] == ERR_AUTH_REQUIRED
    assert payload["error"]["message"] == INVALID_SIG_MESSAGE
    assert _auth_http_status(payload) == 401


def test_malformed_signature_digest_is_fail_closed_no_500():
    """Direct _auth_digest call on garbage: no exception (no 500), and the
    resulting key is already consumed, so the replay guard rejects it."""
    from sincor2.a2a_integration import (
        _AUTH_DIGEST_MALFORMED,
        _auth_digest,
        _auth_replay_seen,
    )
    for garbage in ("not-hex", "", "0x1234", "0x" + "zz" * 65):
        digest = _auth_digest("any message", garbage)  # must not raise
        assert digest == _AUTH_DIGEST_MALFORMED
        assert _auth_replay_seen(digest) is True


# --- (g) bid-commit path replay guard ------------------------------------------

def _bid_body(signature: str, ts: int, nonce: str, task_id: str,
              agent_id: str, payload_hex: str) -> dict:
    return {
        "authSignature": signature,
        "authTimestamp": ts,
        "authNonce": nonce,
        "taskId": task_id,
        "agentId": agent_id,
        "commitment": payload_hex,
    }


def _bid_signed_pair(want_native_v: int):
    """Mint a bid-commit (message, ts, nonce, ids, sig_native, sig_alt) pair."""
    ts = int(time.time())
    task_id = "task-" + uuid.uuid4().hex[:8]
    agent_id = "agent-" + uuid.uuid4().hex[:8]
    commitment = "0x" + "cc" * 32
    for _ in range(256):
        nonce = uuid.uuid4().hex
        message = _auth_bid_message("commit", task_id, agent_id, commitment,
                                    ts, nonce)
        sig = _sign(message)
        if bytes.fromhex(sig[2:])[64] == want_native_v:
            return (message, ts, nonce, task_id, agent_id, commitment,
                    sig, _flip_v(sig, _alt_v(want_native_v)))
    raise AssertionError(f"could not mint a native v={want_native_v} bid signature")


@pytest.mark.parametrize("native_v", [27, 28])
def test_bid_commit_v_flip_replay_rejected(native_v):
    """Route-level pin on the bid-commit path: the same signed commit
    re-encoded with a flipped v must be rejected as already-used on the
    second call, in both submission orders."""
    alt = _alt_v(native_v)

    (message, ts, nonce, task_id, agent_id, commitment,
     sig_native, sig_alt) = _bid_signed_pair(native_v)

    wallet = _server_bound_bidder_wallet(
        _bid_body(sig_native, ts, nonce, task_id, agent_id, commitment),
        purpose="commit", task_id=task_id, agent_id=agent_id,
        payload=commitment)
    assert wallet == WALLET_A, "first (honest) commit must be attributed"

    replay = _server_bound_bidder_wallet(
        _bid_body(sig_alt, ts, nonce, task_id, agent_id, commitment),
        purpose="commit", task_id=task_id, agent_id=agent_id,
        payload=commitment)
    assert replay is None, (
        f"v-flipped bid commit was ACCEPTED as fresh "
        f"(order {native_v}->{alt}): attributed to {replay}"
    )

    # flipped-first order (fresh commit)
    (message2, ts2, nonce2, task_id2, agent_id2, commitment2,
     sig_native2, sig_alt2) = _bid_signed_pair(native_v)
    wallet2 = _server_bound_bidder_wallet(
        _bid_body(sig_alt2, ts2, nonce2, task_id2, agent_id2, commitment2),
        purpose="commit", task_id=task_id2, agent_id=agent_id2,
        payload=commitment2)
    assert wallet2 == WALLET_A, "first (alt-encoding) commit must be attributed"
    replay2 = _server_bound_bidder_wallet(
        _bid_body(sig_native2, ts2, nonce2, task_id2, agent_id2, commitment2),
        purpose="commit", task_id=task_id2, agent_id=agent_id2,
        payload=commitment2)
    assert replay2 is None, (
        f"native bid commit was ACCEPTED as fresh after alt-encoding "
        f"(order {alt}->{native_v}): attributed to {replay2}"
    )


def test_bid_commit_distinct_nonce_accepted():
    """Sanity: the bid path still accepts genuinely distinct commits."""
    for _ in range(2):
        ts = int(time.time())
        nonce = uuid.uuid4().hex
        task_id = "task-" + uuid.uuid4().hex[:8]
        agent_id = "agent-" + uuid.uuid4().hex[:8]
        commitment = "0x" + "dd" * 32
        message = _auth_bid_message("commit", task_id, agent_id, commitment,
                                    ts, nonce)
        wallet = _server_bound_bidder_wallet(
            _bid_body(_sign(message), ts, nonce, task_id, agent_id, commitment),
            purpose="commit", task_id=task_id, agent_id=agent_id,
            payload=commitment)
        assert wallet == WALLET_A
