from __future__ import annotations

"""Coordinator for SINC ecosystem expansion workflows."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Dict, List

from verticals.ecosystem.competitive_intel import CompetitorProfile, CompetitiveIntelEngine
from verticals.ecosystem.holder_analytics import HolderAnalyticsEngine, HolderSnapshot
from verticals.ecosystem.liquidity_monitor import LiquidityMonitor, LiquidityVenue
from verticals.ecosystem.use_case_engine import UseCaseParameters, UseCaseViabilityEngine


class ExecutionCadence(Enum):
    """Execution cadences for ecosystem automation runs."""

    REALTIME = 'realtime'
    DAILY = 'daily'
    WEEKLY = 'weekly'
    MONTHLY = 'monthly'
    QUARTERLY = 'quarterly'


class PhaseStatus(Enum):
    """Lifecycle status for an ecosystem expansion phase."""

    IDLE = 'idle'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'


@dataclass
class EcosystemHealthMetrics:
    """High-level ecosystem health indicators used for executive reporting."""

    active_addresses: int
    transaction_count: int
    staking_participation_rate: float
    smart_contract_interactions: int
    new_holder_growth_rate: float
    holder_retention_90d: float
    developer_commits: int
    dapps_integrated: int
    positive_sentiment_ratio: float
    security_audit_pass_rate: float
    governance_participation_rate: float
    timestamp: str


@dataclass
class PhaseResult:
    """Execution result for a single ecosystem automation phase."""

    phase_name: str
    status: PhaseStatus
    output: Dict
    duration_seconds: float
    timestamp: str


class SINCEcosystemCoordinator:
    """Coordinates use-case discovery, holder analytics, liquidity, and intel workflows."""

    def __init__(self) -> None:
        self.use_case_engine = UseCaseViabilityEngine()
        self.holder_analytics_engine = HolderAnalyticsEngine()
        self.liquidity_monitor = LiquidityMonitor()
        self.competitive_intel_engine = CompetitiveIntelEngine()
        self._phase_results: List[PhaseResult] = []
        self._holder_history: List[HolderSnapshot] = []
        self.sinc_token_address = '0x9C8cd8d3961F445D653713dE65C6578bE11668e7'
        self.treasury_address = '0xAf9B539D8043C634b7E611818518BA7E850F289e'

    def run_use_case_discovery(self, use_case_params: List[UseCaseParameters]) -> PhaseResult:
        """Run viability scoring and return the top-ranked use cases."""

        started = time.perf_counter()
        ranked_briefs = self.use_case_engine.rank_use_cases(use_case_params)
        result = PhaseResult(
            phase_name='use_case_discovery',
            status=PhaseStatus.COMPLETED,
            output={
                'top_use_cases': [self._serialize(brief) for brief in ranked_briefs[:3]],
                'evaluated_count': len(ranked_briefs),
            },
            duration_seconds=round(time.perf_counter() - started, 4),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._phase_results.append(result)
        return result

    def run_holder_analytics(self, snapshot: HolderSnapshot) -> PhaseResult:
        """Run holder concentration analytics and produce a health report."""

        started = time.perf_counter()
        metrics = self.holder_analytics_engine.compute_metrics(snapshot)
        health_report = self.holder_analytics_engine.generate_health_report(snapshot, self._holder_history)
        self._holder_history.append(snapshot)
        result = PhaseResult(
            phase_name='holder_analytics',
            status=PhaseStatus.COMPLETED,
            output={
                'metrics': self._serialize(metrics),
                'health_report': health_report,
            },
            duration_seconds=round(time.perf_counter() - started, 4),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._phase_results.append(result)
        return result

    def run_liquidity_analysis(self, venues: List[LiquidityVenue]) -> PhaseResult:
        """Run venue fragmentation and spread analytics across liquidity venues."""

        started = time.perf_counter()
        fragmentation_report = self.liquidity_monitor.assess_fragmentation(venues)
        spreads = {venue.venue_id: self.liquidity_monitor.compute_spread(venue) for venue in venues}
        result = PhaseResult(
            phase_name='liquidity_analysis',
            status=PhaseStatus.COMPLETED,
            output={
                'fragmentation_report': fragmentation_report,
                'spreads': spreads,
                'venue_count': len(venues),
            },
            duration_seconds=round(time.perf_counter() - started, 4),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._phase_results.append(result)
        return result

    def run_competitive_intelligence(self, profiles: List[CompetitorProfile]) -> PhaseResult:
        """Run competitor scoring and narrative detection for tracked profiles."""

        started = time.perf_counter()
        threat_scores = {
            profile.token_symbol: self.competitive_intel_engine.score_competitor_threat(profile) for profile in profiles
        }
        narrative_trends = self.competitive_intel_engine.detect_narrative_trends(profiles)
        result = PhaseResult(
            phase_name='competitive_intelligence',
            status=PhaseStatus.COMPLETED,
            output={
                'threat_scores': threat_scores,
                'narrative_trends': [trend.value for trend in narrative_trends],
                'sinc_positioning': {
                    trend.value: self.competitive_intel_engine.generate_sinc_positioning(trend) for trend in narrative_trends
                },
            },
            duration_seconds=round(time.perf_counter() - started, 4),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._phase_results.append(result)
        return result

    def generate_ecosystem_health_report(self, metrics: EcosystemHealthMetrics) -> Dict:
        """Generate a summarized ecosystem health report."""

        overall_health_score = (
            (0.3 * metrics.staking_participation_rate)
            + (0.2 * metrics.holder_retention_90d)
            + (0.2 * metrics.positive_sentiment_ratio)
            + (0.15 * metrics.governance_participation_rate)
            + (0.15 * metrics.security_audit_pass_rate)
        )
        if overall_health_score >= 0.7:
            health_band = 'healthy'
        elif overall_health_score >= 0.4:
            health_band = 'moderate'
        else:
            health_band = 'at_risk'
        report = self._serialize(metrics)
        report['overall_health_score'] = round(overall_health_score, 4)
        report['health_band'] = health_band
        return report

    def get_phase_results(self, cadence: ExecutionCadence = None) -> List[PhaseResult]:
        """Return accumulated phase results."""

        return list(self._phase_results)

    def _serialize(self, value):
        """Convert dataclasses and enums into plain Python structures."""

        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return {key: self._serialize(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {key: self._serialize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._serialize(item) for item in value]
        return value


__all__ = [
    'EcosystemHealthMetrics',
    'ExecutionCadence',
    'PhaseResult',
    'PhaseStatus',
    'SINCEcosystemCoordinator',
]
