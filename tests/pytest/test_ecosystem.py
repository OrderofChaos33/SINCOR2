from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sincor2.sinc_ecosystem_coordinator import (
    EcosystemHealthMetrics,
    PhaseStatus,
    SINCEcosystemCoordinator,
)
from verticals.ecosystem.competitive_intel import (
    CompetitorProfile,
    CompetitiveIntelEngine,
    NarrativeTrend,
    SentimentDataPoint,
)
from verticals.ecosystem.holder_analytics import HolderAnalyticsEngine, HolderSnapshot, HolderTier
from verticals.ecosystem.liquidity_monitor import LiquidityMonitor, LiquidityVenue, OrderBookLevel, VenueType
from verticals.ecosystem.use_case_engine import UseCaseParameters, UseCaseVertical, UseCaseViabilityEngine


@pytest.fixture
def use_case_engine():
    return UseCaseViabilityEngine()


@pytest.fixture
def holder_engine():
    return HolderAnalyticsEngine()


@pytest.fixture
def liquidity_monitor():
    return LiquidityMonitor()


@pytest.fixture
def competitive_engine():
    return CompetitiveIntelEngine()


@pytest.fixture
def sample_use_cases():
    return [
        UseCaseParameters(UseCaseVertical.DEFI, 'Yield Router', 1000, 12, 4, 2, 2, 4),
        UseCaseParameters(UseCaseVertical.PAYMENTS, 'Merchant Checkout', 3000, 25, 2, 3, 2, 3),
        UseCaseParameters(UseCaseVertical.DAO, 'Treasury Governance', 800, 6, 3, 2, 1.5, 2),
    ]


@pytest.fixture
def sample_snapshot():
    return HolderSnapshot(
        snapshot_id='snap-1',
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_holders=4,
        balances={'0x1': 6000.0, '0x2': 2500.0, '0x3': 1000.0, '0x4': 500.0},
        total_supply=100000.0,
        staking_participation_rate=0.42,
        new_holders_24h=1,
    )


@pytest.fixture
def sample_venues():
    timestamp = datetime.now(timezone.utc).isoformat()
    dominant = LiquidityVenue(
        venue_id='venue-a',
        name='Alpha DEX',
        venue_type=VenueType.DEX,
        token_pair='SINC/USDC',
        bids=[OrderBookLevel(price=100.0, size=100.0)],
        asks=[OrderBookLevel(price=102.0, size=100.0)],
        volume_24h=50000.0,
        lp_reward_emission=1200.0,
        last_updated=timestamp,
    )
    minor = LiquidityVenue(
        venue_id='venue-b',
        name='Beta CEX',
        venue_type=VenueType.CEX,
        token_pair='SINC/USDC',
        bids=[OrderBookLevel(price=99.0, size=5.0)],
        asks=[OrderBookLevel(price=103.0, size=5.0)],
        volume_24h=500.0,
        lp_reward_emission=100.0,
        last_updated=timestamp,
    )
    return [dominant, minor]


@pytest.fixture
def sample_profiles():
    return [
        CompetitorProfile(
            token_symbol='AAA',
            name='Alpha',
            market_cap_usd=900_000_000,
            holder_count=400_000,
            staking_rate=0.45,
            recent_integrations=['dex', 'wallet', 'bridge'],
            narratives=[NarrativeTrend.PAYMENTS, NarrativeTrend.RWA],
            sentiment_score=0.3,
        ),
        CompetitorProfile(
            token_symbol='BBB',
            name='Beta',
            market_cap_usd=700_000_000,
            holder_count=250_000,
            staking_rate=0.35,
            recent_integrations=['custody', 'sdk'],
            narratives=[NarrativeTrend.PAYMENTS, NarrativeTrend.GOVERNANCE],
            sentiment_score=0.1,
        ),
        CompetitorProfile(
            token_symbol='CCC',
            name='Gamma',
            market_cap_usd=400_000_000,
            holder_count=200_000,
            staking_rate=0.2,
            recent_integrations=['marketplace'],
            narratives=[NarrativeTrend.PAYMENTS, NarrativeTrend.RWA],
            sentiment_score=-0.05,
        ),
    ]


def test_use_case_score_returns_positive_float(use_case_engine):
    score = use_case_engine.score(
        UseCaseParameters(UseCaseVertical.DEFI, 'Liquidity Hub', 1000, 10, 3, 2, 2, 6)
    )
    assert isinstance(score, float)
    assert score > 0


def test_use_case_score_clamps_zero_denominator(use_case_engine):
    score = use_case_engine.score(
        UseCaseParameters(UseCaseVertical.PAYMENTS, 'Zero Risk', 100, 2, 1, 0, 0, 0)
    )
    assert score > 0


def test_generate_brief_populates_fields(use_case_engine):
    brief = use_case_engine.generate_brief(
        UseCaseParameters(UseCaseVertical.DEFI, 'Yield Router', 1000, 12, 4, 2, 2, 4)
    )
    assert brief.vertical == UseCaseVertical.DEFI
    assert brief.name == 'Yield Router'
    assert brief.viability_score > 0
    assert 'staking_pool' in brief.smart_contract_templates
    assert len(brief.technical_requirements) == 3
    assert brief.generated_at


def test_rank_use_cases_orders_by_score(use_case_engine, sample_use_cases):
    briefs = use_case_engine.rank_use_cases(sample_use_cases)
    assert len(briefs) == 3
    assert briefs[0].rank == 1
    assert briefs[0].viability_score >= briefs[1].viability_score >= briefs[2].viability_score


