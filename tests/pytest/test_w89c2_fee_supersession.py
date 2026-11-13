"""W-89 C2 — projected->realized fee transition must be booked exactly once.

DEFECT: when a wash flag is cleared, the projected fee is re-booked as
realized but the original projected event is never superseded, so
ledger_summary_24h double-counts.

These tests fail on the pristine w89-rebased code and pass after the fix:
  (a) projected->realized transition appears exactly once in
      ledger_summary_24h (0.5 realized, 0.0 projected);
  (b) the original projected event is mechanically superseded (a
      supersedes pointer, not just a note in text);
  (c) activity_engine._record_fee does not re-book when the task already
      carries fee_realized_on_clearance=True.
"""
from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2 import axm_antigaming as ag
from sincor2.a2a_inbound import _now_ms, get_fabric, reset_fabric
from sincor2.a2a_inbound_market import create_task, submit_proof
from sincor2.onchain.stake_ledger import reset_stake_ledger, stake_ledger
from sincor2.wash_guard import POSTER_ACCEPT_DOMAIN, clear_wash_anomaly

ONE_AXM = 10 ** 18


@pytest.fixture
def iso(tmp_path, monkeypatch):
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    ag.reset_antigaming_store(path=str(tmp_path / "antigaming.json"))
    # Keep the bounty pool isolated too: settlement paths spend pool
    # allocations (allocated -> spent), draining the process-global pool
    # monotonically for later suites.  Fresh funded pool per test.
    from sincor2.a2a_bounty_pool import reset_bounty_pool
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=10000)
    monkeypatch.setenv("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", "1000")
    import sincor2.treasury_inflow as treasury_inflow
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "ledger.jsonl")
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


def _flagged_proof():
    """Create a flagged settlement; returns (poster_key, task_id, proof)."""
    poster_key = _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    task = create_task("gig", bounty_axm=10.0, poster_id="ext-poster")
    task_id = task["task_id"]
    fabric = get_fabric()
    now = _now_ms()
    with fabric.lock:
        t = fabric.tasks[task_id]
        t["state"] = "assigned"
        t["assigned_to"] = "ext-worker"
        t["assigned_at"] = now - 5_000
        t["winning_bid_axm"] = 10.0
        fabric.bids[f"bid_{task_id}_ext-worker"] = {
            "bid_id": f"bid_{task_id}_ext-worker", "task_id": task_id,
            "agent_id": "ext-worker", "revealed": True,
        }
    receipt = "0x" + "ab" * 32
    proof = submit_proof(task_id, "ext-worker", receipt)
    assert (proof.get("wash_anomaly") or {}).get("anomalous") is True
    return poster_key, task_id, proof


def _sign_acceptance(key, task_id, proof_id, receipt):
    message = "\n".join([POSTER_ACCEPT_DOMAIN, task_id, proof_id, receipt])
    return Account.sign_message(
        encode_defunct(text=message), private_key=key.key).signature.hex()


def test_c2_clearance_appears_exactly_once_in_summary(iso):
    """(a) 0.5 AXM projected at settlement, cleared -> 0.5 realized,
    0.0 projected, no double count in ledger_summary_24h."""
    import sincor2.treasury_inflow as ti
    from sincor2 import activity_engine as eng

    poster_key, task_id, proof = _flagged_proof()
    fabric = get_fabric()
    task = fabric.tasks[task_id]

    # Settlement path: engine books the fee PROJECTED (quarantined).
    rec = {"task_key": "c2", "fee_recorded": False}
    fee = eng._record_fee(task, rec, {})
    assert fee == pytest.approx(0.5)  # 5% of 10.0 AXM
    mid = ti.ledger_summary_24h()
    assert mid["usd_total_24h"] == pytest.approx(0.5)
    assert mid["usd_projected_24h"] == pytest.approx(0.5)

    # Poster clears the flag -> fee re-booked as realized.
    sig = _sign_acceptance(poster_key, task_id,
                           proof["proof_id"], proof["receipt_hash"])
    result = clear_wash_anomaly(task_id, proof["proof_id"], sig)
    assert result["cleared"] is True
    assert result["fee_realized_axm"] == pytest.approx(0.5)

    summary = ti.ledger_summary_24h()
    assert summary["usd_total_24h"] == pytest.approx(0.5), summary
    assert summary["usd_realized_24h"] == pytest.approx(0.5), summary
    assert summary["usd_projected_24h"] == pytest.approx(0.0), summary
    assert summary["by_asset"].get("AXM") == pytest.approx(0.5), summary


def test_c2_original_projected_event_mechanically_superseded(iso):
    """(b) The realized re-booking carries a supersedes pointer to the
    original projected event's id — a mechanical supersession, not text."""
    import sincor2.treasury_inflow as ti
    from sincor2 import activity_engine as eng

    poster_key, task_id, proof = _flagged_proof()
    fabric = get_fabric()
    task = fabric.tasks[task_id]
    eng._record_fee(task, {"task_key": "c2", "fee_recorded": False}, {})

    sig = _sign_acceptance(poster_key, task_id,
                           proof["proof_id"], proof["receipt_hash"])
    assert clear_wash_anomaly(task_id, proof["proof_id"], sig)["cleared"]

    events = [e.to_dict() for e in ti._read_ledger_events()]
    projected = [e for e in events if e.get("projected")]
    realized = [e for e in events if not e.get("projected")]
    assert len(projected) == 1 and len(realized) == 1
    proj_id = projected[0].get("event_id")
    assert proj_id, "projected booking must carry a stable event id"
    assert realized[0].get("supersedes") == proj_id, (
        "realized re-booking must point at the projected event it supersedes")


def test_c2_record_fee_guard_no_rebook_after_clearance(iso, monkeypatch):
    """(c) _record_fee must not re-book when the task already has
    fee_realized_on_clearance=True (fee booked by clear_wash_anomaly)."""
    from sincor2 import activity_engine as eng
    from sincor2 import treasury_settlement as ts

    calls = []
    monkeypatch.setattr(
        ts, "record_inflow",
        lambda amount, **kw: calls.append((amount, kw)) or None)

    task = {
        "task_id": "tsk_cleared",
        "payout_axm": 20.0,
        "wash_anomaly": {
            "anomalous": False,
            "cleared_by_acceptance": True,
            "fee_realized_on_clearance": True,
        },
    }
    rec = {"task_key": "cleared", "fee_recorded": False}
    eng._record_fee(task, rec, {})
    assert rec["fee_recorded"] is True
    assert calls == [], "no second booking may happen after clearance re-book"
