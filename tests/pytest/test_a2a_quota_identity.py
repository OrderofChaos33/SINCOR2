"""Phase 3 wave 18 (G2.6): free quota keyed on verified identity.

The free quota on the top-5 subsidised skills used to be keyed on the
self-declared ``caller_id`` request field, so rotating caller IDs granted
unlimited free calls. It is now keyed on the wallet address recovered from a
fresh EIP-191 quota signature, with a mandatory ``wallet`` claim checked
against the recovered signer (ECDSA recovery returns *some* address for any
message/signature pair, so the claim check is what binds signature→message —
without it, one signature over anything would mint a fresh quota bucket per
task input).
"""

import time
import uuid

import pytest

from sincor2 import a2a_integration as ai

SKILL_ID = "lead-enrichment"

# The merged tree (w06, caller ownership) enforces EIP-191 create-auth on
# every message/send — unsigned sends are rejected 401 before quota logic
# runs. The w18-only tree has no auth gate, so the create-auth fields added
# below are simply ignored there. Tests that send *without* create-auth
# therefore see a different (but equally "no free work") rejection per tree;
# those assertions branch on this flag with both behaviors pinned.
_STRICT_CREATE_AUTH = hasattr(ai, "_verify_create_auth")

# w06's exact task-create message domain. Kept as a literal (not imported)
# because the w18-only tree does not define _auth_create_message; the merged
# tree verifies against this identical string.
_CREATE_AUTH_DOMAIN = "SINCOR-A2A task-create"


def _create_auth_fields(acct, caller_id, skill_id=SKILL_ID):
    """w06-style EIP-191 create-auth fields for a message/send.

    Signed by the same wallet as the quota signature so the quota identity
    is unchanged. Each call mints a fresh nonce: the merged tree's replay
    guard is single-use per (message, signature) digest.
    """
    from eth_account.messages import encode_defunct

    ts = int(time.time())
    nonce = uuid.uuid4().hex
    message = (
        f"{_CREATE_AUTH_DOMAIN}\n"
        f"agent_id:{caller_id}\n"
        f"skill_id:{skill_id}\n"
        f"timestamp:{ts}\n"
        f"nonce:{nonce}"
    )
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    return {
        "authSignature": "0x" + sig,
        "authTimestamp": ts,
        "authNonce": nonce,
        "ownerWallet": acct.address,
    }


def _skill():
    return next(s for s in ai.SINCOR_SKILLS if s.id == SKILL_ID)


def _wallet():
    from eth_account import Account

    return Account.create()


def _sign_send(acct, input_text, ts=None, skill_id=SKILL_ID):
    """Signature fields for a tasks/send free-quota claim."""
    from eth_account.messages import encode_defunct

    ts = ts if ts is not None else int(time.time() * 1000)
    message = ai.quota_message_for_send(skill_id, input_text, ts)
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    return {"signature": "0x" + sig, "quota_ts": str(ts), "wallet": acct.address}


def _sign_quote(acct, ts=None, skill_id=SKILL_ID):
    """Signature fields for a quote free-quota query."""
    from eth_account.messages import encode_defunct

    ts = ts if ts is not None else int(time.time() * 1000)
    message = ai.quota_message_for_quote(skill_id, ts)
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    return {"signature": "0x" + sig, "quota_ts": str(ts), "wallet": acct.address}


def _send_body(input_text, caller_id, sig_fields=None, skill_id=SKILL_ID,
               auth_acct=None):
    """Build a message/send body.

    ``auth_acct``: when given, w06-style create-auth fields signed by that
    account are added to params (required by the merged tree's strict
    send-auth; ignored on the w18-only tree). Pass the same account used for
    the quota signature so quota identity is unchanged.
    """
    metadata = dict(sig_fields or {})
    params = {
        "skillId": skill_id,
        "callerId": caller_id,
        "message": {
            "role": "user",
            "parts": [{"text": input_text}],
            "contextId": f"ctx-{caller_id}",
            "metadata": metadata,
        },
    }
    if auth_acct is not None:
        params.update(_create_auth_fields(auth_acct, caller_id, skill_id))
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": params,
    }


def _quote_body(sig_fields, skill_id=SKILL_ID):
    return {"skill_id": skill_id, **sig_fields}


