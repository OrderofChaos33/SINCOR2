"""W-212 — ProofEventStore cross-instance dedupe (hunt 65).

Two instances constructed before either records must not both accept
the same event_id: the duplicate on disk bricks a fresh instance's
_load() with ProofStoreError. Also: different ids from two instances
must both survive, and a stale instance must see appends by another
instance via list_events/totals.
"""
from __future__ import annotations

import json

import pytest

from sincor2 import proof_events
from sincor2.proof_events import ProofEventStore, ProofStoreError

TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32


def _event(event_id="evt-1", tx_hash=TX_A, **over):
    e = {
        "event_id": event_id,
        "source": "moebiusx_v3.1",
        "origin": "sincor_agent",
        "label": "MoebiusX trade",
        "detail": "SELL settled on Base — $8.24 USDC",
        "amount": "8.24",
        "asset": "USDC",
        "tx_hash": tx_hash,
        "occurred_at": "2026-11-14T15:27:09Z",
    }
    e.update(over)
    return e


def _lines_for(path, event_id):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec["event_id"] == event_id:
                out.append(rec)
    return out


def test_same_id_two_preconstructed_instances_only_one_wins(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    a = ProofEventStore(path)
    b = ProofEventStore(path)
    # Force both to load (cache) before either records — the hunt PoC.
    assert a.totals()["total"] == 0
    assert b.totals()["total"] == 0
    r_a = a.record_event(_event("evt-dup"))
    r_b = b.record_event(_event("evt-dup"))
    assert sorted([r_a, r_b]) == [False, True]
    assert len(_lines_for(path, "evt-dup")) == 1
    fresh = ProofEventStore(path)  # must not raise ProofStoreError
    assert fresh.totals()["total"] == 1
    assert [e["event_id"] for e in fresh.list_events()] == ["evt-dup"]


def test_different_ids_two_instances_both_survive(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    a = ProofEventStore(path)
    b = ProofEventStore(path)
    assert a.totals()["total"] == 0
    assert b.totals()["total"] == 0
    assert a.record_event(_event("evt-1", TX_A)) is True
    assert b.record_event(_event("evt-2", TX_B)) is True
    fresh = ProofEventStore(path)
    assert fresh.totals()["total"] == 2
    assert sorted(e["event_id"] for e in fresh.list_events(limit=10)) == ["evt-1", "evt-2"]


def test_stale_instance_sees_other_instances_append(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    a = ProofEventStore(path)
    b = ProofEventStore(path)
    # a loads/caches empty state first
    assert a.list_events() == []
    assert a.totals()["total"] == 0
    assert b.record_event(_event("evt-new", TX_B)) is True
    # a must not serve stale state
    events = a.list_events()
    assert [e["event_id"] for e in events] == ["evt-new"]
    assert a.totals()["total"] == 1
    assert a.totals()["by_origin"]["sincor_agent"] == 1
