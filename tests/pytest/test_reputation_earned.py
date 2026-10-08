"""Reputation is earned-only, never declared.

Covers the self-reported reputation exploit chain:
1. ``register_agent_record()`` took ``body["reputation"]`` verbatim, so a
   self-declared 1.0 skipped probation entirely and bought +0.3 bid score
   (roughly equivalent to bidding ~25% cheaper) in ``calculate_bid_score``.
2. Ghosting and upheld quality disputes slashed stake in the ledger but
   never touched the fabric agent's reputation.

Now: registration silently ignores the body field; re-registration
preserves earned value; ghosting zeroes reputation and restores
probation; an upheld dispute halves it (idempotently); bid scoring
still rewards reputation — but only earned.
"""

from __future__ import annotations

from conftest import hb_headers

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Flask

from sincor2.a2a_inbound import (
    REPUTATION_MERIT_THRESHOLD,
    _apply_reputation,
    _now_ms,
    get_fabric,
    reset_fabric,
)
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_ext import register_agent_record
from sincor2.a2a_inbound_market import _dispute_message, sealed_commitment
from sincor2.contract_net import calculate_bid_score
from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    reset_stake_ledger,
    stake_ledger,
)

ONE_AXM = 10**18
BOUNTY = 1.5

PLATFORM_AGENT_ID = "sincor-agent-swarm"


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def adjudicator():
    return Account.create()


@pytest.fixture(autouse=True)
def _adjudicator_env(monkeypatch, adjudicator):
    monkeypatch.setenv(ADJUDICATOR_ENV, adjudicator.address)


_REGISTER_KEYS = {}


def _register(client, agent_id, **extra):
    # Each test agent gets a real key: first registration uses its address
    # as the wallet with a valid EIP-191 proof (C3 fail-closed); 
    # re-registration is proof-gated (G2.2), so the helper attaches an
    # EIP-191 signature by the registered wallet.
    key = _REGISTER_KEYS.setdefault(agent_id, Account.create())
    body = {
        "agent_id": agent_id,
        "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://agent.example/rpc",
        "wallet": key.address,
    }
    body.update(extra)
    if agent_id in get_fabric().agents:
        from sincor2.a2a_inbound import build_reregistration_message
        ts = _now_ms()
        message = build_reregistration_message(body, ts)
        sig = key.sign_message(encode_defunct(text=message)).signature
        body["registration_ts"] = ts
        body["registration_signature"] = "0x" + bytes(sig).hex()
    else:
        from sincor2.a2a_identity import register_message
        ts = _now_ms()
        message = register_message(agent_id, ts)
        sig = key.sign_message(encode_defunct(text=message)).signature
        body["registration_wallet"] = key.address
        body["registration_ts"] = ts
        body["registration_signature"] = "0x" + bytes(sig).hex()
    r = client.post("/v1/a2a/register", json=body)
    assert r.status_code in (200, 201), r.get_json()
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id}, headers=hb_headers())
    assert r.status_code == 200, r.get_json()
    return r


def _sealed_task(client, poster_id="poster-p"):
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "tags": ["lead-enrichment"],
              "bounty_axm": BOUNTY, "sealed": True, "poster_id": poster_id},
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()["task_id"]


def _commitment(bid_axm, nonce_hex, agent_id):
    price_wei = int(round(float(bid_axm) * 1e18))
    return "0x" + sealed_commitment(price_wei, bytes.fromhex(nonce_hex),
                                    agent_id).hex()


def _commit(client, task_id, agent_id, bid_axm=1.0, nonce_hex="aa" * 32):
    return client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": task_id, "agent_id": agent_id,
              "commitment": _commitment(bid_axm, nonce_hex, agent_id)},
    )


def _signed_dispute(key, task_id, agent_id, upheld, expires_at_ms=None):
    expires = (expires_at_ms if expires_at_ms is not None
               else _now_ms() + 60_000)
    message = _dispute_message(task_id, agent_id, upheld, key.address,
                               expires)
    sig = Account.sign_message(encode_defunct(text=message),
                               private_key=key.key).signature.hex()
    return {
        "task_id": task_id, "agent_id": agent_id, "upheld": upheld,
        "adjudicator_address": key.address, "expires_at_ms": expires,
        "signature": "0x" + sig,
    }


