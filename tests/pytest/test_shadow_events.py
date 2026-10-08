"""Tests for sincor2.shadow_monitor.events (Phase 1 shadow-mode decision logging)."""

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest

from sincor2.shadow_monitor.events import (
    DATA_CLASSIFICATIONS,
    HUMAN_DISPOSITIONS,
    POLICY_RESULTS,
    RISK_TIERS,
    DecisionEvent,
    EventStore,
    EventStoreTampered,
    RawPIIWarning,
    redact,
    validate_no_raw_pii,
)


def make_event(**overrides):
    base = dict(
        task_id="task-001",
        tenant="tenant-a",
        agent_id="agent-7",
        human_owner="owner-alice",
        model_version="model-v3",
        prompt_version="prompt-v12",
        config_version="config-v2",
        tool_version="tool-v9",
        policy_version="policy-v4",
        risk_tier="medium",
        data_classification="internal",
        input_source_refs=["crm://case/88213", "kb://doc/441"],
        input_freshness={"crm://case/88213": 42.5},
        proposed_action={"kind": "refund", "target": "order-555", "estimated_cost": 19.99},
        policy_result="allow",
        policy_reason_codes=["R-101"],
        blocked_in_live_mode=False,
    )
    base.update(overrides)
    return DecisionEvent(**base)


def make_store(tmp_path, name="events.jsonl"):
    return EventStore(tmp_path / name)


# ---------------------------------------------------------------------------
# Field contract
# ---------------------------------------------------------------------------


def test_defaults_all_fields_present():
    e = make_event()
    assert re.fullmatch(r"[0-9a-f]{32}", e.trace_id)
    datetime.fromisoformat(e.timestamp)  # valid ISO8601
    assert e.human_disposition == "not_reviewed"
    assert e.outcome_evidence is None
    assert e.latency_ms is None
    assert e.seq is None
    assert e.proposed_action == {"kind": "refund", "target": "order-555", "estimated_cost": 19.99}
    assert e.input_source_refs == ["crm://case/88213", "kb://doc/441"]
    assert e.blocked_in_live_mode is False


def test_invalid_enums_raise():
    with pytest.raises(ValueError):
        make_event(risk_tier="extreme")
    with pytest.raises(ValueError):
        make_event(policy_result="maybe")
    with pytest.raises(ValueError):
        make_event(data_classification="topsecret")
    with pytest.raises(ValueError):
        make_event(human_disposition="ignored")
    with pytest.raises(ValueError):
        make_event(latency_ms=-1.0)


def test_proposed_action_normalized():
    e = make_event(proposed_action={"kind": "email", "target": "x", "estimated_cost": 0, "junk": 1})
    assert e.proposed_action == {"kind": "email", "target": "x", "estimated_cost": 0}
    e2 = make_event(proposed_action={"kind": "noop"})
    assert e2.proposed_action == {"kind": "noop", "target": None, "estimated_cost": None}


# ---------------------------------------------------------------------------
# Store round-trip + integrity
# ---------------------------------------------------------------------------


def test_jsonl_round_trip(tmp_path):
    store = make_store(tmp_path)
    e = make_event(latency_ms=12.5, outcome_evidence={"ref": "ticket-9"})
    store.append(e)
    got = store.get(e.trace_id)
    assert got is not None
    assert got.to_dict() == e.to_dict()
    assert got.seq == 1


def test_get_unknown_returns_none(tmp_path):
    assert make_store(tmp_path).get("nope") is None


def test_count(tmp_path):
    store = make_store(tmp_path)
    assert store.count() == 0
    for i in range(3):
        store.append(make_event(task_id=f"task-{i}"))
    assert store.count() == 3


def test_hash_chain_verifies(tmp_path):
    store = make_store(tmp_path)
    for i in range(5):
        store.append(make_event(task_id=f"task-{i}", agent_id=f"agent-{i % 2}"))
    assert store.verify_chain() is True
    # Reopening an existing file keeps the chain valid.
    assert EventStore(store.path).verify_chain() is True


def test_tamper_detection_modify_line(tmp_path):
    store = make_store(tmp_path)
    for i in range(3):
        store.append(make_event(task_id=f"task-{i}"))
    lines = open(store.path, encoding="utf-8").read().splitlines()
    record = json.loads(lines[1])
    record["event"]["risk_tier"] = "critical"  # attacker edits payload
    lines[1] = json.dumps(record)
    with open(store.path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    with pytest.raises(EventStoreTampered):
        EventStore(store.path).verify_chain()


def test_tamper_detection_forged_line(tmp_path):
    store = make_store(tmp_path)
    store.append(make_event())
    with open(store.path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"line_hash": "x" * 64, "prev_hash": "bogus",
                             "stream": "t/a", "seq": 99, "event": {}}) + "\n")
    with pytest.raises(EventStoreTampered):
        EventStore(store.path).verify_chain()


# ---------------------------------------------------------------------------
# Sequence completeness
# ---------------------------------------------------------------------------


def test_seq_monotonic_per_stream(tmp_path):
    store = make_store(tmp_path)
    a1 = store.append(make_event(tenant="t1", agent_id="a1"))
    a2 = store.append(make_event(tenant="t1", agent_id="a1"))
    b1 = store.append(make_event(tenant="t1", agent_id="b1"))
    b2 = store.append(make_event(tenant="t2", agent_id="a1"))
    assert (a1.seq, a2.seq, b1.seq, b2.seq) == (1, 2, 1, 1)


def _write_raw_line(path, prev_hash, stream, seq, event):
    body = {"prev_hash": prev_hash, "stream": stream, "seq": seq, "event": event.to_dict()}
    line_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"line_hash": line_hash, **body}, separators=(",", ":")) + "\n")
    return line_hash


