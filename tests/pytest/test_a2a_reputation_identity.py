"""Wave 24: reputation keyed on verified EIP-191 wallet identity.

Covers:
- _resolve_verified_wallet: happy path, quote shape, missing/invalid wallet
  claim, wallet mismatch, stale timestamp, tampered input, bad signature.
- _reputation_key: verified and unverified rows live in separate trust
  domains (unsigned callers cannot reach a wallet's rows by declaring its
  address as caller_id).
- ReputationLedger: rotation under one wallet accumulates in one bucket;
  distinct wallets are independent; reset() zeroes a wallet (ghost penalty).
- End-to-end via the Flask client: a signed send accrues reputation under
  wallet:<addr>; an unsigned caller declaring the victim's wallet address as
  caller_id does NOT inherit the victim's score.
"""

import time

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import a2a_integration as ai


def _acct():
    return Account.create()


def _sign(acct, message: str) -> str:
    return acct.sign_message(encode_defunct(text=message)).signature.hex()


def _send_identity_params(acct, skill_id, input_text, skew_ms=0):
    ts = int(time.time() * 1000) + skew_ms
    message = ai.identity_message_for_send(skill_id, input_text, ts)
    return {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": acct.address,
        "message": {"metadata": {}},
    }, message


# ── _resolve_verified_wallet unit tests ─────────────────────────────────────

def test_resolve_verified_wallet_happy_path():
    acct = _acct()
    skill_id, input_text = "lead-enrichment", "Enrich Acme Corp"
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_send(skill_id, input_text, ts)
    params = {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": acct.address,
    }
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params=params, msg_obj={}, input_text=input_text
    ) == acct.address.lower()


def test_resolve_verified_wallet_quote_shape():
    acct = _acct()
    skill_id = "lead-enrichment"
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_quote(skill_id, ts)
    params = {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": acct.address,
    }
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params=params, msg_obj={}, input_text=None
    ) == acct.address.lower()


def test_resolve_verified_wallet_metadata_shape():
    """Signature fields inside message.metadata are accepted."""
    acct = _acct()
    skill_id, input_text = "lead-enrichment", "hello"
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_send(skill_id, input_text, ts)
    msg_obj = {"metadata": {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": acct.address,
    }}
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params={}, msg_obj=msg_obj, input_text=input_text
    ) == acct.address.lower()


def test_resolve_verified_wallet_missing_wallet_claim():
    acct = _acct()
    skill_id, input_text = "lead-enrichment", "hello"
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_send(skill_id, input_text, ts)
    params = {"signature": _sign(acct, message), "quota_ts": str(ts)}
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params=params, msg_obj={}, input_text=input_text
    ) is None


def test_resolve_verified_wallet_claim_mismatch():
    """Claimed wallet != recovered signer → rejected (the ECDSA footgun)."""
    acct, other = _acct(), _acct()
    skill_id, input_text = "lead-enrichment", "hello"
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_send(skill_id, input_text, ts)
    params = {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": other.address,  # attacker claims someone else's wallet
    }
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params=params, msg_obj={}, input_text=input_text
    ) is None


def test_resolve_verified_wallet_stale_timestamp():
    acct = _acct()
    skill_id, input_text = "lead-enrichment", "hello"
    ts = int(time.time() * 1000) - 10 * 60 * 1000  # 10 min old
    message = ai.identity_message_for_send(skill_id, input_text, ts)
    params = {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": acct.address,
    }
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params=params, msg_obj={}, input_text=input_text
    ) is None


def test_resolve_verified_wallet_tampered_input():
    """Signature over input A does not validate for input B."""
    acct = _acct()
    skill_id = "lead-enrichment"
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_send(skill_id, "input A", ts)
    params = {
        "signature": _sign(acct, message),
        "quota_ts": str(ts),
        "wallet": acct.address,
    }
    assert ai._resolve_verified_wallet(
        skill_id=skill_id, params=params, msg_obj={}, input_text="input B"
    ) is None


def test_resolve_verified_wallet_no_signature():
    assert ai._resolve_verified_wallet(
        skill_id="lead-enrichment", params={}, msg_obj={}, input_text="x"
    ) is None


