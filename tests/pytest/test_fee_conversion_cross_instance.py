"""W-202: ConversionLedger cross-instance lost-update regression tests.

Two ConversionLedger instances on the same path (e.g. the fee-event
listener and the executor in separate processes) must never lose each
other's writes. Before the fix each instance loaded once in __init__
and rewrote its whole stale snapshot on every save, so the second
writer silently erased the first writer's obligations.
"""

import json

from sincor2.onchain.fee_conversion_executor import (
    ConversionLedger,
    STATUS_PENDING,
    STATUS_QUOTED,
)


def _path(tmp_path):
    return str(tmp_path / "ledger.json")


def _on_disk(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["obligations"]


def test_two_instances_interleaved_records_both_survive(tmp_path):
    path = _path(tmp_path)
    a = ConversionLedger(path)
    b = ConversionLedger(path)
    a.record("ob-a", "AXM", 100)
    b.record("ob-b", "AXM", 200)
    a.record("ob-c", "SINC", 300)
    assert set(_on_disk(path)) == {"ob-a", "ob-b", "ob-c"}
    fresh = ConversionLedger(path)
    assert fresh.get("ob-a")["amount_wei"] == "100"
    assert fresh.get("ob-b")["amount_wei"] == "200"
    assert fresh.get("ob-c")["amount_wei"] == "300"


def test_stale_instance_record_does_not_drop_other_instance(tmp_path):
    path = _path(tmp_path)
    stale = ConversionLedger(path)  # constructed before any writes
    other = ConversionLedger(path)
    other.record("ob-other", "AXM", 111)
    # stale still holds its empty __init__ snapshot; its save must not
    # erase ob-other.
    stale.record("ob-stale", "AXM", 222)
    assert set(_on_disk(path)) == {"ob-other", "ob-stale"}


def test_transition_from_stale_instance_preserves_other_records(tmp_path):
    path = _path(tmp_path)
    a = ConversionLedger(path)
    a.record("ob-1", "AXM", 100)
    b = ConversionLedger(path)
    b.record("ob-2", "AXM", 200)
    # a never saw ob-2; transitioning ob-1 must not drop ob-2.
    a.transition("ob-1", STATUS_QUOTED)
    obs = _on_disk(path)
    assert set(obs) == {"ob-1", "ob-2"}
    assert obs["ob-1"]["status"] == STATUS_QUOTED
    assert obs["ob-2"]["status"] == STATUS_PENDING


def test_add_tx_from_stale_instance_preserves_other_records(tmp_path):
    path = _path(tmp_path)
    a = ConversionLedger(path)
    a.record("ob-1", "AXM", 100)
    b = ConversionLedger(path)
    b.record("ob-2", "AXM", 200)
    a.add_tx("ob-1", "swap", "0xdead")
    obs = _on_disk(path)
    assert set(obs) == {"ob-1", "ob-2"}
    assert [t["tx_hash"] for t in obs["ob-1"]["txs"]] == ["0xdead"]


def test_get_and_pending_see_other_instance_writes(tmp_path):
    path = _path(tmp_path)
    a = ConversionLedger(path)
    b = ConversionLedger(path)
    b.record("ob-b", "AXM", 200)
    # a's snapshot predates ob-b; reads must reload, not serve stale.
    assert a.get("ob-b") is not None
    assert a.get("ob-b")["amount_wei"] == "200"
    assert [ob["id"] for ob in a.pending()] == ["ob-b"]


def test_interleaved_transitions_history_accumulates(tmp_path):
    path = _path(tmp_path)
    a = ConversionLedger(path)
    a.record("ob-1", "AXM", 100)
    b = ConversionLedger(path)
    a.transition("ob-1", STATUS_QUOTED)
    b.transition("ob-1", "approved")
    obs = _on_disk(path)
    assert [h["status"] for h in obs["ob-1"]["history"]] == [
        "pending", "quoted", "approved",
    ]
    assert obs["ob-1"]["status"] == "approved"


def test_record_idempotent_across_instances(tmp_path):
    path = _path(tmp_path)
    a = ConversionLedger(path)
    b = ConversionLedger(path)
    a.record("ob-1", "AXM", 100, {"tx_hash": "0xabc"})
    again = b.record("ob-1", "AXM", 100, {"tx_hash": "0xabc"})
    assert again["status"] == STATUS_PENDING
    obs = _on_disk(path)
    assert list(obs) == ["ob-1"]
    assert len(obs["ob-1"]["history"]) == 1
