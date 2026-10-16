"""W-89 wash-anomaly guard tests — adversarial, no mocks of the guard itself.

Covers, for real:
  1. Wash settlement caught: external poster+worker, sole bidder, instant
     settle, bare receipt -> flagged; settlement still completes (never bricks).
  2. Internal (cohort) winners exempt: zero flags on engine-pattern traffic.
  3. Legit external competitive (multi-bidder, bare receipt) -> clean.
  4. Legit external with bound deliverable -> clean even sole+instant.
  5. Self-deal wallet (poster == winner wallet) flags alone.
  6. Poster EIP-191 acceptance clears the flag.
  7. Pair concentration corroborates after repeated pair wins.
  8. Fee quarantine: flagged task books projected, never realized.
  9. False-positive analysis: realistic engine traffic -> zero flags.
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
    verify_poster_acceptance,
)

ONE_AXM = 10 ** 18


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Isolated fabric + stake ledger + antigaming store + bounty pool."""
    reset_fabric()
    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    ag.reset_antigaming_store(path=str(tmp_path / "antigaming.json"))
    # W-89 C2: settle/allocate paths drain the process-global bounty pool
    # monotonically (allocated -> spent).  Without a reset, a suite that
    # runs after this one inherits a drained pool and its pool-backed
    # engine tests fail spuriously.  Fresh funded pool per test.
    from sincor2.a2a_bounty_pool import reset_bounty_pool
    reset_bounty_pool(path=str(tmp_path / "pool.json"), reserve_axm=10000)
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
    """Create a task and force it into assigned state (test-only)."""
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


def test_wash_caught_and_settlement_not_bricked(iso):
    _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    task_id = _mk_assigned_task("ext-poster", "ext-worker", assigned_ago_ms=5_000)
    _add_bid(task_id, "ext-worker")  # sole bidder
    proof = submit_proof(task_id, "ext-worker", "0x" + "ab" * 32)
    verdict = proof.get("wash_anomaly") or {}
    assert verdict.get("anomalous") is True
    names = {s["name"] for s in verdict.get("signals", [])}
    assert "unbound_no_acceptance" in names
    assert "sole_bidder" in names
    assert "instant_settle" in names
    # Never bricks: settlement still completes, payout still stages.
    assert proof["status"] in ("paid", "payout_staged", "rejected")
    fabric = get_fabric()
    assert fabric.tasks[task_id]["state"] in ("settled", "failed")
    assert fabric.tasks[task_id].get("wash_anomaly", {}).get("anomalous") is True


def test_internal_winner_exempt(iso):
    _mk_agent("sincor-liveness-01", origin="internal")
    _mk_agent("ext-poster", origin="external")
    task_id = _mk_assigned_task("ext-poster", "sincor-liveness-01",
                               assigned_ago_ms=5_000)
    _add_bid(task_id, "sincor-liveness-01")  # sole bidder + instant, but internal
    proof = submit_proof(task_id, "sincor-liveness-01", "0x" + "cd" * 32)
    verdict = proof.get("wash_anomaly") or {}
    assert verdict.get("anomalous") is False
    assert verdict.get("exempt") is True


def test_legit_external_competitive_clean(iso):
    _mk_agent("ext-poster", origin="external")
    _mk_agent("worker-a", origin="external")
    _mk_agent("worker-b", origin="external")
    _mk_agent("worker-c", origin="external")
    task_id = _mk_assigned_task("ext-poster", "worker-a",
                               assigned_ago_ms=60_000)  # 60s: real work time, not stale
    for w in ("worker-a", "worker-b", "worker-c"):
        _add_bid(task_id, w)
    proof = submit_proof(task_id, "worker-a", "0x" + "ef" * 32)
    verdict = proof.get("wash_anomaly") or {}
    assert verdict.get("anomalous") is False


