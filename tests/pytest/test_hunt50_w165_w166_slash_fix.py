"""Hunt-50 W-165 / W-166 regression tests (slash_dynamic hardening).

W-165: slash_dynamic() branched on a caller-supplied reason string —
"ghosting" reached slash_ghost() with NO adjudicator check, took
100 %, credited a caller-chosen poster, and the audit record claimed
the curve's computed bps (e.g. 50 %) instead of the 100 % taken.

W-166: a supplied TOA decision was encoded/digested without ever being
checked against the slash (agent, task, severity, prior count,
adjudicator, computed bps all unverified) and its nonce was never
consumed, so one decision could be digest-bound into any number of
slashes, including other victims'.

These tests pin the fixed contract:
* both slash_dynamic branches are adjudicator-gated (the ungated
  protocol path remains slash_ghost() called directly by
  close_auction, exactly as documented);
* the penalty record + return value carry applied_slash_bps — the
  ratio ACTUALLY taken — next to the curve's computed value;
* a TOA decision must describe THIS slash field-by-field, and its
  nonce is single-use, durably.
"""

import pytest

from sincor2.onchain import toa_penalty as tp
from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    GHOST_SLASH_BPS,
    QUALITY_SLASH_BPS,
    StakeLedger,
    UnauthorizedSlashing,
)

ONE_AXM = 10**18


@pytest.fixture
def ledger(tmp_path):
    return StakeLedger(path=str(tmp_path / "stake.json"))


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv(tp.DYNAMIC_SLASH_ENV, "1")
    monkeypatch.setenv(ADJUDICATOR_ENV, "judge-1")


def _fund(led, agent, task, deposit=10, bid=4):
    led.deposit(agent, deposit * ONE_AXM)
    led.lock_for_commit(agent, task, bid * ONE_AXM)  # 2 AXM locked


def _slash_events(led, task):
    return [e for e in led._data["events"]
            if e["kind"] == "slash" and e["task_id"] == task]


# ---------------------------------------------------------------------------
# W-165: ghosting via the dynamic entry point is adjudicator-gated
# ---------------------------------------------------------------------------

def test_dynamic_ghosting_without_adjudicator_rejected(ledger, gate_on):
    # PoC A verbatim shape: gate on, adjudicator env set but NOT supplied.
    _fund(ledger, "victim", "task-1")
    with pytest.raises(UnauthorizedSlashing):
        ledger.slash_dynamic("victim", "task-1", anchor_bps=5000,
                             severity=0, reason="ghosting",
                             adjudicator=None, poster_id="attacker")
    # Nothing moved: lock intact, no slash event, no attacker credit.
    assert ledger.balance_of("victim")["locked_wei"] == str(2 * ONE_AXM)
    assert _slash_events(ledger, "task-1") == []
    assert ledger.reauction_credit("attacker") == 0
    assert ledger.offense_count("victim") == 0


def test_dynamic_ghosting_wrong_adjudicator_rejected(ledger, gate_on):
    _fund(ledger, "victim", "task-1")
    with pytest.raises(UnauthorizedSlashing):
        ledger.slash_dynamic("victim", "task-1", anchor_bps=5000,
                             severity=0, reason="ghosting",
                             adjudicator="mallory", poster_id="attacker")
    assert ledger.balance_of("victim")["locked_wei"] == str(2 * ONE_AXM)
    assert ledger.reauction_credit("attacker") == 0


def test_protocol_slash_ghost_path_unchanged(ledger, gate_on):
    # The trusted close_auction path calls slash_ghost() directly and
    # must keep working with no adjudicator argument (documented
    # protocol path — the market establishes the ghost, not a caller).
    _fund(ledger, "ghost", "task-1")
    out = ledger.slash_ghost("ghost", "task-1", poster_id="poster-p")
    assert out["slashed_wei"] == str(2 * ONE_AXM)
    assert ledger.reauction_credit("poster-p") == 2 * ONE_AXM


# ---------------------------------------------------------------------------
# W-165: the audit trail records the bps ACTUALLY taken
# ---------------------------------------------------------------------------

