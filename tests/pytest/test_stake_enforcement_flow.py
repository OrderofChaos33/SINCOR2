"""Stake enforcement wired into the sealed-bid market flow.

commit_bid locks bounty*50 % (rejected when short), reveal_bid tops up to
bid*50 %, close_auction slashes ghosts 100 % and releases losers, and the
winner's lock survives until proof settlement.
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import sealed_commitment
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger

ONE_AXM = 10**18
BOUNTY = 1.5
COMMIT_LOCK = int(BOUNTY * ONE_AXM * 5000 // 10000)  # 0.75 AXM


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id, stake_axm=10):
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
    assert r.status_code == 200
    if stake_axm:
        stake_ledger().deposit(agent_id, int(stake_axm * ONE_AXM))


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
    salt = bytes.fromhex(nonce_hex)
    return "0x" + sealed_commitment(price_wei, salt, agent_id).hex()


def _commit(client, task_id, agent_id, bid_axm, nonce_hex):
    return client.post(
        "/v1/a2a/bids/commit",
        json={"task_id": task_id, "agent_id": agent_id,
              "commitment": _commitment(bid_axm, nonce_hex, agent_id)},
    )


def _reveal(client, task_id, agent_id, bid_axm, nonce_hex, est=300):
    return client.post(
        "/v1/a2a/bids/reveal",
        json={"task_id": task_id, "agent_id": agent_id, "bid_axm": bid_axm,
              "nonce": nonce_hex, "estimated_seconds": est},
    )


def _force_reveal_window(task_id):
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000


def _force_past_reveal_deadline(task_id):
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1


def test_commit_rejected_without_stake(client):
    _register(client, "broke-agent", stake_axm=0)
    task_id = _sealed_task(client)
    r = _commit(client, task_id, "broke-agent", 1.0, "aa" * 32)
    assert r.status_code == 403
    assert "stake" in r.get_json()["error"].lower()


def test_commit_locks_stake_and_reveal_keeps_it(client):
    _register(client, "staked-a")
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "staked-a", 1.0, "aa" * 32).status_code == 201
    bal = stake_ledger().balance_of("staked-a")
    assert bal["locked_wei"] == str(COMMIT_LOCK)

    _force_reveal_window(task_id)
    # Bid 1.0 needs 0.5 AXM; the 0.75 bounty-based lock already covers it.
    assert _reveal(client, task_id, "staked-a", 1.0, "aa" * 32).status_code == 201
    assert stake_ledger().balance_of("staked-a")["locked_wei"] == str(COMMIT_LOCK)


def test_reveal_rejected_when_top_up_short(client):
    _register(client, "thin-a", stake_axm=0.75)  # exactly the commit lock
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "thin-a", 2.0, "aa" * 32).status_code == 201
    _force_reveal_window(task_id)
    # Bid 2.0 needs 1.0 AXM locked; only 0.75 available -> reveal rejected.
    r = _reveal(client, task_id, "thin-a", 2.0, "aa" * 32)
    assert r.status_code == 403
    assert "stake" in r.get_json()["error"].lower()


def test_ghost_slashed_100_percent_at_close(client):
    _register(client, "honest-a")
    _register(client, "ghost-b")
    task_id = _sealed_task(client, poster_id="poster-p")

    assert _commit(client, task_id, "honest-a", 1.0, "aa" * 32).status_code == 201
    assert _commit(client, task_id, "ghost-b", 0.5, "bb" * 32).status_code == 201

    _force_reveal_window(task_id)
    assert _reveal(client, task_id, "honest-a", 1.0, "aa" * 32).status_code == 201
    # ghost-b never reveals.

    _force_past_reveal_deadline(task_id)
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "assigned"
    assert body["ghosted_commits"] == 1

    ledger = stake_ledger()
    ghost_bal = ledger.balance_of("ghost-b")
    assert ghost_bal["slashed_wei"] == str(COMMIT_LOCK)
    assert ghost_bal["locked_wei"] == "0"
    # 100 % of the slash becomes poster re-auction credit.
    assert ledger.reauction_credit("poster-p") == COMMIT_LOCK

    # Loser... honest-a won; check the winner is still locked.
    assert ledger.balance_of("honest-a")["locked_wei"] == str(COMMIT_LOCK)


def test_losers_released_winner_stays_locked(client):
    _register(client, "w1")
    _register(client, "w2")
    task_id = _sealed_task(client)
    assert _commit(client, task_id, "w1", 1.0, "aa" * 32).status_code == 201
    assert _commit(client, task_id, "w2", 2.0, "bb" * 32).status_code == 201
    _force_reveal_window(task_id)
    assert _reveal(client, task_id, "w1", 1.0, "aa" * 32).status_code == 201
    assert _reveal(client, task_id, "w2", 2.0, "bb" * 32).status_code == 201
    _force_past_reveal_deadline(task_id)
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["assigned_to"] == "w1"  # cheaper bid wins

    ledger = stake_ledger()
    assert ledger.balance_of("w2")["locked_wei"] == "0"  # loser released
    assert ledger.balance_of("w1")["locked_wei"] == str(COMMIT_LOCK)  # winner held


def test_registration_velocity_endpoint(client):
    _register(client, "ext-1")
    _register(client, "ext-2")
    r = client.get("/v1/a2a/registration-velocity")
    assert r.status_code == 200
    body = r.get_json()
    assert body["external"] >= 2
    assert body["external_per_day"] >= 0
    weights = body["toa_objective_weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert weights["velocity"] >= 0.30  # volume-over-vanity anchor


def test_origin_stamped_at_registration(client):
    _register(client, "brand-new-ext")
    agent = get_fabric().agents["brand-new-ext"]
    assert agent["origin"] == "external"
    assert agent["registered_at"] > 0