def test_legit_external_bound_deliverable_clean(iso):
    _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    task_id = _mk_assigned_task("ext-poster", "ext-worker", assigned_ago_ms=5_000)
    _add_bid(task_id, "ext-worker")  # sole + instant, but BOUND
    proof = submit_proof(
        task_id, "ext-worker", "0x" + "12" * 32,
        deliverable="real work output",
        deliverable_uri="ipfs://bafyworkoutput",
    )
    verdict = proof.get("wash_anomaly") or {}
    assert verdict.get("anomalous") is False
    assert proof.get("content_bound") is True


def test_self_deal_wallet_flags_alone(iso):
    shared = "0x" + "99" * 20
    _mk_agent("poster-self", wallet=shared, origin="external")
    _mk_agent("worker-self", wallet=shared, origin="external")
    task_id = _mk_assigned_task("poster-self", "worker-self",
                               assigned_ago_ms=60_000)  # 60s: not instant, not stale
    _add_bid(task_id, "worker-self")
    _add_bid(task_id, "other-bidder")
    _mk_agent("other-bidder", origin="external")
    proof = submit_proof(task_id, "worker-self", "0x" + "34" * 32)
    verdict = proof.get("wash_anomaly") or {}
    assert verdict.get("anomalous") is True
    assert "self_deal_wallet" in {s["name"] for s in verdict["signals"]}


def test_poster_acceptance_clears(iso):
    key = _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    task_id = _mk_assigned_task("ext-poster", "ext-worker", assigned_ago_ms=5_000)
    _add_bid(task_id, "ext-worker")
    fabric = get_fabric()
    task = fabric.tasks[task_id]
    # Direct unit check of the acceptance path.
    proof_id = "prf_testaccept"
    receipt = "0x" + "56" * 32
    message = "\n".join([POSTER_ACCEPT_DOMAIN, task_id, proof_id, receipt])
    sig = Account.sign_message(
        encode_defunct(text=message), private_key=key.key).signature.hex()
    assert verify_poster_acceptance(task, proof_id, receipt, sig,
                                   fabric=fabric) is True
    # And the full assessment clears with a valid acceptance.
    verdict = assess_settlement(
        task, "ext-worker", content_bound=False, receipt_hash=receipt,
        proof_id=proof_id, poster_acceptance_sig=sig,
        fabric=fabric, now_ms=_now_ms())
    assert verdict.get("anomalous") is False
    assert verdict.get("cleared_by_acceptance") is True
    # A bad signature does not clear.
    bad = assess_settlement(
        task, "ext-worker", content_bound=False, receipt_hash=receipt,
        proof_id=proof_id, poster_acceptance_sig="0x" + "00" * 65,
        fabric=fabric, now_ms=_now_ms())
    assert bad.get("anomalous") is True


def test_pair_concentration_corroborates(iso, monkeypatch):
    monkeypatch.setenv("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", "100")
    _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    store = ag.antigaming_store()
    # Simulate a standing collusion pair: 3 wins already in the window.
    for _ in range(3):
        ag.record_auction_close("ext-poster", "ext-worker")
    task_id = _mk_assigned_task("ext-poster", "ext-worker",
                               assigned_ago_ms=5_000)  # instant + pair
    _add_bid(task_id, "ext-worker")
    _add_bid(task_id, "decoy")
    _mk_agent("decoy", origin="external")
    proof = submit_proof(task_id, "ext-worker", "0x" + "78" * 32)
    verdict = proof.get("wash_anomaly") or {}
    assert verdict.get("anomalous") is True
    assert "pair_concentration" in {s["name"] for s in verdict["signals"]}


