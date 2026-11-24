"""W-206 — AuditTrail cross-instance hash-chain fork (persistence audit).

Two AuditTrail instances sharing one log path must produce a single
unbroken hash chain: seq strictly increasing with no duplicates, and
each entry's prev_hash equal to the previous entry's entry_hash.

On the base, record() builds the entry from the cached self._seq /
self._head (recovered once in __init__) before taking the lock, so two
pre-constructed instances fork the chain (duplicate seq, same prev).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sincor2.obs_skus import audit_trail
from sincor2.obs_skus.audit_trail import AuditTrail


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("SINCOR_AUDIT_TRAIL_ENABLED", "SINCOR_AUDIT_SIGNING_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SINCOR_AUDIT_TRAIL_ENABLED", "1")
    audit_trail._default_trail = None
    yield
    audit_trail._default_trail = None


def _read(path: Path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _assert_single_chain(entries):
    seqs = [e["seq"] for e in entries]
    assert seqs == list(range(len(entries))), f"seq not strictly increasing/unique: {seqs}"
    prev = "00" * 32
    for e in entries:
        assert e["prev_hash"] == prev, (
            f"forked chain at seq {e['seq']}: prev_hash {e['prev_hash']} != {prev}")
        prev = e["entry_hash"]


def test_two_preconstructed_instances_interleaved_single_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = AuditTrail(log_path=path)
    b = AuditTrail(log_path=path)
    for i in range(5):
        a.record(agent_id="agent-a", action=f"a:{i}")
        b.record(agent_id="agent-b", action=f"b:{i}")
    entries = _read(path)
    assert len(entries) == 10
    _assert_single_chain(entries)
    fresh = AuditTrail(log_path=path)
    report = fresh.verify_chain()
    assert report["ok"], report["failures"]
    assert report["entries"] == 10


def test_second_instance_after_first_wrote_continues_chain(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = AuditTrail(log_path=path)
    a.record(agent_id="agent-a", action="first")
    # b constructed after a wrote: recovers head correctly even on base.
    # Then a (stale) writes again — must not fork.
    b = AuditTrail(log_path=path)
    b.record(agent_id="agent-b", action="second")
    a.record(agent_id="agent-a", action="third")
    entries = _read(path)
    assert len(entries) == 3
    _assert_single_chain(entries)
    assert AuditTrail(log_path=path).verify_chain()["ok"]


def test_stale_instance_head_hash_reflects_chain_after_record(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = AuditTrail(log_path=path)
    b = AuditTrail(log_path=path)
    ea = a.record(agent_id="agent-a", action="one")
    eb = b.record(agent_id="agent-b", action="two")
    assert eb["seq"] == ea["seq"] + 1
    assert eb["prev_hash"] == ea["entry_hash"]
    # After b's record, a's next record chains onto b's head.
    ec = a.record(agent_id="agent-a", action="three")
    assert ec["seq"] == eb["seq"] + 1
    assert ec["prev_hash"] == eb["entry_hash"]
    assert a.head_hash() == ec["entry_hash"]
