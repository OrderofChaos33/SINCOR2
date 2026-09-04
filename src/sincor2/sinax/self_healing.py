"""Automated quarantine and re-admission for Contract-Net routing nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Set


@dataclass(frozen=True)
class NodeHealthState:
    node_id: str
    epoch_id: str
    determinism_ok: bool
    sync_ok: bool


@dataclass
class ActiveRoutingTable:
    active_nodes: Set[str] = field(default_factory=set)
    quarantined_nodes: Dict[str, str] = field(default_factory=dict)

    def seed(self, node_ids: Iterable[str]) -> None:
        for node_id in node_ids:
            if node_id not in self.quarantined_nodes:
                self.active_nodes.add(node_id)

    def quarantine(self, node_id: str, reason: str) -> None:
        self.active_nodes.discard(node_id)
        self.quarantined_nodes[node_id] = reason

    def reinstate(self, node_id: str) -> None:
        self.quarantined_nodes.pop(node_id, None)
        self.active_nodes.add(node_id)


@dataclass(frozen=True)
class SelfHealingReport:
    expected_epoch: str
    quarantined: Dict[str, str]
    reinstated: Set[str]
    active_count: int


class SelfHealingCoordinator:
    """Prunes nodes that fail determinism or epoch sync checks until healthy."""

    def __init__(self, table: ActiveRoutingTable) -> None:
        self.table = table

    def reconcile(self, *, expected_epoch: str, states: Iterable[NodeHealthState]) -> SelfHealingReport:
        quarantined: Dict[str, str] = {}
        reinstated: Set[str] = set()

        for state in states:
            if not state.sync_ok or state.epoch_id != expected_epoch:
                reason = "epoch_sync_failed"
                self.table.quarantine(state.node_id, reason)
                quarantined[state.node_id] = reason
                continue
            if not state.determinism_ok:
                reason = "determinism_failed"
                self.table.quarantine(state.node_id, reason)
                quarantined[state.node_id] = reason
                continue

            if state.node_id in self.table.quarantined_nodes:
                self.table.reinstate(state.node_id)
                reinstated.add(state.node_id)
            else:
                self.table.active_nodes.add(state.node_id)

        return SelfHealingReport(
            expected_epoch=expected_epoch,
            quarantined=quarantined,
            reinstated=reinstated,
            active_count=len(self.table.active_nodes),
        )
