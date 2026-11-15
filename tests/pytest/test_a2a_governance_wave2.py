"""A2A governance hardening wave 2 (2026-11-08) — red-team register fixes.

Findings closed here, each with the hunt PoC as the failing test:

- **W-122 (highest priority, pre-wiring):** ``EscalationStore.decide()``
  took ``by``/``role`` as caller-supplied strings; one unauthenticated
  caller attesting "founder" as mallory and "cfo" as mallory2 completed
  the quorum (string-theater distinctness). Fix: an opt-in operator
  registry (``operator_keys`` / ``SINCOR_ESCALATION_OPERATOR_KEYS_FILE``)
  binds every decision to a registered Ed25519 identity — the claimed
  ``by`` must be registered, the claimed ``role`` must be authorized
  for that identity, and the attestation must carry a valid signature
  over the canonical attestation payload. ``require_decision`` re-
  verifies the signatures, so a forged attestation written straight
  into the state file does not count. With no registry configured the
  Phase 1 string behavior is preserved byte-for-byte.

- **W-129:** ``decide``/``get_package``/``find_package`` were tenant-
  blind. Each now accepts an optional ``tenant``; when given, a
  mismatch is indistinguishable from a missing package.

- **W-130:** ``WorkflowStateStore.execute_step`` keyed idempotency on
  (run, key) only — reusing one key across two step names returned the
  first step's result and never ran the second step. A key/name
  mismatch now raises.

- **W-131:** ``resume()`` replayed events without ever calling
  ``verify_chain()`` — a tampered log silently rebuilt wrong state.
  ``resume()`` now fails closed on a broken chain.

- **W-134:** Merkle anchor epochs were caller-supplied with no
  partition requirement — overlapping or gapped epochs verified True.
  ``create_anchor`` now requires contiguity with the previous anchor
  (and a record count matching the range); ``verify_anchors`` asserts
  the epoch set partitions the log with no overlap or gap.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

import pytest

import sincor2.escalation as esc
from sincor2.escalation import EscalationStore, require_decision
from sincor2.workflow_state import (
    WorkflowStateError,
    WorkflowStateStore,
)
from sincor2.merkle_anchor import AnchorStore, merkle_root


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    return priv, pub


def _sign(priv, package_id, kind, role, by, note=None):
    payload = esc.attestation_payload_bytes(package_id, kind, role, by, note)
    return priv.sign(payload).hex()


def _package(store, tenant="sincor"):
    return store.create_package(
        source_surface="test", actor="test", action_category="metrics",
        summary="wave2", tenant=tenant,
        violations=[{"policy_id": "test", "confidence": 0.9}],
        required_signers=["founder", "cfo"],
    )


@pytest.fixture
def registry():
    founder_priv, founder_pub = _keypair()
    cfo_priv, cfo_pub = _keypair()
    keys = {
        "founder-op": {"pubkey": founder_pub, "roles": ["founder"]},
        "cfo-op": {"pubkey": cfo_pub, "roles": ["cfo"]},
    }
    return keys, {"founder-op": founder_priv, "cfo-op": cfo_priv}


# ---------------------------------------------------------------------------
# W-122: quorum identity binding
# ---------------------------------------------------------------------------

def test_w122_unsigned_decide_rejected_when_registry_configured(
        tmp_path, registry):
    keys, _ = registry
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    with pytest.raises(ValueError):
        store.decide(pkg.id, "approve", by="founder-op", role="founder")


def test_w122_unregistered_identity_rejected(tmp_path, registry):
    keys, _ = registry
    mallory_priv, _ = _keypair()
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    sig = _sign(mallory_priv, pkg.id, "approve", "founder", "mallory")
    with pytest.raises(ValueError):
        store.decide(pkg.id, "approve", by="mallory", role="founder",
                     signature=sig)


def test_w122_wrong_key_signature_rejected(tmp_path, registry):
    keys, privs = registry
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    # cfo's key signing a claim to be founder-op.
    sig = _sign(privs["cfo-op"], pkg.id, "approve", "founder", "founder-op")
    with pytest.raises(ValueError):
        store.decide(pkg.id, "approve", by="founder-op", role="founder",
                     signature=sig)


def test_w122_role_not_authorized_for_identity_rejected(tmp_path, registry):
    keys, privs = registry
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    sig = _sign(privs["founder-op"], pkg.id, "approve", "cfo", "founder-op")
    with pytest.raises(ValueError):
        store.decide(pkg.id, "approve", by="founder-op", role="cfo",
                     signature=sig)


def test_w122_one_identity_cannot_fill_two_roles(tmp_path):
    priv, pub = _keypair()
    keys = {"ops-lead": {"pubkey": pub, "roles": ["founder", "cfo"]}}
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    sig1 = _sign(priv, pkg.id, "approve", "founder", "ops-lead")
    store.decide(pkg.id, "approve", by="ops-lead", role="founder",
                 signature=sig1)
    sig2 = _sign(priv, pkg.id, "approve", "cfo", "ops-lead")
    after = store.decide(pkg.id, "approve", by="ops-lead", role="cfo",
                         signature=sig2)
    assert after.state == esc.PENDING
    assert require_decision(store, pkg.id) is False


def test_w122_two_registered_identities_complete_quorum(tmp_path, registry):
    keys, privs = registry
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    sig1 = _sign(privs["founder-op"], pkg.id, "approve", "founder",
                 "founder-op")
    store.decide(pkg.id, "approve", by="founder-op", role="founder",
                 signature=sig1)
    sig2 = _sign(privs["cfo-op"], pkg.id, "approve", "cfo", "cfo-op")
    after = store.decide(pkg.id, "approve", by="cfo-op", role="cfo",
                         signature=sig2)
    assert after.state == esc.APPROVED
    assert require_decision(store, pkg.id) is True


def test_w122_legacy_store_without_registry_keeps_phase1_behavior(tmp_path):
    # Control: no registry -> caller-supplied strings still work exactly
    # as Phase 1 (the enforcement is opt-in, like the Phase 2 signer).
    store = EscalationStore(str(tmp_path))
    pkg = _package(store)
    store.decide(pkg.id, "approve", by="alice", role="founder")
    after = store.decide(pkg.id, "approve", by="bob", role="cfo")
    assert after.state == esc.APPROVED


def test_w122_require_decision_rejects_forged_stored_attestation(
        tmp_path, registry):
    keys, privs = registry
    store = EscalationStore(str(tmp_path), operator_keys=keys)
    pkg = _package(store)
    sig1 = _sign(privs["founder-op"], pkg.id, "approve", "founder",
                 "founder-op")
    store.decide(pkg.id, "approve", by="founder-op", role="founder",
                 signature=sig1)
    sig2 = _sign(privs["cfo-op"], pkg.id, "approve", "cfo", "cfo-op")
    store.decide(pkg.id, "approve", by="cfo-op", role="cfo", signature=sig2)
    assert require_decision(store, pkg.id) is True
    # Forge: corrupt one stored attestation signature in the state file.
    state_path = os.path.join(str(tmp_path), EscalationStore.STATE_FILENAME)
    with open(state_path, encoding="utf-8") as fh:
        data = json.load(fh)
    atts = data["packages"][pkg.id]["attestations"]
    atts[0]["signature"] = "00" * 64
    with open(state_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    fresh = EscalationStore(str(tmp_path), operator_keys=keys)
    assert require_decision(fresh, pkg.id) is False


def test_w122_operator_keys_loader(tmp_path, monkeypatch, registry):
    keys, _ = registry
    path = tmp_path / "operators.json"
    path.write_text(json.dumps(keys), encoding="utf-8")
    monkeypatch.setenv(esc.OPERATOR_KEYS_ENV_VAR, str(path))
    loaded = esc.load_operator_keys_from_env()
    assert loaded["founder-op"]["pubkey"] == keys["founder-op"]["pubkey"]
    assert loaded["founder-op"]["roles"] == ["founder"]
    monkeypatch.delenv(esc.OPERATOR_KEYS_ENV_VAR)
    assert esc.load_operator_keys_from_env() is None


# ---------------------------------------------------------------------------
# W-129: tenant checks on decide/get/find
# ---------------------------------------------------------------------------

def test_w129_decide_wrong_tenant_rejected(tmp_path):
    store = EscalationStore(str(tmp_path))
    pkg = _package(store, tenant="acme")
    with pytest.raises(esc.PackageNotFoundError):
        store.decide(pkg.id, "approve", by="alice", role="founder",
                     tenant="globex")
    after = store.decide(pkg.id, "approve", by="alice", role="founder",
                         tenant="acme")
    assert after is not None


def test_w129_get_and_find_wrong_tenant(tmp_path):
    store = EscalationStore(str(tmp_path))
    pkg = _package(store, tenant="acme")
    with pytest.raises(esc.PackageNotFoundError):
        store.get_package(pkg.id, tenant="globex")
    assert store.find_package(pkg.id, tenant="globex") is None
    assert store.get_package(pkg.id, tenant="acme").id == pkg.id
    assert store.find_package(pkg.id, tenant="acme").id == pkg.id
    # Legacy: no tenant argument still returns the package.
    assert store.get_package(pkg.id).id == pkg.id


# ---------------------------------------------------------------------------
# W-130: idempotency key collision across step names
# ---------------------------------------------------------------------------

def test_w130_step_key_reuse_across_step_names_raises():
    store = WorkflowStateStore()
    store.create_run("run-1")
    calls = []
    first = store.execute_step(
        "run-1", "pay", "k1", lambda: calls.append("pay") or 100)
    assert first == 100
    with pytest.raises(WorkflowStateError):
        store.execute_step(
            "run-1", "refund", "k1",
            lambda: calls.append("refund") or -100)
    assert calls == ["pay"]  # the refund fn never ran
    assert store.step_result("run-1", "k1") == 100
    # Control: same name + same key still returns the recorded result.
    again = store.execute_step(
        "run-1", "pay", "k1", lambda: calls.append("pay2") or 999)
    assert again == 100
    assert calls == ["pay"]


# ---------------------------------------------------------------------------
# W-131: resume() must verify the chain before replaying
# ---------------------------------------------------------------------------

def test_w131_resume_fails_closed_on_tampered_chain(tmp_path):
    db_path = str(tmp_path / "ws.db")
    store = WorkflowStateStore.open(db_path)
    store.create_run("run-1")
    store.apply_patch("run-1", {"balance": 100})
    store.apply_patch("run-1", {"balance": 200})
    ok = store.resume("run-1")
    assert ok.state["balance"] == 200

    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE ws_events SET payload = ? WHERE run_id = 'run-1'"
        " AND seq = 3",
        ('{"patch": {"balance": 999999}}',),
    )
    conn.commit()
    conn.close()
    assert store.verify_chain("run-1") is False
    with pytest.raises(WorkflowStateError):
        store.resume("run-1")


# ---------------------------------------------------------------------------
# W-134: anchor epochs must partition the log
# ---------------------------------------------------------------------------

def _records(n=6):
    return [
        {"seq": i, "record_hash": hashlib.sha256(f"r{i}".encode()).hexdigest()}
        for i in range(1, n + 1)
    ]


def test_w134_create_anchor_overlap_rejected(tmp_path):
    records = _records()
    anchors = AnchorStore(str(tmp_path))
    anchors.create_anchor(records[:2], seq_start=1, seq_end=2)
    with pytest.raises(ValueError):
        anchors.create_anchor(records[1:3], seq_start=2, seq_end=3)


def test_w134_create_anchor_gap_rejected(tmp_path):
    records = _records()
    anchors = AnchorStore(str(tmp_path))
    anchors.create_anchor(records[:2], seq_start=1, seq_end=2)
    with pytest.raises(ValueError):
        anchors.create_anchor(records[4:], seq_start=5, seq_end=6)


def test_w134_create_anchor_count_mismatch_rejected(tmp_path):
    records = _records()
    anchors = AnchorStore(str(tmp_path))
    with pytest.raises(ValueError):
        anchors.create_anchor(records[:2], seq_start=1, seq_end=4)


def _write_anchor_log(tmp_path, epochs):
    """Hand-write an anchor log with cryptographically valid roots for
    the given (seq_start, seq_end) epochs — the pre-fix shape."""
    records = _records()
    by_seq = {r["seq"]: r["record_hash"] for r in records}
    lines = []
    prev = "0" * 64
    for start, end in epochs:
        hashes = [by_seq[s] for s in range(start, end + 1)]
        root = merkle_root(hashes)
        lines.append(json.dumps({
            "epoch_id": f"epoch_{start}_{end}",
            "seq_start": start, "seq_end": end,
            "entry_count": len(hashes), "merkle_root": root,
            "prev_anchor_root": prev,
            "created_at": "2026-11-08T00:00:00+00:00",
            "status": "PREPARED",
        }, sort_keys=True))
        prev = root
    (tmp_path / AnchorStore.ANCHOR_FILENAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    return records


def test_w134_verify_rejects_overlapping_epochs(tmp_path):
    records = _write_anchor_log(tmp_path, [(1, 2), (2, 3)])
    anchors = AnchorStore(str(tmp_path))
    assert anchors.verify_anchors(records) is False


def test_w134_verify_rejects_gapped_epochs(tmp_path):
    records = _write_anchor_log(tmp_path, [(1, 2), (5, 6)])
    anchors = AnchorStore(str(tmp_path))
    assert anchors.verify_anchors(records) is False


def test_w134_verify_accepts_contiguous_partition(tmp_path):
    records = _records()
    anchors = AnchorStore(str(tmp_path))
    anchors.create_anchor(records[:2], seq_start=1, seq_end=2)
    anchors.create_anchor(records[2:4], seq_start=3, seq_end=4)
    assert anchors.verify_anchors(records) is True
