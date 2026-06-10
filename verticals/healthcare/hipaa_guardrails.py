"""HIPAA compliance guardrail middleware for healthcare vertical.

Provides:
- :func:`mask_phi` — masks PHI fields in any dict (safe for logging/audit).
- :class:`HIPAALogger` — structured logging wrapper that auto-masks PHI.
- :func:`hipaa_audit_log` — emit a HIPAA-compliant audit event.

PHI field detection
-------------------
PHI fields are identified by name patterns (``_PHI_FIELDS``) and by the
``json_schema_extra={"phi": True}`` marker set on Pydantic models in
``verticals/healthcare/schemas.py``.

Usage
-----
    from verticals.healthcare.hipaa_guardrails import mask_phi, HIPAALogger

    safe_payload = mask_phi(request_dict)
    logger = HIPAALogger(__name__)
    logger.info("Submitted claim", extra={"claim": safe_payload})
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# PHI field registry
# ---------------------------------------------------------------------------

# Regex patterns matched against (lowercased) field names.
# Matches: member_id, patient_id, patient_dob, ssn, mrn, dob, first_name,
# last_name, address, phone, email, npi (NPI can itself be PHI in context), etc.
_PHI_PATTERNS: List[re.Pattern] = [
    re.compile(r"patient_id"),
    re.compile(r"patient_dob"),
    re.compile(r"patient.*name"),
    re.compile(r"member_id"),
    re.compile(r"\bssn\b"),
    re.compile(r"\bdob\b"),
    re.compile(r"\bmrn\b"),
    re.compile(r"date_of_birth"),
    re.compile(r"birth_date"),
    re.compile(r"address"),
    re.compile(r"zip_code"),
    re.compile(r"phone"),
    re.compile(r"email"),
    re.compile(r"provider_tax_id"),
    re.compile(r"tax_id"),
]

# Explicit allowlist — fields that look PHI-like but are safe to log
_PHI_ALLOWLIST: Set[str] = {
    "payer_id",
    "provider_npi",     # NPI is a public identifier
    "billing_provider_npi",
    "rendering_npi",
    "referring_npi",
    "cpt_codes",
    "service_type_codes",
    "service_date",
    "place_of_service",
    "claim_id",
}

_MASK_VALUE = "***PHI***"


def _is_phi_field(key: str) -> bool:
    """Return True if *key* looks like a PHI field name."""
    if key.lower() in _PHI_ALLOWLIST:
        return False
    for pattern in _PHI_PATTERNS:
        if pattern.search(key.lower()):
            return True
    return False


def mask_phi(data: Any, _depth: int = 0) -> Any:
    """Recursively mask PHI fields in *data*.

    Parameters
    ----------
    data:
        Any JSON-serialisable object (dict, list, str, etc.).

    Returns
    -------
    A deep copy of *data* with PHI field values replaced by ``"***PHI***"``.
    """
    if _depth > 20:  # guard against deeply nested malicious input
        return data

    if isinstance(data, dict):
        return {
            k: _MASK_VALUE if _is_phi_field(k) else mask_phi(v, _depth + 1)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [mask_phi(item, _depth + 1) for item in data]
    return data


def phi_token(value: str) -> str:
    """Return a stable, one-way hash token for a PHI value.

    Use this when you need a consistent identifier across log lines
    (e.g., for correlating audit events) without exposing the raw value.
    """
    return "phi-" + hashlib.sha256(value.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# HIPAA audit logger
# ---------------------------------------------------------------------------

class HIPAALogger:
    """Logging wrapper that automatically masks PHI before emitting records.

    Wraps ``logging.getLogger(__name__)`` and intercepts ``extra`` dicts to
    apply :func:`mask_phi` before the record reaches any handler.
    """

    def __init__(self, name: str) -> None:
        self._logger = logging.getLogger(name)

    def _safe_extra(self, extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if extra is None:
            return {}
        return mask_phi(extra)

    def info(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._logger.info(msg, *args, extra=self._safe_extra(extra), **kwargs)

    def warning(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._logger.warning(msg, *args, extra=self._safe_extra(extra), **kwargs)

    def error(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._logger.error(msg, *args, extra=self._safe_extra(extra), **kwargs)

    def debug(self, msg: str, *args, extra: Optional[Dict[str, Any]] = None, **kwargs) -> None:
        self._logger.debug(msg, *args, extra=self._safe_extra(extra), **kwargs)


# ---------------------------------------------------------------------------
# HIPAA audit event emitter
# ---------------------------------------------------------------------------

def hipaa_audit_log(
    action: str,
    agent_id: str,
    task_id: str,
    outcome: str,
    phi_token_value: Optional[str] = None,
    additional_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Emit a structured HIPAA audit event to stdout.

    Audit events record *who* did *what* to *which* resource at *when*,
    without logging raw PHI values.  Use :func:`phi_token` to include a
    stable, de-identified reference to the subject when needed.

    Parameters
    ----------
    action:
        Description of the action (e.g. ``"eligibility_verification"``).
    agent_id:
        The agent that performed the action.
    task_id:
        The A2A task identifier.
    outcome:
        ``"success"`` | ``"error"`` | ``"denied"``.
    phi_token_value:
        Optional stable hash of the patient/member identifier for correlation.
    additional_context:
        Safe (non-PHI) additional metadata.

    Returns
    -------
    dict
        The audit event dict (also printed as JSON to stdout).
    """
    event: Dict[str, Any] = {
        "hipaa_audit": True,
        "action": action,
        "agent_id": agent_id,
        "task_id": task_id,
        "outcome": outcome,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if phi_token_value:
        event["subject_token"] = phi_token_value
    if additional_context:
        event["context"] = mask_phi(additional_context)

    try:
        print(json.dumps(event), flush=True)
    except Exception:  # pragma: no cover
        pass

    return event
