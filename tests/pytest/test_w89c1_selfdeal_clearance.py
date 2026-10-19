"""C1 fix tests — colluding poster must not clear a self-deal wash flag.

DEFECT: a colluding poster+worker could clear a wash-anomaly flag via valid
EIP-191 acceptance even when the signals include ``self_deal_wallet``.
Self-attestation of self-dealing proves nothing.

These tests reproduce the defect FIRST on the unmodified code (fail-before),
then verify the fix:
  (a) W-100: the settle-time acceptance parameter was REMOVED from
      assess_settlement (no poster could sign the canonical message before
      proof_id is minted server-side) — passing it is now a TypeError, so a
      self_deal_wallet flag is un-clearable at settle time by construction.
  (b) clear_wash_anomaly: refuses self-deal with the exact reason string;
      the flag stays anomalous=True.
  (c) non-self-deal clearance (post-settle) retains a machine-readable
      wash_history marker: original signals + clearance record (who
      cleared, when, by what mechanism).
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import axm_antigaming as ag
from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound_market import create_task, submit_proof
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger
from sincor2.wash_guard import (
    POSTER_ACCEPT_DOMAIN,
    assess_settlement,
    clear_wash_anomaly,
    verify_poster_acceptance,
)

ONE_AXM = 10 ** 18


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated fabric + stake ledger + antigaming store."""
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    ag.reset_antigaming_store(path=str(tmp_path / "antigaming.json"))
    monkeypatch.setenv("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", "1000")
    yield
    reset_fabric()


def _mk_agent(agent_id, wallet=None, origin="external"):
    fabric = get_fabric()
    key = Account.create()
    fabric.agents[agent_id] = {
        "agent_id": agent_id,
        "capability_tags": ["gig"],
        "last_heartbeat": _now_ms(),
        "reputation": 0.5,
        "wallet": wallet or key.address,
        "origin": origin,
    }
    stake_ledger().deposit(agent_id, 100 * ONE_AXM)
    return key


def _mk_assigned_task(poster_id, winner_id, bounty=10.0, assigned_ago_ms=5_000):
    task = create_task("gig", bounty_axm=bounty, poster_id=poster_id)
    task_id = task["task_id"]
    fabric = get_fabric()
    now = _now_ms()
    with fabric.lock:
        t = fabric.tasks[task_id]
        t["state"] = "assigned"
        t["assigned_to"] = winner_id
        t["assigned_at"] = now - assigned_ago_ms
        t["winning_bid_axm"] = bounty
    return task_id


def _add_bid(task_id, agent_id):
    fabric = get_fabric()
    bid_id = f"bid_{task_id}_{agent_id}"
    with fabric.lock:
        fabric.bids[bid_id] = {
            "bid_id": bid_id, "task_id": task_id, "agent_id": agent_id,
            "revealed": True,
        }


def _sign_acceptance(key, task_id, proof_id, receipt):
    message = "\n".join([POSTER_ACCEPT_DOMAIN, task_id, proof_id, receipt])
    return Account.sign_message(
        encode_defunct(text=message), private_key=key.key).signature.hex()


def _shared_wallet_poc(iso_unused=None):
    """Colluding external poster+worker sharing the POSTER's own wallet,
    sole bidder, instant settle, bare receipt.  The shared wallet is the
    poster's real registered wallet, so the poster can produce a VALID
    EIP-191 acceptance — the whole point of the PoC.
    Returns (poster_key, task_id)."""
    poster_key = _mk_agent("c1-poster", origin="external")
    shared = get_fabric().agents["c1-poster"]["wallet"]
    _mk_agent("c1-worker", wallet=shared, origin="external")
    task_id = _mk_assigned_task("c1-poster", "c1-worker", assigned_ago_ms=5_000)
    _add_bid(task_id, "c1-worker")  # sole bidder
    return poster_key, task_id


def test_poc_baseline_flags_self_deal(iso):
    """PoC baseline (pre-fix sanity): the shared-wallet collusion flags with
    self_deal_wallet among the signals."""
    _, task_id = _shared_wallet_poc()
    receipt = "0x" + "ab" * 32
    proof = submit_proof(task_id, "c1-worker", receipt)
    verdict = proof.get("wash_anomaly") or {}
    names = {s["name"] for s in verdict.get("signals", [])}
    assert verdict.get("anomalous") is True
    assert "self_deal_wallet" in names
    assert "unbound_no_acceptance" in names
    assert "sole_bidder" in names
    assert "instant_settle" in names


def test_selfdeal_acceptance_cannot_clear_in_assess(iso):
    """(a) W-100: the settle-time acceptance path is REMOVED from
    assess_settlement — passing poster_acceptance_sig is a TypeError, so a
    self_deal_wallet flag is un-clearable at settle time by construction.
    The signature itself still verifies (unit), which is what the
    post-settle clear_wash_anomaly path relies on."""
    poster_key, task_id = _shared_wallet_poc()
    fabric = get_fabric()
    task = fabric.tasks[task_id]
    proof_id = "prf_c1_selfdeal"
    receipt = "0x" + "ab" * 32
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    assert verify_poster_acceptance(task, proof_id, receipt, sig,
                                   fabric=fabric) is True  # valid sig
    with pytest.raises(TypeError):
        assess_settlement(
            task, "c1-worker", content_bound=False, receipt_hash=receipt,
            proof_id=proof_id, poster_acceptance_sig=sig,
            fabric=fabric, now_ms=_now_ms())
    # Without the (removed) acceptance path, self-deal still flags.
    verdict = assess_settlement(
        task, "c1-worker", content_bound=False, receipt_hash=receipt,
        fabric=fabric, now_ms=_now_ms())
    assert verdict.get("anomalous") is True
    assert verdict.get("cleared_by_acceptance") is False
    assert "self_deal_wallet" in {s["name"] for s in verdict["signals"]}


