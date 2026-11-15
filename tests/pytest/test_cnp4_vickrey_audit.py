"""CNP-4 (audit): pin the OBSERVED live sealed-bid settlement behavior.

Audit finding (see docs/architecture/CNP-4-VICKREY-AUDIT.md):
``marketplace/contract_net/vickrey.py`` (``clear_vickrey``) is NOT on the live
money path. The live A2A sealed-bid path
(``commit_bid`` -> ``reveal_bid`` -> ``close_auction`` -> ``submit_proof`` in
``src/sincor2/a2a_inbound_market.py``) settles at FIRST PRICE: the winner is
paid exactly their own winning bid (``winning_bid_axm``). These tests pin that
observed behavior so any rewiring — including a future founder-approved move to
second-price settlement — fails loudly here first.

NOTE: routing settlement through second-price is FOUNDER-GATED (no economic
change implemented in this wave). The pins below encode the current first-price
reality and must be deliberately updated if the founder ever approves a change.
"""
from __future__ import annotations

from conftest import hb_headers

from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import sealed_commitment
from sincor2.contract_net import calculate_bid_score


def _mk_client(tmp_path):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return app.test_client()


def _register(client, agent_id):
    from sincor2.onchain.stake_ledger import stake_ledger

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
    r = client.post("/v1/a2a/heartbeat", json={"agent_id": agent_id}, headers=hb_headers())
    assert r.status_code == 200
    stake_ledger().deposit(agent_id, 10 * 10**18)


def _sealed_task(client):
    r = client.post(
        "/v1/a2a/tasks",
        json={"skill": "lead-enrichment", "tags": ["lead-enrichment"],
              "bounty_axm": 1.5, "sealed": True},
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


def _full_round(client, bids):
    """Commit+reveal for (agent_id, bid_axm, est) tuples, then close past deadline."""
    for agent_id, _, _ in bids:
        _register(client, agent_id)
    task_id = _sealed_task(client)
    nonces = {}
    for i, (agent_id, bid_axm, _) in enumerate(bids):
        nonce = f"{i + 1:02x}" * 32
        nonces[agent_id] = nonce
        assert _commit(client, task_id, agent_id, bid_axm, nonce).status_code == 201
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000
    for agent_id, bid_axm, est in bids:
        r = _reveal(client, task_id, agent_id, bid_axm, nonces[agent_id], est=est)
        assert r.status_code == 201, r.get_json()
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1
    body = client.post(f"/v1/a2a/tasks/{task_id}/close").get_json()
    assert body["state"] == "assigned", body
    return task_id, body


def test_settlement_is_first_price_not_second_price(tmp_path):
    """Winner is paid their OWN bid, not the second-lowest bid.

    With bids 1.0 / 2.0 / 3.0 (equal reputation, equal time estimate),
    ``clear_vickrey`` would clear the winner at 2.0. The live path pays 1.0.
    """
    client = _mk_client(tmp_path)
    task_id, body = _full_round(client, [
        ("cnp4-a", 1.0, 300), ("cnp4-b", 2.0, 300), ("cnp4-c", 3.0, 300),
    ])
    assert body["assigned_to"] == "cnp4-a"
    assert body["winning_bid_axm"] == 1.0
    # No second-price residue anywhere on the task record: none of the
    # Award-schema fields that clear_vickrey emits ever appear.
    task = get_fabric().tasks[task_id]
    for field in ("clearing_price", "savings_vs_first_price", "mechanism",
                  "valid_bid_count", "rejected_bid_count", "winner_bid_price"):
        assert field not in task, f"vickrey residue field {field!r} on task"
    # ... and the winner's score is the composite score of the winning bid,
    # exactly as recorded at reveal time (score = calculate_bid_score).
    winning_bids = [b for b in get_fabric().bids.values()
                    if b.get("task_id") == task_id and b.get("agent_id") == "cnp4-a"]
    assert len(winning_bids) == 1
    assert winning_bids[0]["score"] == calculate_bid_score(
        winning_bids[0]["bid_axm"],
        winning_bids[0]["estimated_seconds"],
        winning_bids[0]["reputation"],
    )
    assert body["winner_score"] == winning_bids[0]["score"]


def test_winner_selected_by_highest_score_not_lowest_price(tmp_path):
    """Winner selection is composite score, not lowest valid price.

    cnp4-cheap bids 1.0 but takes 2 hours; cnp4-fast bids 5.0 and takes 1
    minute. Vickrey would award cnp4-cheap (lowest price); the live path
    awards cnp4-fast (higher score). Proves the money path is not clear_vickrey.
    """
    client = _mk_client(tmp_path)
    task_id, body = _full_round(client, [
        ("cnp4-cheap", 1.0, 7200), ("cnp4-fast", 5.0, 60),
    ])
    score_cheap = calculate_bid_score(1.0, 7200, 0.0)
    score_fast = calculate_bid_score(5.0, 60, 0.0)
    assert score_fast > score_cheap  # guard: the score ordering we rely on
    assert body["assigned_to"] == "cnp4-fast"
    assert body["winning_bid_axm"] == 5.0  # winner's own bid, not 1.0


def test_payout_amount_equals_winning_bid_axm(tmp_path):
    """Proof settlement pays winning_bid_axm verbatim into payout_axm."""
    client = _mk_client(tmp_path)
    task_id, body = _full_round(client, [
        ("cnp4-p1", 1.0, 300), ("cnp4-p2", 2.5, 300),
    ])
    assert body["winning_bid_axm"] == 1.0
    r = client.post(
        "/v1/a2a/proofs",
        json={"task_id": task_id, "agent_id": "cnp4-p1",
              "receipt_hash": "0x" + "ab" * 32},
    )
    assert r.status_code == 202, r.get_json()
    task = get_fabric().tasks[task_id]
    assert task["state"] == "settled"
    assert task["payout_axm"] == body["winning_bid_axm"] == 1.0
    # Second-price would have paid 2.5 here; first-price reality pins 1.0.
    assert task["payout_axm"] != 2.5


def test_close_auction_does_not_reference_vickrey(tmp_path):
    """Change detector: the money-path module must not invoke clear_vickrey.

    If settlement is ever legitimately rewired through vickrey.py
    (founder-gated), this test must be updated along with the behavioral pins
    above. It is a deliberate tripwire, not a behavior assertion.
    """
    import inspect
    import re

    import sincor2.a2a_inbound_market as money_path

    source = inspect.getsource(money_path)
    # No identifier-level use of the Vickrey clearing function anywhere in the
    # money-path module (line 33 carries a prose comment mentioning
    # "marketplace.contract_net"; this pin checks code, not comments).
    assert "clear_vickrey" not in source
    assert not re.search(r"^\s*(from|import)\s+marketplace\.contract_net", source, re.M)
    assert not re.search(r"import\s+marketplace\.contract_net", source)
