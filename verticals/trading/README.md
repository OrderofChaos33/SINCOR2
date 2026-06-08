# Trading Vertical

The trading pack focuses on market discovery, probabilistic edge estimation, and position sizing for operators running automated or semi-automated prediction strategies.

## Modules

- `openclaw_agent.py` — signal generation, Kelly sizing, win-rate adaptation, and position management.
- `polymarket_agent.py` — open market discovery, edge calculations, and mock order placement.
- `agent_card.json` — A2A-compliant discoverability document for trading capabilities.

## Notes

These modules provide decision-support and orchestration primitives only. Operators should layer venue-specific compliance, execution controls, and human review where appropriate.
