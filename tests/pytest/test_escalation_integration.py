"""Phase 1 escalation layer — full-loop integration tests + demo harness.

Proves the handoffs, not just the units (the "wired" gate):

    surface raises package → policy escalates a hard-gate action →
    operator approves → gate opens → guarded execution runs

plus the negative loops (no decision → shut, denied → shut, escalated →
shut) and an audit-log ordering check across the whole loop. Execution
is simulated with a local stand-in function: the layer is deliberately
not wired to any production surface in v1, so the loop proves the
contract a real surface will plug into.

The four ``test_demo_scenario_*`` tests at the bottom ARE the demo
harness (sanctions hit / threshold exceeded / override / co-sign
quorum) — written as readable narratives so they can be run live, in
order, as the demo.
"""

import pytest

from sincor2.escalation import (
    APPROVED,
    DENIED,
    ESCALATED,
    OVERRIDDEN,
    PENDING,
    EscalationStore,
    IllegalTransitionError,
    PolicyEngine,
    require_decision,
)

T0 = "2026-11-01T00:00:00+00:00"
T1 = "2026-11-01T01:00:00+00:00"
T2 = "2026-11-01T02:00:00+00:00"


class GuardedExecution:
    """Stand-in for execution code behind the gate hook."""

    def __init__(self):
        self.ran_with = []

    def run(self, store, package_id, payload):
        if not require_decision(store, package_id):
            raise PermissionError("gate shut: no APPROVED/OVERRIDDEN decision")
        self.ran_with.append(payload)
        return f"executed:{payload}"


@pytest.fixture
def store(tmp_path):
    return EscalationStore(str(tmp_path / "esc"))


def _surface_raise(store, category="merge"):
    """A surface asking to perform a consequential action."""
    return store.create_package(
        source_surface="founder-approval-card",
        actor="builder-agent-7",
        action_category=category,
        summary=f"agent requests {category}",
        evidence_refs=["pr:600", "tests:smoke-green"],
        severity="critical",
        now=T0,
    )


def test_full_loop_raise_escalate_approve_gate_opens(store):
    execution = GuardedExecution()

    # 1. Surface raises the package for a hard-gate action.
    pkg = _surface_raise(store, category="merge")
    assert pkg.state == PENDING

    # 2. Policy escalated it: the classification on record is ESCALATE,
    #    and the raw classifier agrees independently.
    classified = [h for h in pkg.history if h["event"] == "classified"]
    assert "verdict=ESCALATE" in classified[0]["detail"]
    assert PolicyEngine.classify("merge").verdict == "ESCALATE"

    # 3. Before any decision the gate is shut and execution cannot run.
    assert require_decision(store, pkg.id) is False
    with pytest.raises(PermissionError):
        execution.run(store, pkg.id, "merge-pr-600")
    assert execution.ran_with == []

    # 4. The operator approves.
    decided = store.decide(pkg.id, "approve", by="court", note="ship it", now=T1)
    assert decided.state == APPROVED

    # 5. The gate opens and the guarded execution runs exactly once.
    assert require_decision(store, pkg.id) is True
    assert execution.run(store, pkg.id, "merge-pr-600") == "executed:merge-pr-600"
    assert execution.ran_with == ["merge-pr-600"]

    # 6. The audit log contains every transition of the loop, in order.
    records = [r for r in store.read_audit_log() if r["package_id"] == pkg.id]
    assert [r["event"] for r in records] == [
        "created",
        "classified",
        "decision:approve",
    ]
    assert [r["seq"] for r in records] == sorted(r["seq"] for r in records)
    assert records[-1]["from_state"] == PENDING
    assert records[-1]["to_state"] == APPROVED


def test_negative_loop_no_decision_gate_stays_shut(store):
    execution = GuardedExecution()
    pkg = _surface_raise(store, category="treasury")
    with pytest.raises(PermissionError):
        execution.run(store, pkg.id, "move-funds")
    assert execution.ran_with == []
    assert store.get_package(pkg.id).state == PENDING