def test_fee_quarantine_on_flagged_task(iso, tmp_path, monkeypatch):
    """_record_fee books PROJECTED (never realized) for flagged tasks."""
    from sincor2 import activity_engine as eng
    from sincor2 import treasury_settlement as ts
    events = []
    monkeypatch.setattr(
        ts, "record_inflow",
        lambda amount, **kw: events.append(kw) or _FakeEvent(amount, kw))
    task = {
        "task_id": "tsk_q", "payout_axm": 20.0,
        "wash_anomaly": {"anomalous": True,
                         "signals": [{"name": "sole_bidder"}]},
    }
    rec = {"task_key": "q", "fee_recorded": False}
    state = {}
    fee = eng._record_fee(task, rec, state)
    assert fee == pytest.approx(1.0)
    assert rec["fee_recorded"] is True
    assert rec["fee_projected"] is True
    assert events and events[0].get("projected") is True
    # Unflagged control: realized.
    events.clear()
    task2 = {"task_id": "tsk_ok", "payout_axm": 20.0}
    rec2 = {"task_key": "ok", "fee_recorded": False}
    eng._record_fee(task2, rec2, {})
    assert events and events[0].get("projected") is False


class _FakeEvent:
    def __init__(self, amount, kw):
        self.amount = amount
        self.kw = kw

    def to_dict(self):
        return {"amount": self.amount, **self.kw}


def test_false_positive_analysis_engine_traffic(iso):
    """Realistic engine-pattern traffic: internal winners, fast settles,
    bare receipts -> zero flags.  This is the FP analysis as an executable
    assertion."""
    _mk_agent("sincor-liveness-01", origin="internal")
    _mk_agent("sincor-liveness-02", origin="internal")
    _mk_agent("axm-activity-engine", origin="internal")
    flags = 0
    for i in range(12):
        winner = "sincor-liveness-01" if i % 2 == 0 else "sincor-liveness-02"
        task_id = _mk_assigned_task("axm-activity-engine", winner,
                                   assigned_ago_ms=30_000)
        _add_bid(task_id, "sincor-liveness-01")
        _add_bid(task_id, "sincor-liveness-02")
        proof = submit_proof(task_id, winner, "0x" + f"{i:02x}" * 32)
        if (proof.get("wash_anomaly") or {}).get("anomalous"):
            flags += 1
    assert flags == 0


# ---------------------------------------------------------------------------
# Poster-acceptance clearance tests (fix-up: verify_poster_acceptance was
# dead code — submit_proof never passed poster_acceptance_sig and no API
# surface accepted one).
# ---------------------------------------------------------------------------

def _sign_acceptance(key, task_id, proof_id, receipt):
    message = "\n".join([POSTER_ACCEPT_DOMAIN, task_id, proof_id, receipt])
    return Account.sign_message(
        encode_defunct(text=message), private_key=key.key).signature.hex()


def _flagged_proof(iso_unused=None):
    """Create a flagged settlement; returns (poster_key, task_id, proof)."""
    poster_key = _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    task_id = _mk_assigned_task("ext-poster", "ext-worker",
                               assigned_ago_ms=5_000)
    _add_bid(task_id, "ext-worker")  # sole bidder
    receipt = "0x" + "ab" * 32
    proof = submit_proof(task_id, "ext-worker", receipt)
    assert (proof.get("wash_anomaly") or {}).get("anomalous") is True
    return poster_key, task_id, proof


def test_clearance_valid_clears_and_rebooks_realized(iso, tmp_path, monkeypatch):
    import sincor2.treasury_inflow as treasury_inflow
    from sincor2.wash_guard import clear_wash_anomaly
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "ledger.jsonl")
    poster_key, task_id, proof = _flagged_proof()
    proof_id = proof["proof_id"]
    receipt = proof["receipt_hash"]
    # Simulate the engine having booked the fee projected at quarantine.
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["wash_fee_projected"] = {"fee_axm": 0.5}
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    result = clear_wash_anomaly(task_id, proof_id, sig)
    assert result["cleared"] is True
    assert result["fee_realized_axm"] == 0.5
    stamp = fabric.tasks[task_id]["wash_anomaly"]
    assert stamp["anomalous"] is False
    assert stamp["cleared_by_acceptance"] is True
    assert stamp["fee_realized_on_clearance"] is True
    # Signals kept as history.
    assert len(stamp["signals"]) > 0
    # Ledger: exactly one realized event, superseding note, no double.
    events = treasury_inflow._read_ledger_events()
    realized = [e for e in events if not e.projected]
    assert len(realized) == 1
    assert realized[0].amount == 0.5
    assert "supersedes projected" in realized[0].note


