# Code Builder Handoff — 2026-09-19 EOD

Repo: `OrderofChaos33/SINCOR2`  
Treasury: `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`  
AXM: `0x4c3fb66f14fbaa2088c9ae91017ba770da53715a`  
SINC live: `0xe1D836087F6573b665d25CE088793E916D7892f8`  
Morpho Gauntlet USDC (gtUSDCp): `0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61`  
SharedLiquidityVault (DO NOT DEPOSIT): `0xeA90a257e5Dae20a0472C4812775F28614459bb6`

Parent brief: `docs/CEO_DAILY_BRIEF_2026-09-19.md`  
Track: #247

## Rules
- Feature branches. Full unit tests. Auditor before merge.
- `EXECUTE_LIVE` default stays `0` in source.
- Fees only to treasury. `projected=false` requires real `tx_hash`.
- No SharedLiquidityVault. No full-balance dump. First yield slice cap **$50 USDC**.

## Ticket 1 — A2A fee path (P0)
Files likely: A2A quote route, `record_platform_fee_inflow`, settlement modules, #116 / #228.

Do:
1. Quote response includes deterministic `fee_split` → treasury.
2. On successful on-chain settle, call `record_platform_fee_inflow(fee, asset="AXM"|"USDC", source="a2a_settlement", tx_hash=..., projected=False)` exactly once.
3. Simulate / `free_call` / `0xSIMULATED` → zero realized ledger writes.
4. Tests: success path 1 write; fail/sim 0 writes; reject non-AXM on new flows.

Done when: pytest green + one integration test with fake tx hash format check.

## Ticket 2 — Land or rebase open PRs (P0)
Priority order:
1. #243 healthcheck truth (do not ship a lying /health)
2. #228 AXM-only settlement
3. #116 OLTWAMMIHook + quote fee-split
4. #242 genesis funnel (needed for 26 Sep; do not block fee path)
5. #102 TWAMMI / NeutralYield
6. #81 Moebius v2 (park if conflict)

Done when: conflict-free against `main`, CI or local `ruff` + targeted pytest + forge where Solidity changed.

## Ticket 3 — Yield Aggregator product + capped live slice spec (P1)
Code: `src/sincor2/defi/yield_aggregator.py`  
Agent: `agents/defi_yield_aggregator_agent.yaml`  
Script: `scripts/emit_yield_live_intents.py`

DRY_RUN already computed for $181.65 / risk 0.30:

| Strategy | Weight | $ on full book | APR |
|----------|--------|----------------|-----|
| cash_reserve | 0.400 | 72.66 | 0% |
| morpho_usdc | 0.305 | 55.39 | 4.5% |
| aave_usdc | 0.295 | 53.60 | 3.8% |

Blended 2.49% → ~$4.53/yr on full $181. That is not the business. Conversion is.

**Authorized first live action (founder signer only):**
1. Approve USDC to Morpho vault `0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61` for **50000000** (6 decimals = $50).
2. Deposit $50 only.
3. Leave ≥$130 USDC + existing ETH for gas.
4. Record deposit tx into treasury inflow ledger as `source="yield_aggregator_morpho"` with `projected=False`.
5. Do not set `EXECUTE_LIVE=1` in git. Set only on Railway/session for that tx.

Builder work:
- Add a `plan_rebalance(capital_usd=50, risk_budget=0.20)` fixture test that Morpho weight is dominant when cash floor is reduced for a dedicated earn slice.
- Sales demo table in agent skill output (APR, risk, fee-to-treasury bps).
- Keep SharedLiquidityVault `enabled=False`.

## Ticket 4 — Swarm scheduler proof (P1)
Confirm `scripts/defi_swarm_checkin_scheduler.py` process on Railway.
If dead: wire Procfile/worker or document the exact start command in `docs/AGENT_OPS.md`.
TOA must call `ingest_feedback` every tick and persist ranking deltas. If rankings are identical for 3 consecutive ticks with new feedback, the loop is broken — fix the ingest path.

## Ticket 5 — Outreach actually sends (P0 for negotiators)
Production env: `OUTREACH_ENABLED=true`, mailer key present, `AUTONOMOUS_AGENTS=true`.
Auditor must sign each outbox envelope.
CTA only `https://getsincor.com/buy`.

## Out of scope
- Deploying unverified SharedLiquidityVault
- Spending founder ~$800 on yield
- Fake dashboard numbers
- Closing #247 before a realized fee or paid lock
