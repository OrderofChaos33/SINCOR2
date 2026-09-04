"""Node package manifest for deterministic SINAX memory-engine deployment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

from .swarm_protocol_lock import MEMORY_ENGINE_INTERFACE_LOCK_V1


@dataclass(frozen=True)
class NodePackageManifest:
    package_name: str
    version: str
    interface_lock: str
    deterministic_routing: bool


def build_node_package_manifest(version: str = "1.0.0") -> Dict[str, str | bool]:
    manifest = NodePackageManifest(
        package_name="sincor-sinax-node",
        version=version,
        interface_lock=MEMORY_ENGINE_INTERFACE_LOCK_V1,
        deterministic_routing=True,
    )
    return {
        "package_name": manifest.package_name,
        "version": manifest.version,
        "interface_lock": manifest.interface_lock,
        "deterministic_routing": manifest.deterministic_routing,
    }