def test_seq_gaps_detected(tmp_path):
    path = tmp_path / "gaps.jsonl"
    e1 = make_event(tenant="t1", agent_id="a1")
    e3 = make_event(tenant="t1", agent_id="a1", task_id="task-3")
    prev = _write_raw_line(path, "GENESIS", "t1/a1", 1, e1)
    _write_raw_line(path, prev, "t1/a1", 3, e3)  # seq 2 dropped
    gaps = EventStore(path).check_completeness()
    assert gaps == [{"tenant": "t1", "agent_id": "a1", "missing": [2]}]


def test_no_gaps_when_complete(tmp_path):
    store = make_store(tmp_path)
    for _ in range(4):
        store.append(make_event())
    store.append(make_event(agent_id="other"))
    assert store.check_completeness() == []


# ---------------------------------------------------------------------------
# Redaction + PII gate
# ---------------------------------------------------------------------------


def test_redact_email():
    out = redact("contact jane.doe@example.com for details")
    m = re.search(r"<email:([0-9a-f]{8})>", out)
    assert m and "jane.doe@example.com" not in out
    assert m.group(1) == hashlib.sha256(b"jane.doe@example.com").hexdigest()[:8]


def test_redact_phone():
    for phone in ("(415) 555-2671", "415-555-2671", "+14155552671"):
        out = redact(f"call {phone} now")
        assert phone not in out
        assert re.search(r"<phone:[0-9a-f]{8}>", out), phone


def test_redact_long_digit_id():
    out = redact("customer id 9876543210987654 on file")
    assert "9876543210987654" not in out
    m = re.search(r"<id:([0-9a-f]{8})>", out)
    assert m.group(1) == hashlib.sha256(b"9876543210987654").hexdigest()[:8]


def test_redact_leaves_plain_text_alone():
    assert redact("no identifiers here, just words") == "no identifiers here, just words"


def test_raw_pii_warning_fires():
    e = make_event(outcome_evidence={"note": "emailed jane.doe@example.com the refund"})
    findings = validate_no_raw_pii(e)
    assert findings, "expected PII findings"
    assert any("email" in f for f in findings)


def test_validate_no_raw_pii_clean_event():
    assert validate_no_raw_pii(make_event()) == []


def test_append_warns_but_does_not_raise_on_pii(tmp_path):
    store = make_store(tmp_path)
    e = make_event(outcome_evidence={"note": "call (415) 555-2671"})
    with pytest.warns(RawPIIWarning):
        store.append(e)
    assert store.count() == 1  # still appended


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def test_by_agent(tmp_path):
    store = make_store(tmp_path)
    store.append(make_event(agent_id="a1"))
    store.append(make_event(agent_id="a2"))
    store.append(make_event(agent_id="a1"))
    assert len(store.by_agent("a1")) == 2
    assert store.by_agent("ghost") == []


def test_by_policy_result(tmp_path):
    store = make_store(tmp_path)
    store.append(make_event(policy_result="allow"))
    store.append(make_event(policy_result="deny", policy_reason_codes=["R-9"]))
    store.append(make_event(policy_result="needs_approval"))
    assert len(store.by_policy_result("deny")) == 1
    with pytest.raises(ValueError):
        store.by_policy_result("maybe")


def test_by_time_range(tmp_path):
    store = make_store(tmp_path)
    base = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    for i, hour in enumerate([10, 12, 14]):
        ts = (base.replace(hour=hour)).isoformat()
        store.append(make_event(task_id=f"t{i}", timestamp=ts))
    hits = store.by_time_range("2026-10-07T11:00:00+00:00", "2026-10-07T13:00:00+00:00")
    assert [e.task_id for e in hits] == ["t1"]
    hits_dt = store.by_time_range(
        base - timedelta(hours=3), base + timedelta(hours=3))
    assert len(hits_dt) == 3


def test_pending_review(tmp_path):
    store = make_store(tmp_path)
    store.append(make_event())
    store.append(make_event(task_id="t2", human_disposition="accepted"))
    store.append(make_event(task_id="t3", human_disposition="rejected"))
    pending = store.pending_review()
    assert [e.task_id for e in pending] == ["task-001"]


# ---------------------------------------------------------------------------
# Metrics-safe summary
# ---------------------------------------------------------------------------


def test_labels_safe_summary_no_high_cardinality(tmp_path):
    store = make_store(tmp_path)
    e1 = store.append(make_event(risk_tier="high", policy_result="deny"))
    e2 = store.append(make_event(risk_tier="low", policy_result="allow", task_id="task-xyz"))
    summary = store.labels_safe_summary()
    assert summary["total"] == 2
    assert summary["by_risk_tier"]["high"] == 1
    assert summary["by_risk_tier"]["low"] == 1
    assert summary["by_policy_result"]["deny"] == 1
    assert summary["by_policy_result"]["allow"] == 1
    blob = json.dumps(summary)
    for secret in (e1.trace_id, e2.trace_id, "task-xyz", "task-001",
                   "agent-7", "tenant-a", "owner-alice"):
        assert secret not in blob, f"high-cardinality value leaked: {secret}"
    assert set(summary.keys()) == {"total", "by_risk_tier", "by_policy_result", "by_hour"}


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_appends_keep_chain_valid(tmp_path):
    store = make_store(tmp_path)
    errors = []

    def worker(n):
        try:
            for i in range(10):
                store.append(make_event(task_id=f"w{n}-{i}", agent_id=f"agent-{n % 3}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert store.count() == 80
    assert store.verify_chain() is True
    # Per-stream seqs are unique and gap-free under concurrency.
    assert store.check_completeness() == []
