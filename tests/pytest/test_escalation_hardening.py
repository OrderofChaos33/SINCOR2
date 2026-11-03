"""Pre-wiring hardening regressions for the escalation layer.

Covers the hunt-37 findings fixed on the land-control-plane-stack
branch (the stack itself — sanctions wire, Phase 2 signing/Merkle —
lands by merge; these tests pin the fixes applied on top):

- W-125: ``create_package`` stored ``required_signers`` RAW while
  ``decide()`` lowercases roles, so a package created with
  ``["Founder", "CFO"]`` could never reach quorum and sat PENDING
  forever. Creation now normalizes (strip + lower) and rejects
  duplicates/empties after normalization.
- W-127: ``PolicyEngine.classify`` compared amount > threshold only
  when both were int/float, so a string amount (``"999999"``, common
  from JSON) or NaN silently skipped the spend check and returned
  ALLOW. Amounts/thresholds are now coerced to finite numbers and an
  uncoercible value under a present threshold fails closed.
- W-128: ``verify_chain`` anchored the first record's ``prev_hash``
  at GENESIS unconditionally, so any mid-log slice falsely failed.
  Slices now anchor at their first record's ``prev_hash`` (with the
  genesis position protected against masquerade).
- Sink concurrency: the sanctions tap builds a FRESH
  ``EscalationStore`` per emission and calls ``create_package`` —
  exactly the pattern exercised here in parallel. Concurrent
  emissions must produce ONE un-forked hash chain with no lost
  packages (the store now serializes writes with a file lock and
  reloads under it).
"""
from __future__ import annotations

import datetime
import json
import threading

import pytest

import sincor2.sanctions_screen as ss
from sincor2.defi.ofac_sdn import load_snapshot
from sincor2.escalation import (
    ALLOW,
    APPROVED,
    ESCALATE,
    GENESIS_PREV_HASH,
    AuditSigner,
    EscalationStore,
    PolicyEngine,
    _compute_record_hash,
    require_decision,
    verify_chain,
)

T1 = "2026-11-03T10:00:00+00:00"
T2 = "2026-11-03T10:05:00+00:00"
T3 = "2026-11-03T10:10:00+00:00"

# Verified present in the vendored OFAC snapshot (same address the
# sanctions wire tests use).
FLAGGED_ADDR = "0x252a8bd2319d8a555b872990601221b3a2053bce"
SINK_ENV = "SINCOR_ESCALATION_SINK"


@pytest.fixture()
def store(tmp_path):
    return EscalationStore(str(tmp_path / "esc"))


def _raise(store, **overrides):
    kwargs = {
        "source_surface": "hardening-test",
        "actor": "tester",
        "action_category": "funds",
        "summary": "hardening regression package",
    }
    kwargs.update(overrides)
    return store.create_package(**kwargs)


# ---------------------------------------------------------------------------
# W-125 — required_signers normalization
# ---------------------------------------------------------------------------


def test_w125_mixed_case_signers_normalized_and_quorum_completes(store):
    """The hunt-37 PoC: ["Founder", "CFO"] + founder/cfo decisions
    used to land advisory_only forever. Now the quorum completes."""
    pkg = _raise(store, required_signers=["Founder", " CFO "])
    assert pkg.risk["required_signers"] == ["founder", "cfo"]

    decided = store.decide(pkg.id, "approve", by="court", role="founder", now=T1)
    assert decided.state == "PENDING"  # quorum still open

    decided = store.decide(pkg.id, "approve", by="casey", role="CFO", now=T2)
    assert decided.state == APPROVED
    assert require_decision(store, pkg.id) is True


@pytest.mark.parametrize(
    "signers",
    [
        ["Founder", "founder"],
        ["cfo", " CFO"],
        ["cto", "cto"],
        ["Founder", "CFO", " founder "],
    ],
)
def test_w125_duplicate_signers_after_normalization_rejected(store, signers):
    with pytest.raises(ValueError):
        _raise(store, required_signers=signers)


@pytest.mark.parametrize("signers", [["   "], ["founder", " "], ["\t\n"]])
def test_w125_blank_signer_after_normalization_rejected(store, signers):
    with pytest.raises(ValueError):
        _raise(store, required_signers=signers)


