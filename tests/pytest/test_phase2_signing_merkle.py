"""Phase 2 — execution-log signing (pillar 2.1) + Merkle anchoring (2.4).

Covers the Phase 2 build spec (2026-11-01):

1. Signed append -> signature verifies; tampering any content field
   (or the signature itself) -> verification fails.
2. Wrong-key signature (signed by A, verified expecting B) -> invalid.
3. Unsigned legacy (Phase 1) chains still verify — backward compat.
4. Mixed chains (unsigned history + signed tail) verify.
5. generate_signing_key refuses overwrite; key-file round-trip loads
   and signs; the env-var loader resolves the key file; no private
   key material ever lands in the audit log.
6. Merkle roots: known vectors for 1/2/3/4 leaves, computed
   independently in this file with hashlib (never by calling the
   implementation twice).
7. Inclusion proofs verify for every index of a 5-leaf tree; a
   mutated leaf, a mutated proof step, or a wrong root fails.
8. Anchors over real escalation audit records: creation, chaining,
   payload, verify; a tampered source record or a tampered anchor
   record fails; an empty epoch is refused.

Nothing here (or in the implementation) claims on-chain anchoring:
anchor records are status "PREPARED" — a payload ready for a future,
founder-gated submission step that this build deliberately omits.
"""

import hashlib
import json

import pytest

from sincor2.escalation import (
    GENESIS_PREV_HASH,
    PACKAGE_SCHEMA_VERSION,
    AuditSigner,
    EscalationStore,
    generate_signing_key,
    load_signer_from_env,
    verify_chain,
)
from sincor2.merkle_anchor import (
    AnchorStore,
    anchor_payload,
    inclusion_proof,
    merkle_root,
    verify_inclusion,
)

NOW = "2026-11-01T00:00:00+00:00"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hconcat(*hex_hashes: str) -> str:
    return hashlib.sha256(b"".join(bytes.fromhex(h) for h in hex_hashes)).hexdigest()


def _make_package(store, **overrides):
    kwargs = dict(
        source_surface="test-surface",
        actor="agent-1",
        action_category="funds",  # hard-gate category -> policy_id "hard-gate"
        summary="move funds (test)",
        now=NOW,
    )
    kwargs.update(overrides)
    return store.create_package(**kwargs)


# ---------------------------------------------------------------------------
# 1. Signed append + tamper evidence
# ---------------------------------------------------------------------------


def test_signed_append_verifies_and_carries_signature_block(tmp_path):
    signer = AuditSigner.generate("test-interceptor")
    store = EscalationStore(str(tmp_path / "store"), signer=signer)
    _make_package(store)

    records = store.read_audit_log()
    assert len(records) == 2  # created + classified
    for record in records:
        sig = record["signature"]
        assert sig["alg"] == "Ed25519"
        assert sig["signer_id"] == "test-interceptor"
        assert sig["signer_pubkey"] == signer.signer_pubkey
        assert len(sig["signature"]) == 128  # 64-byte Ed25519 sig, hex

    assert verify_chain(records) is True
    assert store.verify_audit_chain() is True


def test_signed_records_carry_policy_id_and_version(tmp_path):
    signer = AuditSigner.generate("test-interceptor")
    store = EscalationStore(str(tmp_path / "store"), signer=signer)
    _make_package(store)

    for record in store.read_audit_log():
        assert record["policy_id"] == "hard-gate"
        assert record["policy_version"] == PACKAGE_SCHEMA_VERSION


def test_tampering_any_content_field_breaks_signed_chain(tmp_path):
    signer = AuditSigner.generate("test-interceptor")
    store = EscalationStore(str(tmp_path / "store"), signer=signer)
    _make_package(store)
    records = store.read_audit_log()

    for field_name, forged in (
        ("detail", "forged detail"),
        ("by", "mallory"),
        ("policy_id", "internal-allowlist"),
        ("note", "forged note"),
    ):
        tampered = [dict(r) for r in records]
        tampered[0][field_name] = forged
        assert verify_chain(tampered) is False, field_name

    # Forging the signature bytes themselves also fails.
    tampered = [dict(r) for r in records]
    sig = dict(tampered[0]["signature"])
    sig["signature"] = ("0" if sig["signature"][0] != "0" else "1") + sig[
        "signature"
    ][1:]
    tampered[0]["signature"] = sig
    assert verify_chain(tampered) is False

    # Signature PRESENCE is deliberately not hash-bound: the spec's
    # backward-compat rule says records without a signature block
    # verify on hash links alone (Phase 1 history is not retroactively
    # invalidated, and mixed chains verify), so a record whose
    # signature block was stripped is indistinguishable from legacy
    # unsigned history and verifies as such. What the signature does
    # guarantee, per the cases above: the record's CONTENT cannot be
    # altered, and the signature bytes cannot be forged. (Detecting
    # signature-stripping would need a require-signed-from-seq mode,
    # which the spec does not define.)
    stripped = [dict(r) for r in records]
    del stripped[0]["signature"]
    assert verify_chain(stripped) is True


