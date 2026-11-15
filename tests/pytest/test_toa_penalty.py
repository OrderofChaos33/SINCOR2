"""CNP-3: dynamic slashing curve + deterministic TOA penalty encodings.

Covers:
* curve unit tests incl. cap enforcement and repeat-offender saturation,
* gate fail-closed behavior,
* TOA decision encoding determinism (byte-for-byte, pinned vector),
* ledger integration: ledger-derived prior counts, TOA binding,
  re-entry safety, audit-log coverage,
* the ratified 100 % / 50 % anchors remain the ungated defaults.
"""

import os

import pytest

from sincor2.onchain import toa_penalty as tp
from sincor2.onchain.stake_ledger import (
    ADJUDICATOR_ENV,
    GHOST_SLASH_BPS,
    QUALITY_SLASH_BPS,
    StakeLedger,
)

ONE_AXM = 10**18

# Pinned vectors: same decision -> same bytes, byte-for-byte, in every
# process.  If the canonical serialization ever changes, this fails LOUDLY.
PINNED_DECISION_KWARGS = dict(
    agent_id="agent-a", task_id="tsk-1", offense="quality_failure",
    severity=70, prior_offenses=2, anchor_bps=5000,
    recommended_slash_bps=6250, adjudicator_id="judge-1", nonce=7,
)
PINNED_HEX = (
    "544f4150454e013a7b2261646a7564696361746f725f6964223a226a756467652d3122"
    "2c226167656e745f6964223a226167656e742d61222c22616e63686f725f627073223"
    "a353030302c226e6f6e6365223a372c226f6666656e7365223a227175616c6974795f"
    "6661696c757265222c227072696f725f6f6666656e736573223a322c2270726f746f"
    "636f6c223a22746f615f70656e616c7479222c227265636f6d6d656e6465645f736c"
    "6173685f627073223a363235302c227365766572697479223a37302c227461736b5f"
    "6964223a2274736b2d31222c2276657273696f6e223a317d"
)
PINNED_DIGEST = (
    "287d092370c3870bc0a9c51ff9a977719d37528a06d5a168bef5ca5e9a0c4c88"
)


@pytest.fixture
def ledger(tmp_path):
    return StakeLedger(path=str(tmp_path / "stake.json"))


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv(tp.DYNAMIC_SLASH_ENV, "1")
    monkeypatch.setenv(ADJUDICATOR_ENV, "judge-1")


@pytest.fixture
def gate_off(monkeypatch):
    monkeypatch.delenv(tp.DYNAMIC_SLASH_ENV, raising=False)


# ---------------------------------------------------------------------------
# Curve unit tests
# ---------------------------------------------------------------------------

def test_anchor_reproduced_at_zero_severity_first_offense():
    # The ratified anchors are the dynamic curve's base case: severity 0,
    # first offense -> exactly the anchor, not a replacement.
    assert tp.compute_dynamic_slash_bps(QUALITY_SLASH_BPS, 0, 0) == 5000
    assert tp.compute_dynamic_slash_bps(GHOST_SLASH_BPS, 0, 0) == 10000


def test_severity_escalates_within_cap():
    assert tp.compute_dynamic_slash_bps(5000, 50, 0) == 7500   # 5000*1.5
    assert tp.compute_dynamic_slash_bps(5000, 100, 0) == 10000  # 5000*2 -> cap


def test_repeat_offender_multiplier():
    assert tp.compute_dynamic_slash_bps(5000, 0, 1) == 6250   # 5000*1.25
    assert tp.compute_dynamic_slash_bps(5000, 0, 2) == 7812   # *1.25 again


def test_hard_cap_never_exceeded():
    for anchor in (1, 2500, 5000, 7500, 10000):
        for severity in (0, 33, 67, 100):
            for prior in (0, 1, 2, 5, 8, 20, 1000):
                bps = tp.compute_dynamic_slash_bps(anchor, severity, prior)
                assert 0 < bps <= tp.HARD_CAP_BPS, (
                    anchor, severity, prior, bps)