def test_negative_loop_denied_gate_stays_shut(store):
    execution = GuardedExecution()
    pkg = _surface_raise(store, category="deploy")
    store.decide(pkg.id, "deny", by="court", note="not before launch", now=T1)
    assert store.get_package(pkg.id).state == DENIED
    assert require_decision(store, pkg.id) is False
    with pytest.raises(PermissionError):
        execution.run(store, pkg.id, "deploy-prod")
    assert execution.ran_with == []


def test_override_loop_opens_gate_with_recorded_dissent(store):
    execution = GuardedExecution()
    pkg = _surface_raise(store, category="external_send")
    decided = store.decide(
        pkg.id,
        "override",
        by="court",
        note="send only the redacted version",
        now=T1,
    )
    assert decided.state == OVERRIDDEN
    assert decided.decision.note == "send only the redacted version"
    assert execution.run(store, pkg.id, "send-redacted") == "executed:send-redacted"
    audit_notes = [
        r["note"] for r in store.read_audit_log() if r["event"] == "decision:override"
    ]
    assert audit_notes == ["send only the redacted version"]


def test_escalate_loop_gate_shut_and_terminal_for_agents(store):
    execution = GuardedExecution()
    pkg = _surface_raise(store, category="legal_compliance")
    store.decide(pkg.id, "escalate", by="operator", note="founder call", now=T1)
    assert store.get_package(pkg.id).state == ESCALATED
    assert require_decision(store, pkg.id) is False
    with pytest.raises(PermissionError):
        execution.run(store, pkg.id, "flip-policy")
    # An agent cannot reopen or re-decide an escalated package; the
    # founder's decision re-enters as a new record, out of band.
    with pytest.raises(IllegalTransitionError):
        store.decide(pkg.id, "approve", by="agent-1", now=T1)


def test_loop_survives_store_restart_between_raise_and_decision(tmp_path):
    base = str(tmp_path / "esc")
    execution = GuardedExecution()

    raising_store = EscalationStore(base)
    pkg = _surface_raise(raising_store, category="withdrawal")
    assert require_decision(raising_store, pkg.id) is False

    # Process restarts: a fresh store instance on the same directory
    # sees the pending package, the operator decides, the gate opens.
    deciding_store = EscalationStore(base)
    assert [p.id for p in deciding_store.list_pending()] == [pkg.id]
    deciding_store.decide(pkg.id, "approve", by="court", now=T1)

    gate_store = EscalationStore(base)
    assert execution.run(gate_store, pkg.id, "withdraw") == "executed:withdraw"
    events = [r["event"] for r in gate_store.read_audit_log()]
    assert events == ["created", "classified", "decision:approve"]


# ---------------------------------------------------------------------------
# DEMO SCENARIOS — the Tuesday demo harness.
# Run in order: A (sanctions hit → deny → dropped), B (threshold →
# approve → executes once), C (override with note → executes, note in
# audit). Each scenario narrates the same loop a live surface will run:
# attempt → policy HALTS execution → EscalationEnvelope emitted →
# operator decides → downstream executes or is dropped, deterministically.
# ---------------------------------------------------------------------------


class MockDownstream:
    """The downstream action an agent wants to perform (transfer, spend,
    send). Records every invocation so tests can prove it ran exactly
    once — or never."""

    def __init__(self):
        self.calls = []

    def __call__(self, **payload):
        self.calls.append(payload)
        return "downstream-ok"


def _attempt(store, downstream, package_id, **payload):
    """The execution path every surface uses: gate first, act second."""
    if not require_decision(store, package_id):
        return "halted"
    return downstream(**payload)


