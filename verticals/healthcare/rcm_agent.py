from __future__ import annotations

"""Healthcare revenue cycle workflows with mock EDI transaction support."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class PriorAuthRequest:
    """Represents a prior authorization submission for payer review."""

    patient_id: str
    member_id: str
    payer_id: str
    provider_npi: str
    cpt_codes: List[str]
    diagnosis_codes: List[str]
    requested_start_date: date
    requested_units: int = 1
    notes: str = ""
    request_id: str = field(default_factory=lambda: f"PA-{uuid4().hex[:10].upper()}")
    status: str = "draft"


@dataclass
class ClaimSubmission:
    """Represents a professional claim submitted via a mock 837 payload."""

    claim_id: str
    patient_id: str
    payer_id: str
    provider_npi: str
    cpt_codes: List[str]
    total_charge: Decimal
    billed_date: date
    status: str = "created"
    metadata: Dict[str, Any] = field(default_factory=dict)


class HealthcareRCMAgent:
    """Coordinates common revenue cycle tasks for healthcare operators."""

    def __init__(self) -> None:
        self.prior_auth_requests: Dict[str, PriorAuthRequest] = {}
        self.claims: Dict[str, ClaimSubmission] = {}
        self.era_history: List[Dict[str, Any]] = []

    def submit_prior_auth(self, request: PriorAuthRequest) -> Dict[str, Any]:
        """Create a mock EDI 278 prior authorization request and acceptance receipt."""
        request.status = "submitted"
        self.prior_auth_requests[request.request_id] = request
        service_date = request.requested_start_date.strftime('%Y%m%d')
        now = datetime.now(timezone.utc)
        edi_278 = (
            f"ISA*00*          *00*          *ZZ*SINCOR2        *ZZ*{request.payer_id:<15}"
            f"*{now.strftime('%y%m%d')}*{now.strftime('%H%M')}"
            "*^*00501*000000905*0*T*:~GS*HI*SINCOR2*PAYER*"
            f"{now.strftime('%Y%m%d')}*{now.strftime('%H%M')}"
            "*1*X*005010X217~ST*278*0001*005010X217~BHT*0007*13*AUTHREQ*"
            f"{service_date}*{now.strftime('%H%M')}~HL*1**20*1~NM1*1P*2*SINCOR2*****XX*{request.provider_npi}"
            f"~NM1*IL*1*{request.patient_id}****MI*{request.member_id}~UM*HS*I*{request.requested_units}***{service_date}"
            f"~HI*ABK:{':'.join(request.diagnosis_codes)}~SV1*HC:{':'.join(request.cpt_codes)}*{request.requested_units}*UN~SE*9*0001~GE*1*1~IEA*1*000000905~"
        )
        return {
            'request_id': request.request_id,
            'status': request.status,
            'edi_278': edi_278,
            'estimated_turnaround_days': 5,
            'payer_reference': f"AUTH-{uuid4().hex[:8].upper()}",
        }

    def check_claim_status(self, claim_id: str) -> Dict[str, Any]:
        """Return a normalized claim status with mock adjudication details."""
        claim = self.claims.get(claim_id)
        if claim is None:
            return {
                'claim_id': claim_id,
                'status': 'not_found',
                'message': 'Claim is not present in the in-memory RCM ledger.',
            }

        age_days = max((date.today() - claim.billed_date).days, 0)
        if age_days < 2:
            lifecycle_status = 'submitted'
        elif age_days < 7:
            lifecycle_status = 'in_review'
        elif age_days < 21:
            lifecycle_status = 'pending_payment'
        else:
            lifecycle_status = 'paid'

        claim.status = lifecycle_status
        return {
            'claim_id': claim.claim_id,
            'status': lifecycle_status,
            'payer_id': claim.payer_id,
            'age_days': age_days,
            'claim_total': str(claim.total_charge.quantize(Decimal('0.01'))),
            'next_action': 'monitor_remittance' if lifecycle_status == 'pending_payment' else 'none',
            'mock_837_summary': {
                'provider_npi': claim.provider_npi,
                'patient_id': claim.patient_id,
                'cpt_codes': claim.cpt_codes,
            },
        }

    def process_era(self, era_payload: Any) -> Dict[str, Any]:
        """Parse a mock ERA / 835 payload into claim payment details."""
        if isinstance(era_payload, str):
            segments = [segment for segment in era_payload.strip().split('~') if segment]
            claim_lines = [segment for segment in segments if segment.startswith('CLP')]
            total_paid = Decimal('0.00')
            payments: List[Dict[str, Any]] = []
            for line in claim_lines:
                parts = line.split('*')
                claim_id = parts[1] if len(parts) > 1 else 'UNKNOWN'
                paid = Decimal(parts[4]) if len(parts) > 4 else Decimal('0.00')
                total_paid += paid
                payments.append({'claim_id': claim_id, 'paid_amount': str(paid), 'status': parts[2] if len(parts) > 2 else 'unknown'})
        else:
            payments = list(era_payload.get('payments', []))
            total_paid = sum((Decimal(str(item.get('paid_amount', '0'))) for item in payments), Decimal('0.00'))

        acknowledgement = (
            'ST*835*0001~BPR*C*{paid}*C*ACH*CCP*01*999999999*DA*123456789*1512345678**01*011000015*DA*9988776655*'
            '{effective}~TRN*1*{trace}*1512345678~SE*4*0001~'
        ).format(
            paid=str(total_paid.quantize(Decimal('0.01'))),
            effective=datetime.now(timezone.utc).strftime('%Y%m%d'),
            trace=uuid4().hex[:12].upper(),
        )
        summary = {
            'payment_count': len(payments),
            'total_paid': str(total_paid.quantize(Decimal('0.01'))),
            'payments': payments,
            'mock_835_acknowledgement': acknowledgement,
            'processed_at': datetime.now(timezone.utc).isoformat(),
        }
        self.era_history.append(summary)
        return summary

    def verify_eligibility(self, member_id: str, payer_id: str, service_date: date, provider_npi: str) -> Dict[str, Any]:
        """Build a mock 270 request and 271 response for eligibility verification."""
        request_270 = (
            f"ST*270*0001*005010X279A1~BHT*0022*13*ELIG{member_id[-4:]}*{service_date.strftime('%Y%m%d')}*"
            f"{datetime.now(timezone.utc).strftime('%H%M')}~NM1*1P*2*SINCOR2*****XX*{provider_npi}~"
            f"NM1*IL*1*MEMBER****MI*{member_id}~DMG*D8*19800101~DTP*291*D8*{service_date.strftime('%Y%m%d')}~SE*6*0001~"
        )
        response_271 = {
            'member_id': member_id,
            'payer_id': payer_id,
            'service_date': service_date.isoformat(),
            'coverage_status': 'active',
            'copay': '35.00',
            'deductible_remaining': '500.00',
            'benefits': ['professional_services', 'outpatient', 'telehealth'],
            'mock_270': request_270,
            'mock_271': (
                f"ST*271*0001*005010X279A1~HL*1**20*1~NM1*PR*2*PAYER*****PI*{payer_id}~"
                f"NM1*IL*1*MEMBER****MI*{member_id}~EB*1**30***ACTIVE COVERAGE~EB*B**98***COPAY 35.00~SE*6*0001~"
            ),
        }
        return response_271

    def create_claim(self, claim: ClaimSubmission) -> ClaimSubmission:
        """Persist a claim in the local ledger for later status checks."""
        claim.status = 'submitted'
        self.claims[claim.claim_id] = claim
        return claim