def test_repeat_escalation_saturates_at_cap():
    # Beyond MAX_PRIOR_OFFENSES the exponent adds nothing: escalation has
    # saturated (at the cap for any meaningful anchor).
    assert (tp.compute_dynamic_slash_bps(5000, 0, 8)
            == tp.compute_dynamic_slash_bps(5000, 0, 10**6))
    assert tp.compute_dynamic_slash_bps(5000, 100, 99) == tp.HARD_CAP_BPS
    assert tp.compute_dynamic_slash_bps(10000, 100, 99) == tp.HARD_CAP_BPS


def test_invalid_curve_inputs_rejected():
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(5000, -1, 0)
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(5000, 101, 0)
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(5000, 0, -1)
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(0, 0, 0)
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(10001, 0, 0)
    # bool/float are NOT ints here (no silent coercion on the money path).
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(5000, True, 0)
    with pytest.raises(ValueError):
        tp.compute_dynamic_slash_bps(5000, 50.0, 0)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def test_gate_defaults_off(gate_off):
    assert tp.dynamic_slash_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", " Yes "])
def test_gate_enabled_values(monkeypatch, val):
    monkeypatch.setenv(tp.DYNAMIC_SLASH_ENV, val)
    assert tp.dynamic_slash_enabled() is True


@pytest.mark.parametrize("val", ["0", "no", "false", ""])
def test_gate_disabled_values(monkeypatch, val):
    monkeypatch.setenv(tp.DYNAMIC_SLASH_ENV, val)
    assert tp.dynamic_slash_enabled() is False


# ---------------------------------------------------------------------------
# TOA encoding determinism
# ---------------------------------------------------------------------------

def test_encoding_is_deterministic_byte_for_byte():
    d1 = tp.make_toa_decision(**PINNED_DECISION_KWARGS)
    # Same logical decision, different key order -> identical bytes.
    d2 = dict(reversed(list(d1.items())))
    assert tp.encode_toa_penalty(d1) == tp.encode_toa_penalty(d2)


def test_pinned_encoding_vector():
    d = tp.make_toa_decision(**PINNED_DECISION_KWARGS)
    assert tp.encode_toa_penalty(d).hex() == PINNED_HEX
    assert tp.toa_penalty_digest(d) == PINNED_DIGEST


def test_decode_reencode_roundtrip():
    d = tp.make_toa_decision(**PINNED_DECISION_KWARGS)
    assert tp.encode_toa_penalty(tp.decode_toa_penalty(
        tp.encode_toa_penalty(d))) == tp.encode_toa_penalty(d)


def test_different_decisions_different_digests():
    d1 = tp.make_toa_decision(**PINNED_DECISION_KWARGS)
    d2 = tp.make_toa_decision(**{**PINNED_DECISION_KWARGS, "nonce": 8})
    assert tp.toa_penalty_digest(d1) != tp.toa_penalty_digest(d2)


def test_unicode_normalized():
    kw = dict(PINNED_DECISION_KWARGS)
    d1 = tp.make_toa_decision(**{**kw, "agent_id": "caf\u00e9"})
    d2 = tp.make_toa_decision(**{**kw, "agent_id": "cafe\u0301"})
    assert tp.encode_toa_penalty(d1) == tp.encode_toa_penalty(d2)


def _bad_decision(**overrides):
    kw = dict(PINNED_DECISION_KWARGS)
    kw.update(overrides)
    return tp.make_toa_decision(**kw) if "protocol" not in overrides else None


def test_unknown_fields_rejected():
    d = dict(tp.make_toa_decision(**PINNED_DECISION_KWARGS))
    d["sneaky"] = 1
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(d)


def test_missing_fields_rejected():
    d = dict(tp.make_toa_decision(**PINNED_DECISION_KWARGS))
    del d["nonce"]
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(d)


def test_float_and_bool_rejected():
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(
            tp.make_toa_decision(**{**PINNED_DECISION_KWARGS, "severity": 70.0}))
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(
            tp.make_toa_decision(**{**PINNED_DECISION_KWARGS, "nonce": True}))