# ---------------------------------------------------------------------------
# 2. Wrong key
# ---------------------------------------------------------------------------


def test_wrong_expected_key_invalidates_signed_chain(tmp_path):
    signer_a = AuditSigner.generate("node-a")
    signer_b = AuditSigner.generate("node-b")
    store_dir = str(tmp_path / "store")
    store = EscalationStore(store_dir, signer=signer_a)
    _make_package(store)
    records = store.read_audit_log()

    # Embedded-pubkey verification (no expectation) passes...
    assert verify_chain(records) is True
    # ...expecting the actual signer passes...
    assert verify_chain(records, expected_signer_pubkey=signer_a.signer_pubkey)
    # ...expecting any other key fails.
    assert (
        verify_chain(records, expected_signer_pubkey=signer_b.signer_pubkey)
        is False
    )

    # A verifying store configured with the wrong signer also fails;
    # one configured with the right signer passes.
    assert EscalationStore(store_dir, signer=signer_b).verify_audit_chain() is False
    assert EscalationStore(store_dir, signer=signer_a).verify_audit_chain() is True


# ---------------------------------------------------------------------------
# 3 + 4. Backward compatibility: legacy unsigned and mixed chains
# ---------------------------------------------------------------------------


def test_unsigned_legacy_chain_still_verifies(tmp_path):
    store = EscalationStore(str(tmp_path / "store"))  # signer=None (default)
    package = _make_package(store)
    store.decide(package.id, "approve", by="operator", now=NOW)

    records = store.read_audit_log()
    assert records, "expected audit records"
    assert all("signature" not in r for r in records)
    assert verify_chain(records) is True
    assert store.verify_audit_chain() is True
    # A JSON round-trip (what any external auditor would do) verifies too.
    assert verify_chain(json.loads(json.dumps(records))) is True


def test_mixed_unsigned_history_and_signed_tail_verifies(tmp_path):
    store_dir = str(tmp_path / "store")
    legacy = EscalationStore(store_dir)
    _make_package(legacy, summary="legacy unsigned package")

    signer = AuditSigner.generate("tail-node")
    upgraded = EscalationStore(store_dir, signer=signer)
    pkg = _make_package(upgraded, summary="signed tail package")
    upgraded.decide(pkg.id, "deny", by="operator", now=NOW)

    records = upgraded.read_audit_log()
    unsigned = [r for r in records if "signature" not in r]
    signed = [r for r in records if "signature" in r]
    assert unsigned and signed, "chain must actually be mixed"
    assert [r["seq"] for r in unsigned] == [1, 2]
    assert verify_chain(records) is True
    assert verify_chain(records, expected_signer_pubkey=signer.signer_pubkey)
    assert upgraded.verify_audit_chain() is True


# ---------------------------------------------------------------------------
# 5. Key generation / loading
# ---------------------------------------------------------------------------


def test_generate_signing_key_refuses_overwrite_and_round_trips(
    tmp_path, monkeypatch
):
    key_path = tmp_path / "keys" / "interceptor.pem"
    pubkey = generate_signing_key(str(key_path))
    assert key_path.exists()
    pem_text = key_path.read_text(encoding="utf-8")
    assert "BEGIN PRIVATE KEY" in pem_text

    with pytest.raises(FileExistsError):
        generate_signing_key(str(key_path))

    signer = AuditSigner.from_key_file(str(key_path), "file-node")
    assert signer.signer_pubkey == pubkey

    # The env-var loader resolves the same key file.
    monkeypatch.setenv("SINCOR_ESCALATION_SIGNING_KEY_FILE", str(key_path))
    env_signer = load_signer_from_env(signer_id="env-node")
    assert env_signer is not None
    assert env_signer.signer_pubkey == pubkey
    assert env_signer.signer_id == "env-node"
    monkeypatch.delenv("SINCOR_ESCALATION_SIGNING_KEY_FILE")
    assert load_signer_from_env() is None

    # A store signing with the file-loaded key verifies, and no private
    # key material ever lands in the audit log.
    store = EscalationStore(str(tmp_path / "store"), signer=signer)
    _make_package(store)
    assert store.verify_audit_chain() is True
    audit_text = (tmp_path / "store" / EscalationStore.AUDIT_FILENAME).read_text(
        encoding="utf-8"
    )
    assert "PRIVATE KEY" not in audit_text
    pem_body_line = pem_text.strip().splitlines()[1]
    assert pem_body_line not in audit_text


# ---------------------------------------------------------------------------
# 6. Merkle root vectors (computed independently, with hashlib only)
# ---------------------------------------------------------------------------


def _leaves(n):
    return [_sha(b"leaf-%d" % i) for i in range(n)]


