from __future__ import annotations

"""Ideal customer profile matching utilities."""

from typing import Dict, List

from .outbound_agent import Lead


class ICPMatcher:
    """Matches lead attributes against a defined ICP profile."""

    def __init__(self) -> None:
        self.icp: Dict[str, object] = {}

    def define_icp(self, criteria: Dict[str, object]) -> Dict[str, object]:
        """Persist ICP criteria for downstream matching."""
        self.icp = dict(criteria)
        return self.icp

    def match_leads(self, leads: List[Lead]) -> List[Dict[str, object]]:
        """Score each lead against the stored ICP definition."""
        results = []
        industries = set(self.icp.get('industries', []))
        min_employees = int(self.icp.get('min_employees', 0))
        min_revenue = float(self.icp.get('min_revenue', 0.0))
        for lead in leads:
            fit = 0.0
            if not industries or lead.industry in industries:
                fit += 0.4
            if lead.employee_count >= min_employees:
                fit += 0.3
            if lead.annual_revenue >= min_revenue:
                fit += 0.3
            results.append({'lead': lead, 'fit_score': round(fit, 3)})
        return results

    def rank_by_fit(self, leads: List[Lead]) -> List[Dict[str, object]]:
        """Return ICP matches ordered from best fit to weakest fit."""
        return sorted(self.match_leads(leads), key=lambda item: item['fit_score'], reverse=True)
