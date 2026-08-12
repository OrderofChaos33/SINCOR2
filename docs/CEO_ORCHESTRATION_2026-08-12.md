# CEO Master Orchestration — 2026-08-12

**Orchestrator:** TOA / CEO Swarm Oversight  
**Branch:** `ceo/2026-08-12-orchestration-swarm`  
**Primary KPI:** Realized Treasury inflow to `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac`  
**Mode:** Parallel elite builders. No scaffolds. Production-ready only. Thoroughly tested. Do not break live mainnet state or existing green paths.

## Hard Guards (every builder)
1. Full unit tests required before any claim of done.
2. Default remains DRY_RUN / measurement-only. Never set `EXECUTE_LIVE=1` from code.
3. No mutation of live mainnet addresses, bonding curve, or already-deployed vault/hook bytecode.
4. Match existing style, error handling, and logging patterns.
5. Fee / settlement paths record **only platform fee** to treasury ledger (never principal).
6. Any on-chain work targets Sepolia first or CREATE2-mined addresses; mainnet only after explicit checklist.
7. PR must be merge-ready or explicitly blocked with data.

## Builder Roster & Assignments (one task each, parallel)

### Builder 1 — Settlement & Treasury Accounting (P0)
**Task:** Close the realized A2A conversion loop.  
- Ensure `/api/a2a/quote` (or equivalent) returns explicit `treasury_fee_split` / `platform_fee_*` fields.  
- On successful settlement, call `treasury_inflow.record_inflow(..., projected=False, source="a2a_settlement", tx_hash=...)` with **fee only**.  
- Unit tests proving fee calculation + exact one call to `record_inflow` on success path.  
**Acceptance:** pytest green, no fund movement, fee-only accounting.  
**Status:** OPEN — highest priority. Builds on draft PR #139 lineage.

### Builder 2 — On-Chain Compile & CI Guardian (P0)
**Task:** Make `forge build` + `forge test` + Slither CI fully green.  
- Consolidate pragma (`^0.8.26`), visibility (`public` where internal call needed), shadowing, and Codacy fixes from draft PRs #103/#121/#125/#136.  
- Single clean PR. No behavior change to production contracts.  
**Acceptance:** `forge build && forge test` clean on the pinned solc 0.8.26; Slither medium+ clean under existing config.  
**Status:** OPEN.

### Builder 3 — Hook Deploy & CREATE2 Miner (P1)
**Task:** Production-ready Base Sepolia + CREATE2 path for SharedLiquidityHook and LiquidityAmplifierHook.  
- Deploy scripts with required V4 flag bits, salt search, env overrides.  
- Chain-aware hook_stats + scheduler TOA ingest of hook metrics.  
- Tests for chain routing and CREATE2 determinism.  
**Acceptance:** Sepolia deploy script runs dry-run clean; metrics feed TOA; no mainnet broadcast.  
**Status:** OPEN — builds on draft PR #140.

### Builder 4 — Dashboard Integrity (P0)
**Task:** Payment-gated dashboard with zero fabricated metrics.  
- Session + confirmed payment_status required.  
- All numbers from real DB / order records or explicit `None`.  
- Template safe for missing telemetry.  
**Acceptance:** Unauthenticated and unpaid users redirected; no hardcoded lead counts or utilization %.  
**Status:** OPEN — builds on draft PR #132.

### Builder 5 — Scheduler & TOA Feedback Hardener (P1)
**Task:** Production scheduler with rich, honest feedback.  
- Replace or clearly label dummy PnL.  
- Use YieldAggregator `toa_summary()` + fee projection.  
- Every cycle writes structured TOA ingest + projected ledger entries with correct source tags.  
- One-shot and indefinite modes both tested.  
**Acceptance:** `--once` produces clean ledger lines and TOA payloads; no false realized claims.  
**Status:** PARTIAL (toa_summary already enriched 08-12); finish hardening.

### Builder 6 — External A2A Liquidity (P1)
**Task:** Zero-friction external agent onboarding.  
- Production-quality `examples/a2a_external_caller.py` (discover → quote → submit → poll).  
- Complete `examples/EXTERNAL_A2A_ONBOARDING.md` with exact curl and pricing notes.  
- Works against live or local instance with `--simulate`.  
**Acceptance:** Example runs end-to-end in simulate mode; guide matches current quote/settlement contract.  
**Status:** OPEN — builds on draft PR #115.

## Oversight Loop
- TOA ranks every builder output by expected Treasury inflow velocity.  
- Any builder that introduces breakage or theater is paused and reallocated.  
- End-of-cycle: one realized (or cleanly instrumented) fee path + green CI + at least one Sepolia-ready deploy script.  
- Daily brief updated with delta only.

## Immediate Execution Order
1. Settlement fee path (Builder 1)  
2. Compile/CI green (Builder 2)  
3. External caller + onboarding (Builder 6) — parallel, low conflict  
4. Dashboard integrity (Builder 4)  
5. Hook CREATE2 + scheduler (Builders 3 & 5)

**Results only. No half-measures.**
