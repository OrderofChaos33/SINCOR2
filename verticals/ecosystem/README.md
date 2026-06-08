# SINC Ecosystem Expansion Vertical

This vertical packages the SINC token ecosystem expansion system into reusable Python modules for discovery, monitoring, and competitive response.

## 7-phase system

1. **Use case viability** — score expansion opportunities by demand, transaction density, savings, and delivery friction.
2. **Design partner intake** — validate candidate opportunities with early integrators.
3. **Holder analytics** — measure concentration risk, retention, participation, and distribution health.
4. **Treasury routing** — align reward budgets and rollout sequencing with treasury goals.
5. **Liquidity monitoring** — track venue depth, spreads, fragmentation, and LP emission efficiency.
6. **Execution review** — compare live performance against launch assumptions and rebalance priorities.
7. **Competitive intelligence** — monitor rival narratives, threats, sentiment, and response strategy.

## Modules

- `use_case_engine.py` — Phase 1 scoring, briefing, and ranking.
- `holder_analytics.py` — Phase 3 holder concentration and retention analytics.
- `liquidity_monitor.py` — Phase 5 DEX/CEX liquidity and emissions analysis.
- `competitive_intel.py` — Phase 7 narrative, threat, and sentiment tracking.
- `src/sincor2/sinc_ecosystem_coordinator.py` — central coordinator for the core expansion workflows.

## Quick start

### Score and rank use cases

```python
from verticals.ecosystem.use_case_engine import UseCaseParameters, UseCaseVertical, UseCaseViabilityEngine

engine = UseCaseViabilityEngine()
params = UseCaseParameters(
    vertical=UseCaseVertical.PAYMENTS,
    name='Merchant checkout',
    addressable_user_base=5000,
    transaction_frequency=18,
    fee_savings=1.8,
    integration_complexity=2.5,
    regulatory_risk=1.8,
    time_to_market_months=3,
)
brief = engine.generate_brief(params)
```

### Analyze holder health

```python
from verticals.ecosystem.holder_analytics import HolderAnalyticsEngine, HolderSnapshot

engine = HolderAnalyticsEngine()
snapshot = HolderSnapshot(
    snapshot_id='snap-001',
    timestamp='2026-06-08T00:00:00+00:00',
    total_holders=3,
    balances={'0x1': 6000.0, '0x2': 2500.0, '0x3': 1500.0},
    total_supply=100000.0,
    staking_participation_rate=0.42,
    new_holders_24h=8,
)
report = engine.generate_health_report(snapshot, [])
```

### Review liquidity

```python
from verticals.ecosystem.liquidity_monitor import LiquidityMonitor

monitor = LiquidityMonitor()
fragmentation = monitor.assess_fragmentation(venues)
emissions = monitor.optimize_pool_emissions(venues, target_competitor_yield=0.12)
```

### Run competitive intelligence

```python
from verticals.ecosystem.competitive_intel import CompetitiveIntelEngine

intel = CompetitiveIntelEngine()
trends = intel.detect_narrative_trends(profiles)
summary = intel.analyze_sentiment(data_points)
```

### Coordinate the full workflow

```python
from sincor2.sinc_ecosystem_coordinator import SINCEcosystemCoordinator

coordinator = SINCEcosystemCoordinator()
result = coordinator.run_use_case_discovery(use_case_params)
health = coordinator.generate_ecosystem_health_report(metrics)
```

## Notes

- All Python modules use UTC ISO timestamps.
- Dataclasses are used for all structured payloads.
- The coordinator stores phase results so the calling app can build scheduled reports or dashboards.
