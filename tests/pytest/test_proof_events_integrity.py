"""SH-1 — ProofEventStore load-time integrity repair.

A store file containing a duplicate event_id or a structurally
invalid record must no longer brick the proof wall: load quarantines
each bad/duplicate record to the sidecar
``<store>.quarantine.jsonl`` (appended, with a reason) and rebuilds
in-memory state from the valid remainder. A clean store loads
unchanged and writes no quarantine file. Corrupt JSON (an
unparseable complete line / unparseable whole file) still fails
closed with ProofStoreError — quarantine applies to per-record
*structural* problems only.
"""
from __future__ import annotations

import json

import pytest

from sincor2.proof_events import ProofEventStore, ProofStoreError

TX_A = "0x" + "aa" * 32
TX_B = "0x" + "bb" * 32
TX_C = "0x" + "cc" * 32


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


def _write(path, records):
    """Write records (dicts dumped as JSON, strings verbatim), one per line."""
    lines = []
    for rec in records:
        lines.append(rec if isinstance(rec, str) else json.dumps(rec))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quarantine_path(path):
    return path.parent / (path.name + ".quarantine.jsonl")


def _quarantine_entries(path):
    q = _quarantine_path(path)
    if not q.exists():
        return []
    return [json.loads(line) for line in q.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_duplicate_event_id_loads_first_and_quarantines_dup(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    _write(path, [
        _event("evt-1", TX_A, label="first"),
        _event("evt-2", TX_B),
        _event("evt-1", TX_C, label="duplicate"),
    ])
    store = ProofEventStore(path)  # must not raise
    assert store.totals()["total"] == 2
    events = store.list_events(limit=10)
    by_id = {e["event_id"]: e for e in events}
    assert by_id["evt-1"]["label"] == "first"  # first occurrence kept
    assert by_id["evt-1"]["tx_hash"] == TX_A
    entries = _quarantine_entries(path)
    assert len(entries) == 1
    assert entries[0]["reason"]
    assert "duplicate" in entries[0]["reason"]
    assert entries[0]["record"]["event_id"] == "evt-1" or \
        "evt-1" in entries[0]["raw"]


def test_structurally_invalid_record_quarantined_valid_load(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    bad = _event("evt-bad", TX_B)
    del bad["tx_hash"]  # missing required field -> structurally invalid
    bad2 = _event("evt-bad2", "0xnothex")  # bad tx_hash shape
    _write(path, [_event("evt-1", TX_A), bad, bad2, _event("evt-2", TX_C)])
    store = ProofEventStore(path)  # must not raise
    assert store.totals()["total"] == 2
    assert sorted(e["event_id"] for e in store.list_events(limit=10)) == [
        "evt-1", "evt-2"]
    entries = _quarantine_entries(path)
    assert len(entries) == 2
    reasons = " ".join(e["reason"] for e in entries)
    assert reasons  # every quarantined record carries a reason


def test_non_object_record_quarantined(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    _write(path, [_event("evt-1", TX_A), '["not", "an", "object"]'])
    store = ProofEventStore(path)
    assert store.totals()["total"] == 1
    assert len(_quarantine_entries(path)) == 1


def test_clean_store_loads_unchanged_and_no_quarantine(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    _write(path, [_event("evt-1", TX_A), _event("evt-2", TX_B)])
    before = path.read_bytes()
    store = ProofEventStore(path)
    assert store.totals()["total"] == 2
    assert [e["event_id"] for e in store.list_events(limit=10)] == [
        "evt-2", "evt-1"]
    assert path.read_bytes() == before  # clean file never rewritten
    assert not _quarantine_path(path).exists()


def test_unparseable_file_still_raises(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    # Complete (newline-terminated) line that is not JSON at all:
    # corrupt-JSON fails closed, it is NOT quarantined.
    _write(path, [_event("evt-1", TX_A), '{"event_id": "broken", garbage'])
    store = ProofEventStore(path)
    with pytest.raises(ProofStoreError):
        store.totals()
    with pytest.raises(ProofStoreError):
        store.list_events()


def test_quarantine_is_append_only_across_reloads(tmp_path):
    path = tmp_path / "proof_events.jsonl"
    _write(path, [_event("evt-1", TX_A), _event("evt-1", TX_B)])
    store = ProofEventStore(path)
    assert store.totals()["total"] == 1
    # A second fresh load must not duplicate the quarantine entry.
    fresh = ProofEventStore(path)
    assert fresh.totals()["total"] == 1
    assert len(_quarantine_entries(path)) == 1
