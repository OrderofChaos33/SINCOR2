"""CNP-7: async dispute lifecycle + arbitration-oracle adapters.

Covers: full lifecycle (file -> evidence window -> resolved), timeout
auto-resolve (fail-closed: not upheld), oracle ruling application,
griefing via repeated filings, stalled evidence windows, forged/tampered/
replayed oracle rulings, invalid transitions, and transition-log
completeness. EIP-191 signature verification is mandatory on the oracle
path.
"""

from __future__ import annotations

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from sincor2.sinax.async_dispute_resolution import (
    AdjudicatorOracleAdapter,
    ArbitrationOracleAdapter,
    AsyncDispute,
    DisputeRegistry,
    DisputeState,
    DuplicateDispute,
    InvalidDisputeInput,
    InvalidRuling,
    InvalidTransition,
    OracleRuling,
    oracle_message,
)

HASH = "0x" + "ab" * 32


class Clock:
    def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
        self.now = start_ms

    def __call__(self) -> int:
        return self.now

    def advance(self, ms: int) -> None:
        self.now += ms


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def registry(clock):
    return DisputeRegistry(evidence_window_ms=60_000, now_ms=clock)


@pytest.fixture
def adjudicator():
    return Account.create()


@pytest.fixture
def adapter(adjudicator, clock):
    return AdjudicatorOracleAdapter(
        expected_adjudicator=adjudicator.address, now_ms=clock)


def _file(registry, task="task-1", agent="agent-1", filer="poster-1",
          reason="delivery did not match spec"):
    return registry.file_dispute(task_id=task, agent_id=agent,
                                 filer_id=filer, reason=reason)


def _signed_ruling(key, dispute_id, task_id, agent_id, upheld, clock,
                   expires_at_ms=None):
    expires = (expires_at_ms if expires_at_ms is not None
               else clock.now + 60_000)
    ruling = OracleRuling(
        dispute_id=dispute_id, task_id=task_id, agent_id=agent_id,
        upheld=upheld, adjudicator_address=key.address,
        expires_at_ms=expires, signature="")
    sig = Account.sign_message(
        encode_defunct(text=oracle_message(ruling)),
        private_key=key.key).signature.hex()
    return OracleRuling(
        dispute_id=dispute_id, task_id=task_id, agent_id=agent_id,
        upheld=upheld, adjudicator_address=key.address,
        expires_at_ms=expires, signature="0x" + sig)


# --------------------------------------------------------------------------
# Lifecycle
# --------------------------------------------------------------------------

def test_full_lifecycle_upheld(registry, clock, adapter, adjudicator):
    d = _file(registry)
    assert d.state is DisputeState.FILED
    registry.open_evidence_window(d.dispute_id)
    assert d.state is DisputeState.EVIDENCE_OPEN
    assert d.evidence_deadline_ms == clock.now + 60_000
    registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                             uri="https://example.com/e1.json",
                             content_hash=HASH)
    registry.submit_evidence(d.dispute_id, submitter_id="agent-1",
                             uri="https://example.com/e2.json",
                             content_hash=HASH)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    registry.apply_oracle_ruling(ruling, adapter)
    assert d.state is DisputeState.RESOLVED
    assert d.outcome_upheld is True
    # Every transition was logged.
    kinds = [(e.kind, e.from_state, e.to_state) for e in d.events]
    assert ("transition", "filed", "filed") in kinds
    assert ("transition", "filed", "evidence_open") in kinds
    assert ("transition", "evidence_open", "resolved") in kinds


def test_full_lifecycle_rejected(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            False, clock)
    registry.apply_oracle_ruling(ruling, adapter)
    assert d.state is DisputeState.RESOLVED
    assert d.outcome_upheld is False


def test_open_window_twice_rejected(registry):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    with pytest.raises(InvalidTransition):
        registry.open_evidence_window(d.dispute_id)


def test_ruling_before_window_rejected(registry, adapter, adjudicator, clock):
    d = _file(registry)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    with pytest.raises(InvalidTransition):
        registry.apply_oracle_ruling(ruling, adapter)
    assert d.state is DisputeState.FILED


def test_file_requires_reason(registry):
    with pytest.raises(InvalidDisputeInput):
        registry.file_dispute(task_id="t", agent_id="a", filer_id="f",
                              reason="")


def test_unknown_dispute_rejected(registry):
    with pytest.raises(InvalidDisputeInput):
        registry.get("no-such-dispute")


# --------------------------------------------------------------------------
# Timeouts
# --------------------------------------------------------------------------

def test_timeout_auto_resolves_not_upheld(registry, clock):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    clock.advance(61_000)
    resolved = registry.process_timeouts()
    assert [r.dispute_id for r in resolved] == [d.dispute_id]
    assert d.state is DisputeState.AUTO_RESOLVED
    # Fail-closed: no adjudicator ruling -> no upheld outcome.
    assert d.outcome_upheld is False
    assert registry.list_open() == []