def _assert_valid_envelope(envelope_dict):
    """The serialized payload must be a Canonical Escalation Envelope."""
    assert envelope_dict["schema"] == "EscalationEnvelope"
    assert envelope_dict["schema_version"] == "escalation-envelope/1.0"
    assert envelope_dict["action_choices"] == ["approve", "override", "escalate"]
    assert set(envelope_dict["triggered_rule"]) == {"policy_id", "rule", "detail"}
    assert set(envelope_dict["state_snapshot"]) == {
        "input_parameters",
        "dry_run_state",
    }
    assert set(envelope_dict["risk"]) == {
        "violations",
        "exposure",
        "recommended_action",
        "required_signers",
    }
    assert envelope_dict["source_agent_id"]
    assert envelope_dict["source_context"]["surface"]


def test_demo_scenario_a_sanctions_hit(store):
    """Scenario A — "sanctions hit".

    A mock agent attempts a screened transfer. The sanctions screen
    fires an OFAC match: execution HALTS before anything moves, an
    EscalationEnvelope is emitted carrying the triggered rule and the
    dry-run state snapshot, the gate stays shut while the operator
    reviews, and the operator's DENY drops the downstream call for
    good — the mock downstream is never invoked.
    """
    downstream = MockDownstream()
    attempted_amount = 500  # AXM — the mock agent's proposed transfer
    matcher_confidence = 0.98  # the mock sanctions matcher's score

    # The agent attempts the transfer; the screen's rule hit rides in
    # the policy context, with the proposed inputs + dry-run outcome
    # captured as the envelope's proof payload.
    pkg = store.create_package(
        source_surface="sanctions-screen",
        source_agent_id="treasury-agent-3",
        actor="treasury-agent-3",
        action_category="funds",
        summary="transfer 500 AXM to counterparty wallet",
        evidence_refs=["ofac:sdn-snapshot-2026-10-28"],
        severity="critical",
        context={
            "rule_hit": {
                "policy_id": "sanctions-screen",
                "rule": "ofac-match",
                "detail": "counterparty wallet matches OFAC SDN entry",
                "confidence": matcher_confidence,
            }
        },
        input_parameters={"to": "0xSANCTIONED", "amount_axm": attempted_amount},
        dry_run_state={"screen_result": "match", "would_transfer": False},
        exposure={"amount": attempted_amount, "currency": "AXM"},
        recommended_action="deny",
        session_id="sess-demo-a",
        trace_id="trace-demo-a",
        now=T0,
    )

    # Envelope emitted, payload format validates, rule stamped verbatim.
    envelope = pkg.to_dict()
    _assert_valid_envelope(envelope)
    assert envelope["triggered_rule"] == {
        "policy_id": "sanctions-screen",
        "rule": "ofac-match",
        "detail": "counterparty wallet matches OFAC SDN entry",
    }
    assert envelope["state_snapshot"]["input_parameters"]["amount_axm"] == 500
    assert envelope["state"] == PENDING

    # Risk block: the violation carries the matcher's real confidence,
    # exposure is the attempted amount itself, recommendation is deny.
    risk = envelope["risk"]
    assert risk["violations"] == [
        {
            "policy_id": "sanctions-screen",
            "detail": "counterparty wallet matches OFAC SDN entry",
            "confidence": matcher_confidence,
        }
    ]
    assert risk["exposure"] == {"amount": attempted_amount, "currency": "AXM"}
    assert risk["recommended_action"] == "deny"

    # HALTED while waiting: the gate is shut, the attempt is dropped,
    # and the downstream mock has not been touched.
    assert _attempt(store, downstream, pkg.id, to="0xSANCTIONED") == "halted"
    assert downstream.calls == []

    # Operator reviews the envelope and decides: DENY.
    store.decide(pkg.id, "deny", by="court", note="OFAC match — no.", now=T1)
    assert store.get_package(pkg.id).state == DENIED

    # Deterministic drop: even a retry after the decision cannot run it.
    assert _attempt(store, downstream, pkg.id, to="0xSANCTIONED") == "halted"
    assert downstream.calls == []