def test_merkle_root_known_vectors():
    leaves = _leaves(4)

    assert merkle_root(leaves[:1]) == leaves[0]

    assert merkle_root(leaves[:2]) == _hconcat(leaves[0], leaves[1])

    # Odd count: the last leaf is duplicated (Bitcoin rule).
    h01 = _hconcat(leaves[0], leaves[1])
    h22 = _hconcat(leaves[2], leaves[2])
    assert merkle_root(leaves[:3]) == _hconcat(h01, h22)

    h23 = _hconcat(leaves[2], leaves[3])
    assert merkle_root(leaves[:4]) == _hconcat(h01, h23)


def test_merkle_root_empty_raises():
    with pytest.raises(ValueError):
        merkle_root([])


# ---------------------------------------------------------------------------
# 7. Inclusion proofs
# ---------------------------------------------------------------------------


def test_inclusion_proofs_five_leaf_tree():
    leaves = _leaves(5)
    root = merkle_root(leaves)

    for index, leaf in enumerate(leaves):
        proof = inclusion_proof(leaves, index)
        assert verify_inclusion(leaf, proof, root) is True

    # Single-leaf tree: empty proof, root is the leaf.
    assert inclusion_proof(leaves[:1], 0) == []
    assert verify_inclusion(leaves[0], [], leaves[0]) is True

    proof0 = inclusion_proof(leaves, 0)
    # A leaf that is not in the tree fails against a real proof + root.
    assert verify_inclusion(_sha(b"not-in-tree"), proof0, root) is False
    # The right leaf/proof against the wrong root fails.
    assert verify_inclusion(leaves[0], proof0, merkle_root(_leaves(4))) is False
    # A mutated proof step fails.
    bad_proof = [dict(step) for step in proof0]
    bad_proof[0]["hash"] = _sha(b"forged-sibling")
    assert verify_inclusion(leaves[0], bad_proof, root) is False
    # A proof for one index does not prove a different leaf.
    assert verify_inclusion(leaves[1], proof0, root) is False

    with pytest.raises(IndexError):
        inclusion_proof(leaves, 5)


# ---------------------------------------------------------------------------
# 8. Anchors over real escalation audit records
# ---------------------------------------------------------------------------


def _real_records(tmp_path):
    store = EscalationStore(str(tmp_path / "esc-store"))
    _make_package(store, summary="first")
    _make_package(store, summary="second")
    records = store.read_audit_log()
    assert len(records) == 4
    return records


def test_anchor_creation_chaining_and_verify(tmp_path):
    records = _real_records(tmp_path)
    anchors = AnchorStore(str(tmp_path / "anchors"))

    first = anchors.create_anchor(records[:2], seq_start=1, seq_end=2, now=NOW)
    second = anchors.create_anchor(records[2:], seq_start=3, seq_end=4, now=NOW)

    assert first["status"] == "PREPARED"
    assert first["entry_count"] == 2
    assert first["merkle_root"] == merkle_root(
        [r["record_hash"] for r in records[:2]]
    )
    assert first["prev_anchor_root"] == GENESIS_PREV_HASH  # 64 zeros
    assert second["prev_anchor_root"] == first["merkle_root"]
    assert second["entry_count"] == 2

    payload = anchor_payload(second)
    assert payload["merkle_root"] == second["merkle_root"]
    assert payload["seq_start"] == 3
    assert payload["seq_end"] == 4
    assert payload["status"] == "PREPARED"

    assert anchors.verify_anchors(records) is True
    # A fresh instance reading the same directory verifies too.
    assert AnchorStore(str(tmp_path / "anchors")).verify_anchors(records) is True


def test_anchor_verify_fails_on_tampered_source_or_anchor(tmp_path):
    records = _real_records(tmp_path)
    anchor_dir = tmp_path / "anchors"
    anchors = AnchorStore(str(anchor_dir))
    anchors.create_anchor(records, seq_start=1, seq_end=4, now=NOW)
    assert anchors.verify_anchors(records) is True

    # Tamper one source record's hash -> the recomputed root differs.
    forged = [dict(r) for r in records]
    forged[1]["record_hash"] = _sha(b"forged-record")
    assert anchors.verify_anchors(forged) is False

    # Drop a source record -> entry count / root no longer matches.
    assert anchors.verify_anchors(records[:3]) is False

    # Tamper the stored anchor record itself -> verify fails.
    anchor_path = anchor_dir / AnchorStore.ANCHOR_FILENAME
    lines = anchor_path.read_text(encoding="utf-8").strip().splitlines()
    stored = json.loads(lines[0])
    stored["merkle_root"] = _sha(b"forged-root")
    anchor_path.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    assert AnchorStore(str(anchor_dir)).verify_anchors(records) is False


def test_anchor_empty_epoch_refused(tmp_path):
    anchors = AnchorStore(str(tmp_path / "anchors"))
    with pytest.raises(ValueError):
        anchors.create_anchor([], seq_start=5, seq_end=4, now=NOW)
    assert anchors.read_anchors() == []