def test_no_auto_resolve_before_deadline(registry, clock):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    clock.advance(59_999)
    assert registry.process_timeouts() == []
    assert d.state is DisputeState.EVIDENCE_OPEN


def test_stalled_evidence_window_rejects_submission_and_ruling(
        registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    clock.advance(61_000)
    with pytest.raises(InvalidTransition):
        registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                                 uri="https://example.com/late.json",
                                 content_hash=HASH)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    with pytest.raises(InvalidTransition):
        registry.apply_oracle_ruling(ruling, adapter)
    # Only the timeout sweep can move it now.
    registry.process_timeouts()
    assert d.state is DisputeState.AUTO_RESOLVED
    # After auto-resolution, a second ruling is also rejected.
    with pytest.raises(InvalidTransition):
        registry.apply_oracle_ruling(ruling, adapter)


def test_auto_resolve_policy_configurable(clock):
    reg = DisputeRegistry(evidence_window_ms=10_000, auto_resolve_upheld=True,
                          now_ms=clock)
    d = reg.file_dispute(task_id="t", agent_id="a", filer_id="f",
                         reason="r")
    reg.open_evidence_window(d.dispute_id)
    clock.advance(11_000)
    reg.process_timeouts()
    assert d.outcome_upheld is True


# --------------------------------------------------------------------------
# Anti-griefing
# --------------------------------------------------------------------------

def test_repeated_filing_rejected(registry):
    d = _file(registry)
    with pytest.raises(DuplicateDispute) as exc:
        _file(registry)
    assert exc.value.existing_dispute_id == d.dispute_id
    # The original dispute is untouched.
    assert registry.get(d.dispute_id).state is DisputeState.FILED


def test_refile_after_resolution_allowed(registry, clock, adapter,
                                         adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            False, clock)
    registry.apply_oracle_ruling(ruling, adapter)
    d2 = _file(registry)
    assert d2.dispute_id != d.dispute_id


def test_evidence_cap_enforced(registry):
    from sincor2.sinax.async_dispute_resolution import MAX_EVIDENCE_ITEMS
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    for i in range(MAX_EVIDENCE_ITEMS):
        registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                                 uri=f"https://example.com/e{i}.json",
                                 content_hash=HASH)
    with pytest.raises(InvalidDisputeInput):
        registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                                 uri="https://example.com/overflow.json",
                                 content_hash=HASH)
    assert len(d.evidence) == MAX_EVIDENCE_ITEMS


def test_evidence_hash_must_be_32_byte_hex(registry):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    with pytest.raises(InvalidDisputeInput):
        registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                                 uri="https://example.com/e.json",
                                 content_hash="not-a-hash")


def test_evidence_after_resolution_rejected(registry, clock, adapter,
                                            adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    registry.apply_oracle_ruling(ruling, adapter)
    with pytest.raises(InvalidTransition):
        registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                                 uri="https://example.com/e.json",
                                 content_hash=HASH)


# --------------------------------------------------------------------------
# Oracle ruling verification (EIP-191 mandatory)
# --------------------------------------------------------------------------