def test_resolve_verified_wallet_bad_signature():
    params = {
        "signature": "0xdeadbeef",
        "quota_ts": str(int(time.time() * 1000)),
        "wallet": "0x0000000000000000000000000000000000000001",
    }
    assert ai._resolve_verified_wallet(
        skill_id="lead-enrichment", params=params, msg_obj={}, input_text="x"
    ) is None


# ── _reputation_key trust domains ───────────────────────────────────────────

def test_reputation_key_separates_trust_domains():
    wallet = "0xAbC0000000000000000000000000000000000001"
    verified = ai._reputation_key(wallet, "some-id")
    unverified = ai._reputation_key(None, wallet)  # attacker declares the address
    assert verified == "wallet:0xabc0000000000000000000000000000000000001"
    assert unverified == "id:" + wallet
    assert verified != unverified


# ── ReputationLedger behavior (isolated temp db) ─────────────────────────────

@pytest.fixture()
def ledger(tmp_path):
    return ai.ReputationLedger(db_path=str(tmp_path / "rep.db"))


def test_rotation_same_wallet_accumulates_one_bucket(ledger):
    """Rotating caller_id does not evade reputation: one wallet, one bucket."""
    wallet_key = ai._reputation_key("0xabc0000000000000000000000000000000000001", "id-a")
    ledger.record(wallet_key, "skill-x", "task-1")
    ledger.record(ai._reputation_key("0xabc0000000000000000000000000000000000001", "id-b"),
                  "skill-x", "task-2")
    assert ledger.score(wallet_key) == 2
    # The bare caller_ids accrue nothing in the verified namespace.
    assert ledger.score(ai._reputation_key(None, "id-a")) == 0


def test_distinct_wallets_independent(ledger):
    k1 = ai._reputation_key("0x1111000000000000000000000000000000000001", "x")
    k2 = ai._reputation_key("0x2222000000000000000000000000000000000002", "y")
    ledger.record(k1, "skill-x", "task-1")
    ledger.record(k1, "skill-x", "task-2")
    ledger.record(k2, "skill-x", "task-3")
    assert ledger.score(k1) == 2
    assert ledger.score(k2) == 1


def test_reset_zeroes_wallet(ledger):
    k = ai._reputation_key("0x9999000000000000000000000000000000000009", "ghost")
    for i in range(3):
        ledger.record(k, "skill-x", f"task-{i}")
    assert ledger.score(k) == 3
    assert ledger.reset(k) == 3
    assert ledger.score(k) == 0
    assert ledger.reset(k) == 0  # idempotent


def test_reset_only_touches_target_wallet(ledger):
    k1 = ai._reputation_key("0x1111000000000000000000000000000000000001", "a")
    k2 = ai._reputation_key("0x2222000000000000000000000000000000000002", "b")
    ledger.record(k1, "skill-x", "task-1")
    ledger.record(k2, "skill-x", "task-2")
    ledger.reset(k1)
    assert ledger.score(k1) == 0
    assert ledger.score(k2) == 1


# ── Ghost penalty: settlement ledger zeroed for the ghost's wallet ──────────

class _FakeFabric:
    def __init__(self, agents):
        self.agents = agents
        import threading
        self.lock = threading.Lock()


def test_ghost_reset_zeroes_ghost_wallet_only():
    """Ghosting zeroes the right wallet's reputation, not anyone else's."""
    from sincor2 import a2a_inbound_market as mkt

    import uuid
    run = uuid.uuid4().hex[:8]
    ghost_acct, innocent_acct = _acct(), _acct()
    ghost_key = ai._reputation_key(ghost_acct.address, "ghost-agent")
    innocent_key = ai._reputation_key(innocent_acct.address, "innocent-agent")
    ai._reputation_ledger.record(ghost_key, "skill-x", f"ghost-task-1-{run}")
    ai._reputation_ledger.record(ghost_key, "skill-x", f"ghost-task-2-{run}")
    ai._reputation_ledger.record(innocent_key, "skill-x", f"innocent-task-1-{run}")
    fabric = _FakeFabric({
        "ghost-agent": {"wallet": ghost_acct.address},  # mixed case on card
        "innocent-agent": {"wallet": innocent_acct.address},
    })
    removed = mkt._reset_ghost_settlement_reputation(fabric, ["ghost-agent"])
    assert removed == 2
    assert ai._reputation_ledger.score(ghost_key) == 0
    assert ai._reputation_ledger.score(innocent_key) == 1


