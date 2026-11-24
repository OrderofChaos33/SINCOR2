"""W-209/W-215: TokenBudgetController cross-instance lost updates.

Two TokenBudgetController instances on one ledger path: the second
instance was constructed before the first instance's writes, so it
holds a stale snapshot.  Its persisting mutators (record_usage /
kill_agent / reinstate_agent / register_agent /
reset_agent_daily_count) must reload under a file lock before
mutating/saving, or they erase the first instance's recorded usage —
and, worst case, a stale save silently resurrects a killed agent,
undoing the operator kill switch and resetting the daily ceiling
(PoC-verified in red-team hunt 67).
"""
from __future__ import annotations

from sincor2.token_budget_controller import TokenBudgetController


def _ctrl(path):
    return TokenBudgetController(ledger_path=path)


def _fresh(path):
    return TokenBudgetController(ledger_path=path)


def test_usage_from_two_stale_instances_accumulates(tmp_path):
    path = tmp_path / "ledger.json"
    a = _ctrl(path)
    b = _ctrl(path)  # stale: constructed before A's write

    a.record_usage("agent-1", 100)
    b.record_usage("agent-1", 50)  # stale save would erase A's 100

    fresh = _fresh(path)
    assert fresh.get_status("agent-1")["tokens_used_today"] == 150
    # B's own in-memory state is re-synced from the persisted state.
    assert b.get_status("agent-1")["tokens_used_today"] == 150


def test_usage_for_distinct_agents_from_stale_instances_both_survive(tmp_path):
    path = tmp_path / "ledger.json"
    a = _ctrl(path)
    b = _ctrl(path)

    a.record_usage("agent-a", 111)
    b.record_usage("agent-b", 222)  # stale save would erase agent-a

    fresh = _fresh(path)
    assert fresh.get_status("agent-a")["tokens_used_today"] == 111
    assert fresh.get_status("agent-b")["tokens_used_today"] == 222


def test_kill_switch_survives_stale_instance_persist(tmp_path):
    path = tmp_path / "ledger.json"
    a = _ctrl(path)
    b = _ctrl(path)  # stale: does not know about the kill below

    a.kill_agent("agent-x", reason="operator_override")
    b.record_usage("agent-y", 10)  # stale save would resurrect agent-x

    fresh = _fresh(path)
    status = fresh.get_status("agent-x")
    assert status["killed"] is True
    assert status["is_allowed"] is False
    assert fresh.get_status("agent-y")["tokens_used_today"] == 10


def test_stale_mutation_does_not_reset_ceiling_or_usage(tmp_path):
    path = tmp_path / "ledger.json"
    a = _ctrl(path)
    b = _ctrl(path)  # stale: constructed before A registers/records

    a.register_agent("custom", 12345)
    a.record_usage("custom", 777)
    b.register_agent("other", 999)  # stale save would wipe A's agent

    fresh = _fresh(path)
    status = fresh.get_status("custom")
    assert status["daily_ceiling"] == 12345
    assert status["tokens_used_today"] == 777
    assert fresh.get_status("other")["daily_ceiling"] == 999


def test_stale_kill_and_reinstate_do_not_clobber_other_instance(tmp_path):
    path = tmp_path / "ledger.json"
    a = _ctrl(path)
    b = _ctrl(path)

    a.record_usage("agent-1", 300)
    b.kill_agent("agent-2")  # stale save would erase agent-1's usage

    fresh = _fresh(path)
    assert fresh.get_status("agent-1")["tokens_used_today"] == 300
    assert fresh.get_status("agent-2")["killed"] is True

    # A is now stale w.r.t. B's kill; reinstating via A must operate on
    # the fresh state and must not erase agent-1's usage either.
    a.reinstate_agent("agent-2")
    fresh2 = _fresh(path)
    assert fresh2.get_status("agent-2")["killed"] is False
    assert fresh2.get_status("agent-1")["tokens_used_today"] == 300
