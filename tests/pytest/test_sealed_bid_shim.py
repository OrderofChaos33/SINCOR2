"""Sealed-bid commit/reveal shim — commit -> reveal -> close lifecycle."""
from __future__ import annotations

import secrets
import time

from conftest import hb_headers

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_identity import register_message
from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import sealed_commitment
from sincor2.a2a_integration import _auth_bid_message
from sincor2.contract_net import calculate_bid_score


@pytest.fixture
def client(tmp_path):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.a2a_rate_limits import reset_a2a_limits
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    # Registration is rate-limited (register policy, 3600s window); the
    # limiter is module-global, so reset it for per-test isolation.
    reset_a2a_limits()
    # Stake enforcement is active: each test gets an isolated ledger.
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id):
    """Register an agent WITH a wallet (W-77: bids bind to the registered
    wallet). Returns the agent's signing key for bid auth."""
    from sincor2.onchain.stake_ledger import stake_ledger

    key = Account.create()
    ts = _now_ms()
    message = register_message(agent_id, ts)
    sig = key.sign_message(encode_defunct(text=message)).signature
    r = client.post(
        "/v1/a2a/register",
        json={
            "agent_id": agent_id,
            "capability_tags": ["lead-enrichment"],
            "rpc_callback": "https://agent.example/rpc",
            # The claimed wallet (stored on the agent record) plus the
            # EIP-191 proof binding it (C3 fail-closed path).
            "wallet": key.address,
            "registration_wallet": key.address,
            "registration_ts": ts,
            "registration_signature": "0x" + bytes(sig).hex(),
        },
    )
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id}, headers=hb_headers())
    assert r.status_code == 200
    # Fund the agent so the commit-time stake lock (bounty * 50 %) succeeds.
    stake_ledger().deposit(agent_id, 10 * 10**18)
    return key


def _bid_auth(key, purpose, task_id, agent_id, payload):
    """EIP-191 bid auth fields for the commit/reveal routes (W-77)."""
    ts = int(time.time())
    nonce = secrets.token_hex(8)
    message = _auth_bid_message(purpose, task_id, agent_id, payload, ts, nonce)
    sig = key.sign_message(encode_defunct(text=message)).signature
    return {
        "authSignature": "0x" + bytes(sig).hex(),
        "authTimestamp": ts,
        "authNonce": nonce,
    }


def _sealed_task(client, skill="lead-enrichment"):
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": skill, "tags": ["lead-enrichment"], "bounty_axm": 1.5, "sealed": True},
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body["sealed"] is True
    assert body["commit_deadline"] and body["reveal_deadline"]
    assert body["reveal_deadline"] - body["commit_deadline"] == 5 * 60 * 1000
    return body["task_id"]


def _commitment(bid_axm, nonce_hex, agent_id):
    price_wei = int(round(float(bid_axm) * 1e18))
    salt = bytes.fromhex(nonce_hex)
    return "0x" + sealed_commitment(price_wei, salt, agent_id).hex()


def _commit(client, key, task_id, agent_id, bid_axm, nonce_hex):
    commitment = _commitment(bid_axm, nonce_hex, agent_id)
    body = {"task_id": task_id, "agent_id": agent_id, "commitment": commitment}
    body.update(_bid_auth(key, "commit", task_id, agent_id, commitment))
    return client.post("/v1/a2a/bids/commit", json=body)


def _reveal(client, key, task_id, agent_id, bid_axm, nonce_hex, est=300):
    payload = f"{float(bid_axm)}:{nonce_hex}"
    body = {"task_id": task_id, "agent_id": agent_id, "bid_axm": bid_axm,
            "nonce": nonce_hex, "estimated_seconds": est}
    body.update(_bid_auth(key, "reveal", task_id, agent_id, payload))
    return client.post("/v1/a2a/bids/reveal", json=body)


def _force_reveal_window(task_id):
    # Move into the reveal window: commit phase over, reveal phase open.
    fabric = get_fabric()
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000


def _force_past_reveal_deadline(task_id):
    fabric = get_fabric()
    fabric.tasks[task_id]["reveal_deadline"] = _now_ms() - 1


def test_commitment_scheme_matches_contract_encoding():
    # Pins the exact CommitRevealAuction preimage:
    # keccak256(abi.encodePacked(bytes32(price), salt, keccak256(agent_id))).
    from eth_hash.auto import keccak

    price_wei = int(1.5 * 1e18)
    salt = bytes.fromhex("ab" * 32)
    agent_id = "seal-agent-1"
    expected = keccak(price_wei.to_bytes(32, "big") + salt + keccak(agent_id.encode()))
    assert sealed_commitment(price_wei, salt, agent_id) == expected