def test_dynamic_ghosting_audit_records_applied_bps(ledger, gate_on):
    # PoC A's numbers, now WITH adjudication: anchor 5000, severity 0
    # -> curve computes 5000, but ghosting takes 100 %.  The record
    # must say so.
    _fund(ledger, "victim", "task-1")
    out = ledger.slash_dynamic("victim", "task-1", anchor_bps=5000,
                               severity=0, reason="ghosting",
                               adjudicator="judge-1",
                               poster_id="poster-p")
    assert out["slashed_wei"] == str(2 * ONE_AXM)  # 100 % taken
    assert out["computed_slash_bps"] == 5000        # what the curve said
    assert out["applied_slash_bps"] == GHOST_SLASH_BPS  # what was taken
    events = _slash_events(ledger, "task-1")
    assert len(events) == 1
    ev = events[0]
    assert ev["slash_bps"] == GHOST_SLASH_BPS
    assert ev["penalty"]["applied_slash_bps"] == GHOST_SLASH_BPS
    assert ev["penalty"]["computed_slash_bps"] == 5000


def test_dynamic_quality_audit_applied_equals_computed(ledger, gate_on):
    _fund(ledger, "agent-a", "tsk-1")
    out = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 50,
                               "quality_failure_upheld",
                               adjudicator="judge-1")
    assert out["computed_slash_bps"] == 7500
    assert out["applied_slash_bps"] == 7500
    ev = _slash_events(ledger, "tsk-1")[0]
    assert ev["penalty"]["applied_slash_bps"] == 7500


# ---------------------------------------------------------------------------
# W-166: TOA decisions are bound to the slash and single-use
# ---------------------------------------------------------------------------

def _alien_decision():
    # The PoC's decision: different agent/task/adjudicator entirely.
    return tp.make_toa_decision(
        agent_id="someone-else", task_id="other-task",
        offense="quality_failure", severity=100, prior_offenses=0,
        anchor_bps=5000, recommended_slash_bps=10000,
        adjudicator_id="evil-1", nonce=7)


def test_alien_toa_decision_rejected_on_ghost_slash(ledger, gate_on):
    _fund(ledger, "victim", "task-1")
    with pytest.raises(tp.InvalidToaDecision):
        ledger.slash_dynamic("victim", "task-1", anchor_bps=5000,
                             severity=0, reason="ghosting",
                             adjudicator="judge-1", poster_id="attacker",
                             toa_decision=_alien_decision())
    assert ledger.balance_of("victim")["locked_wei"] == str(2 * ONE_AXM)
    assert _slash_events(ledger, "task-1") == []
    assert ledger.reauction_credit("attacker") == 0


def test_alien_toa_decision_replay_across_victims_rejected(ledger, gate_on):
    # PoC B: the same alien decision replayed on a second victim's
    # quality slash must die too.
    _fund(ledger, "victim2", "task-2")
    with pytest.raises(tp.InvalidToaDecision):
        ledger.slash_dynamic("victim2", "task-2", QUALITY_SLASH_BPS, 0,
                             "quality_failure_upheld",
                             adjudicator="judge-1", poster_id="attacker",
                             toa_decision=_alien_decision())
    assert ledger.balance_of("victim2")["locked_wei"] == str(2 * ONE_AXM)
    assert _slash_events(ledger, "task-2") == []


def test_toa_nonce_reuse_rejected_even_when_fields_match(ledger, gate_on):
    ledger.deposit("agent-a", 20 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    first = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-1", offense="quality_failure",
        severity=0, prior_offenses=0, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=5000, adjudicator_id="judge-1", nonce=5)
    out = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 0,
                               "quality_failure_upheld",
                               adjudicator="judge-1", toa_decision=first)
    assert out["slashed_wei"] == str(ONE_AXM)
    # Second slash: every field updated to describe it truthfully
    # (prior count now 1, curve price now 6250) — only the nonce is
    # replayed.  That alone must reject it.
    ledger.lock_for_commit("agent-a", "tsk-2", 4 * ONE_AXM)
    replay = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-2", offense="quality_failure",
        severity=0, prior_offenses=1, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=6250, adjudicator_id="judge-1", nonce=5)
    with pytest.raises(tp.InvalidToaDecision):
        ledger.slash_dynamic("agent-a", "tsk-2", QUALITY_SLASH_BPS, 0,
                             "quality_failure_upheld",
                             adjudicator="judge-1", toa_decision=replay)
    assert ledger.balance_of("agent-a")["locked_wei"] == str(2 * ONE_AXM)
    assert ledger.offense_count("agent-a") == 1


