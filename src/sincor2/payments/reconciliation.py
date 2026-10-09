"""WP4: auction settlement reconciliation scaffolding.

D4 (TOA): OFF-CHAIN ONLY for launch. On-chain anchoring/funding is deferred
to post-launch v2. This module builds the reconciliation SCAFFOLDING now —
the mapping from off-chain auction assignment to winner/price/beneficiary —
so the v2 has the data model ready. ALL broadcast/funding flags stay OFF.

There is deliberately NO code here that:
- signs transactions
- broadcasts to any chain
- funds escrow on-chain
- calls any contract

The reconciler records off-chain settlement facts and exposes the mapping
a future on-chain v2 will need to verify against. When v2 lands, it must
prove: off-chain assigned worker == on-chain winner, agreed price ==
funded amount, beneficiary == intended recipient — before any funding.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True, slots=True)
class SettlementRecord:
    """Immutable off-chain settlement fact.

    Maps an off-chain auction assignment to the settlement that the
    (future) on-chain v2 must reconcile against before funding.
    """

    record_id: str
    # Off-chain auction/task identifiers
    auction_id: str
    task_id: str
    # The worker the off-chain system assigned
    assigned_worker_id: str
    assigned_worker_wallet: str
    # Agreed economics (atomic units; asset is USDC|AXM per D3)
    asset: str
    amount_atomic: int
    # Intended beneficiary (may differ from worker for platform fees)
    beneficiary_wallet: str
    platform_fee_atomic: int = 0
    # Lifecycle: assigned -> settled_offchain -> (v2: anchored_onchain)
    status: str = "assigned"
    created_at: float = field(default_factory=time.time)
    settled_at: Optional[float] = None
    # v2 hook: filled when on-chain anchoring lands; NEVER filled now
    onchain_tx_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if self.asset not in ("USDC", "AXM"):
            raise ValueError(f"unsupported asset {self.asset!r}: USDC|AXM only (D3)")
        if self.amount_atomic <= 0:
            raise ValueError("amount_atomic must be positive")
        if self.status not in ("assigned", "settled_offchain", "anchored_onchain"):
            raise ValueError(f"unknown status {self.status!r}")
        if self.onchain_tx_hash is not None:
            # D4: no on-chain anchoring until v2. This field must stay None.
            raise ValueError(
                "onchain_tx_hash must be None: on-chain anchoring deferred to v2 (D4)"
            )


class SettlementReconciler:
    """Thread-safe registry of off-chain settlement records.

    v2 reconciliation contract (to be enforced when on-chain lands):
      assigned_worker_wallet == onchain_winner
      amount_atomic == onchain_funded_amount
      beneficiary_wallet == onchain_beneficiary
    Any mismatch -> funding REFUSED. This class stores the left side of
    those comparisons; the right side does not exist yet (D4).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, SettlementRecord] = {}

    def record_assignment(self, record: SettlementRecord) -> SettlementRecord:
        """Store an off-chain assignment. Fails on duplicate record_id."""
        with self._lock:
            if record.record_id in self._records:
                raise ValueError(f"duplicate record_id {record.record_id!r}")
            self._records[record.record_id] = record
            return record

    def mark_settled_offchain(self, record_id: str) -> SettlementRecord:
        """Transition assigned -> settled_offchain. No chain interaction."""
        with self._lock:
            rec = self._records.get(record_id)
            if rec is None:
                raise KeyError(f"unknown record_id {record_id!r}")
            if rec.status != "assigned":
                raise ValueError(f"cannot settle from status {rec.status!r}")
            updated = SettlementRecord(
                record_id=rec.record_id,
                auction_id=rec.auction_id,
                task_id=rec.task_id,
                assigned_worker_id=rec.assigned_worker_id,
                assigned_worker_wallet=rec.assigned_worker_wallet,
                asset=rec.asset,
                amount_atomic=rec.amount_atomic,
                beneficiary_wallet=rec.beneficiary_wallet,
                platform_fee_atomic=rec.platform_fee_atomic,
                status="settled_offchain",
                created_at=rec.created_at,
                settled_at=time.time(),
            )
            self._records[record_id] = updated
            return updated

    def get(self, record_id: str) -> Optional[SettlementRecord]:
        with self._lock:
            return self._records.get(record_id)

    def pending_reconciliation(self) -> List[SettlementRecord]:
        """Records settled off-chain but not yet anchored (all of them, per D4)."""
        with self._lock:
            return [r for r in self._records.values() if r.status == "settled_offchain"]

    def reconciliation_view(self, record_id: str) -> Optional[dict]:
        """The mapping v2 must verify against on-chain data before funding."""
        rec = self.get(record_id)
        if rec is None:
            return None
        return {
            "expected_onchain_winner": rec.assigned_worker_wallet,
            "expected_funded_amount_atomic": rec.amount_atomic,
            "expected_beneficiary": rec.beneficiary_wallet,
            "expected_platform_fee_atomic": rec.platform_fee_atomic,
            "asset": rec.asset,
            # v2 MUST compare these against actual chain state; any
            # mismatch refuses funding. No chain state exists yet (D4).
            "onchain_anchored": False,
        }

    def clear(self) -> None:
        """Test helper."""
        with self._lock:
            self._records.clear()
