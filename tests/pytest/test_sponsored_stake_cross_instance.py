"""W-195: SponsoredStakeLedger cross-instance lost-update regression.

Two SponsoredStakeLedger instances on the same path, both constructed
before either saves, must not erase each other's records: a stale
instance's save used to rewrite its load-once snapshot over another
instance's stakes (cross-instance double-front in the hunt PoC: the
stake ledger was credited while the sponsored ledger lost a record).
The fix mirrors W-213: flock + reload-under-lock before mutate+persist.
"""
from __future__ import annotations

import pytest

from sincor2.sponsored_stake import (
    ENABLE_ENV,
    SponsoredStakeLedger,
)

AXM = 10**18


@pytest.fixture
def path(tmp_path, monkeypatch):
    from sincor2.onchain.stake_ledger import reset_stake_ledger

    reset_stake_ledger(path=str(tmp_path / "stake.json"))
    monkeypatch.setenv(ENABLE_ENV, "1")
    return str(tmp_path / "sponsored.json")


def test_two_stale_instances_front_distinct_agents_both_survive(path):
    a = SponsoredStakeLedger(path=path)
    b = SponsoredStakeLedger(path=path)  # constructed before either saves

    a.front("agent-a", 100 * AXM, approved_by="op")
    b.front("agent-b", 100 * AXM, approved_by="op")

    fresh = SponsoredStakeLedger(path=path)
    assert fresh.status_of("agent-a") is not None
    assert fresh.status_of("agent-b") is not None
    assert fresh.outstanding_wei("agent-a") == 100 * AXM
    assert fresh.outstanding_wei("agent-b") == 100 * AXM
    kinds = [e["kind"] for e in fresh._data["events"]]
    assert kinds.count("front") == 2


def test_stale_instance_recoup_does_not_erase_other_recoup(path):
    seed = SponsoredStakeLedger(path=path)
    seed.front("agent-a", 100 * AXM, approved_by="op")

    c = SponsoredStakeLedger(path=path)
    d = SponsoredStakeLedger(path=path)  # stale snapshot: recouped == 0

    out_c = c.recoup("agent-a", 40 * AXM, task_id="t-c")
    assert out_c["recouped_wei"] == str(40 * AXM)
    out_d = d.recoup("agent-a", 40 * AXM, task_id="t-d")
    # d must recoup against the fresh outstanding (60), not its stale 100.
    assert out_d["recouped_wei"] == str(40 * AXM)
    assert out_d["outstanding_wei"] == str(20 * AXM)

    fresh = SponsoredStakeLedger(path=path)
    rec = fresh.status_of("agent-a")
    assert rec["recouped_wei"] == str(80 * AXM)
    assert fresh.outstanding_wei("agent-a") == 20 * AXM


def test_stale_instance_clawback_does_not_erase_other_clawback(path):
    seed = SponsoredStakeLedger(path=path)
    seed.front("agent-a", 100 * AXM, approved_by="op")

    c = SponsoredStakeLedger(path=path)
    d = SponsoredStakeLedger(path=path)  # stale snapshot: clawed_back == 0

    assert c.apply_clawback("agent-a", 30 * AXM, task_id="slash-1") == 30 * AXM
    assert d.apply_clawback("agent-a", 30 * AXM, task_id="slash-2") == 30 * AXM

    fresh = SponsoredStakeLedger(path=path)
    rec = fresh.status_of("agent-a")
    assert rec["clawed_back_wei"] == str(60 * AXM)
    assert fresh.outstanding_wei("agent-a") == 40 * AXM


def test_stale_instance_front_does_not_resurrect_settled_record(path):
    """Reload must replace state wholesale, not merge stale keys: a stale
    instance must see an agent settled by another instance, not its old
    'fronted' snapshot."""
    seed = SponsoredStakeLedger(path=path)
    stale = SponsoredStakeLedger(path=path)
    seed.front("agent-a", 100 * AXM, approved_by="op")
    seed.recoup("agent-a", 100 * AXM, task_id="t-full")

    # The stale instance never saw the front at all; fronting again from
    # it is legitimate only if it reloads and sees the settled record —
    # either way the settled history must not be silently merged away.
    fresh = SponsoredStakeLedger(path=path)
    assert fresh.status_of("agent-a")["status"] == "settled"
    # stale reads through the locked reload see fresh state too
    assert stale.status_of("agent-a")["status"] == "settled"
    assert stale.outstanding_wei("agent-a") == 0
