"""Production Pydantic schemas for healthcare RCM tasks.

Covers:
- Generic task I/O (``TaskInput``, ``TaskOutput``)
- Eligibility verification (837 270/271)
- Claim submission and status (837P / 276/277)
- Prior authorization (278)
- Denial management
- Credentialing workflow

HIPAA notes
-----------
All schemas that contain PHI (patient identifiers, dates of birth, etc.) carry
field-level ``json_schema_extra`` metadata so the HIPAA guardrail middleware
can identify and mask them in logs.  See ``hipaa_guardrails.py``.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# PHI field marker used by the HIPAA guardrail middleware
_PHI_MARKER = {"phi": True}


# ---------------------------------------------------------------------------
# Generic task I/O (unchanged from original for backward compat)
# ---------------------------------------------------------------------------

class TaskInput(BaseModel):
    task_type: str
    payload: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class TaskOutput(BaseModel):
    status: str = Field(..., pattern="^(success|error|partial)$")
    result: Dict[str, Any]
    error: Optional[str] = None
    correlation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Eligibility verification (270/271)
# ---------------------------------------------------------------------------

class EligibilityRequest(BaseModel):
    """Request payload for an X12 270 eligibility inquiry."""

    member_id: str = Field(..., description="Insurance member / subscriber ID.", json_schema_extra=_PHI_MARKER)
    payer_id: str = Field(..., description="Payer identifier (e.g. BCBS01).")
    service_date: date = Field(..., description="Date of service to verify.")
    provider_npi: str = Field(..., description="Billing provider NPI (10 digits).")
    patient_last_name: Optional[str] = Field(None, json_schema_extra=_PHI_MARKER)
    patient_first_name: Optional[str] = Field(None, json_schema_extra=_PHI_MARKER)
    patient_dob: Optional[date] = Field(None, json_schema_extra=_PHI_MARKER)
    service_type_codes: List[str] = Field(
        default_factory=lambda: ["30"],
        description="X12 service type codes (default: '30' = Health Benefit Plan Coverage).",
    )

    @field_validator("provider_npi")
    @classmethod
    def npi_must_be_10_digits(cls, v: str) -> str:
        if not re.fullmatch(r"\d{10}", v):
            raise ValueError("provider_npi must be exactly 10 digits")
        return v


class EligibilityResponse(BaseModel):
    """Normalised 271 eligibility response."""

    member_id: str = Field(..., json_schema_extra=_PHI_MARKER)
    payer_id: str
    service_date: str
    coverage_status: str = Field(..., description="'active' | 'inactive' | 'unknown'")
    copay: Optional[str] = None
    deductible_remaining: Optional[str] = None
    benefits: List[str] = Field(default_factory=list)
    out_of_pocket_max: Optional[str] = None
    plan_name: Optional[str] = None
    raw_271: Optional[str] = Field(None, description="Raw X12 271 transaction segment string.")


# ---------------------------------------------------------------------------
# Claim submission (837P)
# ---------------------------------------------------------------------------

class DiagnosisCode(BaseModel):
    code: str = Field(..., description="ICD-10-CM diagnosis code.")
    qualifier: str = Field("ABK", description="X12 qualifier (ABK = principal, ABF = secondary).")


class ServiceLine(BaseModel):
    """A single service line in an 837P claim."""

    cpt_code: str = Field(..., description="CPT / HCPCS procedure code.")
    modifier_codes: List[str] = Field(default_factory=list)
    units: int = Field(1, ge=1)
    charge_amount: Decimal = Field(..., gt=Decimal("0"))
    service_date: date
    place_of_service: str = Field("11", description="CMS POS code (11=Office, 21=Inpatient, etc.)")
    rendering_npi: Optional[str] = None


class ClaimSubmissionRequest(BaseModel):
    """Request payload to submit a professional (837P) claim."""

    patient_id: str = Field(..., json_schema_extra=_PHI_MARKER)
    patient_dob: date = Field(..., json_schema_extra=_PHI_MARKER)
    member_id: str = Field(..., json_schema_extra=_PHI_MARKER)
    payer_id: str
    billing_provider_npi: str
    billing_provider_tax_id: str
    diagnosis_codes: List[DiagnosisCode] = Field(..., min_length=1)
    service_lines: List[ServiceLine] = Field(..., min_length=1)
    referring_npi: Optional[str] = None
    prior_auth_number: Optional[str] = None

    @property
    def total_charge(self) -> Decimal:
        return sum(line.charge_amount * line.units for line in self.service_lines)


class ClaimSubmissionResponse(BaseModel):
    """Response after submitting a claim."""

    claim_id: str
    status: str = Field(..., description="'submitted' | 'accepted' | 'rejected'")
    payer_control_number: Optional[str] = None
    clearinghouse_id: Optional[str] = None
    rejection_reason: Optional[str] = None
    total_charge: str


# ---------------------------------------------------------------------------
# Claim status (276/277)
# ---------------------------------------------------------------------------

class ClaimStatusRequest(BaseModel):
    claim_id: str
    payer_id: Optional[str] = None


class ClaimStatusResponse(BaseModel):
    claim_id: str
    status: str = Field(..., description="'submitted'|'in_review'|'pending_payment'|'paid'|'denied'")
    payer_id: Optional[str] = None
    age_days: int = 0
    claim_total: str
    paid_amount: Optional[str] = None
    denial_reason: Optional[str] = None
    next_action: str = "none"


# ---------------------------------------------------------------------------
# Prior authorization (278)
# ---------------------------------------------------------------------------

class PriorAuthRequest(BaseModel):
    """Request payload for a 278 prior authorization submission."""

    patient_id: str = Field(..., json_schema_extra=_PHI_MARKER)
    member_id: str = Field(..., json_schema_extra=_PHI_MARKER)
    payer_id: str
    provider_npi: str
    cpt_codes: List[str] = Field(..., min_length=1)
    diagnosis_codes: List[str] = Field(..., min_length=1)
    requested_start_date: date
    requested_units: int = Field(1, ge=1)
    notes: str = ""


class PriorAuthResponse(BaseModel):
    request_id: str
    status: str = Field(..., description="'submitted' | 'approved' | 'denied' | 'pending'")
    payer_reference: Optional[str] = None
    estimated_turnaround_days: int = 5
    raw_edi_278: Optional[str] = Field(None, description="Raw X12 EDI 278 segment string.")


# ---------------------------------------------------------------------------
# Denial management
# ---------------------------------------------------------------------------

class DenialRecord(BaseModel):
    """Represents a denied claim requiring follow-up action."""

    claim_id: str
    denial_code: str = Field(..., description="CARC / RARC denial code.")
    denial_reason: str
    original_amount: Decimal
    denied_amount: Decimal
    appeal_deadline: Optional[date] = None
    action_recommended: str = Field(
        "review",
        description="'appeal' | 'resubmit' | 'write_off' | 'review'",
    )


class DenialManagementResponse(BaseModel):
    processed: int
    appealable: int
    write_offs: int
    denials: List[Dict[str, Any]]


# ---------------------------------------------------------------------------
# Credentialing
# ---------------------------------------------------------------------------

class CredentialingRequest(BaseModel):
    provider_id: str
    provider_npi: str = Field(..., json_schema_extra=_PHI_MARKER)
    provider_name: str = Field(..., json_schema_extra=_PHI_MARKER)
    specialty: str
    payer_ids: List[str] = Field(..., min_length=1)
    license_number: Optional[str] = None
    license_state: Optional[str] = None


class CredentialingResponse(BaseModel):
    provider_id: str
    status: str = Field(..., description="'submitted' | 'in_progress' | 'credentialed' | 'rejected'")
    payers_submitted: List[str]
    estimated_completion_days: int = 90
    notes: str = ""