def _run_auction_to_winner(client, agent_id="winner-a"):
    """Commit -> reveal -> close; returns (task_id, locked_wei)."""
    _register(client, agent_id)
    stake_ledger().deposit(agent_id, int(10 * ONE_AXM))
    task_id = _sealed_task(client)
    r = _commit(client, task_id, agent_id)
    assert r.status_code == 201, r.get_json()
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    r = client.post("/v1/a2a/bids/reveal", json={
        "task_id": task_id, "agent_id": agent_id, "bid_axm": 1.0,
        "nonce": "aa" * 32, "estimated_seconds": 300})
    assert r.status_code == 201, r.get_json()
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    r = client.post(f"/v1/a2a/tasks/{task_id}/close")
    assert r.status_code == 200, r.get_json()
    locked = int(stake_ledger().balance_of(agent_id)["locked_wei"])
    assert locked > 0
    return task_id, locked


# -- the exploit, closed -------------------------------------------------------

def test_registration_ignores_declared_reputation(client):
    """The original exploit: body reputation 1.0 must not stick."""
    r = client.post("/v1/a2a/register", json={
        "agent_id": "evil-1", "capability_tags": ["lead-enrichment"],
        "rpc_callback": "https://evil.example/rpc",
        "reputation": 1.0})
    assert r.status_code in (200, 201), r.get_json()
    agent = get_fabric().agents["evil-1"]
    assert agent["reputation"] == 0.0
    assert agent["probation"] is True
    assert agent["requires_merit"] is True
    assert agent["status"] == "probation"
    # The field stays in the response, but as read-only truth.
    assert r.get_json()["probation"] is True


def test_platform_agent_id_reserved_over_http(client):
    """Naming the platform agent id via HTTP cannot mint its reputation
    (or overwrite its record at all)."""
    before = dict(get_fabric().agents[PLATFORM_AGENT_ID])
    r = client.post("/v1/a2a/register", json={
        "agent_id": PLATFORM_AGENT_ID, "capability_tags": ["x"],
        "rpc_callback": "https://evil.example/rpc",
        "reputation": 1.0})
    assert r.status_code == 400, r.get_json()
    after = get_fabric().agents[PLATFORM_AGENT_ID]
    assert after["wallet"] == before["wallet"]
    assert after["rpc_callback"] == before["rpc_callback"]
    assert after["reputation"] == before["reputation"] == 1.0


def test_internal_seed_assigns_platform_reputation():
    """The internal platform seed keeps its 1.0 intent — via the
    internal-only path, never the request body."""
    reset_fabric()
    snap = register_agent_record(
        {"agent_id": PLATFORM_AGENT_ID, "capability_tags": ["x"],
         "rpc_callback": "https://getsincor.com/api/a2a",
         "wallet": "0x" + "44" * 20},
        _internal_reputation=1.0)
    assert snap["reputation"] == 1.0
    assert snap["probation"] is False
    assert snap["requires_merit"] is False
    assert snap["status"] == "live"


def test_reregistration_preserves_earned_reputation(client):
    """Re-registration (e.g. heartbeat re-register) must not reset honest
    agents to zero — and a declared value cannot overwrite earned value."""
    _register(client, "honest-1")
    fabric = get_fabric()
    with fabric.lock:
        _apply_reputation(fabric.agents["honest-1"], 0.4)  # two settlements
    # Re-register with no reputation field: earned value preserved.
    _register(client, "honest-1")
    agent = fabric.agents["honest-1"]
    assert agent["reputation"] == pytest.approx(0.4)
    assert agent["probation"] is False
    assert agent["status"] == "live"
    # Re-register *with* a declared 1.0: still cannot overwrite earned value.
    # (Re-registration is proof-gated, so it goes through the signed helper.)
    r = _register(client, "honest-1", reputation=1.0)
    assert fabric.agents["honest-1"]["reputation"] == pytest.approx(0.4)


# -- negative outcomes ----------------------------------------------------------

