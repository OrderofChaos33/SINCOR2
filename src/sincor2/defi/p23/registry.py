"""P23 collection whitelist registry with 48-hour timelocked admin.

Adds and removals go through queue -> 48 h delay -> execute. A collection
removed from the whitelist freezes new deposits immediately (the vault reads
the active set at deposit time); existing positions keep full redemption
rights. Every change emits an event mirrored here for the Python monitor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import PARAMS


@dataclass
class TimelockOp:
    op_id: str
    kind: str            # "add" | "remove"
    collection: str      # ERC-721 collection address (checksummed string)
    queued_at: float
    executable_at: float
    executed: bool = False


@dataclass
class RegistryEvent:
    kind: str            # queued | executed | (un)whitelisted
    collection: str
    ts: float
    detail: str = ""


class RegistryError(Exception):
    """Whitelist invariant violation."""


class CollectionRegistry:
    def __init__(self, timelock_s: Optional[float] = None) -> None:
        self.timelock_s = timelock_s if timelock_s is not None else PARAMS["whitelist_timelock_s"]
        self._whitelisted: Dict[str, bool] = {}
        self._pending: Dict[str, TimelockOp] = {}
        self._op_seq = 0
        self.events: List[RegistryEvent] = []

    # -- admin (timelocked) ---------------------------------------------
    def _validate_address(self, collection: str) -> None:
        h = collection.lower().removeprefix("0x")
        if len(h) != 40 or any(c not in "0123456789abcdef" for c in h):
            raise RegistryError(f"bad collection address: {collection!r}")

    def queue_add(self, collection: str, now: Optional[float] = None) -> str:
        return self._queue("add", collection, now)

    def queue_remove(self, collection: str, now: Optional[float] = None) -> str:
        return self._queue("remove", collection, now)

    def _queue(self, kind: str, collection: str, now: Optional[float]) -> str:
        self._validate_address(collection)
        now = time.time() if now is None else now
        self._op_seq += 1
        op_id = f"op_{self._op_seq:06d}"
        self._pending[op_id] = TimelockOp(
            op_id=op_id, kind=kind, collection=collection,
            queued_at=now, executable_at=now + self.timelock_s,
        )
        self.events.append(RegistryEvent("queued", collection, now,
                                         f"{kind} {op_id}"))
        return op_id

    def execute(self, op_id: str, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        op = self._pending.get(op_id)
        if op is None:
            raise RegistryError(f"unknown timelock op {op_id!r}")
        if op.executed:
            raise RegistryError(f"op {op_id!r} already executed")
        if now < op.executable_at:
            raise RegistryError(
                f"op {op_id!r} timelocked until {op.executable_at:.0f} "
                f"(now {now:.0f}): 48 h delay not satisfied"
            )
        if op.kind == "add":
            self._whitelisted[op.collection] = True
        else:
            # Removal freezes new deposits immediately; redemptions stay open
            # (the vault checks this same set at deposit time only).
            self._whitelisted.pop(op.collection, None)
        op.executed = True
        self.events.append(RegistryEvent("executed", op.collection, now,
                                         f"{op.kind} {op_id}"))

    # -- reads -----------------------------------------------------------
    def is_whitelisted(self, collection: str) -> bool:
        return self._whitelisted.get(collection, False)

    def whitelisted_collections(self) -> List[str]:
        return sorted(self._whitelisted)

    def pending_ops(self) -> List[TimelockOp]:
        return [op for op in self._pending.values() if not op.executed]