def test_commit_reveal_close_assigns_winner(client):
    key_a = _register(client, "seal-a")
    key_b = _register(client, "seal-b")
    task_id = _sealed_task(client)
    nonce_a, nonce_b = "aa" * 32, "bb" * 32

    assert _commit(client, key_a, task_id, "seal-a", 1.0, nonce_a).status_code == 201
    assert _commit(client, key_b, task_id, "seal-b", 2.0, nonce_b).status_code == 201

    # Reveal is rejected during the commit window (mirrors RevealTooEarly).
    assert _reveal(client, key_a, task_id, "seal-a", 1.0, nonce_a).status_code == 403

    _force_reveal_window(task_id)
    assert _reveal(client, key_a, task_id, "seal-a", 1.0, nonce_a).status_code == 201
    assert _reveal(client, key_b, task_id, "seal-b", 2.0, nonce_b).status_code == 201

    _force_past_reveal_deadline(task_id)
    closed = client.post(f"/v1/a2a/tasks/{task_id}/close")
    assert closed.status_code == 200
    body = closed.get_json()
    assert body["state"] == "assigned"
    # Cheaper bid wins on composite score (both rep 0, same time est).
    assert body["assigned_to"] == "seal-a"
    assert body["winning_bid_axm"] == 1.0
    assert body["ghosted_commits"] == 0