def test_demo_scenario_b_threshold_exceeded(store):
    """Scenario B — "threshold exceeded".

    A mock agent attempts a spend above its threshold. The policy
    engine's spend-threshold rule escalates it into an envelope; the
    downstream spend stays halted while PENDING; the operator APPROVES
    and the downstream executes exactly once. The audit log shows the
    full ordered transition chain.
    """
    downstream = MockDownstream()
    amount = 25_000
    threshold = 10_000
    # The mock's own arithmetic drives the envelope's risk block:
    # exposure is the attempted amount; the recommendation follows the
    # over-threshold ratio (<= 2.5x → approve is reasonable; beyond
    # that the mock would have recommended escalate).
    ratio = amount / threshold
    recommended = "approve" if ratio <= 2.5 else "escalate"

    pkg = store.create_package(
        source_surface="agent-spend-control",
        source_agent_id="procurement-agent-1",
        actor="procurement-agent-1",
        action_category="funds",
        summary="purchase compute credits",
        severity="high",
        context={"amount": amount, "threshold": threshold},
        input_parameters={"vendor": "compute-co", "amount_usd": amount},
        dry_run_state={"balance_after_usd": 100_000 - amount},
        exposure={"amount": amount, "currency": "USD"},
        recommended_action=recommended,
        now=T0,
    )

    envelope = pkg.to_dict()
    _assert_valid_envelope(envelope)
    assert envelope["triggered_rule"]["policy_id"] == "spend-threshold"
    assert envelope["triggered_rule"]["rule"] == "amount_exceeds_threshold"
    assert envelope["risk"]["exposure"] == {"amount": amount, "currency": "USD"}
    assert envelope["risk"]["recommended_action"] == recommended == "approve"
    assert envelope["state_snapshot"]["dry_run_state"] == {
        "balance_after_usd": 75_000
    }

    # Wait state: halted, nothing spent.
    assert _attempt(store, downstream, pkg.id, amount_usd=25_000) == "halted"
    assert downstream.calls == []

    # Operator approves → executes exactly once.
    store.decide(pkg.id, "approve", by="court", note="within Q4 budget", now=T1)
    assert _attempt(store, downstream, pkg.id, amount_usd=25_000) == "downstream-ok"
    assert downstream.calls == [{"amount_usd": 25_000}]

    # Full ordered transition chain in the audit log.
    records = [r for r in store.read_audit_log() if r["package_id"] == pkg.id]
    assert [r["event"] for r in records] == [
        "created",
        "classified",
        "decision:approve",
    ]
    assert [r["seq"] for r in records] == sorted(r["seq"] for r in records)
    assert records[1]["detail"].startswith("verdict=ESCALATE")
    assert "spend-threshold" in records[1]["detail"]


def test_demo_scenario_c_override(store):
    """Scenario C — "override".

    Same threshold breach, but the operator OVERRIDES with a recorded
    modification note: the downstream executes under the override, and
    the audit log carries the note verbatim. An override attempted
    with an empty note is rejected outright — no note, no override.
    """
    downstream = MockDownstream()

    pkg = store.create_package(
        source_surface="agent-spend-control",
        source_agent_id="procurement-agent-1",
        actor="procurement-agent-1",
        action_category="funds",
        summary="emergency infra purchase",
        severity="high",
        context={"amount": 12_500, "threshold": 10_000},
        input_parameters={"vendor": "infra-co", "amount_usd": 12_500},
        dry_run_state={"balance_after_usd": 87_500},
        now=T0,
    )
    _assert_valid_envelope(pkg.to_dict())

    # Empty-note override is rejected; package unchanged, still halted.
    with pytest.raises(ValueError):
        store.decide(pkg.id, "override", by="court", note="  ", now=T1)
    assert store.get_package(pkg.id).state == PENDING
    assert _attempt(store, downstream, pkg.id, amount_usd=12_500) == "halted"
    assert downstream.calls == []

    # Override with the modification recorded → executes.
    note = "proceed, but cap the order at $12,500 and log the exception"
    store.decide(pkg.id, "override", by="court", note=note, now=T1)
    assert store.get_package(pkg.id).state == OVERRIDDEN
    assert _attempt(store, downstream, pkg.id, amount_usd=12_500) == "downstream-ok"
    assert downstream.calls == [{"amount_usd": 12_500}]

    # The audit log carries the override note verbatim.
    override_records = [
        r for r in store.read_audit_log() if r["event"] == "decision:override"
    ]
    assert len(override_records) == 1
    assert override_records[0]["note"] == note
    assert override_records[0]["to_state"] == OVERRIDDEN


