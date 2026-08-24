from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from marketplace.contract_net.keccak import keccak256  # noqa: E402
from marketplace.cortex_sim import run_settlement_scenario  # noqa: E402
from marketplace.optimistic import (  # noqa: E402
    CHALLENGE_BLOCKS,
    OptimisticBatcher,
    bid_commitment_hex,
    merkle_proof,
    merkle_root,
    verify_proof,
    verify_reveal,
)
from marketplace.optimistic.merkle import leaf_hash  # noqa: E402


def test_challenge_window_is_300_blocks() -> None:
    assert CHALLENGE_BLOCKS == 300


def test_merkle_root_is_deterministic_and_order_sensitive() -> None:
    a, b, c = keccak256(b"a"), keccak256(b"b"), keccak256(b"c")
    assert merkle_root([a, b, c]) == merkle_root([a, b, c])
    assert merkle_root([a, b, c]) != merkle_root([c, b, a])


def test_merkle_proof_roundtrip() -> None:
    leaves = [keccak256(bytes([i])) for i in range(5)]
    root = merkle_root(leaves)
    for i, leaf in enumerate(leaves):
        proof = merkle_proof(leaves, i)
        assert verify_proof(leaf, proof, root) is True
    # Tamper
    proof = merkle_proof(leaves, 0)
    assert verify_proof(keccak256(b"nope"), proof, root) is False


def test_single_leaf_root_is_the_leaf() -> None:
    leaf = keccak256(b"solo")
    assert merkle_root([leaf]) == leaf


def test_odd_leaf_count_self_pairs() -> None:
    leaves = [keccak256(bytes([i])) for i in range(3)]
    root = merkle_root(leaves)
    assert verify_proof(leaves[2], merkle_proof(leaves, 2), root)


def test_empty_root_is_keccak_empty() -> None:
    assert merkle_root([]) == keccak256(b"")


def test_commit_reveal_hides_price_until_match() -> None:
    salt = keccak256(b"salt-1")
    commit = bid_commitment_hex(850_000, salt, "atlas-scout")
    assert verify_reveal(commit, 850_000, salt, "atlas-scout")
    assert not verify_reveal(commit, 850_001, salt, "atlas-scout")
    assert not verify_reveal(commit, 850_000, keccak256(b"other"), "atlas-scout")
    assert not verify_reveal(commit, 850_000, salt, "other-agent")
    assert not commit.endswith("850000")
    assert "850000" not in commit


def test_batch_finalize_requires_300_blocks() -> None:
    batcher = OptimisticBatcher()
    batcher.channel.credit("atlas-scout", 50_000, "t1", 1.0)
    batcher.channel.credit("helix-synth", 40_000, "t2", 2.0)
    batch = batcher.seal(submitted_block=100)
    assert batch is not None
    assert batch.event_count == 2
    assert batch.challenge_deadline == 400
    try:
        batcher.finalize(batch.batch_id, 399)
        raise AssertionError("window still open")
    except ValueError as exc:
        assert "window" in str(exc)
    done = batcher.finalize(batch.batch_id, 400)
    assert done.status == "finalized"


def test_challenge_freezes_mismatched_leaf() -> None:
    batcher = OptimisticBatcher()
    batcher.channel.credit("atlas-scout", 10_000, "t1", 1.0)
    batcher.channel.credit("sybil-alpha", 10_000, "t2", 2.0)
    batch = batcher.seal(submitted_block=1)
    fake = leaf_hash(b"forged-credit")
    challenged = batcher.challenge(batch.batch_id, 1, fake, current_block=10)
    assert challenged.status == "challenged"
    try:
        batcher.finalize(batch.batch_id, 1 + 300)
        raise AssertionError("challenged batch must not finalize")
    except ValueError:
        pass


def test_matching_claimed_leaf_is_not_fraud() -> None:
    batcher = OptimisticBatcher()
    event = batcher.channel.credit("atlas-scout", 10_000, "t1", 1.0)
    batch = batcher.seal(submitted_block=1)
    try:
        batcher.challenge(batch.batch_id, 0, event.leaf, current_block=2)
        raise AssertionError("identical leaf is not a challenge")
    except ValueError as exc:
        assert "matches" in str(exc)
    assert batch.status == "pending"


def test_wrong_reveal_rejected_on_channel() -> None:
    batcher = OptimisticBatcher()
    salt = keccak256(b"s")
    batcher.commit_bid("auc", "atlas-scout", 1_000, salt, 1.0)
    bad = batcher.reveal_bid("auc", "atlas-scout", 1_000, keccak256(b"no"))
    assert bad.valid_reveal is False
    assert bad.reject_reason == "commitment_mismatch"


def test_duplicate_commit_rejected() -> None:
    batcher = OptimisticBatcher()
    salt = keccak256(b"s")
    batcher.commit_bid("auc", "atlas-scout", 1_000, salt, 1.0)
    try:
        batcher.commit_bid("auc", "atlas-scout", 2_000, salt, 2.0)
        raise AssertionError("duplicate commit")
    except ValueError:
        pass


def test_gas_amortized_across_events() -> None:
    batcher = OptimisticBatcher()
    for i in range(8):
        batcher.channel.credit("atlas-scout", 1000 + i, f"t{i}", float(i))
    batch = batcher.seal(10)
    assert batch.event_count == 8
    # One root submission instead of 8 L1 writes.
    assert batch.event_count - 1 == 7


def test_sim_finalizes_then_freezes_fraud() -> None:
    payload = run_settlement_scenario()
    assert payload["finalized"]["status"] == "finalized"
    assert payload["challenged"]["status"] == "challenged"
    assert payload["bad_reveal"]["valid"] is False
    assert payload["too_early"]
    assert payload["gas_saved_vs_per_event"] >= 23
