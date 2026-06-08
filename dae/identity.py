from __future__ import annotations

"""Decentralized identity and attestation helpers using did:key identifiers."""

import base64
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


@dataclass
class Attestation:
    """Represents a lightweight credential or claim attached to a DID."""

    issuer_did: str
    subject_did: str
    claim_type: str
    value: Dict[str, Any]
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attestation_id: str = field(default_factory=lambda: f"att-{uuid4().hex[:10]}")


class DecentralizedIdentity:
    """Creates, resolves, and verifies did:key identities and attestations."""

    def __init__(self) -> None:
        self.documents: Dict[str, Dict[str, Any]] = {}
        self.attestations: List[Attestation] = []

    def create_did(self, public_material: str) -> Dict[str, Any]:
        """Create a deterministic did:key document from public material."""
        fingerprint = base64.urlsafe_b64encode(hashlib.sha256(public_material.encode('utf-8')).digest()).decode('utf-8').rstrip('=')
        did = f'did:key:{fingerprint}'
        document = {
            'id': did,
            'verificationMethod': [
                {
                    'id': f'{did}#key-1',
                    'type': 'Ed25519VerificationKey2020',
                    'controller': did,
                    'publicKeyMultibase': fingerprint,
                }
            ],
            'authentication': [f'{did}#key-1'],
            'assertionMethod': [f'{did}#key-1'],
        }
        self.documents[did] = document
        return document

    def issue_attestation(self, issuer_did: str, subject_did: str, claim_type: str, value: Dict[str, Any]) -> Dict[str, Any]:
        """Issue an attestation from one DID to another."""
        attestation = Attestation(issuer_did=issuer_did, subject_did=subject_did, claim_type=claim_type, value=value)
        self.attestations.append(attestation)
        return asdict(attestation)

    def verify_credential(self, credential: Dict[str, Any]) -> bool:
        """Verify that a credential references known issuer and subject DIDs."""
        issuer = credential.get('issuer_did') or credential.get('issuerDid')
        subject = credential.get('subject_did') or credential.get('subjectDid')
        return issuer in self.documents and subject in self.documents

    def resolve_did(self, did: str) -> Dict[str, Any]:
        """Resolve a DID document from local state."""
        return self.documents[did]
