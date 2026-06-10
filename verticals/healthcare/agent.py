from __future__ import annotations

"""Healthcare RCM vertical agent — production-grade with HIPAA guardrails."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from ..agent import VerticalAgent
from .hipaa_guardrails import HIPAALogger, hipaa_audit_log, phi_token
from .rcm_agent import HealthcareRCMAgent, ClaimSubmission, PriorAuthRequest as LegacyPAR
from .schemas import (
    ClaimStatusRequest,
    ClaimSubmissionRequest,
    CredentialingRequest,
    DenialRecord,
    EligibilityRequest,
    PriorAuthRequest,
    TaskInput,
    TaskOutput,
)

_hipaa_logger = HIPAALogger(__name__)


class HealthcareAgent(VerticalAgent):
    name = "healthcare_rcm_agent"
    version = "0.3.0"
    description = (
        "Revenue cycle management, eligibility verification, prior authorization, "
        "credentialing, denial management, and payer workflow automation. "
        "HIPAA-compliant: PHI is masked in all logs and audit events."
    )
    capabilities = [
        "eligibility_verification",
        "prior_authorization",
        "claims_status_tracking",
        "claim_submission",
        "denial_management",
        "credentialing_workflow",
        "payer_reconciliation",
    ]
    tags = ["healthcare", "rcm", "payer", "automation", "hipaa"]

    def __init__(self) -> None:
        super().__init__()
        self._rcm = HealthcareRCMAgent()

    def execute(self, task: dict) -> dict:  # type: ignore[override]
        task_input = TaskInput.model_validate(task)
        task_type = task_input.task_type
        payload = task_input.payload
        cid = task_input.correlation_id

        try:
            if task_type == "eligibility_verification":
                return self._eligibility(payload, cid)
            elif task_type == "prior_authorization":
                return self._prior_auth(payload, cid)
            elif task_type in ("claims_status_tracking", "claim_status"):
                return self._claim_status(payload, cid)
            elif task_type == "claim_submission":
                return self._claim_submission(payload, cid)
            elif task_type == "denial_management":
                return self._denial_management(payload, cid)
            elif task_type == "credentialing_workflow":
                return self._credentialing(payload, cid)
            elif task_type == "payer_reconciliation":
                return self._payer_reconciliation(payload, cid)

            return TaskOutput(
                status="error",
                result={},
                error=f"Unsupported healthcare task: {task_type}",
                correlation_id=cid,
            ).model_dump()

        except Exception as exc:
            _hipaa_logger.error(
                "HealthcareAgent error task_type=%s error=%s", task_type, str(exc)
            )
            return TaskOutput(
                status="error",
                result={},
                error=str(exc),
                correlation_id=cid,
            ).model_dump()

    # ------------------------------------------------------------------
    # Task implementations
    # ------------------------------------------------------------------

    def _eligibility(self, payload: dict, cid) -> dict:
        req = EligibilityRequest.model_validate(payload)
        raw = self._rcm.verify_eligibility(
            member_id=req.member_id,
            payer_id=req.payer_id,
            service_date=req.service_date,
            provider_npi=req.provider_npi,
        )
        subject = phi_token(req.member_id)
        hipaa_audit_log(
            action="eligibility_verification",
            agent_id=self.name,
            task_id=cid or "",
            outcome="success",
            phi_token_value=subject,
        )
        # Mask PHI before returning (member_id kept as token)
        result = {
            "coverage_status": raw.get("coverage_status", "unknown"),
            "copay": raw.get("copay"),
            "deductible_remaining": raw.get("deductible_remaining"),
            "benefits": raw.get("benefits", []),
            "service_date": str(req.service_date),
            "payer_id": req.payer_id,
            "subject_token": subject,
        }
        return TaskOutput(status="success", result=result, correlation_id=cid).model_dump()

    def _prior_auth(self, payload: dict, cid) -> dict:
        req = PriorAuthRequest.model_validate(payload)
        legacy = LegacyPAR(
            patient_id=req.patient_id,
            member_id=req.member_id,
            payer_id=req.payer_id,
            provider_npi=req.provider_npi,
            cpt_codes=req.cpt_codes,
            diagnosis_codes=req.diagnosis_codes,
            requested_start_date=req.requested_start_date,
            requested_units=req.requested_units,
            notes=req.notes,
        )
        raw = self._rcm.submit_prior_auth(legacy)
        hipaa_audit_log(
            action="prior_authorization",
            agent_id=self.name,
            task_id=cid or "",
            outcome="success",
            phi_token_value=phi_token(req.member_id),
        )
        return TaskOutput(
            status="success",
            result={
                "request_id": raw["request_id"],
                "status": raw["status"],
                "payer_reference": raw.get("payer_reference"),
                "estimated_turnaround_days": raw.get("estimated_turnaround_days", 5),
            },
            correlation_id=cid,
        ).model_dump()

    def _claim_status(self, payload: dict, cid) -> dict:
        req = ClaimStatusRequest.model_validate(payload)
        raw = self._rcm.check_claim_status(req.claim_id)
        return TaskOutput(
            status="success",
            result={
                "claim_id": raw.get("claim_id"),
                "status": raw.get("status", "unknown"),
                "payer_id": raw.get("payer_id"),
                "age_days": raw.get("age_days", 0),
                "claim_total": raw.get("claim_total", "0.00"),
                "next_action": raw.get("next_action", "none"),
            },
            correlation_id=cid,
        ).model_dump()

    def _claim_submission(self, payload: dict, cid) -> dict:
        req = ClaimSubmissionRequest.model_validate(payload)
        claim_id = f"CLM-{uuid4().hex[:10].upper()}"
        # Build a legacy ClaimSubmission for RCM agent
        legacy_claim = ClaimSubmission(
            claim_id=claim_id,
            patient_id=req.patient_id,
            payer_id=req.payer_id,
            provider_npi=req.billing_provider_npi,
            cpt_codes=[line.cpt_code for line in req.service_lines],
            total_charge=req.total_charge,
            billed_date=req.service_lines[0].service_date if req.service_lines else date.today(),
        )
        self._rcm.create_claim(legacy_claim)
        hipaa_audit_log(
            action="claim_submission",
            agent_id=self.name,
            task_id=cid or "",
            outcome="success",
            phi_token_value=phi_token(req.member_id),
        )
        return TaskOutput(
            status="success",
            result={
                "claim_id": claim_id,
                "status": "submitted",
                "total_charge": str(req.total_charge.quantize(Decimal("0.01"))),
                "payer_id": req.payer_id,
            },
            correlation_id=cid,
        ).model_dump()

    def _denial_management(self, payload: dict, cid) -> dict:
        denials_raw = payload.get("denials", [])
        processed = appealable = write_offs = 0
        results = []
        for d in denials_raw:
            denial = DenialRecord.model_validate(d)
            processed += 1
            if denial.action_recommended == "appeal":
                appealable += 1
            elif denial.action_recommended == "write_off":
                write_offs += 1
            results.append({
                "claim_id": denial.claim_id,
                "denial_code": denial.denial_code,
                "action": denial.action_recommended,
                "amount": str(denial.denied_amount),
            })
        return TaskOutput(
            status="success",
            result={
                "processed": processed,
                "appealable": appealable,
                "write_offs": write_offs,
                "denials": results,
            },
            correlation_id=cid,
        ).model_dump()

    def _credentialing(self, payload: dict, cid) -> dict:
        req = CredentialingRequest.model_validate(payload)
        hipaa_audit_log(
            action="credentialing_workflow",
            agent_id=self.name,
            task_id=cid or "",
            outcome="success",
            phi_token_value=phi_token(req.provider_npi),
        )
        return TaskOutput(
            status="success",
            result={
                "provider_id": req.provider_id,
                "status": "submitted",
                "payers_submitted": req.payer_ids,
                "estimated_completion_days": 90,
            },
            correlation_id=cid,
        ).model_dump()

    def _payer_reconciliation(self, payload: dict, cid) -> dict:
        era_payload = payload.get("era_payload", payload.get("input", ""))
        raw = self._rcm.process_era(era_payload)
        return TaskOutput(
            status="success",
            result={
                "payment_count": raw.get("payment_count", 0),
                "total_paid": raw.get("total_paid", "0.00"),
                "payments": raw.get("payments", []),
            },
            correlation_id=cid,
        ).model_dump()
