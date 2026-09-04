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


class EpochStateCommitmentPipeline:
    """Attaches active epoch commitments to task settlement/payout transitions."""

    def __init__(self, engine: ThreeTierVectorEngine) -> None:
        self.engine = engine
        self.validator = ERC7579EpochSessionValidator(engine.swap)

    def build_envelope(self, payload: Mapping[str, Any], *, epoch_id: Optional[str] = None) -> EpochCommitmentEnvelope:
        proof = self.validator.attest_payload(payload, epoch_id=epoch_id)
        if not proof.epoch_id:
            return EpochCommitmentEnvelope(payload=dict(payload), epoch_id="", epoch_merkle_root="", state_commitment="")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{proof.epoch_id}|{proof.epoch_merkle_root}|{canonical}".encode("utf-8")).hexdigest()
        return EpochCommitmentEnvelope(
            payload=dict(payload),
            epoch_id=proof.epoch_id,
            epoch_merkle_root=proof.epoch_merkle_root,
            state_commitment="0x" + digest,
        )

    def verify_envelope(self, envelope: EpochCommitmentEnvelope) -> bool:
        if not envelope.epoch_id or not envelope.epoch_merkle_root:
            return False
        proof = self.validator.attest_payload(envelope.payload, epoch_id=envelope.epoch_id)
        if not self.validator.validate(proof):
            return False
        canonical = json.dumps(envelope.payload, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(f"{envelope.epoch_id}|{envelope.epoch_merkle_root}|{canonical}".encode("utf-8")).hexdigest()
        return envelope.state_commitment == ("0x" + expected)
