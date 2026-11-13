"""W-141: verify_ledger must stay conclusive past the journal trim window.

Defect (verified live 2026-11-12): _save() persists only the last 500
journal entries while ``history_writes`` counts lifetime writes. After
any restart past 500 writes, verify_ledger() can only answer
``inconclusive: "journal trimmed ... replay cannot cover full
lifetime"`` — the pool's self-verification is permanently blind even
though the books are exact.

Fix contract asserted here:
* A journal whose history exceeds the trim window still verifies
  (anchor checkpoint folded at trim time + retained-suffix replay +
  hash chain), or fails for a REAL discrepancy — never "inconclusive"
  purely due to trimming.
* A legacy already-trimmed ledger file (counter present, no anchor —
  the exact shape production carries today) bootstraps an anchor from
  record state at load and verifies from then on.
* Conservation checks are NOT weakened: funded/allocated/spent from
  the journal must still match the records exactly, and a drifted
  record or a tampered journal entry is a discrepancy, not a pass.
"""
import json
from pathlib import Path

from sincor2.a2a_bounty_pool import BountyPool

ONE_AXM_WEI = 10 ** 18


def _drive_past_trim_window(base, cycles=251, reserve_axm=1000.0):
    """Fresh pool driven past 500 journal entries.

    fund (1 entry) + ``cycles`` allocate/spend pairs (2 entries each)
    plus a trailing allocate/release pair, so the journal holds spend
    AND release history past the 500-entry retention window.
    Returns (pool, path, spent_cycles).
    """
    path = str(Path(base) / "pool.json")
    p = BountyPool(path=path, reserve_axm=reserve_axm)
    p.fund()
    spent = 0
    for i in range(cycles):
        a = p.allocate(f"task-{i}", 1.0, reason="w141")
        p.spend(a["allocation_id"])
        spent += 1
    a = p.allocate("task-tail", 1.0, reason="w141")
    p.release(a["allocation_id"])
    assert p._history_writes > 500, (
        f"test setup must exceed the trim window, got {p._history_writes}")
    return p, path, spent


def _reload(path, reserve_axm=1000.0):
    return BountyPool(path=path, reserve_axm=reserve_axm)


def test_journal_beyond_trim_window_verifies_after_reload(tmp_path):
    """THE W-141 repro: >500 writes + restart must still verify."""
    p, path, _spent = _drive_past_trim_window(tmp_path)
    # Same-process verification is conclusive too (anchor folded live).
    v = p.verify_ledger()
    assert v["inconclusive"] is None, v
    assert v["ok"], v
    # After a restart the persisted journal is the trimmed window; the
    # anchor must carry the folded prefix so replay still covers the
    # pool's full lifetime.
    p2 = _reload(path)
    assert len(p2._history) <= 500
    assert p2._history_writes > 500
    v2 = p2.verify_ledger()
    assert v2["inconclusive"] is None, (
        f"W-141: verification inconclusive purely due to trimming: "
        f"{v2['inconclusive']}")
    assert v2["ok"], f"ledger divergence past trim window: {v2}"


def test_full_loop_conservation_past_trim_window(tmp_path):
    """Full loop (post/allocate/settle/expire) past the trim window
    ends with verify ok=true and EXACT conservation in wei."""
    _p, path, spent_cycles = _drive_past_trim_window(tmp_path, cycles=260)
    p = _reload(path)
    v = p.verify_ledger()
    assert v["inconclusive"] is None and v["ok"], v
    # Exact wei-level expectations, computed independently of the pool.
    assert p._funded_wei == 1000 * ONE_AXM_WEI
    assert p._spent_wei == spent_cycles * ONE_AXM_WEI
    record_spent = sum(int(a["amount_wei"]) for a in p._allocations.values()
                       if a.get("state") == "spent")
    assert record_spent == p._spent_wei
    assert p._allocated_wei() == 0  # tail allocation was released
    # Conservation identity on counters: funded = spent + allocated +
    # available, exactly, in wei.
    assert (p._funded_wei
            == p._spent_wei + p._allocated_wei() + p._available_wei())


def test_legacy_trimmed_ledger_bootstraps_anchor_and_verifies(tmp_path):
    """A pre-fix ledger file (history_writes counter, NO anchor keys,
    history already trimmed) — production's current shape — must heal:
    load bootstraps an anchor and verification becomes conclusive."""
    _p, path, _spent = _drive_past_trim_window(tmp_path, cycles=300)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    # Rewrite into the exact pre-fix shape: drop every anchor-era key.
    for key in list(raw.keys()):
        if "anchor" in key or "digest" in key:
            raw.pop(key)
    assert len(raw["history"]) == 500
    assert raw["history_writes"] > 500
    Path(path).write_text(json.dumps(raw, indent=2), encoding="utf-8")

    p = _reload(path)
    v = p.verify_ledger()
    assert v["inconclusive"] is None, (
        f"legacy trimmed ledger still inconclusive: {v['inconclusive']}")
    assert v["ok"], v
    # The anchor is honestly marked as bootstrapped from record state
    # (the trimmed prefix entries are gone; the anchor is a checkpoint
    # of record, verified against everything from that point forward).
    assert v["anchor"]["bootstrapped"] is True
    assert v["anchor"]["writes"] == raw["history_writes"] - 500
    # And it keeps verifying as new entries land past the anchor.
    a = p.allocate("task-post-bootstrap", 1.0)
    p.spend(a["allocation_id"])
    v2 = p.verify_ledger()
    assert v2["inconclusive"] is None and v2["ok"], v2


def test_record_drift_past_trim_window_is_discrepancy_not_inconclusive(
        tmp_path):
    """Checks are not weakened: a drifted counter past the trim window
    is a REAL discrepancy (ok=False), never masked as inconclusive."""
    _p, path, _spent = _drive_past_trim_window(tmp_path)
    p = _reload(path)
    p._spent_wei += 1  # 1-wei record drift, journal untouched
    v = p.verify_ledger()
    assert v["inconclusive"] is None, v
    assert v["ok"] is False
    assert v["discrepancies"], "drift must be named as a discrepancy"


def test_journal_entry_tamper_past_trim_window_fails_verification(
        tmp_path):
    """Editing a retained journal entry in the saved file (records and
    counters untouched) must fail verification after reload — the
    replay and/or hash chain must catch it."""
    _p, path, _spent = _drive_past_trim_window(tmp_path)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entry = raw["history"][-1]
    entry["amount_wei"] = str(int(entry["amount_wei"]) + ONE_AXM_WEI)
    Path(path).write_text(json.dumps(raw, indent=2), encoding="utf-8")

    p = _reload(path)
    v = p.verify_ledger()
    assert v["ok"] is False
    assert v["discrepancies"], (
        f"tampered journal entry produced no discrepancy: {v}")
