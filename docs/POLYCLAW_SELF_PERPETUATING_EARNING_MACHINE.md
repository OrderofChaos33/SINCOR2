# Polyclaw Self-Perpetuating Earning Machine

**Status**: Spec + Implementation Plan (Safe, Additive Documentation Only)
**Date**: 2026-07-02
**Tag**: test fix x199

## Goal
Make Polyclaw the primary autonomous revenue engine for SINCOR.
- Runs on fixed schedule
- Uses TOA for intelligent forecasting, scenario simulation, decision making, and risk pruning
- Executes trades on Polymarket + perps
- Routes intelligently: Renegade dark pool for large/private/zero-impact trades (complements IntentHookV2 MEV capture) or public DEX fallback
- Feeds all PnL and activity into AccountingHub / Treasury
- Triggers parallel self-funding wheels (RehypothecationAdapter yield + IntentHookV2 MEV donation + Renegade for zero-slippage SINC position/liquidity management)
- Self-improves via observer_improver + TOA feedback loops
- Compounds capital back into itself or treasury buffer

This creates a true self-perpetuating machine that earns autonomously while strengthening the overall DAE / self-funding system.

## Current State (Verified)
- Core components exist and are solid:
  - `verticals/trading/polyclaw/core_agent.py`
  - `verticals/trading/polyclaw/simulation_engine.py`
  - `verticals/trading/polyclaw/observer_improver.py`
  - `verticals/trading/polyclaw/autonomous_rebalancer.py`
  - `verticals/trading/polyclaw/run_simulation.py`
  - `verticals/trading/polyclaw/dashboard.py`
- TOA full stack ready: `agents/toa/orchestrator.py`, `forecaster.py`, `collapser.py`, `simulator.py`, `feedback.py`, `state.py`
- Schedulers available in `src/sincor2/` (polyclaw_scheduler.py, daily_ops_scheduler.py, revenue_orchestrator.py)
- Self-funding primitives live and recently hardened: IntentHookV2 (MEV), RehypothecationAdapter + AccountingHub, Renegade integration path open

## The Self-Perpetuating Loop (Autonomous + Scheduled)

1. **Schedule Trigger**
   - Primary: `polyclaw_scheduler.py` (or wired into `revenue_orchestrator.py` / `daily_ops_scheduler.py`)
   - Cadence: Every 2-4 hours during active markets (configurable)
   - Runs with TOA oversight

2. **TOA Brain (Forecast + Decide + Risk Prune)**
   - `orchestrator.py` + `forecaster.py` + `simulator.py` + `collapser.py`
   - Generates probabilistic futures, simulates scenarios, collapses to ranked actions
   - Applies risk pruning and aggregate well-being objectives
   - Outputs: trade direction/size, route decision (Renegade vs public), risk level

3. **Intelligent Execution Router**
   - If size > threshold or MEV risk high → Route via Renegade dark pool (private, midpoint pricing, zero price impact, zero MEV leakage)
   - Else → Public DEX fallback with conservative sizing
   - Always log decision and rationale

4. **Polyclaw Core Execution**
   - `core_agent.py` + `autonomous_rebalancer.py`
   - Polymarket prediction markets + perpetuals
   - Uses existing simulation gate before live capital

5. **Post-Trade Feedback & Self-Improvement**
   - `observer_improver.py` + TOA `feedback.py`
   - Updates win-rate, strategy parameters, and TOA state
   - System gets measurably better over time autonomously

6. **PnL & Treasury Recording**
   - All results flow to `AccountingHub` (principal ledger, MEV tracking)
   - Treasury updated in real time

7. **Parallel Self-Funding Wheels (Triggered Automatically)**
   - RehypothecationAdapter → yield on treasury deposits
   - IntentHookV2 → MEV donation capture → protocol/treasury
   - Renegade → zero-impact large SINC accumulation, distribution, or liquidity adjustments (no slippage, no public signaling that could move bonding curve)

8. **Compounding / Reinvestment**
   - TOA reviews overall system state and decides allocation % back to Polyclaw capital or treasury buffer
   - Loop closes → machine perpetuates and grows

## Resilience & Hardening (Added in This Spec)
- Simulation gate: Only commit real capital if simulation + observer shows positive expectancy above threshold
- Circuit breaker: Automatic pause or size reduction on drawdown streak or TOA risk signal
- Fallbacks: Renegade unavailable or low depth → conservative public DEX mode + full logging
- Decision logging: Every routing, size, and risk decision recorded for audit and TOA improvement
- TOA central brain: All high-level decisions go through TOA so risk pruning and timeline optimization are consistent
- Monitoring: Wire to existing `monitoring_dashboard.py`, `production_logger.py`, and `observability.py`

## Quantifiable Edge
- Eliminates slippage + MEV leakage + signaling tax on material SINC / liquidity moves
- Profits compound autonomously into treasury → higher allocation → exponential earning curve
- Clean, private execution data supports stronger LBP and ongoing self-funding
- System improves itself via feedback without manual intervention

## Implementation Roadmap (Safe, Incremental, No Breaking Changes)
Phase 1 (Now - Safe Docs + Stubs)
- This document (additive only)
- Add TOA decision stub + Renegade routing comment in Polyclaw core files (non-breaking comments + small helper functions)
- Add simple /health endpoint for Railway resilience and monitoring

Phase 2 (Next 24h)
- Wire `polyclaw_scheduler.py` to call TOA orchestrator before every run
- Implement Renegade routing option (small adapter or extend existing intent logic)
- Connect PnL output to AccountingHub recording path
- Enable simulation gate + basic circuit breaker

Phase 3 (Scale)
- Full autonomous scheduling + TOA optimization live
- Dashboard metrics for Polyclaw PnL, win-rate trend, Renegade usage, self-funding contribution
- TOA continuously tunes parameters based on live feedback

## Success Metrics (Tracked Automatically)
- Win rate trend (observer_improver)
- PnL attribution (Polyclaw vs self-funding wheels)
- Capital growth / compounding rate
- Renegade vs public execution split and slippage saved
- TOA decision quality and risk pruning effectiveness
- System uptime and deploy resilience (healthcheck)

## Notes for Safety
- All changes in this spec are additive or comments only until explicitly approved for runtime code
- No modifications to core deployment files, app.py, or anything that could trigger another site/deploy break
- On-chain work remains isolated; web/agent work stays in controlled increments
- Revert capability preserved at every step

This turns recent on-chain hardening (IntentHookV2, RehypothecationAdapter, AccountingHub) into a real, live, self-perpetuating revenue engine while keeping the entire system more resilient than before the previous incident.

Next action after review: Approve specific code stubs to push (e.g. healthcheck + TOA decision helper). Then enable scheduled runs.

**Pushed as test fix x199 - zero risk to existing functionality.**