def test_clearance_invalid_sig_keeps_flag(iso):
    from sincor2.wash_guard import clear_wash_anomaly
    poster_key, task_id, proof = _flagged_proof()
    proof_id = proof["proof_id"]
    receipt = proof["receipt_hash"]
    good = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    # Tamper: flip the last hex digit.
    bad = good[:-1] + ("0" if good[-1] != "0" else "1")
    result = clear_wash_anomaly(task_id, proof_id, bad)
    assert result["cleared"] is False
    assert result["reason"] == "invalid acceptance"
    fabric = get_fabric()
    assert fabric.tasks[task_id]["wash_anomaly"]["anomalous"] is True


def test_clearance_wrong_task_replay_resisted(iso):
    from sincor2.wash_guard import clear_wash_anomaly
    poster_key, task_id_a, proof_a = _flagged_proof()
    # Second flagged settlement for the replay target.
    _mk_agent("ext-poster2", origin="external")
    task_id_b = _mk_assigned_task("ext-poster2", "ext-worker",
                                 assigned_ago_ms=5_000)
    _add_bid(task_id_b, "ext-worker")
    proof_b = submit_proof(task_id_b, "ext-worker", "0x" + "cd" * 32)
    assert (proof_b.get("wash_anomaly") or {}).get("anomalous") is True
    # Acceptance signed for task A must not clear task B.
    sig_a = _sign_acceptance(poster_key, task_id_a,
                            proof_a["proof_id"], proof_a["receipt_hash"])
    result = clear_wash_anomaly(task_id_b, proof_b["proof_id"], sig_a)
    assert result["cleared"] is False
    fabric = get_fabric()
    assert fabric.tasks[task_id_b]["wash_anomaly"]["anomalous"] is True


def test_clearance_non_poster_wallet_rejected(iso):
    from sincor2.wash_guard import clear_wash_anomaly
    poster_key, task_id, proof = _flagged_proof()
    proof_id = proof["proof_id"]
    receipt = proof["receipt_hash"]
    # Sign with the WINNER's key instead of the poster's.
    fabric = get_fabric()
    winner_key = Account.create()  # distinct from poster's registered wallet
    sig = _sign_acceptance(winner_key, task_id, proof_id, receipt)
    result = clear_wash_anomaly(task_id, proof_id, sig)
    assert result["cleared"] is False
    assert fabric.tasks[task_id]["wash_anomaly"]["anomalous"] is True


def test_clearance_idempotent(iso, tmp_path, monkeypatch):
    import sincor2.treasury_inflow as treasury_inflow
    from sincor2.wash_guard import clear_wash_anomaly
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "ledger.jsonl")
    poster_key, task_id, proof = _flagged_proof()
    proof_id = proof["proof_id"]
    receipt = proof["receipt_hash"]
    fabric = get_fabric()
    with fabric.lock:
        fabric.tasks[task_id]["wash_fee_projected"] = {"fee_axm": 0.5}
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    first = clear_wash_anomaly(task_id, proof_id, sig)
    assert first["cleared"] is True
    second = clear_wash_anomaly(task_id, proof_id, sig)
    assert second["cleared"] is False
    assert second["reason"] == "not flagged"
    # Exactly one realized booking — no double on re-clear.
    events = treasury_inflow._read_ledger_events()
    assert len([e for e in events if not e.projected]) == 1


