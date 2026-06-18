from __future__ import annotations

"""Provider credentialing workflows for healthcare onboarding and compliance."""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional
from uuid import uuid4


@dataclass
class ProviderCredential:
    """Represents the credentialing profile for a provider or facility."""

    provider_id: str
    full_name: str
    npi: str
    specialty: str
    license_number: str
    license_state: str
    license_expiry: date
    caqh_id: Optional[str] = None
    facility_name: Optional[str] = None
    participating_payers: List[str] = field(default_factory=list)


class CredentialingAgent:
    """Handles CAQH enrollment, payer enrollment, and license surveillance."""

    def __init__(self) -> None:
        self.providers: Dict[str, ProviderCredential] = {}
        self.enrollment_status: Dict[str, Dict[str, str]] = {}

    def add_provider(self, credential: ProviderCredential) -> ProviderCredential:
        """Store provider data for downstream credentialing operations."""
        self.providers[credential.provider_id] = credential
        return credential

    def initiate_caqh_enrollment(self, credential: ProviderCredential) -> Dict[str, str]:
        """Generate a mock CAQH enrollment package for a provider."""
        self.providers[credential.provider_id] = credential
        caqh_id = credential.caqh_id or f"CAQH-{uuid4().hex[:8].upper()}"
        credential.caqh_id = caqh_id
        status = {
            'provider_id': credential.provider_id,
            'caqh_id': caqh_id,
            'status': 'initiated',
            'application_packet': 'CAQH profile, malpractice, W-9, license, and board certification queued.',
        }
        self.enrollment_status.setdefault(credential.provider_id, {})['caqh'] = 'initiated'
        return status

    def track_license_expiry(self, provider_id: str) -> Dict[str, str]:
        """Return license surveillance details and renewal urgency."""
        credential = self.providers[provider_id]
        days_remaining = (credential.license_expiry - date.today()).days
        if days_remaining <= 30:
            severity = 'critical'
        elif days_remaining <= 90:
            severity = 'warning'
        else:
            severity = 'healthy'
        return {
            'provider_id': provider_id,
            'license_number': credential.license_number,
            'license_state': credential.license_state,
            'license_expiry': credential.license_expiry.isoformat(),
            'days_remaining': str(days_remaining),
            'severity': severity,
        }

    def submit_payer_enrollment(self, provider_id: str, payer_name: str) -> Dict[str, str]:
        """Create a mock payer enrollment submission record."""
        credential = self.providers[provider_id]
        credential.participating_payers.append(payer_name)
        enrollment_id = f"ENR-{uuid4().hex[:10].upper()}"
        self.enrollment_status.setdefault(provider_id, {})[payer_name] = 'submitted'
        return {
            'provider_id': provider_id,
            'payer_name': payer_name,
            'enrollment_id': enrollment_id,
            'status': 'submitted',
            'attachments': 'CAQH attestation, state license, NPI letter, and facility privileges included.',
        }
