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
  caller_id does NOT inherit the victim's score (on the integrated tree the
  unsigned send is rejected with 401 by w06's strict create-auth before any
  quota/reputation logic runs).
- The end-to-end sends also carry w06-style EIP-191 create-auth
  (authSignature/authTimestamp/authNonce/ownerWallet) so they pass the
  strict send auth on the integrated tree; on the w24-only tree the extra
  fields are ignored.
"""

import time
import uuid

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import a2a_integration as ai


def _acct():
    return Account.create()


def _sign(acct, message: str) -> str:
    return acct.sign_message(encode_defunct(text=message)).signature.hex()


def _sign0x(acct, message: str) -> str:
    return "0x" + _sign(acct, message)


def _create_auth_message(agent_label: str, skill_id: str, ts: int,
                         nonce: str) -> str:
    """w06 task-create auth message.

    Uses w06's ``_auth_create_message`` when present (integrated tree);
    otherwise replicates its exact format so this file also runs on the
    w24-only tree (where the auth fields are ignored). The format is the
    w06 protocol constant — pinned here deliberately so an accidental
    format change fails loudly.
    """
    real = getattr(ai, "_auth_create_message", None)
    if real is not None:
        return real(agent_label, skill_id, ts, nonce)
    return (
        "SINCOR-A2A task-create\n"
        f"agent_id:{agent_label}\n"
        f"skill_id:{skill_id}\n"
        f"timestamp:{int(ts)}\n"
        f"nonce:{nonce}"
    )


def _strict_create_auth_active() -> bool:
    """True when w06's create-auth verifier is present (integrated tree)."""
    return callable(getattr(ai, "_verify_create_auth", None))


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


def test_leaderboard_sum_overflow_safe(ledger):
    """SUM(axm_paid_wei) must not overflow sqlite INT64 (route 500).

    Regression: leaderboard() used a plain integer SUM, which raises
    sqlite3.OperationalError("integer overflow") once cumulative
    settlements exceed ~9.2 AXM in wei. Each value below is individually
    storable (< INT64_MAX); their sum is not — the REAL-typed sum must
    return it without raising.
    """
    INT64_MAX = 2**63 - 1
    k = ai._reputation_key("0x7777000000000000000000000000000000000007", "whale")
    ledger.record(k, "skill-x", "task-whale-1", axm_paid_wei=2**62)
    ledger.record(k, "skill-x", "task-whale-2", axm_paid_wei=2**62)
    board = ledger.leaderboard(limit=10)  # must not raise
    assert len(board) == 1
    total = board[0]["total_axm_wei"]
    assert total > INT64_MAX  # an integer SUM would have raised here
    assert total == pytest.approx(float(2**63), rel=1e-9)
    assert board[0]["total_settlements"] == 2
    assert board[0]["total_axm_display"].endswith("AXM")


def test_leaderboard_single_value_near_int64_max(ledger):
    """One settlement near INT64_MAX must be storable and summable."""
    INT64_MAX = 2**63 - 1
    k = ai._reputation_key("0x8888000000000000000000000000000000000008", "big")
    ledger.record(k, "skill-x", "task-big-1", axm_paid_wei=INT64_MAX - 1000)
    board = ledger.leaderboard(limit=10)  # must not raise
    assert len(board) == 1
    assert board[0]["total_axm_wei"] == pytest.approx(
        float(INT64_MAX - 1000), rel=1e-9
    )


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
    # w06 strict create-auth: EIP-191 signature over the task-create message.
    # Required on the integrated tree (401 otherwise); the extra fields are
    # ignored on the w24-only tree. Fresh nonce per call (replay guard).
    auth_ts = int(time.time())
    nonce = uuid.uuid4().hex
    auth_message = _create_auth_message(caller_id, skill_id, auth_ts, nonce)
    return {
        "method": "message/send",
        "id": 1,
        "params": {
            "skillId": skill_id,
            "callerId": caller_id,
            "ownerWallet": acct.address,
            "authSignature": _sign0x(acct, auth_message),
            "authTimestamp": auth_ts,
            "authNonce": nonce,
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
    if _strict_create_auth_active():
        # Integrated tree: w06's strict create-auth rejects the unsigned
        # send with 401 before any quota/reputation logic runs — no row is
        # created at all, not even in the untrusted namespace.
        assert resp.status_code == 401
        assert ai._reputation_ledger.score(
            ai._reputation_key(None, victim.address)
        ) == 0
    else:
        # w24-only tree: the unsigned send is accepted but lands in the
        # UNTRUSTED id: namespace under the exact declared string — it can
        # never reach the victim's wallet: rows.
        assert resp.status_code == 200
        assert ai._reputation_ledger.score(
            ai._reputation_key(None, victim.address)
        ) == 1  # attacker's own row, untrusted namespace
    # Invariant on both trees: the victim's wallet bucket is untouched and
    # unreachable via the spoof.
    assert ai._reputation_ledger.score(
        ai._reputation_key(victim.address, vcid)
    ) == victim_score
    assert ai._reputation_key(None, victim.address) != ai._reputation_key(
        victim.address, vcid
    )