def test_clearance_no_projected_no_rebook(iso, tmp_path, monkeypatch):
    import sincor2.treasury_inflow as treasury_inflow
    from sincor2.wash_guard import clear_wash_anomaly
    monkeypatch.setattr(treasury_inflow, "_LEDGER_PATH",
                        tmp_path / "ledger.jsonl")
    poster_key, task_id, proof = _flagged_proof()
    proof_id = proof["proof_id"]
    receipt = proof["receipt_hash"]
    # No wash_fee_projected stamp: engine hasn't booked (or API path).
    sig = _sign_acceptance(poster_key, task_id, proof_id, receipt)
    result = clear_wash_anomaly(task_id, proof_id, sig)
    assert result["cleared"] is True
    assert result["fee_realized_axm"] is None
    # Nothing booked — the engine will book realized normally on its cycle.
    assert treasury_inflow._read_ledger_events() == []


def test_clearance_unknown_task_no_raise(iso):
    from sincor2.wash_guard import clear_wash_anomaly
    result = clear_wash_anomaly("nope", "prf_nope", "0x" + "00" * 65)
    assert result["cleared"] is False
    assert result["reason"] == "unknown task"


def test_submit_proof_acceptance_sig_threaded(iso):
    """submit_proof accepts the poster_acceptance_sig kwarg (new API surface)
    and threads it to the guard without breaking existing callers.  A valid
    pre-signed acceptance is impossible here (proof_id is server-generated),
    so this asserts the kwarg flows without error; end-to-end valid
    acceptance is covered by the clearance tests above and the existing
    assess-level test."""
    _mk_agent("ext-poster", origin="external")
    _mk_agent("ext-worker", origin="external")
    task_id = _mk_assigned_task("ext-poster", "ext-worker",
                               assigned_ago_ms=5_000)
    _add_bid(task_id, "ext-worker")
    receipt = "0x" + "ef" * 32
    proof = submit_proof(task_id, "ext-worker", receipt,
                         poster_acceptance_sig="0x" + "00" * 65)
    # Invalid sig -> still assessed (flagged), never raises, kwarg accepted.
    assert (proof.get("wash_anomaly") or {}).get("anomalous") is True
    assert proof["status"] in ("paid", "payout_staged", "rejected")


@pytest.mark.parametrize("exc,status", [
    (ValueError("bad input"), 400),
    (KeyError("unknown task"), 404),
    (PermissionError("not the winner"), 403),
    (RuntimeError("already settled"), 409),
])
def test_v1_proofs_error_mapping_restored(iso, monkeypatch, exc, status):
    """Regression guard for the 602d3d9 splice: v1_proofs must keep its full
    error mapping (ValueError->400, KeyError->404, PermissionError->403,
    RuntimeError->409) after the clear-wash-flag route was moved below it.
    Drives the real HTTP route with submit_proof stubbed to raise."""
    from flask import Flask
    from sincor2.a2a_inbound import register as register_inbound
    import sincor2.a2a_inbound_market as mkt

    def _raise(*a, **k):
        raise exc

    monkeypatch.setattr(mkt, "submit_proof", _raise)
    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.post("/v1/a2a/proofs",
                       json={"task_id": "t", "agent_id": "a",
                             "receipt_hash": "0x" + "ab" * 32})
    assert resp.status_code == status, (
        f"{type(exc).__name__} mapped to {resp.status_code}, expected {status}")


def test_v1_clear_wash_flag_route_mounted(iso):
    """The moved clear-wash-flag route still mounts and never bricks: an
    unknown task returns cleared=False with HTTP 200, not a 500."""
    from flask import Flask
    from sincor2.a2a_inbound import register as register_inbound

    app = Flask(__name__)
    register_inbound(app)
    app.config["TESTING"] = True
    client = app.test_client()
    resp = client.post("/v1/a2a/proofs/clear-wash-flag",
                       json={"task_id": "nope", "proof_id": "nope",
                             "poster_acceptance_sig": "0x00"})
    assert resp.status_code == 200
    assert resp.get_json().get("cleared") is False