def test_bad_enum_and_protocol_rejected():
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(
            tp.make_toa_decision(**{**PINNED_DECISION_KWARGS,
                                    "offense": "jaywalking"}))
    bad = dict(tp.make_toa_decision(**PINNED_DECISION_KWARGS))
    bad["protocol"] = "not_toa"
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(bad)
    bad2 = dict(tp.make_toa_decision(**PINNED_DECISION_KWARGS))
    bad2["version"] = 2
    with pytest.raises(tp.InvalidToaDecision):
        tp.encode_toa_penalty(bad2)


def test_bad_version_tag_and_payload_rejected():
    good = tp.encode_toa_penalty(tp.make_toa_decision(**PINNED_DECISION_KWARGS))
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(b"TOAPEN\x02:" + good[8:])
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty(good[:8] + b"{not json")
    with pytest.raises(tp.InvalidToaDecision):
        tp.decode_toa_penalty("not-bytes")


# ---------------------------------------------------------------------------
# Ledger integration
# ---------------------------------------------------------------------------

def test_slash_dynamic_fails_closed_without_gate(ledger, gate_off, monkeypatch):
    monkeypatch.setenv(ADJUDICATOR_ENV, "judge-1")
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)  # 2 locked
    with pytest.raises(tp.DynamicSlashDisabled):
        ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 100,
                             "quality_failure_upheld", adjudicator="judge-1")
    # The lock was NOT consumed by the refused slash.
    assert ledger.balance_of("agent-a")["locked_wei"] == str(2 * ONE_AXM)


def test_anchors_unchanged_when_gate_off(ledger, gate_off, monkeypatch):
    monkeypatch.setenv(ADJUDICATOR_ENV, "judge-1")
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    out = ledger.adjudicate("agent-a", "tsk-1", upheld=True,
                            adjudicator="judge-1")
    assert out["slashed_wei"] == str(ONE_AXM)  # exactly 50 %, as ratified


def test_slash_dynamic_quality_escalates(ledger, gate_on):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)  # 2 locked
    out = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 100,
                               "quality_failure_upheld",
                               adjudicator="judge-1", poster_id="poster-p")
    assert out["computed_slash_bps"] == 10000  # 5000 * 2.0 -> cap
    assert out["slashed_wei"] == str(2 * ONE_AXM)  # whole lock
    assert out["prior_offenses"] == 0
    assert ledger.offense_count("agent-a") == 1
    assert ledger.reauction_credit("poster-p") == 2 * ONE_AXM


def test_repeat_offender_escalates_from_ledger_history(ledger, gate_on):
    ledger.deposit("agent-a", 20 * ONE_AXM)
    # Offense 1: severity 0 -> anchor exactly (no escalation on first).
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)  # 2 locked
    out1 = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 0,
                                "quality_failure_upheld",
                                adjudicator="judge-1")
    assert out1["computed_slash_bps"] == 5000
    assert out1["slashed_wei"] == str(ONE_AXM)
    # Offense 2: same agent, ledger-derived prior=1 -> 6250 bps.
    ledger.lock_for_commit("agent-a", "tsk-2", 4 * ONE_AXM)  # 2 locked
    out2 = ledger.slash_dynamic("agent-a", "tsk-2", QUALITY_SLASH_BPS, 0,
                                "quality_failure_upheld",
                                adjudicator="judge-1")
    assert out2["computed_slash_bps"] == 6250
    assert out2["slashed_wei"] == str(int(2 * ONE_AXM * 6250 / 10000))
    assert out2["prior_offenses"] == 1
    assert ledger.offense_count("agent-a") == 2


def test_caller_cannot_lie_about_prior_offenses(ledger, gate_on):
    ledger.deposit("agent-a", 20 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 0,
                         "quality_failure_upheld", adjudicator="judge-1")
    # A TOA decision claiming prior_offenses=0 does not lower the curve:
    # the ledger's own history (1 prior) governs.
    lying = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-2", offense="quality_failure",
        severity=0, prior_offenses=0, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=5000, adjudicator_id="judge-1", nonce=1)
    ledger.lock_for_commit("agent-a", "tsk-2", 4 * ONE_AXM)
    out = ledger.slash_dynamic("agent-a", "tsk-2", QUALITY_SLASH_BPS, 0,
                               "quality_failure_upheld",
                               adjudicator="judge-1", toa_decision=lying)
    assert out["computed_slash_bps"] == 6250  # ledger count, not the lie
    assert out["penalty"]["prior_offenses"] == 1