# ── identity resolution ────────────────────────────────────────────────────


def test_identity_recovered_from_valid_signature():
    acct = _wallet()
    fields = _sign_send(acct, "hello world")
    identity = ai._resolve_quota_identity(
        skill_id=SKILL_ID,
        params={"signature": fields["signature"], "quota_ts": fields["quota_ts"],
                "wallet": fields["wallet"]},
        msg_obj={},
        input_text="hello world",
    )
    assert identity == acct.address.lower()


def test_missing_signature_gives_no_identity():
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params={"caller_id": "anyone"}, msg_obj={},
        input_text="hello") is None


def test_missing_wallet_claim_gives_no_identity():
    """The wallet claim is mandatory — recovery alone proves nothing."""
    acct = _wallet()
    fields = _sign_send(acct, "hello world")
    params = {"signature": fields["signature"], "quota_ts": fields["quota_ts"]}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={}, input_text="hello world") is None


def test_tampered_signature_rejected():
    acct = _wallet()
    fields = _sign_send(acct, "hello world")
    bad_sig = fields["signature"][:-1] + ("0" if fields["signature"][-1] != "0" else "1")
    params = {"signature": bad_sig, "quota_ts": fields["quota_ts"], "wallet": acct.address}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={}, input_text="hello world") is None


def test_stale_timestamp_rejected():
    acct = _wallet()
    old_ts = int(time.time() * 1000) - 10 * 60 * 1000  # 10 min ago
    fields = _sign_send(acct, "hello world", ts=old_ts)
    params = {"signature": fields["signature"], "quota_ts": fields["quota_ts"],
              "wallet": acct.address}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={}, input_text="hello world") is None


def test_future_timestamp_beyond_window_rejected():
    acct = _wallet()
    future_ts = int(time.time() * 1000) + 10 * 60 * 1000
    fields = _sign_send(acct, "hello world", ts=future_ts)
    params = {"signature": fields["signature"], "quota_ts": fields["quota_ts"],
              "wallet": acct.address}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={}, input_text="hello world") is None


def test_signature_bound_to_exact_input():
    """Replaying a signature with a different task input must not validate.

    Without the mandatory wallet-claim check, ECDSA recovery would return a
    garbage address here and mint a fresh quota bucket per input.
    """
    victim = _wallet()
    fields = _sign_send(victim, "victim input")
    # Attacker replays the victim's signature but with their own input,
    # claiming the victim's wallet.
    params = {"signature": fields["signature"], "quota_ts": fields["quota_ts"],
              "wallet": victim.address}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={},
        input_text="attacker input") is None


def test_signature_bound_to_skill():
    acct = _wallet()
    fields = _sign_send(acct, "hello", skill_id="market-forecast")
    params = {"signature": fields["signature"], "quota_ts": fields["quota_ts"],
              "wallet": acct.address}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={}, input_text="hello") is None


def test_claimed_wallet_mismatch_rejected():
    acct = _wallet()
    other = _wallet()
    fields = _sign_send(acct, "hello world")
    params = {"signature": fields["signature"], "quota_ts": fields["quota_ts"],
              "wallet": other.address}
    assert ai._resolve_quota_identity(
        skill_id=SKILL_ID, params=params, msg_obj={}, input_text="hello world") is None


def test_canonical_metadata_shape_accepted():
    """params.message.metadata.signature is the canonical A2A shape."""
    acct = _wallet()
    fields = _sign_send(acct, "canonical shape")
    body = _send_body("canonical shape", "some-caller", fields)
    identity = ai._resolve_quota_identity(
        skill_id=SKILL_ID,
        params=body["params"],
        msg_obj=body["params"]["message"],
        input_text="canonical shape",
    )
    assert identity == acct.address.lower()


# ── quota behaviour over HTTP ──────────────────────────────────────────────


