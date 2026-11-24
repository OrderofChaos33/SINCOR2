"""W-197 — AntigamingStore cross-instance lost updates (hunt 67 family).

Two instances constructed before either writes hold stale snapshots;
a stale instance's _save() rewrites the whole JSON file, erasing the
other instance's records — silently bypassing collusion caps on the
money path. Mutators must reload-mutate-save under flock, and reads
that feed decisions must not serve a stale snapshot.

Two "processes" are simulated by constructing two AntigamingStore
objects on the same path up front and swapping the module singleton
(``ag._STORE``) to whichever process is acting — exactly the state
each process's own singleton would be in.
"""
from __future__ import annotations

import json
import time

import pytest

from sincor2 import axm_antigaming as ag
from sincor2.axm_antigaming import AntigamingStore


def _now() -> int:
    return int(time.time() * 1000)


@pytest.fixture
def two_instances(tmp_path, monkeypatch):
    """Two pre-constructed instances on one path + lenient rate caps,
    strict rotation caps (so collusion state is observable)."""
    path = str(tmp_path / "antigaming.json")
    monkeypatch.setenv("AXM_AG_MAX_BIDS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_WINS_PER_HOUR", "1000")
    monkeypatch.setenv("AXM_AG_MAX_CONSECUTIVE_WINS", "3")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_CONSECUTIVE", "2")
    monkeypatch.setenv("AXM_AG_MAX_PAIR_WINS_PER_WINDOW", "1000")
    a = AntigamingStore(path)
    b = AntigamingStore(path)  # stale: constructed before A writes
    yield path, a, b
    ag.reset_antigaming_store(path=str(tmp_path / "restore.json"))


def _act_as(store):
    """Make ``store`` the acting process's singleton."""
    ag._STORE = store


def _disk(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


# (a) two instances constructed before either writes — both survive -----

def test_two_preconstructed_instances_both_bid_records_survive(two_instances):
    path, a, b = two_instances
    now = _now()
    _act_as(a)
    ag.record_bid_entry("agent-a", "task-1", now_ms=now)
    _act_as(b)  # b's snapshot predates a's write
    ag.record_bid_entry("agent-b", "task-2", now_ms=now)

    raw = _disk(path)
    assert raw.get("bid_events", {}).get("agent-a") == [now], (
        f"a's bid erased by stale b, disk={raw.get('bid_events')}")
    assert raw.get("bid_events", {}).get("agent-b") == [now]

    fresh = AntigamingStore(path)
    assert fresh.bids_in_window("agent-a", now_ms=now) == 1
    assert fresh.bids_in_window("agent-b", now_ms=now) == 1


def test_two_preconstructed_instances_both_close_records_survive(two_instances):
    path, a, b = two_instances
    now = _now()
    _act_as(a)
    ag.record_auction_close("poster-1", "winner-a", "task-1", now_ms=now)
    _act_as(b)
    ag.record_auction_close("poster-2", "winner-b", "task-2", now_ms=now + 1)

    fresh = AntigamingStore(path)
    assert fresh.wins_in_window("winner-a", now_ms=now + 1) == 1, (
        "a's win event erased by stale b's close")
    assert fresh.wins_in_window("winner-b", now_ms=now + 1) == 1
    assert fresh.pair_wins_in_window("poster-1", "winner-a",
                                     now_ms=now + 1) == 1
    assert fresh.pair_wins_in_window("poster-2", "winner-b",
                                     now_ms=now + 1) == 1


# (b) a stale instance's write does not erase a fresher record ----------

def test_stale_write_does_not_erase_fresher_win(two_instances):
    path, a, b = two_instances
    now = _now()
    _act_as(a)
    ag.record_auction_close("poster-1", "winner-a", "task-1", now_ms=now)
    # b is stale (empty snapshot); its unrelated write must not erase
    # a's win streak / win event.
    _act_as(b)
    ag.record_bid_entry("agent-b", "task-2", now_ms=now)

    fresh = AntigamingStore(path)
    assert fresh.win_streak_of("winner-a") == 1, (
        "stale b's save erased a's win streak")
    assert fresh.wins_in_window("winner-a", now_ms=now) == 1
    assert fresh.bids_in_window("agent-b", now_ms=now) == 1


def test_stale_rejection_audit_does_not_erase_fresher_records(two_instances):
    path, a, b = two_instances
    now = _now()
    _act_as(a)
    ag.record_bid_entry("agent-a", "task-1", now_ms=now)
    # b (stale) audits a rejection via the anonymous-bounty self-bid path.
    _act_as(b)
    with pytest.raises(ag.AnonymousBountyError):
        ag.check_self_bid({"task_id": "task-9", "poster_id": None,
                           "bounty_axm": 5.0}, "agent-b")

    fresh = AntigamingStore(path)
    assert fresh.bids_in_window("agent-a", now_ms=now) == 1, (
        "stale b's audit save erased a's bid record")
    reasons = [e["reason"] for e in fresh.rejection_log()]
    assert "anonymous_bounty_task" in reasons


# (c) collusion/cap state from one instance is visible to others --------

def test_collusion_state_visible_to_fresh_and_stale_instances(two_instances):
    path, a, b = two_instances
    now = _now()
    _act_as(a)
    for i in range(3):
        ag.record_auction_close("poster-1", "winner-a", f"task-{i}",
                                now_ms=now + i)

    fresh = AntigamingStore(path)
    assert fresh.win_streak_of("winner-a") == 3
    assert fresh.pair_streak_of("poster-1", "winner-a") == 3
    assert fresh.pair_wins_in_window("poster-1", "winner-a",
                                     now_ms=now + 2) == 3
    assert fresh.wins_in_window("winner-a", now_ms=now + 2) == 3

    # The stale instance's reads must reflect it too — its reads feed
    # bid-eligibility decisions.
    assert b.win_streak_of("winner-a") == 3, "stale read hid collusion streak"
    assert b.pair_streak_of("poster-1", "winner-a") == 3
    assert b.wins_in_window("winner-a", now_ms=now + 2) == 3


def test_stale_instance_enforces_cap_built_by_other_instance(two_instances):
    path, a, b = two_instances
    now = _now()
    _act_as(a)
    for i in range(3):  # reaches AXM_AG_MAX_CONSECUTIVE_WINS = 3
        ag.record_auction_close("poster-1", "winner-a", f"task-{i}",
                                now_ms=now + i)
    # b never saw those closes in its snapshot; its eligibility check
    # must still fail closed on the accumulated streak.
    _act_as(b)
    with pytest.raises(ag.CollusionCapError):
        ag.check_bid_eligibility(
            {"task_id": "task-next", "poster_id": "poster-9"},
            "winner-a", now_ms=now + 3)
