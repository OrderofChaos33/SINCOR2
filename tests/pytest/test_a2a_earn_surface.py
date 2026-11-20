"""Worker-side onboarding surface (GET /v1/a2a/earn) + discovery fixes.

The agent card sells SINCOR skills to buyers; the earn surface is the
worker side — how an OUTSIDE agent registers, stakes, bids, and gets
paid. The honesty contract under test:

- fee truth: marketplace payout deduction is 0 bps (no code path takes
  a cut of a marketplace payout); the 500 bps platform fee is the
  skill-purchase fee and is labelled as such;
- payout mode is read from has_payout_signer() — staged is reported as
  staged, never dressed up as on-chain payment;
- probation math matches the code constants (one settlement clears
  probation; tasks at MERIT_THRESHOLD_AXM require merit);
- live counts come from the stores (a fresh registry reports
  external_agents == 0 — the platform never inflates its own numbers);
- discovery surfaces (card, manifest, docs, auctions) all point at the
  earn surface and the real task board.
"""

from __future__ import annotations

import pytest
from flask import Flask

from sincor2.a2a_bounty_pool import reset_bounty_pool
from sincor2.a2a_inbound import (
    MERIT_THRESHOLD_AXM,
    REPUTATION_MERIT_THRESHOLD,
    register as register_inbound,
    reset_fabric,
)
from sincor2.a2a_integration import (
    A2A_PLATFORM_FEE_BPS,
    A2ARouter,
    build_agent_card,
    build_marketplace_manifest,
)
from sincor2.contract_net import has_payout_signer


@pytest.fixture
def client(tmp_path):
    reset_fabric()
    app = Flask(__name__)
    register_inbound(app)
    app.register_blueprint(A2ARouter().blueprint)
    app.config["TESTING"] = True
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=0)
    return app.test_client()


def _earn(client):
    resp = client.get("/v1/a2a/earn")
    assert resp.status_code == 200
    return resp.get_json()


def test_earn_shape_and_steps(client):
    data = _earn(client)
    assert data["surface"] == "worker-onboarding"
    actions = [s["action"] for s in data["steps"]]
    assert actions == [
        "register", "heartbeat", "find_work", "stake",
        "bid_commit_reveal", "prove_and_get_paid",
    ]
    register = data["steps"][0]
    assert register["url"].endswith("/v1/a2a/register")
    # The wallet trap is stated, not discovered by failing:
    # heartbeat auth is a signature by the registered wallet.
    assert "wallet" in register["wallet_required"].lower()
    stake = data["steps"][3]
    assert stake["deposit"]["url"].endswith("/v1/a2a/stake/deposit")
    assert "slash" in stake["ghosting_warning"].lower()


def test_earn_fee_truth(client):
    data = _earn(client)
    assert data["fees"]["marketplace_payout_deduction_bps"] == 0
    assert data["fees"]["skill_purchase_platform_fee_bps"] == A2A_PLATFORM_FEE_BPS
    payout = data["steps"][5]["payout"]
    assert payout["marketplace_fee_bps_deducted"] == 0


def test_earn_payout_mode_is_honest(client):
    data = _earn(client)
    expected = "live" if has_payout_signer() else "staged"
    assert data["steps"][5]["payout"]["mode"] == expected
    if expected == "staged":
        assert "NOT an on-chain tx hash" in data["steps"][5]["payout"]["mode_note"]


def test_earn_probation_math_matches_code(client):
    data = _earn(client)
    probation = data["probation"]
    assert probation["merit_threshold"] == REPUTATION_MERIT_THRESHOLD
    assert probation["merit_task_threshold_axm"] == MERIT_THRESHOLD_AXM
    # +0.2 per settlement against a 0.15 threshold: exactly one
    # settlement clears probation. If either constant changes, this
    # test forces the public claim to be re-derived, not stale.
    assert probation["settlements_to_clear_probation"] == 1
    assert probation["per_settlement_gain"] == 0.2


def test_earn_live_counts_start_honest(client):
    data = _earn(client)
    live = data["live"]
    # Fresh registry: only the internal platform seed exists.
    assert live["external_agents"] == 0
    assert live["registered_agents"] >= 1
    assert live["settled_tasks"] == 0


def test_earn_counts_a_real_external_registration(client):
    # Register the way the earn spec prescribes: wallet claim + the
    # EIP-191 first-registration proof over
    # SINCOR-REGISTER|<agent_id>|<ts>. An unproven wallet claim is a
    # 403 by design (anti-squatting), so the test signs for real.
    import time

    from eth_account import Account
    from eth_account.messages import encode_defunct

    acct = Account.create()
    ts = int(time.time() * 1000)
    message = f"SINCOR-REGISTER|outsider-1|{ts}"
    sig = acct.sign_message(encode_defunct(text=message)).signature
    resp = client.post("/v1/a2a/register", json={
        "agent_id": "outsider-1",
        "name": "Outsider",
        "capability_tags": ["lead-enrichment"],
        "wallet": acct.address,
        "registration_signature": "0x" + bytes(sig).hex(),
        "registration_wallet": acct.address,
        "registration_ts": ts,
    })
    assert resp.status_code == 201
    data = _earn(client)
    assert data["live"]["external_agents"] == 1
    # The earn spec must document the proof it demands.
    proof = data["steps"][0]["wallet_proof"]
    assert proof["message_format"] == "SINCOR-REGISTER|{agent_id}|{timestamp_ms}"
    assert "registration_signature" in proof["body_fields"]


def test_card_points_at_earn():
    card = build_agent_card().to_dict()
    assert card["earnUrl"].endswith("/v1/a2a/earn")


def test_manifest_points_at_board_and_earn():
    manifest = build_marketplace_manifest()
    endpoints = manifest["endpoints"]
    assert endpoints["earn"].endswith("/v1/a2a/earn")
    assert endpoints["tasks"].endswith("/v1/a2a/tasks")
    assert endpoints["stake_deposit"].endswith("/v1/a2a/stake/deposit")
    assert endpoints["proofs"].endswith("/v1/a2a/proofs")
    # The stale claim (tasks endpoint not yet merged) must not return.
    assert "When the sibling" not in manifest["upgrade_path"]["task_list"]


def test_docs_point_at_earn(client):
    resp = client.get("/docs/a2a")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["discovery"]["earn"].endswith("/v1/a2a/earn")
    assert data["marketplace"]["tasks"].endswith("/v1/a2a/tasks")


def test_auctions_empty_is_not_a_dead_end(client):
    resp = client.get("/v1/a2a/auctions")
    assert resp.status_code == 200
    data = resp.get_json()
    # No onchain anchor in the test env: the anchored list is empty,
    # but the response must route the reader to the real board + earn.
    assert data["auctions"] == []
    assert data["board"].endswith("/v1/a2a/tasks")
    assert data["earn"].endswith("/v1/a2a/earn")
