"""WP4: atomic idempotency for payment fulfillment.

The tx/event ID (fulfillment_id) is claimed ATOMICALLY before any
fulfillment work begins. Concurrent duplicate submissions for the same
idempotency_key collapse to a single fulfillment: the first claim wins,
all others get the original claim record.

Thread-safe via a single lock. For multi-process deployments the store
must be backed by a real atomic primitive (DB unique constraint / Redis
SETNX) — the in-process store here is the reference implementation and
the test double; production wiring must substitute a durable backend.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    """Immutable record of a claimed fulfillment."""

    idempotency_key: str
    fulfillment_id: str
    intent_id: str
    claimed_at: float = field(default_factory=time.time)
    # True if this claim was the first (the winner); False for duplicates
    # that collapsed onto an existing claim.
    is_original: bool = True


class IdempotencyStore:
    """Thread-safe atomic claim store for fulfillment IDs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claims: Dict[str, ClaimRecord] = {}

    def claim(
        self, *, idempotency_key: str, fulfillment_id: str, intent_id: str
    ) -> ClaimRecord:
        """Atomically claim a fulfillment. First claim wins.

        Returns the ClaimRecord. If the idempotency_key was already
        claimed, returns the ORIGINAL record with is_original=False —
        the caller must NOT fulfill again.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required")
        if not fulfillment_id:
            raise ValueError("fulfillment_id is required")
        with self._lock:
            existing = self._claims.get(idempotency_key)
            if existing is not None:
                return ClaimRecord(
                    idempotency_key=existing.idempotency_key,
                    fulfillment_id=existing.fulfillment_id,
                    intent_id=existing.intent_id,
                    claimed_at=existing.claimed_at,
                    is_original=False,
                )
            record = ClaimRecord(
                idempotency_key=idempotency_key,
                fulfillment_id=fulfillment_id,
                intent_id=intent_id,
            )
            self._claims[idempotency_key] = record
            return record

    def get(self, idempotency_key: str) -> Optional[ClaimRecord]:
        with self._lock:
            return self._claims.get(idempotency_key)

    def clear(self) -> None:
        """Test helper: reset the store."""
        with self._lock:
            self._claims.clear()


# Module-global store (single-process reference). Production must replace
# with a durable atomic backend; see module docstring.
_store = IdempotencyStore()


def claim_fulfillment(
    *, idempotency_key: str, fulfillment_id: str, intent_id: str
) -> ClaimRecord:
    """Claim a fulfillment against the process-global store."""
    return _store.claim(
        idempotency_key=idempotency_key,
        fulfillment_id=fulfillment_id,
        intent_id=intent_id,
    )


def reset_claims() -> None:
    """Test helper: clear the process-global store."""
    _store.clear()
