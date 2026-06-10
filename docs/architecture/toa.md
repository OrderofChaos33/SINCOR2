# Temporal Optimization Agent (TOA)

## Overview

The Temporal Optimization Agent (TOA) is a higher-level decision-optimization layer in SINCOR2 that sits above the existing task routing and orchestration stack. It implements a continuous **forecast → simulate → collapse** pipeline with recursive self-improvement via a feedback loop.

TOA enables any SINCOR2 vertical or orchestration workflow to answer: *"Given the current state and our objectives, which actions should we take next — and in what order?"*

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                       TOAOrchestrator                        │
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐ │
│  │  Forecaster  │──▶│  Simulator   │──▶│    Collapser     │ │
│  │  (KernelF.)  │   │  (MonteCarlo)│   │    (WFCCollapse) │ │
│  └──────────────┘   └──────────────┘   └────────┬─────────┘ │
│          ▲                                       │           │
│          │          ┌──────────────┐   Action    │           │
│          └──────────│  Feedback    │◀── Results  ▼           │
│                     │  (Rolling)   │   TaskRouter / Vertical │
│                     └──────────────┘                         │
└──────────────────────────────────────────────────────────────┘
```

### Sub-Agents

| Class | Role |
|---|---|
| `KernelForecaster` | Nadaraya-Watson kernel smoother + Monte Carlo path generator |
| `MonteCarloSimulator` | Multi-criteria objective scorer (revenue, risk, timeline, compliance, governance) |
| `WFCCollapser` | Wave-function-collapse–style path selector → ranked action dispatches |
| `RollingFeedbackAgent` | Ring-buffer feedback ingestion; computes aggregate signal for next forecast |

All sub-agents implement abstract base classes defined in `agents/toa/base.py` and can be replaced with custom implementations.

## Data Flow

1. **Context in** — caller provides `{"values": [...], ...}` with time-series observations and optional compliance/governance scores.
2. **Forecast** — `KernelForecaster` generates N Monte Carlo paths over the configured horizon.
3. **Simulate** — `MonteCarloSimulator` scores each path against weighted objective functions.
4. **Collapse** — `WFCCollapser` filters by probability threshold, ranks by composite score (utility × probability × priority boost), and returns top-k action dispatches.
5. **Route** — if a `TaskRouter` is wired in, the top action is automatically dispatched to the best-matching SINCOR2 agent.
6. **Feedback** — execution results are ingested via `TOAOrchestrator.ingest_feedback()` and merged into the next forecast context for recursive improvement.
7. **Persist** — session state (run count, last plan) is stored via `TOAStateStore` (JSON file or in-memory).

## Configuration

All settings are configurable via environment variables (prefix `TOA_`) or by instantiating `TOAConfig` directly:

| Env Var | Default | Description |
|---|---|---|
| `TOA_SIMULATION_DEPTH` | `50` | Monte Carlo paths per run |
| `TOA_COLLAPSE_THRESHOLD` | `0.05` | Minimum path probability to keep |
| `TOA_TOP_K_PATHS` | `5` | Number of action plans returned |
| `TOA_FORECAST_HORIZON` | `12` | Time-steps ahead to forecast |
| `TOA_MONTE_CARLO_ITERATIONS` | `1000` | Iterations per scenario |
| `TOA_OBJECTIVE_WEIGHTS` | `revenue:0.35,...` | Comma-separated `key:weight` pairs |
| `TOA_STATE_PATH` | `` | Path for persistent state (empty = memory only) |
| `TOA_STRUCTURED_LOGGING` | `true` | Emit JSON-structured log entries |
| `TOA_FEEDBACK_BUFFER_SIZE` | `500` | Max events in rolling feedback buffer |
| `TOA_RUN_TIMEOUT_SECONDS` | `120` | Per-run timeout |

## Quick Start

```python
from agents.toa import TOAOrchestrator

toa = TOAOrchestrator()

# Run the full pipeline
result = toa.run(context={"values": [100, 102, 105, 108, 110]})
print(result["action_plan"][0]["rationale"])

# Ingest feedback after execution
toa.ingest_feedback({
    "source": "trading_vertical",
    "payload": {"success": True, "quality_rating": 4.5},
})

# Re-run — feedback is incorporated automatically
result2 = toa.run(context={"values": [100, 102, 105, 108, 110]})
```

See `examples/workflows/toa_trading_optimization.py` for a full end-to-end example.

## Extending TOA

### Custom Forecaster
```python
from agents.toa.base import ForecasterAgent

class MyForecaster(ForecasterAgent):
    agent_name = "my_forecaster"
    def forecast(self, context, horizon=None):
        # Return list of {"scenario_id", "probability", "horizon", "values"} dicts
        ...

toa = TOAOrchestrator(forecaster=MyForecaster())
```

### Custom Objective Function
```python
toa.register_objective("liquidity", lambda path: path.get("liquidity_score", 0.5))
```

## Integration Points

- **Task Router**: Pass `task_router=TaskRouter(...)` to auto-dispatch the top action.
- **Verticals**: Call `toa.ingest_feedback(event)` after any vertical task completes.
- **On-chain**: Ingest settlement events (SINC transfers, liquidity changes) as feedback signals.
- **DAE Governance**: Wire governance proposal outcomes as feedback to shape future objective weights.

## Module Structure

```
agents/toa/
├── __init__.py        # Public package API
├── base.py            # Abstract base classes
├── config.py          # TOAConfig (env-driven)
├── state.py           # TOAStateStore (JSON persistence)
├── forecaster.py      # KernelForecaster (reference impl.)
├── simulator.py       # MonteCarloSimulator + objective registry
├── collapser.py       # WFCCollapser (wave-function collapse)
├── feedback.py        # RollingFeedbackAgent
└── orchestrator.py    # TOAOrchestrator (main entry point)
```

## Tests

```bash
PYTHONPATH=src:. pytest tests/pytest/test_toa.py -v
```

33 tests covering unit, integration, and end-to-end scenarios.
