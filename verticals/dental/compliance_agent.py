from __future__ import annotations

"""Compliance automation for dental offices."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List


@dataclass
class ComplianceRecord:
    """Represents a compliance check or completed documentation event."""

    category: str
    status: str
    notes: str
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class DentalComplianceAgent:
    """Tracks HIPAA, OSHA, and infection control documentation readiness."""

    def __init__(self) -> None:
        self.records: List[ComplianceRecord] = []

    def assess_hipaa_controls(self, policies_present: bool, baa_inventory_complete: bool) -> Dict[str, str]:
        """Return a lightweight HIPAA administrative safeguards assessment."""
        status = 'ready' if policies_present and baa_inventory_complete else 'needs_attention'
        self.records.append(ComplianceRecord(category='hipaa', status=status, notes='Administrative safeguards review'))
        return {
            'framework': 'HIPAA',
            'status': status,
            'next_step': 'annual workforce training' if status == 'ready' else 'update privacy policies and BAAs',
        }

    def generate_osha_checklist(self) -> Dict[str, List[str]]:
        """Produce a concise OSHA dental office readiness checklist."""
        self.records.append(ComplianceRecord(category='osha', status='generated', notes='Exposure control checklist created'))
        return {
            'framework': 'OSHA',
            'checklist': [
                'Review exposure control plan and bloodborne pathogen training.',
                'Verify eyewash stations, sharps containers, and PPE inventory.',
                'Confirm SDS binder and hazardous communication signage are current.',
            ],
        }

    def log_infection_control(self, sterilizer_spore_test_passed: bool, waterline_status: str) -> Dict[str, str]:
        """Record infection control posture for operational audits."""
        status = 'compliant' if sterilizer_spore_test_passed and waterline_status == 'within_limits' else 'follow_up_required'
        self.records.append(ComplianceRecord(category='infection_control', status=status, notes=waterline_status))
        return {
            'status': status,
            'sterilizer_spore_test': 'passed' if sterilizer_spore_test_passed else 'failed',
            'waterline_status': waterline_status,
        }