def test_rotating_caller_ids_share_one_quota(client):
    """Same wallet, rotating caller_id: quota is enforced, not reset."""
    acct = _wallet()
    skill = _skill()
    for i in range(skill.free_quota):
        text = f"enrich company number {i}"
        resp = client.post("/api/a2a", json=_send_body(
            text, f"rotating-{i}", _sign_send(acct, text), auth_acct=acct))
        assert resp.status_code == 200
        result = resp.get_json()["result"]
        assert result["metadata"]["free_call"] is True
        assert result["metadata"]["quota_identity"] == acct.address.lower()

    # Quota exhausted: in the test env the task is still created (dev payment
    # bypass) but it is NOT a free call.
    text = "one call too many"
    resp = client.post("/api/a2a", json=_send_body(
        text, "rotating-final", _sign_send(acct, text), auth_acct=acct))
    assert resp.status_code == 200
    assert resp.get_json()["result"]["metadata"]["free_call"] is False

    # Quote agrees: zero remaining for this wallet.
    q = client.post("/api/a2a/quote", json=_quote_body(_sign_quote(acct)))
    data = q.get_json()
    assert data["free_quota_remaining"] == 0
    assert data["is_free"] is False


def test_distinct_wallets_have_independent_quotas(client):
    acct_a = _wallet()
    acct_b = _wallet()
    skill = _skill()
    for i in range(skill.free_quota):
        text = f"drain A {i}"
        resp = client.post("/api/a2a", json=_send_body(
            text, f"drain-a-{i}", _sign_send(acct_a, text), auth_acct=acct_a))
        assert resp.get_json()["result"]["metadata"]["free_call"] is True

    # B is untouched.
    text = "fresh wallet call"
    resp = client.post("/api/a2a", json=_send_body(
        text, "wallet-b-caller", _sign_send(acct_b, text), auth_acct=acct_b))
    assert resp.get_json()["result"]["metadata"]["free_call"] is True
    assert resp.get_json()["result"]["metadata"]["quota_identity"] == acct_b.address.lower()

    q = client.post("/api/a2a/quote", json=_quote_body(_sign_quote(acct_b)))
    assert q.get_json()["free_quota_remaining"] == skill.free_quota - 1


def test_unsigned_caller_gets_no_free_quota(client):
    """A caller with no signature gets no free work — the G2.6 bypass is closed.

    w18-only tree: the unsigned send reaches quota logic and is denied a free
    call (200, free_call=False). Merged tree (w06): the send never reaches
    quota logic — strict create-auth rejects it first (401, -32010). Either
    way, no signature means no free work.
    """
    text = "unsigned attempt"
    resp = client.post("/api/a2a", json=_send_body(text, "brand-new-caller-xyz"))
    if _STRICT_CREATE_AUTH:
        assert resp.status_code == 401
        assert resp.get_json()["error"]["code"] == -32010
    else:
        assert resp.status_code == 200
        meta = resp.get_json()["result"]["metadata"]
        assert meta["free_call"] is False
        assert "quota_identity" not in meta

    q = client.post("/api/a2a/quote", json={"skill_id": SKILL_ID, "caller_id": "brand-new-caller-xyz"})
    data = q.get_json()
    assert data["is_free"] is False
    assert data["free_quota_remaining"] == 0
    assert "signature" in data["note"]


def test_signed_quote_reports_remaining(client):
    acct = _wallet()
    q = client.post("/api/a2a/quote", json=_quote_body(_sign_quote(acct)))
    data = q.get_json()
    assert data["is_free"] is True
    assert data["free_quota_remaining"] == _skill().free_quota
    assert data["axm_price_wei"] == "0"

    text = "use one"
    client.post("/api/a2a", json=_send_body(text, "qc-1", _sign_send(acct, text),
                                           auth_acct=acct))
    q = client.post("/api/a2a/quote", json=_quote_body(_sign_quote(acct)))
    assert q.get_json()["free_quota_remaining"] == _skill().free_quota - 1


def test_quota_exhaustion_returns_payment_required(client, monkeypatch):
    """Exhaustion follows the existing payment-required convention (-32000)."""
    monkeypatch.setenv("FLASK_ENV", "production")
    acct = _wallet()
    skill = _skill()
    for i in range(skill.free_quota):
        text = f"prod probe {i}"
        resp = client.post("/api/a2a", json=_send_body(
            text, f"prod-{i}", _sign_send(acct, text), auth_acct=acct))
        body = resp.get_json()
        assert resp.status_code == 200, body
        assert "error" not in body, body

    text = "over the limit"
    resp = client.post("/api/a2a", json=_send_body(
        text, "prod-final", _sign_send(acct, text), auth_acct=acct))
    body = resp.get_json()
    assert resp.status_code == 200  # JSON-RPC envelope convention on this route
    assert body["error"]["code"] == -32000
    assert "quota exhausted" in body["error"]["message"]


