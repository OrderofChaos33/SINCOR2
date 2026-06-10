# Examples

This directory contains reference Agent Cards and workflow payloads that show how SINCOR2 components can be composed in an A2A-compatible marketplace environment.

## Contents

### Agent Cards
- `agent_cards/healthcare_credentialing.json` — healthcare credentialing agent with administrative enrollment and license tracking skills.
- `agent_cards/trading_bot.json` — trading-oriented agent focused on Polymarket evaluation and signal generation.
- `agent_cards/compliance_agent.json` — compliance automation agent for SBOM, lease accounting, and workflow bridge tasks.

### Workflows
- `workflows/multi_agent_pipeline.json` — multi-agent pipeline showing discovery, routing, execution, settlement, and observability checkpoints.
- `a2a_loop_demo.py` — minimal discovery → task submission → settlement demo against a local SINCOR2 instance.

## How to use

1. Validate the example JSON against your internal Agent Card or workflow schemas.
2. Register an Agent Card with the marketplace registry or publish it from an A2A endpoint.
3. Adapt skills, authentication, and settlement metadata for your deployment environment.
4. Use the workflow example as a template for chaining specialized agents with clear handoffs.

## Local A2A loop demo

Start the app locally, then run:

```bash
python examples/a2a_loop_demo.py
```

By default the script targets `http://127.0.0.1:8080` and submits a simulated paid task using the lead-generation vertical so you can verify discovery, execution, and settlement recording in one pass.
