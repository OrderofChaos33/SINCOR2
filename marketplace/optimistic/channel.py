"""Off-chain state channel for micro-task AXM, merit points, and assignments.

Every mutation appends a canonical leaf. Nothing hits Base until the
batcher seals a Merkle root.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List

from .merkle import encode_event, leaf_hash


@dataclass
class ChannelEvent:
    nonce: int
    agent_id: str
    kind: str  # assignment | merit | credit | debit
    delta_axm: int
    ref: str
    created_at: float
    leaf: bytes = b""

    def to_dict(self) -> Dict[str, object]:
        payload = asdict(self)
        payload["leaf"] = "0x" + self.leaf.hex()
        return payload


class StateChannel:
    def __init__(self) -> None:
        self.balances: Dict[str, int] = {}
        self.merit_points: Dict[str, int] = {}
        self.assignments: Dict[str, str] = {}
        self.events: List[ChannelEvent] = []
        self._nonce = 0

    def _append(
        self,
        *,
        agent_id: str,
        kind: str,
        delta_axm: int,
        ref: str,
        created_at: float,
    ) -> ChannelEvent:
        self._nonce += 1
        payload = encode_event(
            agent_id=agent_id,
            delta_axm=delta_axm,
            nonce=self._nonce,
            kind=kind,
            ref=ref,
        )
        event = ChannelEvent(
            nonce=self._nonce,
            agent_id=agent_id,
            kind=kind,
            delta_axm=delta_axm,
            ref=ref,
            created_at=created_at,
            leaf=leaf_hash(payload),
        )
        self.events.append(event)
        return event

    def assign(self, task_id: str, agent_id: str, created_at: float) -> ChannelEvent:
        self.assignments[task_id] = agent_id
        return self._append(
            agent_id=agent_id,
            kind="assignment",
            delta_axm=0,
            ref=task_id,
            created_at=created_at,
        )

    def credit(self, agent_id: str, amount: int, ref: str, created_at: float) -> ChannelEvent:
        if amount <= 0:
            raise ValueError("credit amount must be positive")
        self.balances[agent_id] = self.balances.get(agent_id, 0) + amount
        return self._append(
            agent_id=agent_id,
            kind="credit",
            delta_axm=amount,
            ref=ref,
            created_at=created_at,
        )

    def debit(self, agent_id: str, amount: int, ref: str, created_at: float) -> ChannelEvent:
        if amount <= 0:
            raise ValueError("debit amount must be positive")
        current = self.balances.get(agent_id, 0)
        if current < amount:
            raise ValueError("insufficient channel balance")
        self.balances[agent_id] = current - amount
        return self._append(
            agent_id=agent_id,
            kind="debit",
            delta_axm=-amount,
            ref=ref,
            created_at=created_at,
        )

    def add_merit(self, agent_id: str, points: int, ref: str, created_at: float) -> ChannelEvent:
        self.merit_points[agent_id] = self.merit_points.get(agent_id, 0) + points
        return self._append(
            agent_id=agent_id,
            kind="merit",
            delta_axm=0,
            ref=f"{ref}:{points}",
            created_at=created_at,
        )

    def snapshot_leaves(self, start_nonce: int = 0) -> List[bytes]:
        return [event.leaf for event in self.events if event.nonce > start_nonce]