def test_ghost_reset_never_raises():
    """Accounting failures never propagate (must not brick auction close)."""
    from sincor2 import a2a_inbound_market as mkt

    class _BrokenFabric:
        @property
        def lock(self):
            raise RuntimeError("boom")

        @property
        def agents(self):
            raise RuntimeError("boom")

    assert mkt._reset_ghost_settlement_reputation(_BrokenFabric(), ["x"]) == 0


# ── End-to-end via the Flask client ──────────────────────────────────────────

def _signed_send_body(acct, caller_id, skill_id="lead-enrichment",
                      text="Enrich Acme Corp", ctx="ctx-rep-01"):
    ts = int(time.time() * 1000)
    message = ai.identity_message_for_send(skill_id, text, ts)
    return {
        "method": "message/send",
        "id": 1,
        "params": {
            "skillId": skill_id,
            "callerId": caller_id,
            "message": {
                "role": "user",
                "parts": [{"text": text}],
                "contextId": ctx,
                "metadata": {
                    "signature": _sign(acct, message),
                    "quota_ts": str(ts),
                    "wallet": acct.address,
                },
            },
        },
    }


def test_end_to_end_signed_send_accrues_wallet_reputation(client):
    """A signed send accrues reputation under wallet:<addr>, not caller_id."""
    acct = _acct()
    caller_id = "rep-e2e-caller-%s" % acct.address[-6:]
    resp = client.post("/api/a2a", json=_signed_send_body(acct, caller_id))
    assert resp.status_code == 200
    result = resp.get_json().get("result", {})
    assert result.get("id")  # task created
    wallet_key = ai._reputation_key(acct.address, caller_id)
    assert ai._reputation_ledger.score(wallet_key) >= 1
    # The self-declared caller_id bucket stays empty in its own namespace.
    assert ai._reputation_ledger.score(ai._reputation_key(None, caller_id)) == 0


def test_end_to_end_rotation_same_wallet_one_bucket(client):
    """Two sends, different callerIds, same wallet → one reputation bucket."""
    acct = _acct()
    base = "rep-rot-%s" % acct.address[-6:]
    before = ai._reputation_ledger.score(ai._reputation_key(acct.address, base))
    for i, cid in enumerate([base + "-a", base + "-b"]):
        resp = client.post(
            "/api/a2a",
            json=_signed_send_body(acct, cid, ctx="ctx-rep-rot-%d" % i),
        )
        assert resp.status_code == 200
    after = ai._reputation_ledger.score(ai._reputation_key(acct.address, base))
    assert after - before == 2


def test_end_to_end_unsigned_cannot_steal_wallet_reputation(client):
    """Declaring a victim's wallet address as caller_id grants nothing."""
    victim = _acct()
    # Victim builds real reputation with a signed send.
    vcid = "rep-victim-%s" % victim.address[-6:]
    resp = client.post("/api/a2a", json=_signed_send_body(victim, vcid))
    assert resp.status_code == 200
    victim_score = ai._reputation_ledger.score(ai._reputation_key(victim.address, vcid))
    assert victim_score >= 1
    # Attacker declares the victim's exact wallet address, unsigned.
    attacker_body = {
        "method": "message/send",
        "id": 1,
        "params": {
            "skillId": "lead-enrichment",
            "callerId": victim.address,  # spoof attempt, no signature
            "message": {
                "role": "user",
                "parts": [{"text": "Enrich Acme Corp"}],
                "contextId": "ctx-rep-spoof",
            },
        },
    }
    resp = client.post("/api/a2a", json=attacker_body)
    assert resp.status_code == 200
    # The attacker's own send lands in the UNTRUSTED id: namespace under the
    # exact declared string — it can never reach the victim's wallet: rows.
    assert ai._reputation_ledger.score(
        ai._reputation_key(None, victim.address)
    ) == 1  # attacker's own row, untrusted namespace
    # Victim's wallet bucket is untouched and unreachable via the spoof.
    assert ai._reputation_ledger.score(
        ai._reputation_key(victim.address, vcid)
    ) == victim_score
    assert ai._reputation_key(None, victim.address) != ai._reputation_key(
        victim.address, vcid
    )