def test_forged_ruling_rejected_state_unchanged(registry, clock, adjudicator):
    adapter = AdjudicatorOracleAdapter(
        expected_adjudicator=adjudicator.address, now_ms=clock)
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    impostor = Account.create()
    ruling = _signed_ruling(impostor, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    # Claim the adjudicator's address but sign with the impostor key.
    ruling = OracleRuling(
        dispute_id=ruling.dispute_id, task_id=ruling.task_id,
        agent_id=ruling.agent_id, upheld=ruling.upheld,
        adjudicator_address=adjudicator.address,
        expires_at_ms=ruling.expires_at_ms, signature=ruling.signature)
    with pytest.raises(InvalidRuling):
        registry.apply_oracle_ruling(ruling, adapter)
    assert d.state is DisputeState.EVIDENCE_OPEN
    assert d.outcome_upheld is None
    # Rejection is noted in the log; state did not move.
    assert any("rejected" in e.detail for e in d.events)


def test_tampered_ruling_field_rejected(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            False, clock)
    tampered = OracleRuling(
        dispute_id=ruling.dispute_id, task_id=ruling.task_id,
        agent_id=ruling.agent_id, upheld=True,  # flipped after signing
        adjudicator_address=ruling.adjudicator_address,
        expires_at_ms=ruling.expires_at_ms, signature=ruling.signature)
    with pytest.raises(InvalidRuling):
        registry.apply_oracle_ruling(tampered, adapter)
    assert d.state is DisputeState.EVIDENCE_OPEN


def test_ruling_replay_rejected(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    registry.apply_oracle_ruling(ruling, adapter)
    # A replayed signature on the same dispute is rejected...
    with pytest.raises(InvalidTransition):
        registry.apply_oracle_ruling(ruling, adapter)
    # ...and cross-dispute replay of a captured ruling is rejected too.
    d2 = _file(registry, task="task-9", agent="agent-9")
    registry.open_evidence_window(d2.dispute_id)
    rebased = OracleRuling(
        dispute_id=d2.dispute_id, task_id="task-9", agent_id="agent-9",
        upheld=ruling.upheld,
        adjudicator_address=ruling.adjudicator_address,
        expires_at_ms=ruling.expires_at_ms, signature=ruling.signature)
    with pytest.raises(InvalidRuling):
        registry.apply_oracle_ruling(rebased, adapter)
    assert d2.state is DisputeState.EVIDENCE_OPEN


def test_expired_ruling_rejected(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock,
                            expires_at_ms=clock.now - 1_000)
    with pytest.raises(InvalidRuling):
        registry.apply_oracle_ruling(ruling, adapter)


def test_far_future_ruling_rejected(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock,
                            expires_at_ms=clock.now + 60 * 60 * 1000)
    with pytest.raises(InvalidRuling):
        registry.apply_oracle_ruling(ruling, adapter)


def test_ruling_task_binding_rejected(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    # Ruling bound to a different dispute/task cannot be applied here.
    ruling = _signed_ruling(adjudicator, "other-dispute", "task-1",
                            "agent-1", True, clock)
    with pytest.raises(InvalidDisputeInput):
        registry.apply_oracle_ruling(ruling, adapter)
    assert d.state is DisputeState.EVIDENCE_OPEN


def test_adapter_without_adjudicator_fails_closed():
    with pytest.raises(InvalidDisputeInput):
        AdjudicatorOracleAdapter(expected_adjudicator="")


# --------------------------------------------------------------------------
# Pluggability
# --------------------------------------------------------------------------

class QuorumOracleStub(ArbitrationOracleAdapter):
    """Example alternate oracle: accepts rulings over a fixed evidence bar."""

    name = "quorum-stub"

    def __init__(self, min_evidence: int = 1) -> None:
        self.min_evidence = min_evidence
        self.seen: list = []

    def verify_ruling(self, ruling: OracleRuling) -> bool:
        self.seen.append(ruling)
        return bool(ruling.signature) and ruling.expires_at_ms > 0


def test_oracle_adapter_is_pluggable(registry, clock):
    stub = QuorumOracleStub()
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = OracleRuling(
        dispute_id=d.dispute_id, task_id="task-1", agent_id="agent-1",
        upheld=True, adjudicator_address="stub-oracle",
        expires_at_ms=clock.now + 60_000, signature="0xstub")
    registry.apply_oracle_ruling(ruling, stub)
    assert d.state is DisputeState.RESOLVED
    assert d.outcome_upheld is True
    assert stub.seen == [ruling]
    assert "quorum-stub" in d.events[-1].detail


def test_oracle_rejection_keeps_dispute_open(registry, clock):
    class DenyAll(ArbitrationOracleAdapter):
        name = "deny-all"

        def verify_ruling(self, ruling: OracleRuling) -> bool:
            return False

    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    ruling = OracleRuling(
        dispute_id=d.dispute_id, task_id="task-1", agent_id="agent-1",
        upheld=True, adjudicator_address="x",
        expires_at_ms=clock.now + 60_000, signature="0xabc")
    with pytest.raises(InvalidRuling):
        registry.apply_oracle_ruling(ruling, DenyAll())
    assert d.state is DisputeState.EVIDENCE_OPEN


# --------------------------------------------------------------------------
# Transition log completeness
# --------------------------------------------------------------------------

def test_every_transition_logged(registry, clock, adapter, adjudicator):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    registry.submit_evidence(d.dispute_id, submitter_id="poster-1",
                             uri="https://example.com/e.json",
                             content_hash=HASH)
    ruling = _signed_ruling(adjudicator, d.dispute_id, "task-1", "agent-1",
                            True, clock)
    registry.apply_oracle_ruling(ruling, adapter)
    states = [e.to_state for e in d.events]
    assert states[0] == "filed"
    # The full path filed -> evidence_open -> resolved appears in order.
    path = [e.to_state for e in d.events if e.kind == "transition"]
    assert path == ["filed", "evidence_open", "resolved"]
    for e in d.events:
        assert e.ts_ms > 0 and e.from_state and e.to_state


def test_snapshot_is_complete(registry, clock):
    d = _file(registry)
    registry.open_evidence_window(d.dispute_id)
    snap = d.snapshot()
    assert snap["state"] == "evidence_open"
    assert snap["dispute_id"] == d.dispute_id
    assert isinstance(snap["events"], list) and len(snap["events"]) >= 2
    assert isinstance(snap["evidence"], list)