def test_unsigned_send_in_production_requires_payment(client, monkeypatch):
    """An unsigned send in production gets no free work.

    w18-only tree: rejected at the payment gate (-32000). Merged tree (w06):
    rejected earlier at the create-auth gate (401, -32010). Either way the
    unsigned caller cannot reach task creation.
    """
    monkeypatch.setenv("FLASK_ENV", "production")
    resp = client.post("/api/a2a", json=_send_body("no sig", "anon-prod"))
    body = resp.get_json()
    if _STRICT_CREATE_AUTH:
        assert resp.status_code == 401
        assert body["error"]["code"] == -32010
    else:
        assert body["error"]["code"] == -32000


def test_legacy_tasks_send_enforces_quota(client, monkeypatch):
    """The legacy REST endpoint shares _handle_send, so it enforces too."""
    monkeypatch.setenv("FLASK_ENV", "production")
    acct = _wallet()
    text = "legacy signed"
    ok = client.post("/api/a2a/tasks/send", json=_send_body(
        text, "legacy-1", _sign_send(acct, text), auth_acct=acct))
    assert ok.status_code == 202
    assert ok.get_json()["result"]["metadata"]["free_call"] is True

    denied = client.post("/api/a2a/tasks/send", json=_send_body("legacy unsigned", "legacy-2"))
    if _STRICT_CREATE_AUTH:
        # Merged tree (w06): the unsigned send is rejected at the create-auth
        # gate before quota/payment logic runs.
        assert denied.status_code == 401
        assert denied.get_json()["error"]["code"] == -32010
    else:
        assert denied.status_code == 400
        assert denied.get_json()["error"]["code"] == -32000


def test_non_free_skill_never_free_even_signed(client):
    acct = _wallet()
    text = "compliance check"
    body = _send_body(text, "sbom-caller", _sign_send(acct, text, skill_id="compliance-sbom"),
                      skill_id="compliance-sbom", auth_acct=acct)
    resp = client.post("/api/a2a", json=body)
    assert resp.get_json()["result"]["metadata"]["free_call"] is False


# ── store interface (durable-state wave seam) ────────────────────────────────


class _RecordingStore:
    """Minimal QuotaStore double proving the tracker only depends on the protocol."""

    def __init__(self):
        self.calls = []
        self._usage = {}

    def get(self, identity_key, skill_id):
        self.calls.append(("get", identity_key, skill_id))
        return self._usage.get((identity_key, skill_id), 0)

    def try_consume(self, identity_key, skill_id, limit):
        self.calls.append(("try_consume", identity_key, skill_id, limit))
        used = self._usage.get((identity_key, skill_id), 0)
        if used >= limit:
            return False
        self._usage[(identity_key, skill_id)] = used + 1
        return True


def test_tracker_depends_only_on_store_protocol():
    store = _RecordingStore()
    tracker = ai._FreeQuotaTracker(store=store)
    skill = _skill()
    identity = "0xAbC1230000000000000000000000000000000001".lower()
    assert tracker.consume_if_available(identity, skill) is True
    assert tracker.remaining(identity, skill) == skill.free_quota - 1
    # Every store call was keyed on the verified identity, never caller_id.
    for call in store.calls:
        assert call[1] == identity
        assert call[2] == SKILL_ID


def test_memory_store_consume_is_atomic():
    store = ai._MemoryQuotaStore()
    results = [store.try_consume("0xid", SKILL_ID, 5) for _ in range(7)]
    assert results == [True] * 5 + [False] * 2
    assert store.get("0xid", SKILL_ID) == 5


def test_tracker_rejects_empty_identity():
    tracker = ai._FreeQuotaTracker()
    skill = _skill()
    assert tracker.consume_if_available("", skill) is False
    assert tracker.is_free("", skill) is False
    assert tracker.remaining("", skill) == 0