def test_w125_stored_signers_are_canonical(store):
    pkg = _raise(store, required_signers=[" CTO ", "Cfo"])
    assert pkg.risk["required_signers"] == ["cto", "cfo"]
    # And the canonical form survives a reload from disk.
    reloaded = EscalationStore(store.base_dir).get_package(pkg.id)
    assert reloaded.risk["required_signers"] == ["cto", "cfo"]


# ---------------------------------------------------------------------------
# W-127 — spend-threshold coercion / fail-closed
# ---------------------------------------------------------------------------


def test_w127_string_amount_over_threshold_escalates():
    verdict = PolicyEngine.classify("metrics", {"amount": "999999", "threshold": 100})
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "spend_threshold_exceeded"


def test_w127_string_amount_under_threshold_allows():
    verdict = PolicyEngine.classify("metrics", {"amount": "50", "threshold": 100})
    assert verdict.verdict == ALLOW


def test_w127_nan_amount_fails_closed():
    verdict = PolicyEngine.classify(
        "metrics", {"amount": float("nan"), "threshold": 100}
    )
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "spend_threshold_unverifiable"


def test_w127_non_numeric_amount_fails_closed():
    verdict = PolicyEngine.classify(
        "metrics", {"amount": "not-a-number", "threshold": 100}
    )
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "spend_threshold_unverifiable"


def test_w127_bool_amount_fails_closed():
    verdict = PolicyEngine.classify("metrics", {"amount": True, "threshold": 100})
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "spend_threshold_unverifiable"


def test_w127_string_threshold_coerced():
    verdict = PolicyEngine.classify("metrics", {"amount": 500, "threshold": "100"})
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "spend_threshold_exceeded"


def test_w127_nan_threshold_fails_closed():
    verdict = PolicyEngine.classify(
        "metrics", {"amount": 500, "threshold": float("nan")}
    )
    assert verdict.verdict == ESCALATE
    assert verdict.reason == "spend_threshold_unverifiable"


def test_w127_numeric_control_and_no_threshold_unchanged():
    over = PolicyEngine.classify("metrics", {"amount": 999999, "threshold": 100})
    assert over.verdict == ESCALATE
    assert over.reason == "spend_threshold_exceeded"
    at = PolicyEngine.classify("metrics", {"amount": 100, "threshold": 100})
    assert at.verdict == ALLOW  # strictly greater only
    # No threshold present: no spend rule at all, string amount or not.
    no_threshold = PolicyEngine.classify("metrics", {"amount": "999999"})
    assert no_threshold.verdict == ALLOW


# ---------------------------------------------------------------------------
# W-128 — verify_chain slice anchoring
# ---------------------------------------------------------------------------


def _make_records(store, count=3):
    for _ in range(count):
        _raise(store)
    return store.read_audit_log()


def test_w128_mid_log_slices_verify(store):
    records = _make_records(store)
    assert len(records) >= 6
    assert verify_chain(records) is True
    assert verify_chain(records[2:]) is True
    assert verify_chain(records[4:]) is True
    assert verify_chain(records[-1:]) is True


def test_w128_tampered_slice_fails(store):
    records = _make_records(store)
    tampered = [dict(r) for r in records[2:]]
    tampered[1]["detail"] = "forged"
    assert verify_chain(tampered) is False


def test_w128_slice_with_deletion_fails(store):
    records = _make_records(store)
    broken = records[2:4] + records[5:]
    assert verify_chain(broken) is False


def test_w128_genesis_position_cannot_be_forged(store):
    """A record claiming seq 1 with a non-genesis prev_hash is a
    forgery, not a slice — even with a self-consistent record_hash."""
    records = _make_records(store, count=1)
    forged = dict(records[0])
    forged["prev_hash"] = "f" * 64
    body = {k: v for k, v in forged.items() if k != "record_hash"}
    forged["record_hash"] = _compute_record_hash(body)
    assert forged["prev_hash"] != GENESIS_PREV_HASH
    assert verify_chain([forged]) is False