def test_selfdeal_clearance_refused_post_settle(iso):
    """(b) clear_wash_anomaly refuses a self-deal flag even with a valid
    poster acceptance: cleared=False, the exact reason string, and the
    flag stays anomalous=True."""
    poster_key, task_id = _shared_wallet_poc()
    receipt = "0x" + "cd" * 32
    proof = submit_proof(task_id, "c1-worker", receipt)
    proof_id = proof["proof_id"]
    fabric = get_fabric()
    stamp = fabric.tasks[task_id]["wash_anomaly"]
    assert stamp.get("anomalous") is True
    assert "self_deal_wallet" in {s["name"] for s in stamp["signals"]}
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    result = clear_wash_anomaly(task_id, proof_id, sig)
    assert result["cleared"] is False
    assert result["reason"] == "self-deal not clearable by poster"
    assert fabric.tasks[task_id]["wash_anomaly"]["anomalous"] is True
    assert fabric.proofs[proof_id]["wash_anomaly"]["anomalous"] is True


def test_selfdeal_refused_even_with_projected_fee(iso, tmp_path, monkeypatch):
    """The refusal must hold even when a projected fee is pending re-booking:
    refusal must not touch the stamp or re-book anything."""
    import sincor2.treasury_inflow as treasury_inflow
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "ledger.jsonl")
    poster_key, task_id = _shared_wallet_poc()
    receipt = "0x" + "ef" * 32
    proof = submit_proof(task_id, "c1-worker", receipt)
    proof_id = proof["proof_id"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["wash_fee_projected"] = {"fee_axm": 0.5}
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    result = clear_wash_anomaly(task_id, proof_id, sig)
    assert result["cleared"] is False
    assert result["reason"] == "self-deal not clearable by poster"
    stamp = fabric.tasks[task_id]["wash_anomaly"]
    assert stamp["anomalous"] is True
    assert stamp.get("cleared_by_acceptance") is not True
    assert stamp.get("wash_history", []) == []
    # Nothing re-booked as realized.
    assert treasury_inflow._read_ledger_events() == []


def test_non_selfdeal_clearance_keeps_wash_history(iso, tmp_path, monkeypatch):
    """(c) A non-self-deal flag cleared by poster acceptance retains a
    machine-readable wash_history marker: original signals + clearance
    record (who cleared, when, by what mechanism)."""
    import sincor2.treasury_inflow as treasury_inflow
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "ledger.jsonl")
    poster_key = _mk_agent("c1-ns-poster", origin="external")
    _mk_agent("c1-ns-worker", origin="external")
    task_id = _mk_assigned_task("c1-ns-poster", "c1-ns-worker",
                               assigned_ago_ms=5_000)
    _add_bid(task_id, "c1-ns-worker")  # sole bidder, instant, bare receipt
    receipt = "0x" + "12" * 32
    proof = submit_proof(task_id, "c1-ns-worker", receipt)
    proof_id = proof["proof_id"]
    fabric = get_fabric()
    stamp_before = fabric.tasks[task_id]["wash_anomaly"]
    assert stamp_before.get("anomalous") is True
    original_names = [s["name"] for s in stamp_before["signals"]]
    assert "self_deal_wallet" not in original_names
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    result = clear_wash_anomaly(task_id, proof_id, sig)
    assert result["cleared"] is True
    stamp = fabric.tasks[task_id]["wash_anomaly"]
    assert stamp["anomalous"] is False
    assert stamp["cleared_by_acceptance"] is True
    history = stamp.get("wash_history") or []
    assert len(history) == 1
    entry = history[0]
    # Original signals preserved.
    assert [s["name"] for s in entry["original_signals"]] == original_names
    assert len(entry["original_signals"]) > 0
    # Clearance record: who, when, by what mechanism.
    rec = entry["clearance"]
    assert rec["cleared_by"] == "c1-ns-poster"
    assert rec["cleared_by_wallet"]  # poster's registered wallet
    assert isinstance(rec["cleared_at"], int) and rec["cleared_at"] > 0
    assert rec["mechanism"] == "poster_eip191_acceptance"
    # Same marker mirrored on the proof stamp.
    proof_history = (fabric.proofs[proof_id]["wash_anomaly"]
                     .get("wash_history") or [])
    assert len(proof_history) == 1
    assert proof_history[0]["clearance"]["mechanism"] == \
        "poster_eip191_acceptance"


def test_clean_settlement_has_no_wash_history(iso):
    """A genuinely clean settlement (no flag, no clearance) carries no
    wash_history — the marker only exists for flagged-then-cleared."""
    _mk_agent("c1-cl-poster", origin="external")
    _mk_agent("c1-cl-a", origin="external")
    _mk_agent("c1-cl-b", origin="external")
    task_id = _mk_assigned_task("c1-cl-poster", "c1-cl-a",
                               assigned_ago_ms=60_000)
    _add_bid(task_id, "c1-cl-a")
    _add_bid(task_id, "c1-cl-b")
    fabric = get_fabric()
    task = fabric.tasks[task_id]
    verdict = assess_settlement(
        task, "c1-cl-a", content_bound=False, receipt_hash="0x" + "56" * 32,
        fabric=fabric, now_ms=_now_ms())
    assert verdict.get("anomalous") is False
    assert verdict.get("cleared_by_acceptance") is False
    assert verdict.get("wash_history") == []