def test_unrevealed_commit_ignored_at_close(client):
    key_a = _register(client, "seal-a")
    key_g = _register(client, "ghost-b")
    task_id = _sealed_task(client)

    assert _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    assert _commit(client, key_g, task_id, "ghost-b", 0.5, "bb" * 32).status_code == 201

    _force_reveal_window(task_id)
    # Only seal-a reveals; ghost-b's cheaper commitment is dropped.
    assert _reveal(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201

    _force_past_reveal_deadline(task_id)
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "assigned"
    assert body["assigned_to"] == "seal-a"
    assert body["ghosted_commits"] == 1


def test_wrong_nonce_rejected(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    _force_reveal_window(task_id)

    r = _reveal(client, key_a, task_id, "seal-a", 1.0, "ff" * 32)
    assert r.status_code == 400
    assert "mismatch" in r.get_json()["error"]

    # Wrong bid amount also fails the commitment check.
    r = _reveal(client, key_a, task_id, "seal-a", 2.0, "aa" * 32)
    assert r.status_code == 400


def test_late_commit_rejected(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1

    r = _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 403
    assert "commit window closed" in r.get_json()["error"]


def test_late_reveal_rejected(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 60_000
    _force_past_reveal_deadline(task_id)

    r = _reveal(client, key_a, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 403
    assert "reveal window closed" in r.get_json()["error"]


def test_double_commit_and_double_reveal_rejected(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    # VERIFIED-2: a retry of the same commit is idempotent — returns the
    # existing record (201) WITHOUT re-locking stake (a double-lock would
    # let a later ghost-slash take 100% of the doubled amount).
    r = _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 201
    assert r.get_json()["commitment"] == _commitment(1.0, "aa" * 32, "seal-a")

    _force_reveal_window(task_id)
    assert _reveal(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    r = _reveal(client, key_a, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 409
    assert "already revealed" in r.get_json()["error"]


def test_reveal_without_commit_rejected(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    _force_reveal_window(task_id)
    r = _reveal(client, key_a, task_id, "seal-a", 1.0, "aa" * 32)
    assert r.status_code == 404


def test_plaintext_bid_rejected_on_sealed_task(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    r = client.post(
        "/v1/a2a/bids",
        json={"task_id": task_id, "agent_id": "seal-a", "bid_axm": 1.0,
              "estimated_seconds": 300},
    )
    assert r.status_code == 409


def test_legacy_plaintext_bid_still_works(client):
    # Backward compat: non-sealed tasks keep the old plaintext flow untouched.
    key_l = _register(client, "legacy-a")
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "tags": ["lead-enrichment"], "bounty_axm": 1.0},
    )
    assert r.status_code == 201
    task_id = r.get_json()["task_id"]
    assert r.get_json()["sealed"] is False

    bid = client.post(
        "/v1/a2a/bids",
        json={"task_id": task_id, "agent_id": "legacy-a", "bid_axm": 1.0,
              "estimated_seconds": 300},
    )
    assert bid.status_code == 201
    assert bid.get_json()["revealed"] is True

    get_fabric().tasks[task_id]["auction_closes_at"] = _now_ms() - 1
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "assigned"
    assert body["assigned_to"] == "legacy-a"


def test_close_unknown_task_404(client):
    assert client.post("/v1/a2a/tasks/nope/close").status_code == 404


def test_sealed_close_before_reveal_deadline_is_noop(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    assert _commit(client, key_a, task_id, "seal-a", 1.0, "aa" * 32).status_code == 201
    # Permissionless close attempted too early: task stays in auction.
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "auction"
    assert body["assigned_to"] is None


# ---------------------------------------------------------------------------
# W-77 — spoofed sealed-bid commit
# ---------------------------------------------------------------------------


def _stake_of(agent_id):
    from sincor2.onchain.stake_ledger import stake_ledger

    return stake_ledger().balance_of(agent_id)


def test_unsigned_commit_rejected(client):
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    # No auth fields at all — the pre-fix code accepted this and locked
    # the named agent's stake on anyone's word.
    r = client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": task_id, "agent_id": "seal-a",
              "commitment": _commitment(1.0, "aa" * 32, "seal-a")},
    )
    assert r.status_code == 403
    assert "registered wallet" in r.get_json()["error"]


def test_spoofed_commit_as_victim_rejected(client):
    """W-77 PoC: attacker commits as the victim's agent_id.  The victim's
    stake must be untouched and no commit record created."""
    key_victim = _register(client, "victim")
    key_attacker = _register(client, "attacker")
    task_id = _sealed_task(client)

    before = _stake_of("victim")
    # Attacker signs a commit naming the VICTIM's agent_id with the
    # ATTACKER's key — recovered wallet != victim's registered wallet.
    r = _commit(client, key_attacker, task_id, "victim", 1.0, "aa" * 32)
    assert r.status_code == 403
    assert "registered wallet" in r.get_json()["error"]
    # Victim's stake untouched, no commit attributed to the victim.
    assert _stake_of("victim") == before
    fabric = get_fabric()
    assert not any(
        c.get("agent_id") == "victim" for c in fabric.commits.values()
    )
    # And the victim can still commit honestly afterwards.
    assert _commit(client, key_victim, task_id, "victim", 1.0, "aa" * 32).status_code == 201


def test_reveal_from_wrong_wallet_rejected(client):
    key_victim = _register(client, "victim")
    key_attacker = _register(client, "attacker")
    task_id = _sealed_task(client)
    assert _commit(client, key_victim, task_id, "victim", 1.0, "aa" * 32).status_code == 201
    _force_reveal_window(task_id)
    r = _reveal(client, key_attacker, task_id, "victim", 1.0, "aa" * 32)
    assert r.status_code == 403


def test_commit_byte_identical_retry_is_idempotent(client):
    # VERIFIED-2: a byte-identical retry of an already-recorded commit
    # returns the existing record (201) WITHOUT re-authenticating and
    # WITHOUT re-locking stake.  No new authorization is created, so the
    # single-use signature guarantee for fresh authorizations stands.
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    commitment = _commitment(1.0, "aa" * 32, "seal-a")
    body = {"task_id": task_id, "agent_id": "seal-a", "commitment": commitment}
    body.update(_bid_auth(key_a, "commit", task_id, "seal-a", commitment))
    r1 = client.post("/v1/a2a/bids/commit", json=body)
    assert r1.status_code == 201
    r2 = client.post("/v1/a2a/bids/commit", json=body)
    assert r2.status_code == 201
    assert r2.get_json() == r1.get_json()


def test_commit_signature_cannot_authorize_different_commitment(client):
    # The same captured signature bytes cannot authorize a DIFFERENT
    # commitment: the signed payload won't match, so auth fails.
    key_a = _register(client, "seal-a")
    task_id = _sealed_task(client)
    commitment = _commitment(1.0, "aa" * 32, "seal-a")
    body = {"task_id": task_id, "agent_id": "seal-a", "commitment": commitment}
    auth = _bid_auth(key_a, "commit", task_id, "seal-a", commitment)
    assert client.post("/v1/a2a/bids/commit", json=body | auth).status_code == 201
    other = _commitment(2.0, "bb" * 32, "seal-a")
    r = client.post("/v1/a2a/bids/commit",
                    json={"task_id": task_id, "agent_id": "seal-a",
                          "commitment": other} | auth)
    assert r.status_code == 403


def test_commit_signature_bound_to_task(client):
    """A commit signature for task A cannot be replayed against task B."""
    key_a = _register(client, "seal-a")
    task_a = _sealed_task(client)
    task_b = _sealed_task(client)
    commitment = _commitment(1.0, "aa" * 32, "seal-a")
    body_a = {"task_id": task_a, "agent_id": "seal-a", "commitment": commitment}
    body_a.update(_bid_auth(key_a, "commit", task_a, "seal-a", commitment))
    assert client.post("/v1/a2a/bids/commit", json=body_a).status_code == 201
    # Same signature bytes, retargeted body: the signed task_id no longer
    # matches, so recovery yields the wrong message -> rejected.
    body_b = dict(body_a)
    body_b["task_id"] = task_b
    r = client.post("/v1/a2a/bids/commit", json=body_b)
    assert r.status_code == 403