def test_w128_signed_slice_verifies(tmp_path):
    signer = AuditSigner.generate("hardening-test")
    signed_store = EscalationStore(str(tmp_path / "signed"), signer=signer)
    records = _make_records(signed_store)
    assert verify_chain(records) is True
    assert verify_chain(records[2:]) is True
    assert (
        verify_chain(records[2:], expected_signer_pubkey=signer.signer_pubkey)
        is True
    )


# ---------------------------------------------------------------------------
# Sink concurrency — parallel emissions, one chain, no lost records
# ---------------------------------------------------------------------------


def _run_parallel(workers):
    """Run each callable in its own thread, all released together.

    Returns the list of exceptions raised (asserted empty by callers —
    thread exceptions otherwise vanish silently)."""
    barrier = threading.Barrier(len(workers))
    errors = []

    def wrap(fn):
        def run():
            try:
                barrier.wait(timeout=30)
                fn()
            except Exception as exc:  # noqa: BLE001 - collected + asserted
                errors.append(exc)

        return run

    threads = [threading.Thread(target=wrap(fn)) for fn in workers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(not thread.is_alive() for thread in threads)
    return errors


def test_parallel_fresh_store_emissions_form_one_chain(tmp_path):
    """Mirror of the sanctions tap: a fresh EscalationStore per
    emission, many threads at once. Before the fix this forked the
    chain (duplicate seq / shared prev_hash) and dropped packages."""
    base = str(tmp_path / "sink")
    thread_count, per_thread = 8, 5
    package_ids = []
    ids_lock = threading.Lock()

    def make_worker():
        def emit():
            for _ in range(per_thread):
                # Fresh store per emission — the tap's exact pattern.
                pkg = EscalationStore(base).create_package(
                    source_surface="sanctions-screen",
                    actor="sincor2.sanctions_screen",
                    action_category="legal_compliance",
                    summary="parallel emission",
                )
                with ids_lock:
                    package_ids.append(pkg.id)

        return emit

    errors = _run_parallel([make_worker() for _ in range(thread_count)])
    assert errors == []

    total = thread_count * per_thread
    assert len(package_ids) == total

    check = EscalationStore(base)
    records = check.read_audit_log()
    # created + classified per package, seq strictly 1..N, one chain.
    assert len(records) == total * 2
    assert [r["seq"] for r in records] == list(range(1, total * 2 + 1))
    assert check.verify_audit_chain() is True
    # No package lost from the state file.
    for package_id in package_ids:
        assert check.find_package(package_id) is not None


def test_parallel_decisions_on_fresh_stores_no_lost_updates(tmp_path):
    base = str(tmp_path / "sink")
    seed = EscalationStore(base)
    package_ids = [_raise(seed).id for _ in range(12)]

    def make_worker(package_id):
        def decide():
            EscalationStore(base).decide(
                package_id, "approve", by="court", role="operator", now=T3
            )

        return decide

    errors = _run_parallel([make_worker(pid) for pid in package_ids])
    assert errors == []

    check = EscalationStore(base)
    for package_id in package_ids:
        assert check.get_package(package_id).state == APPROVED
    assert check.verify_audit_chain() is True


def test_parallel_sanctions_tap_emissions(tmp_path, monkeypatch):
    """End-to-end through the real tap: parallel flagged screens with
    SINCOR_ESCALATION_SINK set must all land, chain intact."""
    sink = tmp_path / "sink"
    monkeypatch.setenv(SINK_ENV, str(sink))
    snapshot = dict(load_snapshot())
    snapshot["fetched_at"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()

    thread_count = 6

    def make_worker():
        def screen():
            result = ss.screen_wallet(FLAGGED_ADDR, snapshot=snapshot)
            assert result["verdict"] == "flagged"

        return screen

    errors = _run_parallel([make_worker() for _ in range(thread_count)])
    assert errors == []

    state = json.loads(
        (sink / "escalation_state.json").read_text(encoding="utf-8")
    )
    assert len(state["packages"]) == thread_count
    check = EscalationStore(str(sink))
    assert len(check.read_audit_log()) == thread_count * 2
    assert check.verify_audit_chain() is True