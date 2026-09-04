"""Node package manifest for deterministic SINAX memory-engine deployment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .memory_engine_contracts import MemoryContracts
from .swarm_protocol_lock import MEMORY_ENGINE_INTERFACE_LOCK_V1


@dataclass(frozen=True)
class NodePackageManifest:
    package_name: str
    version: str
    interface_lock: str
    deterministic_routing: bool
    frozen_contracts: Dict[str, str]
    epoch_id: str = ""
    merkle_root: str = ""


def build_node_package_manifest(
    version: str = "1.0.0",
    *,
    epoch_id: str = "",
    merkle_root: str = "",
) -> Dict[str, str | bool | Dict[str, str]]:
    contracts = MemoryContracts()
    manifest = NodePackageManifest(
        package_name="sincor-sinax-node",
        version=version,
        interface_lock=MEMORY_ENGINE_INTERFACE_LOCK_V1,
        deterministic_routing=True,
        frozen_contracts={
            "QueryPreFilter": contracts.query_pre_filter,
            "InsertSnapshotDelta": contracts.insert_snapshot_delta,
            "CompactWarmSegment": contracts.compact_warm_segment,
            "SwapColdEpoch": contracts.swap_cold_epoch,
        },
        epoch_id=epoch_id,
        merkle_root=merkle_root,
    )
    return {
        "package_name": manifest.package_name,
        "version": manifest.version,
        "interface_lock": manifest.interface_lock,
        "deterministic_routing": manifest.deterministic_routing,
        "frozen_contracts": manifest.frozen_contracts,
        "epoch_id": manifest.epoch_id,
        "merkle_root": manifest.merkle_root,
    }
