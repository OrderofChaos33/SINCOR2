"""W-196 — RecoveryLedger cross-instance lost updates.

Two instances constructed before either mutates hold stale snapshots;
a stale instance's _save() rewrites the whole JSON file, erasing the
other instance's sponsorships and resetting strike counts (strike
ladder bypassed). Mutators must reload-mutate-save under flock, and
reads must not serve a stale snapshot.
"""
from __future__ import annotations

import json

from sincor2.recovery import RecoveryLedger

W1 = "0x" + "11" * 20
W2 = "0x" + "22" * 20


def _spon(event_id, agent):
    return {"bankruptcy_event_id": event_id, "agent_id": agent,
            "wallet": W1, "strike": 1, "cap_wei": "100"}


def _disk(path):
    try:
        return json.loads(open(path, encoding="utf-8").read())
    except FileNotFoundError:
        return {"wallets": {}, "events": []}


def test_two_preconstructed_instances_different_wallets_both_survive(tmp_path):
    path = str(tmp_path / "recovery.json")
    a = RecoveryLedger(path=path)
    b = RecoveryLedger(path=path)  # stale: constructed before A records
    a.record_sponsorship(W1, _spon("evt-a", "agent-a"))
    b.record_sponsorship(W2, {**_spon("evt-b", "agent-b"), "wallet": W2})
    raw = _disk(path)
    assert W1 in raw["wallets"], f"a's wallet erased, disk={list(raw['wallets'])}"
    assert W2 in raw["wallets"], f"b's wallet missing, disk={list(raw['wallets'])}"
    fresh = RecoveryLedger(path=path)
    assert fresh.wallet_state(W1)["strikes"] == 1
    assert fresh.wallet_state(W2)["strikes"] == 1
    assert fresh.find_by_event("evt-a") is not None
    assert fresh.find_by_event("evt-b") is not None


def test_same_wallet_two_instances_strikes_accumulate(tmp_path):
    path = str(tmp_path / "recovery.json")
    a = RecoveryLedger(path=path)
    b = RecoveryLedger(path=path)
    a.record_sponsorship(W1, _spon("evt-1", "agent-1"))
    b.record_sponsorship(W1, _spon("evt-2", "agent-2"))
    fresh = RecoveryLedger(path=path)
    state = fresh.wallet_state(W1)
    assert state["strikes"] == 2, f"strike ladder bypassed: strikes={state['strikes']}"
    assert len(state["sponsorships"]) == 2
    assert fresh.find_by_event("evt-1") is not None
    assert fresh.find_by_event("evt-2") is not None


def test_interleaved_mutations_both_survive(tmp_path):
    path = str(tmp_path / "recovery.json")
    a = RecoveryLedger(path=path)
    b = RecoveryLedger(path=path)
    a.record_sponsorship(W1, _spon("evt-1", "agent-1"))
    b.record_sponsorship(W2, {**_spon("evt-2", "agent-2"), "wallet": W2})
    a.record_sponsorship(W2, {**_spon("evt-3", "agent-3"), "wallet": W2})
    fresh = RecoveryLedger(path=path)
    assert fresh.wallet_state(W1)["strikes"] == 1
    assert fresh.wallet_state(W2)["strikes"] == 2
    for evt in ("evt-1", "evt-2", "evt-3"):
        assert fresh.find_by_event(evt) is not None, f"{evt} lost"


def test_stale_instance_reads_reflect_other_instances_writes(tmp_path):
    path = str(tmp_path / "recovery.json")
    a = RecoveryLedger(path=path)
    b = RecoveryLedger(path=path)  # stale
    a.record_sponsorship(W1, _spon("evt-1", "agent-1"))
    # b's reads must reflect a's writes
    state = b.wallet_state(W1)
    assert state["strikes"] == 1, f"stale wallet_state {state}"
    assert len(state["sponsorships"]) == 1
    found = b.find_by_event("evt-1")
    assert found is not None, "stale find_by_event returned None"
    assert found["agent_id"] == "agent-1"


def test_events_from_both_instances_survive(tmp_path):
    path = str(tmp_path / "recovery.json")
    a = RecoveryLedger(path=path)
    b = RecoveryLedger(path=path)
    a.record_sponsorship(W1, _spon("evt-1", "agent-1"))
    b.record_sponsorship(W2, {**_spon("evt-2", "agent-2"), "wallet": W2})
    raw = _disk(path)
    kinds = [e.get("bankruptcy_event_id") for e in raw["events"]]
    assert "evt-1" in kinds and "evt-2" in kinds, f"events lost: {kinds}"