def test_ghosting_resets_reputation_to_zero(client):
    """Commit-but-never-reveal zeroes reputation and restores probation;
    the honest winner is untouched."""
    _register(client, "ghost-1")
    _register(client, "winner-1")
    stake_ledger().deposit("ghost-1", int(10 * ONE_AXM))
    stake_ledger().deposit("winner-1", int(10 * ONE_AXM))
    fabric = get_fabric()
    with fabric.lock:
        _apply_reputation(fabric.agents["ghost-1"], 0.8)   # earned
        _apply_reputation(fabric.agents["winner-1"], 0.4)  # earned
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "ghost-1", nonce_hex="aa" * 32).status_code == 201
    assert _commit(client, task_id, "winner-1", nonce_hex="bb" * 32).status_code == 201
    fabric.tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    r = client.post("/v1/a2a/bids/reveal", json={
        "task_id": task_id, "agent_id": "winner-1", "bid_axm": 1.0,
        "nonce": "bb" * 32, "estimated_seconds": 300})
    assert r.status_code == 201, r.get_json()
    fabric.tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    r = client.post(f"/v1/a2a/tasks/{task_id}/close")
    assert r.status_code == 200, r.get_json()

    ghost = fabric.agents["ghost-1"]
    assert ghost["reputation"] == 0.0
    assert ghost["probation"] is True
    assert ghost["requires_merit"] is True
    assert ghost["status"] == "probation"
    # Winner keeps earned reputation.
    assert fabric.agents["winner-1"]["reputation"] == pytest.approx(0.4)


def test_upheld_dispute_halves_reputation(client, adjudicator):
    """An upheld quality dispute halves earned reputation (min 0)."""
    task_id, _ = _run_auction_to_winner(client, "winner-a")
    fabric = get_fabric()
    with fabric.lock:
        _apply_reputation(fabric.agents["winner-a"], 0.8)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 200, r.get_json()
    agent = fabric.agents["winner-a"]
    assert agent["reputation"] == pytest.approx(0.4)
    assert agent["probation"] is False      # 0.4 >= 0.15: still out of probation
    assert agent["status"] == "live"


def test_upheld_dispute_replay_does_not_halve_twice(client, adjudicator):
    """Idempotency: the ledger slash no-ops on replay (slashed_wei == 0),
    so a replayed ruling cannot halve reputation again."""
    task_id, _ = _run_auction_to_winner(client, "winner-a")
    fabric = get_fabric()
    with fabric.lock:
        _apply_reputation(fabric.agents["winner-a"], 0.8)
    body = _signed_dispute(adjudicator, task_id, "winner-a", True)
    assert client.post("/v1/a2a/disputes", json=body).status_code == 200
    assert fabric.agents["winner-a"]["reputation"] == pytest.approx(0.4)
    r = client.post("/v1/a2a/disputes", json=body)  # replay
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["slashed_wei"] == "0"
    assert fabric.agents["winner-a"]["reputation"] == pytest.approx(0.4)


def test_rejected_dispute_preserves_reputation(client, adjudicator):
    """upheld=false releases stake; reputation is untouched."""
    task_id, _ = _run_auction_to_winner(client, "winner-a")
    fabric = get_fabric()
    with fabric.lock:
        _apply_reputation(fabric.agents["winner-a"], 0.6)
    body = _signed_dispute(adjudicator, task_id, "winner-a", False)
    r = client.post("/v1/a2a/disputes", json=body)
    assert r.status_code == 200, r.get_json()
    assert fabric.agents["winner-a"]["reputation"] == pytest.approx(0.6)


# -- the helper ------------------------------------------------------------------

def test_apply_reputation_threshold_boundaries():
    reset_fabric()
    agent: dict = {}
    _apply_reputation(agent, REPUTATION_MERIT_THRESHOLD)  # exactly 0.15
    assert agent["reputation"] == pytest.approx(0.15)
    assert agent["probation"] is False
    assert agent["requires_merit"] is False
    assert agent["status"] == "live"
    _apply_reputation(agent, 0.149)
    assert agent["probation"] is True
    assert agent["requires_merit"] is True
    assert agent["status"] == "probation"
    _apply_reputation(agent, 99.0)   # clamps to the 1.0 cap
    assert agent["reputation"] == 1.0
    _apply_reputation(agent, -5.0)   # clamps to 0, back on probation
    assert agent["reputation"] == 0.0
    assert agent["probation"] is True


# -- reputation still matters, but earned ------------------------------------------

def test_bid_scoring_rewards_earned_reputation():
    """Documents that reputation still decides close bids — the +0.3 for a
    1.0 must now be earned (five settlements at +0.2), not declared."""
    fresh = calculate_bid_score(1.0, 300, 0.0)
    earned = calculate_bid_score(1.0, 300, 1.0)
    assert earned > fresh
    assert (earned - fresh) == pytest.approx(0.3)
    one_settlement = calculate_bid_score(1.0, 300, 0.2)
    assert (one_settlement - fresh) == pytest.approx(0.06)