def test_toa_decision_bound_into_audit_event(ledger, gate_on):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    decision = tp.make_toa_decision(
        agent_id="agent-a", task_id="tsk-1", offense="quality_failure",
        severity=50, prior_offenses=0, anchor_bps=QUALITY_SLASH_BPS,
        recommended_slash_bps=7500, adjudicator_id="judge-1", nonce=3)
    out = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 50,
                               "quality_failure_upheld",
                               adjudicator="judge-1", toa_decision=decision)
    assert out["computed_slash_bps"] == 7500
    events = [e for e in ledger._data["events"]
              if e["kind"] == "slash" and e["task_id"] == "tsk-1"]
    assert len(events) == 1
    pen = events[0]["penalty"]
    assert pen["dynamic"] is True
    assert pen["toa_digest"] == tp.toa_penalty_digest(decision)
    assert tp.decode_toa_penalty(bytes.fromhex(pen["toa_encoding_hex"])) == \
        tp.decode_toa_penalty(tp.encode_toa_penalty(decision))


def test_slash_dynamic_ghosting(ledger, gate_on):
    ledger.deposit("ghost", 10 * ONE_AXM)
    ledger.lock_for_commit("ghost", "tsk-1", 4 * ONE_AXM)  # 2 locked
    out = ledger.slash_dynamic("ghost", "tsk-1", GHOST_SLASH_BPS, 100,
                               "ghosting", poster_id="poster-p")
    assert out["computed_slash_bps"] == 10000  # ghosting anchor is the cap
    assert out["slashed_wei"] == str(2 * ONE_AXM)
    # Ghosting still clears the execution record.
    assert ledger.reputation_score("ghost") == 0.0
    assert ledger.offense_count("ghost") == 1


def test_reentry_does_not_inflate_offense_count(ledger, gate_on):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 100,
                         "quality_failure_upheld", adjudicator="judge-1")
    assert ledger.offense_count("agent-a") == 1
    # Second slash on the same task: lock already popped -> 0 slashed,
    # and the counter must NOT move (no capital destroyed).
    out = ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 100,
                               "quality_failure_upheld", adjudicator="judge-1")
    assert out["slashed_wei"] == "0"
    assert ledger.offense_count("agent-a") == 1


def test_every_slash_decision_lands_in_audit_log(ledger, gate_on):
    ledger.deposit("agent-a", 20 * ONE_AXM)
    ledger.deposit("ghost", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "t1", 4 * ONE_AXM)
    ledger.slash("agent-a", "t1", QUALITY_SLASH_BPS, "quality_failure_upheld",
                 adjudicator="judge-1")
    ledger.lock_for_commit("ghost", "t2", 4 * ONE_AXM)
    ledger.slash_ghost("ghost", "t2")
    ledger.lock_for_commit("agent-a", "t3", 4 * ONE_AXM)
    ledger.slash_dynamic("agent-a", "t3", QUALITY_SLASH_BPS, 50,
                         "quality_failure_upheld", adjudicator="judge-1")
    for task in ("t1", "t2", "t3"):
        events = [e for e in ledger._data["events"]
                  if e["kind"] == "slash" and e["task_id"] == task]
        assert len(events) == 1, task
        ev = events[0]
        for field in ("agent_id", "task_id", "slash_bps", "reason",
                      "penalty", "at"):
            assert field in ev, (task, field)
    assert ledger._data["events"][-1]["penalty"]["dynamic"] is True
    t1 = [e for e in ledger._data["events"]
          if e["kind"] == "slash" and e["task_id"] == "t1"][0]
    assert t1["penalty"] == {"dynamic": False}


def test_slash_dynamic_rejects_unknown_reason(ledger, gate_on):
    ledger.deposit("agent-a", 10 * ONE_AXM)
    ledger.lock_for_commit("agent-a", "tsk-1", 4 * ONE_AXM)
    with pytest.raises(ValueError):
        ledger.slash_dynamic("agent-a", "tsk-1", QUALITY_SLASH_BPS, 50,
                             "jaywalking", adjudicator="judge-1")
