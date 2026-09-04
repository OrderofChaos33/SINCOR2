"""Epoch binding publication + ERC-7579-style execution attestation checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from .vector_retrieval_engine import AtomicSwapController


@dataclass(frozen=True)
class ExecutionProof:
    epoch_id: str
    epoch_merkle_root: str
    payload_hash: str


class ERC7579EpochSessionValidator:
    """Validator enforcing active epoch-root binding for execution proofs."""

    def __init__(self, controller: AtomicSwapController) -> None:
        self._controller = controller

    def validate(self, proof: ExecutionProof) -> bool:
        if not proof.epoch_id or not proof.epoch_merkle_root:
            return False
        epoch = self._controller.get_epoch(proof.epoch_id)
        if epoch is None:
            return False
        if epoch.merkle_root != proof.epoch_merkle_root:
            return False
        return True

    def attest_payload(self, payload: Mapping[str, object], epoch_id: Optional[str] = None) -> ExecutionProof:
        epoch = self._controller.get_epoch(epoch_id) if epoch_id else self._controller.active_epoch()
        if epoch is None:
            return ExecutionProof(epoch_id="", epoch_merkle_root="", payload_hash="")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return ExecutionProof(epoch_id=epoch.epoch_id, epoch_merkle_root=epoch.merkle_root, payload_hash=payload_hash)


def publish_epoch_manifest(controller: AtomicSwapController, manifest_path: str) -> dict:
    epoch = controller.active_epoch()
    payload = {
        "epoch_id": epoch.epoch_id if epoch else "",
        "merkle_root": epoch.merkle_root if epoch else "",
    }
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return payload
