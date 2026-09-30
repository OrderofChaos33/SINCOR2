"""Agent SDK: sealed commit/reveal round-trip against the test app.

Drives the REAL endpoints (register -> heartbeat -> stake deposit ->
commit -> reveal -> close -> proof) through SincorAgentSDK with an
in-process Flask test transport. Deadlines are advanced directly in the
fabric (same technique as test_sealed_bid_shim.py) so the suite stays
deterministic instead of sleeping through protocol windows.
"""
from __future__ import annotations

import os

import pytest
from flask import Flask

from sincor2.a2a_inbound import _now_ms, get_fabric
from sincor2.a2a_inbound import register as register_inbound
from sincor2.a2a_inbound_market import sealed_commitment
from sincor2.a2a_sdk import (
    FlaskTestTransport,
    SDKError,
    SincorAgentSDK,
)

AGENT = "sdk-agent-1"
TAGS = ["lead-enrichment"]
WALLET = "0x" + "22" * 20


@pytest.fixture
def sdk(tmp_path):
    from sincor2.a2a_inbound import reset_fabric
    from sincor2.onchain.stake_ledger import reset_stake_ledger
    from sincor2.sponsored_stake import reset_sponsored_ledger

    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    reset_sponsored_ledger(path=str(tmp_path / "sponsored.json"))
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    return SincorAgentSDK(FlaskTestTransport(app.test_client()))


def _onboard(sdk, agent_id=AGENT, stake_axm=2.0):
    reg = sdk.register(
        agent_id, "SDK Agent", TAGS, wallet=WALLET,
        rpc_callback="https://sdk-agent.example/rpc")
    assert reg["status"] == "registered"
    hb = sdk.heartbeat(agent_id, heartbeat_token=os.environ["AGENT_HEARTBEAT_TOKEN"])
    assert hb["ok"] is True
    dep = sdk.deposit_stake(agent_id, stake_axm)
    assert dep["agent_id"] == agent_id
    return reg


def _sealed_task(sdk, bounty_axm=1.5):
    task = sdk.post_task("lead-enrichment", TAGS, bounty_axm, sealed=True)
    assert task["sealed"] is True
    assert task["commit_deadline"] and task["reveal_deadline"]
    return task["task_id"]


def _open_reveal_window(task_id):
    get_fabric().tasks[task_id]["commit_deadline"] = _now_ms() - 1000


def _pass_reveal_deadline(task_id):
    get_fabric().tasks[task_id]["reveal_deadline"] = _now_ms() - 1000


def test_sdk_commitment_matches_server_utility():
    """The SDK's commitment (via bidder_client) must equal the server's
    sealed_commitment byte-for-byte — one preimage, both paths."""
    salt = bytes.fromhex("ab" * 32)
    mine = SincorAgentSDK.make_commitment(0.9, salt, AGENT)
    theirs = "0x" + sealed_commitment(int(0.9 * 1e18), salt, AGENT).hex()
    assert mine == theirs


def test_sdk_sealed_round_trip(sdk):
    _onboard(sdk)
    task_id = _sealed_task(sdk)

    bid = sdk.sealed_commit(task_id, AGENT, 0.9)
    assert bid.commitment.startswith("0x") and len(bid.commitment) == 66
    assert bid.salt_hex.startswith("0x") and len(bid.salt_hex) == 66

    # Stake locked at commit: 50% of the 1.5 AXM bounty.
    bal = sdk.stake_balance(AGENT)
    assert bal["locked_wei"] == str(int(0.75 * 1e18))

    _open_reveal_window(task_id)
    sdk.heartbeat(AGENT, heartbeat_token=os.environ["AGENT_HEARTBEAT_TOKEN"])
    revealed = sdk.sealed_reveal(bid, estimated_seconds=600)
    assert revealed["revealed"] is True
    assert revealed["bid_axm"] == 0.9

    _pass_reveal_deadline(task_id)
    closed = sdk.close_auction(task_id)
    assert closed["state"] == "assigned"
    assert closed["assigned_to"] == AGENT
    assert closed["winning_bid_axm"] == 0.9


