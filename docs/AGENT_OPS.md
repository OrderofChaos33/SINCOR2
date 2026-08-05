# Agent Ops Manual — 24/7 SINCOR Swarm Operation

**Prime directive: everything is measured by treasury inflow.**
Treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (Base 8453).

## Operating rhythm (UTC)

| Time | Event | Owner |
|---|---|---|
| */15 min | Dispatcher heartbeat (due tasks → outbox/workers) | runner.py |
| Hourly :00 | Treasury metrics heartbeat → ledger | runner.py --metrics |
| Every 6h | Scouts prospecting cycle (DeFi + SMB) | scouts |
| Every 2h | Auditor validation of pending outputs | auditors |
| 13:00 | **TOA daily sync** — forecast-simulate-collapse, dispatch order | toa |
| 22:00 | **Auditor EOD report** → CEO review queue | auditors |
| Weekly | Caretakers: archive learnings, SBT promotions | caretakers |

## The daily sync loop (TOA)

1. **Ingest** — treasury snapshot + department KPIs from the ledger.
2. **Forecast** — 7-day revenue contribution per active path.
3. **Simulate collapse** — what breaks if the top path zeros out.
4. **Reprioritize** — departments ranked by projected revenue/risk.
5. **Dispatch** — tomorrow's order with per-department targets.

## Deployment gates (no exceptions)

1. `cd onchain && make ship` green (build + unit/fuzz + fork + invariant).
2. slither clean on changed contracts.
3. Auditor validation recorded in the ledger.
4. CEO (human) approves the actual broadcast — keys never live in the swarm.
5. Post-deploy: address recorded in `CANONICAL_ADDRESSES.md`, dashboard updated.

## Single-command contract pipeline

```bash
cd onchain
make setup      # pinned deps (matches CI exactly)
make build      # forge build --sizes
make test-unit  # unit + fuzz, no RPC
make test-fork  # live Base fork tests
make test-invariant  # stateful solvency invariants
make ship       # all of the above, green or stop
make deploy-adapter  # broadcast SincFluidAdapter (human-signed)
```

## Compliance

- `ComplianceGuard` gates adapter entry/exit (blocklist always on; sanctions
  oracle enabled once legal picks the provider — set via `setOracle`).
- Outreach agents: no return promises, no unverified claims, no contact with
  blocked addresses. Violations = auditor FAIL + ledger incident.

## Revenue instrumentation

- Treasury heartbeat: SINC / AXM / USDC balances hourly → ledger.
- Module fee capture: SharedLiquidityVault `settleUp` protocol cuts → treasury.
- Fluid amplifier: adapter `userValueUSDC` growth + (post-listing) DEX fees.
- Attribution rule: every ledger entry carries the task ID that produced it;
  weekly, caretakers roll revenue back to tasks and rank agents.

## Escalation

scouts → negotiators → toa · builders → auditors → toa · anything anomalous → CEO.
48h of stalled inflow on any owned path = automatic TOA reprioritization.
