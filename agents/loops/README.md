# SINCOR Autonomous Profit Loops

Agent-side capital efficiency loops that sit on top of **existing live contracts** only.
No new on-chain state is introduced that can break current deployments.

## Design Principles

1. **Autonomy within hard bounds** — agents run 24/7 but every action has max notional, health-factor floor, and kill-switch.
2. **Only call audited / live surfaces** — SincFluidAdapter, SharedLiquidityVault, SincLimitOrderHook, SincSwapRouter.
3. **Fee recycling first** — every loop prioritizes turning fees/MEV/spread into more inventory or deeper ladder rather than extraction.
4. **Fail closed** — any oracle / health / rate anomaly pauses the loop and alerts.

## Loops

### Loop 1 — Fluid USDC Yield Recycle (Stage 1 live today)

**Goal:** Turn idle USDC into yield, then drip profits into the SINC limit-order ladder / agent inventory.

**Flow (agent autonomous):**
1. Monitor agent wallet USDC balance above reserve threshold.
2. Deposit excess into SincFluidAdapter.depositUSDC (fUSDC).
3. Periodically harvest / withdraw a portion of yield.
4. Convert yield USDC → SINC (via SincSwapRouter or bonding curve path) or add to USDC side of ladder.
5. Rebalance ladder via SincLimitOrderHook.

**Safety:**
- Max deposit per tx and daily notional caps.
- Never withdraw below a configured reserve.
- Pause if fUSDC convertToAssets drifts abnormally.

**Addresses (Base):** see CANONICAL_ADDRESSES.md + Fluid Integration doc.

### Loop 2 — SharedLiquidityVault Fee Compound + Ladder Drip

**Goal:** All trading fees, MEV capture (Moebius), and vault yield automatically compound back into liquidity and the sell/buy ladder.

**Flow:**
1. Agent monitors SharedLiquidityVault accrued fees / settleUp events.
2. On threshold, call vault harvest / settle paths (existing).
3. Route a configurable % of proceeds into:
   - Single-sided or proportional LP add (when pool exists)
   - Limit-order ladder top-up at $1.50+ floor
   - Agent working inventory
4. Remainder stays in vault for capital efficiency.

**Safety:**
- Only acts on already-earned fees (no principal risk beyond existing vault strategy).
- Percentage splits are config, not code.

### Loop 3 — Inventory + Limit-Order Ladder Maintenance (core MM loop)

**Goal:** Keep continuous two-sided quotes alive on the thin public pool without getting sniped cleanly.

**Flow:**
1. Subscribe to Flashblocks / preconf endpoint for pending large swaps.
2. Maintain dense sell ladder (SINC) at and above target price via SincLimitOrderHook.
3. Maintain buy ladder (USDC) below mid.
4. On large pending swap detection: temporarily widen or pull quotes, then restore.
5. Recycle any filled inventory + fees back into the ladders.
6. Rebalance inventory skew using SincSwapRouter when one side exceeds max inventory ratio.

**Safety:**
- Max inventory per side (absolute + % of controlled SINC).
- Min spread and max quote size.
- Kill switch if price oracle (SincPriceOracle) or pool state diverges beyond tolerance.
- All quotes go through the existing anti-sandwich hook.

### Future (gated) — Morpho / Fluid Smart Collateral Loop

Once SINC-USDC is listed on Fluid with oracle + LTV, or a Morpho market is live with the $1.50 floor oracle:

- Agents can run controlled leverage loops under strict HF floors (e.g. never below 1.25).
- Amplify LP size or inventory, then auto-deleverage on rate or price stress.
- Still uses only existing adapters; no new principal risk surfaces until governance gates clear.

## Autonomy Rules (hard-coded in runner)

| Parameter | Default | Notes |
|-----------|---------|-------|
| max_daily_notional_usdc | 500 | Hard daily spend/deposit cap |
| max_single_tx_usdc | 100 | Per action |
| min_health_factor | 1.25 | For any future leverage |
| max_inventory_sinc_pct | 5 | Of total controlled SINC |
| kill_switch_price_deviation_bps | 300 | Pause if oracle/pool diverges |
| reserve_usdc | 50 | Never deplete below |
| loop_interval_sec | 30–120 | Configurable per loop |

All loops respect these. Changing them requires config update + restart; no silent overrides.

## How to run

```bash
# From repo root after setting env
export PRECONF_RPC=https://mainnet-preconf.base.org
export AGENT_PRIVATE_KEY=...          # dedicated agent wallet only
export SINC_LIMIT_ORDER_HOOK=0x8e0eE51dCa5249c9e84dbec539fDD46b375110C0
export SHARED_LIQUIDITY_VAULT=0xeA90a257e5Dae20a0472C4812775F28614459bb6
export FLUID_ADAPTER=...              # post-deploy address
export TARGET_PRICE_USD=1.50

# Dry-run first
python -m agents.loops.runner --loop all --dry-run

# Live (after dry-run green + small real capital)
python -m agents.loops.runner --loop fluid_yield,ladder_mm --live
```

See `agents/loops/config.yaml` and `runner.py` for exact wiring.

## What this does NOT do

- Does not introduce new Solidity that changes live pool/hook behavior.
- Does not rely on persistent flash-loan inventory (impossible).
- Does not bypass the $1.50 floor or existing ComplianceGuard paths.
- Does not auto-deploy new Morpho/Fluid markets (governance gated).
