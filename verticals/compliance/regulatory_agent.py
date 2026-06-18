from __future__ import annotations

"""Regulatory automation utilities for SBOM, lease accounting, and filing checks."""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List


@dataclass
class SBOMReport:
    """Represents a software bill of materials report."""

    application_name: str
    version: str
    generated_at: str
    components: List[Dict[str, str]]
    format: str = 'cyclonedx-json'
    digest: str = ''


@dataclass
class LeaseEntry:
    """Represents a lease that should be amortized under ASC 842."""

    lease_id: str
    asset_name: str
    commencement_date: date
    monthly_payment: Decimal
    term_months: int
    discount_rate: Decimal
    lease_type: str = 'operating'


class RegulatoryComplianceAgent:
    """Delivers repeatable compliance calculations and requirement lookups."""

    def generate_sbom(self, application_name: str, version: str, components: List[Dict[str, str]]) -> SBOMReport:
        """Generate a deterministic SBOM report from a component list."""
        digest_source = '|'.join(sorted(f"{component.get('name')}:{component.get('version')}" for component in components))
        report = SBOMReport(
            application_name=application_name,
            version=version,
            generated_at=datetime.now(timezone.utc).isoformat(),
            components=components,
            digest=hashlib.sha256(digest_source.encode('utf-8')).hexdigest(),
        )
        return report

    def process_lease_schedule(self, lease: LeaseEntry) -> Dict[str, Any]:
        """Build a simplified lease payment schedule suitable for ASC 842 workflows."""
        monthly_rate = lease.discount_rate / Decimal('12')
        remaining_liability = lease.monthly_payment * lease.term_months
        schedule = []
        for period in range(1, lease.term_months + 1):
            interest = (remaining_liability * monthly_rate).quantize(Decimal('0.01'))
            principal = (lease.monthly_payment - interest).quantize(Decimal('0.01'))
            remaining_liability = max((remaining_liability - principal).quantize(Decimal('0.01')), Decimal('0.00'))
            schedule.append({
                'period': period,
                'payment_date': lease.commencement_date.isoformat(),
                'cash_payment': str(lease.monthly_payment.quantize(Decimal('0.01'))),
                'interest_expense': str(interest),
                'principal_reduction': str(principal),
                'remaining_liability': str(remaining_liability),
            })
        return {
            'lease': asdict(lease),
            'schedule': schedule,
            'summary': {
                'classification': lease.lease_type,
                'initial_liability': str((lease.monthly_payment * lease.term_months).quantize(Decimal('0.01'))),
            },
        }

    def check_regulatory_requirements(self, domain: str, jurisdiction: str) -> Dict[str, Any]:
        """Return a curated requirement set for common regulatory verticals."""
        requirements = {
            'sbom': ['Maintain component inventory', 'Track vulnerabilities', 'Version signed releases'],
            'lease_accounting': ['Recognize right-of-use asset', 'Maintain monthly amortization schedule', 'Retain contract amendments'],
            'cannabis': ['License verification', 'Inventory reconciliation', 'State-specific transport manifest retention'],
            'hemp': ['THC threshold testing records', 'Cultivation registration', 'Chain-of-custody support'],
            'sox': ['Segregation of duties', 'Access reviews', 'Evidence retention for key controls'],
        }
        return {
            'domain': domain,
            'jurisdiction': jurisdiction,
            'requirements': requirements.get(domain, ['Perform jurisdiction-specific review']),
            'reviewed_at': datetime.now(timezone.utc).isoformat(),
        }
