# Treasury Execution Agent — E-treasury-exec-47

**Shipped 19 Aug 2026**  
**Purpose:** Run the exact Yield Aggregator allocations and fee accounting while the operator is away from the shop.

## Capability

| Mode | Trigger | What happens |
|------|---------|--------------|
| Intent Queue (default) | No key / EXECUTE_LIVE=0 | Reads live capital, builds plan, queues exact allocate intents, records projected fee. Safe. |
| Live-armed | EXECUTE_LIVE=1 + ONCHAIN_EXECUTOR_PRIVATE_KEY + kill switch clear | Same + marks intents live_ready. Protocol deposit calldata still required before raw broadcast (prevents principal loss from incomplete adapters). |
| Blocked | Kill switch file present or daily cap hit | Zero action. |

## Hard Safety Rails

- Max daily spend: **$150** (env `TREASURY_EXEC_MAX_DAILY_USD`)
- Max single tx: **$110** (env `TREASURY_EXEC_MAX_SINGLE_TX_USD`)
- Contract whitelist only
- Kill switch: `data/TREASURY_EXEC_HALT` or `--trip-kill`
- Never logs private key material
- Realized inflow only after confirmed success (uses existing `record_platform_fee_inflow`)

## How to run while away

```bash
# Safe measurement + intent queue (no key needed)
python scripts/run_treasury_execution_agent.py --loop --interval 900

# Status
python scripts/run_treasury_execution_agent.py --status

# When you have a secure key ready (Railway / HSM / local):
export EXECUTE_LIVE=1
export ONCHAIN_EXECUTOR_PRIVATE_KEY=0x...
python scripts/run_treasury_execution_agent.py --once
```

## What this does NOT do

- It does not invent capital.
- It does not bypass the Yield Aggregator risk budget.
- It does not broadcast raw USDC transfers without confirmed deposit calldata for the target vault.
- It does not store keys in the repo or in agent memory.

## Next hardening (when you return)

1. Lock exact SharedLiquidityVault deposit ABI + Morpho market ID.
2. Wire OnChainExecutor.send_raw with those calldatas under the live path.
3. Optional: session-key / Safe module with spend limits so the agent never sees the root key.

Results only. The agent is capable; the remaining gate is a controlled key + confirmed protocol adapters.
