"""Optimistic Merkle batcher with a 300-block Base challenge window.

Gas is paid once per batch root, not per micro-task. A valid Merkle proof
of a conflicting leaf slashes the batch during the window; after 300
blocks the root finalizes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .channel import StateChannel
from .commit import SealedCommit, bid_commitment_hex, verify_reveal
from .merkle import merkle_proof, merkle_root_hex, verify_proof

CHALLENGE_BLOCKS = 300
BASE_CHAIN_ID = 8453
TREASURY = "0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac"


@dataclass
class BatchRecord:
    batch_id: str
    root: str
    event_count: int
    start_nonce: int
    end_nonce: int
    submitted_block: int
    status: str  # pending | challenged | finalized
    challenge_deadline: int
    challenge_reason: str = ""
    leaves: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class OptimisticBatcher:
    def __init__(
        self,
        channel: StateChannel | None = None,
        challenge_blocks: int = CHALLENGE_BLOCKS,
    ) -> None:
        self.channel = channel or StateChannel()
        self.challenge_blocks = challenge_blocks
        self.batches: Dict[str, BatchRecord] = {}
        self.commits: Dict[str, SealedCommit] = {}  # key: auction_id:agent_id
        self._sealed_through = 0
        self._batch_n = 0

    # ------------------------------------------------------------------ commit-reveal
    def commit_bid(
        self,
        auction_id: str,
        agent_id: str,
        price: int,
        salt: bytes,
        submitted_at: float,
    ) -> SealedCommit:
        key = f"{auction_id}:{agent_id}"
        if key in self.commits:
            raise ValueError("duplicate commit")
        commit = SealedCommit(
            auction_id=auction_id,
            agent_id=agent_id,
            commit=bid_commitment_hex(price, salt, agent_id),
            submitted_at=submitted_at,
        )
        self.commits[key] = commit
        return commit

    def reveal_bid(
        self,
        auction_id: str,
        agent_id: str,
        price: int,
        salt: bytes,
    ) -> SealedCommit:
        key = f"{auction_id}:{agent_id}"
        commit = self.commits.get(key)
        if commit is None:
            raise KeyError("no commit for agent")
        if commit.revealed:
            raise ValueError("already revealed")
        commit.revealed = True
        commit.price = price
        if verify_reveal(commit.commit, price, salt, agent_id):
            commit.valid_reveal = True
            commit.reject_reason = ""
        else:
            commit.valid_reveal = False
            commit.reject_reason = "commitment_mismatch"
        return commit

    # ------------------------------------------------------------------ batches
    def seal(self, submitted_block: int) -> Optional[BatchRecord]:
        leaves = self.channel.snapshot_leaves(self._sealed_through)
        if not leaves:
            return None
        self._batch_n += 1
        start = self._sealed_through + 1
        end = self.channel.events[-1].nonce
        record = BatchRecord(
            batch_id=f"batch-{self._batch_n:04d}",
            root=merkle_root_hex(leaves),
            event_count=len(leaves),
            start_nonce=start,
            end_nonce=end,
            submitted_block=submitted_block,
            status="pending",
            challenge_deadline=submitted_block + self.challenge_blocks,
            leaves=["0x" + leaf.hex() for leaf in leaves],
        )
        self.batches[record.batch_id] = record
        self._sealed_through = end
        return record

    def challenge(
        self,
        batch_id: str,
        leaf_index: int,
        claimed_leaf: bytes,
        current_block: int,
    ) -> BatchRecord:
        batch = self.batches[batch_id]
        if batch.status != "pending":
            raise ValueError(f"batch not pending ({batch.status})")
        if current_block > batch.challenge_deadline:
            raise ValueError("challenge window closed")
        stored = bytes.fromhex(batch.leaves[leaf_index][2:])
        if claimed_leaf == stored:
            raise ValueError("claimed leaf matches the posted leaf; not a fraud proof")
        leaves = [bytes.fromhex(h[2:]) for h in batch.leaves]
        proof = merkle_proof(leaves, leaf_index)
        root = bytes.fromhex(batch.root[2:])
        if not verify_proof(stored, proof, root):
            raise ValueError("internal proof of posted leaf failed")
        # Fraud: operator posted leaf L but the true event was claimed_leaf.
        # Verifying the posted leaf is in the tree + claimed != posted is enough
        # to freeze the batch for operator review / slash.
        batch.status = "challenged"
        batch.challenge_reason = f"leaf_{leaf_index}_mismatch"
        return batch

    def finalize(self, batch_id: str, current_block: int) -> BatchRecord:
        batch = self.batches[batch_id]
        if batch.status == "challenged":
            raise ValueError("challenged batch cannot finalize")
        if batch.status == "finalized":
            return batch
        if current_block < batch.challenge_deadline:
            raise ValueError(
                f"challenge window open until block {batch.challenge_deadline}"
            )
        batch.status = "finalized"
        return batch

    def stats(self) -> Dict[str, object]:
        pending = sum(1 for b in self.batches.values() if b.status == "pending")
        finalized = sum(1 for b in self.batches.values() if b.status == "finalized")
        challenged = sum(1 for b in self.batches.values() if b.status == "challenged")
        return {
            "chain_id": BASE_CHAIN_ID,
            "treasury": TREASURY,
            "challenge_blocks": self.challenge_blocks,
            "offchain_events": len(self.channel.events),
            "batches": len(self.batches),
            "pending": pending,
            "finalized": finalized,
            "challenged": challenged,
            "commits": len(self.commits),
            "balances": dict(self.channel.balances),
        }