def test_classify_holder_tiers(holder_engine):
    total_supply = 10_000.0
    assert holder_engine.classify_holder(150.0, total_supply) == HolderTier.WHALE
    assert holder_engine.classify_holder(50.0, total_supply) == HolderTier.DOLPHIN
    assert holder_engine.classify_holder(5.0, total_supply) == HolderTier.FISH
    assert holder_engine.classify_holder(0.5, total_supply) == HolderTier.SHRIMP


def test_compute_metrics_returns_valid_holder_metrics(holder_engine, sample_snapshot):
    metrics = holder_engine.compute_metrics(sample_snapshot)
    assert 0 <= metrics.concentration_risk <= 1
    assert metrics.whale_count >= 0
    assert metrics.active_holder_pct > 0


def test_compute_retention_rate_single_snapshot(holder_engine, sample_snapshot):
    assert holder_engine.compute_retention_rate([sample_snapshot]) == 1.0


def test_detect_distribution_shift_when_hhi_changes(holder_engine):
    prev = HolderSnapshot(
        snapshot_id='prev',
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_holders=2,
        balances={'0x1': 5000.0, '0x2': 5000.0},
        total_supply=10000.0,
        staking_participation_rate=0.3,
        new_holders_24h=0,
    )
    curr = HolderSnapshot(
        snapshot_id='curr',
        timestamp=datetime.now(timezone.utc).isoformat(),
        total_holders=2,
        balances={'0x1': 9900.0, '0x2': 100.0},
        total_supply=10000.0,
        staking_participation_rate=0.3,
        new_holders_24h=0,
    )
    assert holder_engine.detect_distribution_shift(prev, curr, threshold=0.1) is True


def test_compute_spread_returns_expected_value(liquidity_monitor, sample_venues):
    spread = liquidity_monitor.compute_spread(sample_venues[0])
    assert spread == pytest.approx((102.0 - 100.0) / 101.0, rel=1e-6)


def test_compute_slippage_returns_zero_for_empty_book(liquidity_monitor):
    venue = LiquidityVenue(
        venue_id='empty',
        name='Empty Venue',
        venue_type=VenueType.DEX,
        token_pair='SINC/USDC',
        bids=[],
        asks=[],
        volume_24h=0.0,
        lp_reward_emission=0.0,
        last_updated=datetime.now(timezone.utc).isoformat(),
    )
    assert liquidity_monitor.compute_slippage(venue, order_size=10.0) == 0.0


def test_assess_fragmentation_flags_dominant_venue(liquidity_monitor, sample_venues):
    report = liquidity_monitor.assess_fragmentation(sample_venues)
    assert report['fragmentation_alert'] is True
    assert report['top_venue'] == 'venue-a'


def test_optimize_pool_emissions_returns_expected_venues(liquidity_monitor, sample_venues):
    recommendations = liquidity_monitor.optimize_pool_emissions(sample_venues, target_competitor_yield=0.1)
    assert [item['venue_id'] for item in recommendations] == ['venue-a', 'venue-b']


def test_score_competitor_threat_is_normalized(competitive_engine, sample_profiles):
    threat = competitive_engine.score_competitor_threat(sample_profiles[0])
    assert 0 <= threat <= 1


def test_detect_narrative_trends_returns_sorted_list(competitive_engine, sample_profiles):
    trends = competitive_engine.detect_narrative_trends(sample_profiles)
    assert trends[0] == NarrativeTrend.PAYMENTS
    assert NarrativeTrend.RWA in trends


def test_analyze_sentiment_counts_and_average(competitive_engine):
    sentiment = competitive_engine.analyze_sentiment([
        SentimentDataPoint('x', 'good', 0.5, datetime.now(timezone.utc).isoformat()),
        SentimentDataPoint('discord', 'bad', -0.4, datetime.now(timezone.utc).isoformat()),
        SentimentDataPoint('forum', 'wait', 0.05, datetime.now(timezone.utc).isoformat()),
    ])
    assert sentiment['positive_count'] == 1
    assert sentiment['negative_count'] == 1
    assert sentiment['neutral_count'] == 1
    assert sentiment['avg_score'] == pytest.approx(0.05, rel=1e-6)


def test_generate_sinc_positioning_for_all_trends(competitive_engine):
    for trend in NarrativeTrend:
        assert competitive_engine.generate_sinc_positioning(trend)


def test_coordinator_use_case_discovery_returns_completed(sample_use_cases):
    coordinator = SINCEcosystemCoordinator()
    result = coordinator.run_use_case_discovery(sample_use_cases)
    assert result.status == PhaseStatus.COMPLETED
    assert len(result.output['top_use_cases']) == 3


def test_coordinator_holder_analytics_returns_output(sample_snapshot):
    coordinator = SINCEcosystemCoordinator()
    result = coordinator.run_holder_analytics(sample_snapshot)
    assert result.status == PhaseStatus.COMPLETED
    assert 'health_report' in result.output


def test_generate_ecosystem_health_report_returns_score():
    coordinator = SINCEcosystemCoordinator()
    report = coordinator.generate_ecosystem_health_report(
        EcosystemHealthMetrics(
            active_addresses=1200,
            transaction_count=4500,
            staking_participation_rate=0.65,
            smart_contract_interactions=900,
            new_holder_growth_rate=0.12,
            holder_retention_90d=0.72,
            developer_commits=48,
            dapps_integrated=6,
            positive_sentiment_ratio=0.68,
            security_audit_pass_rate=0.95,
            governance_participation_rate=0.51,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
    )
    assert 'overall_health_score' in report
    assert 0 <= report['overall_health_score'] <= 1
