"""Phase 2 Merkle epoch anchoring (directive pillar 2.4).

Aggregates escalation audit-record hashes into Merkle trees over
caller-specified epochs and records the roots in an append-only,
hash-chained anchor log. An anchor record is the *payload* a future
ledger-submission step needs — epoch range, entry count, root, and
the previous anchor's root.

**Honesty boundary (hard).** Nothing in this module submits anything
anywhere. Anchor records carry ``status: "PREPARED"`` precisely
because committing a root to a distributed / permissioned ledger is a
broadcast — a founder-gated hard gate that is out of scope for this
build. Code, tests, and docs for this module must never describe an
anchor as on-chain, anchored-on-ledger, or final-settled.

Merkle construction (spec'd so test vectors are stable):

- Leaves are hex-encoded sha256 hashes (the audit records'
  ``record_hash`` values).
- Parent = sha256 over the concatenation of the two child hashes'
  raw bytes.
- Odd leaf/node count duplicates the last item (Bitcoin rule).
- A single leaf is its own root; an empty leaf list is refused.

Anchor chaining: each anchor record stores ``prev_anchor_root`` =
the previous anchor's ``merkle_root``; the first anchor's
``prev_anchor_root`` is 64 zero hex chars (the same genesis constant
shape as the audit chain, deliberately NOT shared state — the anchor
log is its own chain and re-derives everything from the source
records it is handed at verification time).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

__all__ = [
    "ANCHOR_STATUS_PREPARED",
    "GENESIS_ANCHOR_ROOT",
    "AnchorStoreError",
    "AnchorStoreCorruptError",
    "merkle_root",
    "inclusion_proof",
    "verify_inclusion",
    "anchor_payload",
    "AnchorStore",
]

#: The only status an anchor record can carry in this build. Any other
#: status would claim a ledger submission happened — it has not.
ANCHOR_STATUS_PREPARED = "PREPARED"

#: prev_anchor_root of the first anchor record: 64 zero hex chars.
GENESIS_ANCHOR_ROOT = "0" * 64


class AnchorStoreError(Exception):
    """Base class for Merkle anchor errors."""


class AnchorStoreCorruptError(AnchorStoreError):
    """The on-disk anchor log is unreadable/invalid — fail closed."""


# ---------------------------------------------------------------------------
# Merkle tree primitives
# ---------------------------------------------------------------------------


def _hash_pair(left_hex: str, right_hex: str) -> str:
    return hashlib.sha256(
        bytes.fromhex(left_hex) + bytes.fromhex(right_hex)
    ).hexdigest()


def _next_level(level: list) -> list:
    nxt = []
    for i in range(0, len(level), 2):
        left = level[i]
        right = level[i + 1] if i + 1 < len(level) else level[i]
        nxt.append(_hash_pair(left, right))
    return nxt


def merkle_root(hashes: list) -> str:
    """Merkle root over hex hash strings (sha256, Bitcoin odd rule).

    A single leaf is its own root. An empty list raises ValueError —
    there is no root for nothing, and an epoch must never anchor air.
    """
    if not hashes:
        raise ValueError("merkle_root of an empty hash list is undefined")
    level = list(hashes)
    while len(level) > 1:
        level = _next_level(level)
    return level[0]


def inclusion_proof(hashes: list, index: int) -> list:
    """Sibling-path inclusion proof for ``hashes[index]``.

    Returns a list of ``{"position": "left"|"right", "hash": hex}``
    steps, leaf level first: ``position`` says where the sibling sits
    relative to the running node. When the running node is the last
    item of an odd-count level, its sibling is itself (the duplicate
    of the Bitcoin rule), recorded as a right-hand step.
    """
    if not hashes:
        raise ValueError("inclusion_proof of an empty hash list is undefined")
    if not 0 <= index < len(hashes):
        raise IndexError(
            f"leaf index {index} out of range for {len(hashes)} leaves"
        )
    proof = []
    level = list(hashes)
    idx = index
    while len(level) > 1:
        if idx % 2 == 1:
            proof.append({"position": "left", "hash": level[idx - 1]})
        elif idx + 1 < len(level):
            proof.append({"position": "right", "hash": level[idx + 1]})
        else:
            # Last node of an odd-count level: duplicated as its own
            # right-hand sibling.
            proof.append({"position": "right", "hash": level[idx]})
        level = _next_level(level)
        idx //= 2
    return proof


def verify_inclusion(leaf: str, proof: list, root: str) -> bool:
    """Fold a sibling-path proof from ``leaf`` and compare to ``root``.

    Returns False (never raises) for a malformed proof, a leaf that is
    not in the tree, or a root mismatch.
    """
    try:
        current = leaf
        for step in proof:
            sibling = step["hash"]
            if step["position"] == "left":
                current = _hash_pair(sibling, current)
            elif step["position"] == "right":
                current = _hash_pair(current, sibling)
            else:
                return False
        return current == root
    except (KeyError, TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Anchor store
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def anchor_payload(anchor: dict) -> dict:
    """The submission-ready payload for one anchor record.

    Exactly what a future (founder-gated) ledger-submission step
    consumes: the root, the epoch range it covers, the chain link, and
    the honest status. Building this payload submits nothing.
    """
    return {
        "epoch_id": anchor["epoch_id"],
        "seq_start": anchor["seq_start"],
        "seq_end": anchor["seq_end"],
        "entry_count": anchor["entry_count"],
        "merkle_root": anchor["merkle_root"],
        "prev_anchor_root": anchor["prev_anchor_root"],
        "created_at": anchor["created_at"],
        "status": anchor["status"],
    }


class AnchorStore:
    """Append-only JSONL log of Merkle anchor records.

    Layout inside ``base_dir``: ``merkle_anchors.jsonl`` — one JSON
    anchor record per epoch, append only, chained by
    ``prev_anchor_root`` (see the module docstring). Anchors are
    verified by re-computing every root from the source audit records
    handed to :meth:`verify_anchors`; the anchor file alone is never
    trusted.
    """

    ANCHOR_FILENAME = "merkle_anchors.jsonl"

    def __init__(self, base_dir: str):
        self.base_dir = str(base_dir)
        os.makedirs(self.base_dir, exist_ok=True)
        self.anchor_path = os.path.join(self.base_dir, self.ANCHOR_FILENAME)
        self._lock = threading.Lock()
        self._last_root = GENESIS_ANCHOR_ROOT
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.anchor_path):
            try:
                with open(self.anchor_path, "r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            record = json.loads(line)
                            self._last_root = str(record["merkle_root"])
            except (ValueError, KeyError, TypeError) as exc:
                raise AnchorStoreCorruptError(
                    f"anchor log is corrupt: {self.anchor_path}: {exc}"
                ) from exc

    def read_anchors(self) -> list:
        """Return all anchor records in append order (oldest first)."""
        if not os.path.exists(self.anchor_path):
            return []
        anchors = []
        with open(self.anchor_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    anchors.append(json.loads(line))
        return anchors

    def create_anchor(
        self,
        records: list,
        seq_start: int,
        seq_end: int,
        now: Optional[str] = None,
    ) -> dict:
        """Anchor one epoch of audit records and return the anchor.

        ``records`` are audit record dicts in seq order, each carrying
        ``record_hash``; the Merkle root is computed over those hashes
        in the order given. An empty epoch is refused (ValueError) —
        no anchor for nothing. The record is appended to the anchor
        log with status ``"PREPARED"``; nothing is submitted anywhere.
        """
        if not records:
            raise ValueError("cannot anchor an empty epoch (no records)")
        root = merkle_root([str(r["record_hash"]) for r in records])
        with self._lock:
            anchor = {
                "epoch_id": "epoch_" + uuid.uuid4().hex,
                "seq_start": seq_start,
                "seq_end": seq_end,
                "entry_count": len(records),
                "merkle_root": root,
                "prev_anchor_root": self._last_root,
                "created_at": now or _utcnow_iso(),
                "status": ANCHOR_STATUS_PREPARED,
            }
            with open(self.anchor_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(anchor, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            self._last_root = root
            return dict(anchor)

    def verify_anchors(self, records: list) -> bool:
        """Re-verify every anchor against the source audit records.

        ``records`` is the source audit log (dicts with ``seq`` and
        ``record_hash``). For each anchor, in file order: the
        ``prev_anchor_root`` chain link must match the previous
        anchor's root (genesis = 64 zeros for the first), the epoch's
        records must all be present with the recorded entry count, and
        the recomputed Merkle root must equal the anchored root.
        Tampering with an anchor record or with any covered source
        record makes this return False; verification failures never
        raise out of this method.
        """
        try:
            by_seq = {}
            for record in records:
                by_seq[int(record["seq"])] = str(record["record_hash"])
            prev_root = GENESIS_ANCHOR_ROOT
            for anchor in self.read_anchors():
                if anchor.get("status") != ANCHOR_STATUS_PREPARED:
                    return False
                if anchor.get("prev_anchor_root") != prev_root:
                    return False
                seq_start = int(anchor["seq_start"])
                seq_end = int(anchor["seq_end"])
                hashes = []
                for seq in range(seq_start, seq_end + 1):
                    if seq not in by_seq:
                        return False
                    hashes.append(by_seq[seq])
                if len(hashes) != int(anchor["entry_count"]):
                    return False
                if merkle_root(hashes) != anchor.get("merkle_root"):
                    return False
                prev_root = anchor["merkle_root"]
            return True
        except (KeyError, TypeError, ValueError, AttributeError):
            return False

    # Class-level access to the module payload helper, for callers
    # that hold only the store.
    anchor_payload = staticmethod(anchor_payload)
