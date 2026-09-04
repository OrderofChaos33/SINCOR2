"""Node-side pipeline for attaching epoch commitments to on-chain transitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from sincor2.sinax.epoch_attestation import ERC7579EpochSessionValidator
from sincor2.sinax.vector_retrieval_engine import ThreeTierVectorEngine


@dataclass(frozen=True)
class EpochCommitmentEnvelope:
    payload: Dict[str, Any]
    epoch_id: str
    epoch_merkle_root: str
    state_commitment: str
    node_id: str
    node_signature: str


class EpochStateCommitmentPipeline:
    """Attaches active epoch commitments to task settlement/payout transitions."""

    def __init__(self, engine: ThreeTierVectorEngine, *, node_id: str = "node-0", signing_key: str = "sincor-node-key") -> None:
        self.engine = engine
        self.validator = ERC7579EpochSessionValidator(engine.swap)
        self.node_id = node_id
        self.signing_key = signing_key

    def build_envelope(self, payload: Mapping[str, Any], *, epoch_id: Optional[str] = None) -> EpochCommitmentEnvelope:
        proof = self.validator.attest_payload(payload, epoch_id=epoch_id)
        if not proof.epoch_id:
            return EpochCommitmentEnvelope(
                payload=dict(payload),
                epoch_id="",
                epoch_merkle_root="",
                state_commitment="",
                node_id=self.node_id,
                node_signature="",
            )
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{proof.epoch_id}|{proof.epoch_merkle_root}|{canonical}".encode("utf-8")).hexdigest()
        state_commitment = "0x" + digest
        return EpochCommitmentEnvelope(
            payload=dict(payload),
            epoch_id=proof.epoch_id,
            epoch_merkle_root=proof.epoch_merkle_root,
            state_commitment=state_commitment,
            node_id=self.node_id,
            node_signature=self._sign(state_commitment, proof.epoch_id, proof.epoch_merkle_root),
        )

    def verify_envelope(self, envelope: EpochCommitmentEnvelope) -> bool:
        if not envelope.epoch_id or not envelope.epoch_merkle_root:
            return False
        proof = self.validator.attest_payload(envelope.payload, epoch_id=envelope.epoch_id)
        if not self.validator.validate(proof):
            return False
        canonical = json.dumps(envelope.payload, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(f"{envelope.epoch_id}|{envelope.epoch_merkle_root}|{canonical}".encode("utf-8")).hexdigest()
        if envelope.state_commitment != ("0x" + expected):
            return False
        return envelope.node_signature == self._sign(envelope.state_commitment, envelope.epoch_id, envelope.epoch_merkle_root)

    def _sign(self, state_commitment: str, epoch_id: str, epoch_root: str) -> str:
        msg = f"{self.node_id}|{epoch_id}|{epoch_root}|{state_commitment}|{self.signing_key}"
        return "0x" + hashlib.sha256(msg.encode("utf-8")).hexdigest()
