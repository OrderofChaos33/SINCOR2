# marketplace/

A2A marketplace domain — discovery, ranking, reputation, settlement, and network-side inflow surfaces.

## Scope
- Agent Card discoverability surfaces (registry + public directory)
- Capability indexing, matching, and ranking (capability × trust × price × latency)
- Marketplace policy, trust/reputation, quality tiers
- Settlement coordination (AXM primary)
- Public task feed + demand activation
- MCP cross-protocol bridge
- Ops metrics for external agent inflow

## Key Modules
| Module | Role |
|--------|------|
| `registry.py` | Agent Card store + skill search |
| `discovery.py` | Capability matcher + full-text / tag index |
| `public_directory.py` | High-signal public ranking for external agents & registries |
| `mcp_bridge.py` | MCP tools for discover + get card |
| `reputation.py` | EMA trust + SINC stake boost |
| `quality_tiers.py` | experimental → verified → production → staked |
| `settlement.py` | Quotes, AXM settlement, 5% treasury fee |
| `escrow.py` | Optional agent-callable holds + disputes |
| `task_feed.py` | Public task posting + healthcare seed demand |
| `ops_metrics.py` | Agents onboarded / tasks / external treasury inflow |
| `barter_engine.py` | Existing barter / matching helpers |

## Network-Side Package (2026-08-19)
See `docs/transition/NETWORK_SIDE_INFLOW_GAPS_2026-08-19.md` for the full plan that closes the discovery, onboarding, demand, trust, incentives, distribution, and ops gaps.

## Rules
- Additive changes only relative to live production paths.
- Healthcare and regulated verticals stay behind existing compliance guardrails.
- AXM is primary settlement token; SINC retained for legacy/staking paths.
- Coordinate with open PR #159 (A2A discovery bootstrap) before claiming public endpoints live.
