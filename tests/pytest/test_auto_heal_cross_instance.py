"""W-213 — PersistentErrorTracker cross-instance lost updates (hunt 65).

Two instances constructed before either records hold stale snapshots;
a stale instance's _save() rewrites the whole JSON file, erasing the
other instance's records. Mutators must reload-mutate-save under flock,
and reads must not serve a stale snapshot.
"""
from __future__ import annotations

import json

from sincor2.resilience.auto_heal import (
    ErrorClassification,
    PersistentErrorTracker,
)


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start
    def __call__(self) -> float:
        return self.now
    def advance(self, seconds: float) -> None:
        self.now += seconds


def _keys_on_disk(path):
    try:
        raw = json.loads(open(path, encoding="utf-8").read())
    except FileNotFoundError:
        return {}
    return raw


def test_two_preconstructed_instances_both_records_survive(tmp_path):
    path = str(tmp_path / "errors.json")
    a = PersistentErrorTracker(path)
    b = PersistentErrorTracker(path)  # stale: constructed before A records
    a.record("HttpError", "/a", ErrorClassification.TRANSIENT, "from a")
    b.record("TimeoutError", "/b", ErrorClassification.TRANSIENT, "from b")
    # Both survive on disk
    raw = _keys_on_disk(path)
    assert "HttpError@/a" in raw, f"a's record erased, disk={list(raw)}"
    assert "TimeoutError@/b" in raw, f"b's record missing, disk={list(raw)}"
    # And in a fresh third instance
    fresh = PersistentErrorTracker(path)
    assert fresh.get("HttpError", "/a") is not None
    assert fresh.get("TimeoutError", "/b") is not None
    assert fresh.get("HttpError", "/a").count == 1
    assert fresh.get("TimeoutError", "/b").count == 1


def test_same_key_two_instances_counts_accumulate(tmp_path):
    path = str(tmp_path / "errors.json")
    a = PersistentErrorTracker(path)
    b = PersistentErrorTracker(path)
    a.record("HttpError", "/x", ErrorClassification.TRANSIENT)
    b.record("HttpError", "/x", ErrorClassification.TRANSIENT)
    fresh = PersistentErrorTracker(path)
    rec = fresh.get("HttpError", "/x")
    assert rec is not None and rec.count == 2, f"expected count 2, got {rec}"


def test_stale_prune_does_not_erase_other_instances_newer_record(tmp_path):
    clock_a = FakeClock(1000.0)
    clock_b = FakeClock(1000.0)
    path = str(tmp_path / "errors.json")
    a = PersistentErrorTracker(path, clock=clock_a)
    b = PersistentErrorTracker(path, clock=clock_b)  # stale snapshot (empty)
    # A records old + ... actually: A records a record at t=1000
    a.record("Old", "/old", ErrorClassification.TRANSIENT)
    # B (stale) records a newer record after advancing its clock, then A prunes?
    # Scenario from task: stale instance's prune/record does not erase other's newer record
    # Setup: a records New at later time via fresh clock; b is stale and prunes old entries
    clock_a.advance(10000)  # a now 11000
    a.record("New", "/new", ErrorClassification.TRANSIENT)  # last_seen 11000
    # b's clock is still 1000-ish; advance b a little then prune with large window?
    # More direct: b prunes records older than 5000s relative to b's clock at 11000 would drop Old but not New.
    # Use shared time: set b clock to 11000 as well (same wall time) — b is stale in data, not time.
    clock_b.now = 11000.0
    pruned = b.prune(older_than_seconds=5000)  # cutoff 6000: Old (1000) stale, New (11000) fresh
    assert pruned == 1, f"expected prune 1 (Old only), got {pruned}"
    fresh = PersistentErrorTracker(path)
    assert fresh.get("Old", "/old") is None
    assert fresh.get("New", "/new") is not None, "stale prune erased New"


def test_stale_record_does_not_erase_other_instances_record_via_prune_path(tmp_path):
    # Simpler prune variant: stale b records its own key; that save must not erase a's key (covered above)
    # and stale b prune that removes nothing must not erase either (prune only saves if stale non-empty pre-fix,
    # so use a case where b has a stale entry to prune)
    clock = FakeClock(1000.0)
    path = str(tmp_path / "errors.json")
    # Seed file with Old via a first instance
    seed = PersistentErrorTracker(path, clock=clock)
    seed.record("Old", "/old", ErrorClassification.TRANSIENT)
    a = PersistentErrorTracker(path, clock=clock)
    b = PersistentErrorTracker(path, clock=clock)
    clock.advance(10000)
    a.record("New", "/new", ErrorClassification.TRANSIENT)
    # b is stale (has Old, lacks New); b prunes Old
    pruned = b.prune(older_than_seconds=5000)
    assert pruned == 1
    fresh = PersistentErrorTracker(path, clock=clock)
    assert fresh.get("New", "/new") is not None


def test_stale_instance_reads_reflect_other_instances_writes(tmp_path):
    path = str(tmp_path / "errors.json")
    a = PersistentErrorTracker(path)
    b = PersistentErrorTracker(path)  # stale
    a.record("HttpError", "/a", ErrorClassification.TRANSIENT, "from a")
    a.record("HttpError", "/a", ErrorClassification.TRANSIENT, "again")
    # b's reads must reflect a's writes
    rec = b.get("HttpError", "/a")
    assert rec is not None, "stale get() returned None for other instance's record"
    assert rec.count == 2
    all_recs = b.all_records()
    assert any(r.error_type == "HttpError" and r.endpoint == "/a" for r in all_recs)
    stats = b.stats()
    assert stats["total_errors"] == 2, f"stale stats {stats}"
    assert stats["signatures"] == 1


def test_stale_instance_reads_after_other_prunes(tmp_path):
    clock = FakeClock(1000.0)
    path = str(tmp_path / "errors.json")
    a = PersistentErrorTracker(path, clock=clock)
    b = PersistentErrorTracker(path, clock=clock)
    a.record("Old", "/old", ErrorClassification.TRANSIENT)
    # b caches Old via a read (pre-fix it already has it from init; ensure)
    assert b.get("Old", "/old") is not None
    clock.advance(10000)
    a.record("New", "/new", ErrorClassification.TRANSIENT)
    a.prune(older_than_seconds=5000)  # removes Old, keeps New
    # b must now see Old gone and New present
    assert b.get("Old", "/old") is None, "stale read still shows pruned record"
    assert b.get("New", "/new") is not None