def test_sdk_full_settlement_releases_stake_and_earns_reputation(sdk):
    _onboard(sdk)
    task_id = _sealed_task(sdk)
    bid = sdk.sealed_commit(task_id, AGENT, 0.9)
    _open_reveal_window(task_id)
    sdk.heartbeat(AGENT, heartbeat_token=os.environ["AGENT_HEARTBEAT_TOKEN"])
    sdk.sealed_reveal(bid)
    _pass_reveal_deadline(task_id)
    sdk.close_auction(task_id)

    proof = sdk.submit_proof(task_id, AGENT, "0xdeadbeef1234567890")
    assert proof["status"] == "paid"
    task = sdk.get_task(task_id)
    assert task["state"] == "settled"

    # Winner's stake lock released on settlement ...
    bal = sdk.stake_balance(AGENT)
    assert bal["locked_wei"] == "0"
    # ... and one settled task earns +0.2 reputation (earned-only).
    agents = get_fabric().agents
    assert abs(float(agents[AGENT]["reputation"]) - 0.2) < 1e-9
    assert agents[AGENT]["probation"] is False


def test_sdk_reveal_before_window_rejected(sdk):
    _onboard(sdk)
    task_id = _sealed_task(sdk)
    bid = sdk.sealed_commit(task_id, AGENT, 0.9)
    with pytest.raises(SDKError) as exc:
        sdk.sealed_reveal(bid)
    assert exc.value.status == 403
    assert "reveal window not open yet" in str(exc.value.body)


def test_sdk_commitment_mismatch_rejected(sdk):
    _onboard(sdk)
    task_id = _sealed_task(sdk)
    bid = sdk.sealed_commit(task_id, AGENT, 0.9)
    _open_reveal_window(task_id)
    sdk.heartbeat(AGENT, heartbeat_token=os.environ["AGENT_HEARTBEAT_TOKEN"])
    bid.bid_axm = 1.1  # tamper: reveal a different price than committed
    with pytest.raises(SDKError) as exc:
        sdk.sealed_reveal(bid)
    assert exc.value.status == 400
    assert "commitment mismatch" in str(exc.value.body)


def test_sdk_commit_without_stake_rejected(sdk):
    sdk.register(AGENT, "Broke Agent", TAGS, wallet=WALLET)
    sdk.heartbeat(AGENT, heartbeat_token=os.environ["AGENT_HEARTBEAT_TOKEN"])
    task_id = _sealed_task(sdk)
    with pytest.raises(SDKError) as exc:
        sdk.sealed_commit(task_id, AGENT, 0.9)
    assert exc.value.status == 403
    assert "staked" in str(exc.value.body)


def test_sdk_registration_is_earned_only(sdk, tmp_path):
    """Reputation stays earned-only through the SDK path: a declared
    reputation in the raw body is ignored, new agents start at 0.0."""
    client = sdk.t._client
    r = client.post("/v1/a2a/register", json={
        "agent_id": "sdk-earned-1", "capability_tags": TAGS,
        "wallet": WALLET, "reputation": 9.9,
    })
    assert r.status_code == 201
    agent = get_fabric().agents["sdk-earned-1"]
    assert float(agent["reputation"]) == 0.0
    assert agent["probation"] is True


def test_sdk_task_view_is_sealed_safe(sdk):
    _onboard(sdk)
    task_id = _sealed_task(sdk)
    sdk.sealed_commit(task_id, AGENT, 0.9)
    view = sdk.get_task(task_id)
    assert view["task_id"] == task_id
    assert view["sealed"] is True
    assert "commit_deadline" in view and "reveal_deadline" in view
    # No commitment or bid material leaks through the public view.
    blob = str(view)
    assert "commitment" not in blob and "price_wei" not in blob