def test_toa_nonce_stays_consumed_across_reload(tmp_path, gate_on):
    path = str(tmp_path / "stake.json")
    led = StakeLedger(path=path)
    led.deposit("agent-a", 10 * ONE_AXM)
    led.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    decision = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-1", offense="quality_failure",
        severity=0, prior_offenses=0, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=5000, adjudicator_id="judge-1", nonce=9)
    led.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 0,
                      "quality_failure_upheld", adjudicator="judge-1",
                      toa_decision=decision)
    # Fresh instance on the same file: the burned nonce must persist.
    led2 = StakeLedger(path=path)
    led2.lock_for_commit("agent-a", "tsk-2", 4 * ONE_AXM)
    replay = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-2", offense="quality_failure",
        severity=0, prior_offenses=1, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=6250, adjudicator_id="judge-1", nonce=9)
    with pytest.raises(tp.InvalidToaDecision):
        led2.slash_dynamic("agent-a", "tsk-2", QUALITY_SLASH_BPS, 0,
                           "quality_failure_upheld",
                           adjudicator="judge-1", toa_decision=replay)


@pytest.mark.parametrize("field,bad", [
    ("severity", 99),
    ("adjudicator_id", "judge-2"),
    ("recommended_slash_bps", 9999),
    ("anchor_bps", 2500),
    ("offense", "ghosting"),
    ("prior_offenses", 3),
])
def test_toa_field_mismatch_rejected(ledger, gate_on, field, bad):
    _fund(ledger, "agent-a", "tsk-1")
    kwargs = dict(agent_id="agent-a", task_id="tsk-1",
                  offense="quality_failure", severity=0, prior_offenses=0,
                  anchor_bps=QUALITY_SLASH_BPS, recommended_slash_bps=5000,
                  adjudicator_id="judge-1", nonce=21)
    kwargs[field] = bad
    with pytest.raises(tp.InvalidToaDecision):
        ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 0,
                             "quality_failure_upheld",
                             adjudicator="judge-1",
                             toa_decision=tp.make_toa_decision(**kwargs))
    assert ledger.balance_of("agent-a")["locked_wei"] == str(2 * ONE_AXM)
    assert _slash_events(ledger, "tsk-1") == []


# ---------------------------------------------------------------------------
# Legitimate adjudicated slashes (with bound TOA decisions) still work
# ---------------------------------------------------------------------------

def test_legit_quality_slash_with_bound_decision(ledger, gate_on):
    _fund(ledger, "agent-a", "tsk-1")
    decision = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-1", offense="quality_failure",
        severity=50, prior_offenses=0, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=7500, adjudicator_id="judge-1", nonce=31)
    out = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 50,
                               "quality_failure_upheld",
                               adjudicator="judge-1", toa_decision=decision)
    assert out["slashed_wei"] == str(int(2 * ONE_AXM * 7500 / 10000))
    ev = _slash_events(ledger, "tsk-1")[0]
    assert ev["penalty"]["toa_digest"] == tp.toa_penalty_digest(decision)
    assert ev["penalty"]["applied_slash_bps"] == 7500


def test_legit_ghost_slash_with_bound_decision(ledger, gate_on):
    _fund(ledger, "ghost", "tsk-1")
    decision = tp.make_toa_decision(
        agent_id="ghost", task_id="tsk-1", offense="ghosting",
        severity=0, prior_offenses=0, anchor_bps=5000,
        recommended_slash_bps=5000, adjudicator_id="judge-1", nonce=41)
    out = ledger.slash_dynamic("ghost", "tsk-1", anchor_bps=5000,
                               severity=0, reason="ghosting",
                               adjudicator="judge-1",
                               toa_decision=decision)
    assert out["slashed_wei"] == str(2 * ONE_AXM)
    assert out["applied_slash_bps"] == GHOST_SLASH_BPS
    ev = _slash_events(ledger, "tsk-1")[0]
    assert ev["penalty"]["toa_digest"] == tp.toa_penalty_digest(decision)
