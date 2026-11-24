"""W-204 — ProofLedger cross-instance lost update.

Two ProofLedger instances sharing one path must not drop each other's
entries: a stale instance's save must not erase entries recorded by
another instance since it loaded. Also: a stale instance must see
another instance's appends via read()/count().
"""
from __future__ import annotations

import json

from sincor2.defi.proof_ledger import KIND_TEST_RUN, ProofLedger


def test_two_instances_both_entries_survive(tmp_path):
    path = str(tmp_path / "proof_ledger.json")
    a = ProofLedger(path)
    b = ProofLedger(path)
    a.append("SKU-01", KIND_TEST_RUN, {"n": 1})
    b.append("SKU-02", KIND_TEST_RUN, {"n": 2})
    fresh = ProofLedger(path)
    skus = sorted(e["sku"] for e in fresh.read())
    assert skus == ["SKU-01", "SKU-02"]
    assert fresh.count() == 2


def test_stale_instance_sees_other_append(tmp_path):
    path = str(tmp_path / "proof_ledger.json")
    a = ProofLedger(path)
    b = ProofLedger(path)
    assert a.read() == []
    b.append("SKU-09", KIND_TEST_RUN, {"n": 9})
    assert [e["sku"] for e in a.read()] == ["SKU-09"]
    assert a.count() == 1


def test_interleaved_appends_all_survive(tmp_path):
    path = str(tmp_path / "proof_ledger.json")
    a = ProofLedger(path)
    b = ProofLedger(path)
    for i in range(3):
        a.append(f"A-{i}", KIND_TEST_RUN, {"i": i})
        b.append(f"B-{i}", KIND_TEST_RUN, {"i": i})
    fresh = ProofLedger(path)
    assert fresh.count() == 6
    on_disk = json.loads((tmp_path / "proof_ledger.json").read_text())
    assert len(on_disk["entries"]) == 6