def test_demo_scenario_d_cosign_quorum(store):
    """Scenario D — "co-sign quorum".

    A high-exposure action whose envelope requires two signer roles
    (CTO + CFO). One decision is not enough — not even the right kind
    from the wrong person. An override from someone outside the signer
    list is recorded but counts for nothing; the CTO's override alone
    leaves the gate shut; only when the CFO co-signs does the quorum
    complete, the envelope move to OVERRIDDEN, and the downstream
    payment execute. Every attestation lands in the hash-chained audit
    log, in order.
    """
    downstream = MockDownstream()
    amount = 250_000
    threshold = 10_000

    pkg = store.create_package(
        source_surface="agent-spend-control",
        source_agent_id="treasury-agent-3",
        actor="treasury-agent-3",
        action_category="funds",
        summary="high-exposure vendor payment",
        severity="critical",
        context={"amount": amount, "threshold": threshold},
        input_parameters={"vendor": "infra-co", "amount_usd": amount},
        dry_run_state={"balance_after_usd": 750_000},
        exposure={"amount": amount, "currency": "USD"},
        recommended_action="override",
        required_signers=["cto", "cfo"],
        now=T0,
    )
    _assert_valid_envelope(pkg.to_dict())
    assert pkg.risk["required_signers"] == ["cto", "cfo"]
    assert pkg.risk["exposure"] == {"amount": amount, "currency": "USD"}

    # An override from outside the signer list: recorded, counts for
    # nothing, gate stays shut.
    store.decide(pkg.id, "override", by="mallory", role="intern", note="lgtm", now=T1)
    assert store.get_package(pkg.id).state == PENDING
    assert _attempt(store, downstream, pkg.id, amount_usd=amount) == "halted"

    # The CTO signs the override. One of two — still shut.
    store.decide(
        pkg.id, "override", by="alex", role="cto", note="architecture reviewed",
        now=T1,
    )
    assert store.get_package(pkg.id).state == PENDING
    assert _attempt(store, downstream, pkg.id, amount_usd=amount) == "halted"
    assert downstream.calls == []

    # The CFO co-signs. Quorum complete → OVERRIDDEN → executes once.
    decided = store.decide(
        pkg.id, "override", by="blair", role="cfo", note="budget confirmed",
        now=T2,
    )
    assert decided.state == OVERRIDDEN
    assert require_decision(store, pkg.id) is True
    assert _attempt(store, downstream, pkg.id, amount_usd=amount) == "downstream-ok"
    assert downstream.calls == [{"amount_usd": amount}]

    # The audit log shows the whole co-sign chain, in order — outsider,
    # CTO, CFO — each carrying its actor + role attestation, and the
    # hash chain verifies intact over all of it.
    records = [r for r in store.read_audit_log() if r["package_id"] == pkg.id]
    assert [r["event"] for r in records] == [
        "created",
        "classified",
        "decision:override",
        "decision:override",
        "decision:override",
    ]
    decisions = records[2:]
    assert [(r["by"], r["role"]) for r in decisions] == [
        ("mallory", "intern"),
        ("alex", "cto"),
        ("blair", "cfo"),
    ]
    assert [r["detail"] for r in decisions] == [
        "outcome=advisory_only",
        "outcome=quorum_pending",
        "outcome=quorum_completed",
    ]
    assert [r["seq"] for r in records] == sorted(r["seq"] for r in records)
    assert store.verify_audit_chain() is True
