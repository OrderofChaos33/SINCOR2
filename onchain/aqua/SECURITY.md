# Aqua Integration — Security Audit (2026-08-07)

**Scope:** `onchain/aqua/**` live ship path  
**Status:** Hardened after pen-test. 0 critical/high open in this surface.

## Findings fixed this pass

| ID | Severity | Issue | Fix |
|----|----------|-------|-----|
| A1 | High | Live broadcast with no explicit confirmation → accidental treasury ship | Require `CONFIRM=LIVE` env for any broadcast |
| A2 | High | No amount bounds — env typo could ship entire inventory | `MAX_SINC_SHIP` / `MAX_WETH_SHIP` hard caps + positive decimal parse |
| A3 | High | No balance/allowance preflight — failed txs waste gas, unclear failures | On-chain `balanceOf` + `allowance` checks before broadcast |
| A4 | Medium | `@1inch/*-sdk: latest` floating dependency (supply-chain) | Pin `^0.3.0` / `^0.4.0` |
| A5 | Medium | No chainId check — wrong RPC could target another EVM | `getChainId() === 8453` |
| A6 | Medium | Trust SDK registry address alone | Pin `AQUA_REGISTRY` / router in `constants.ts`; cross-check SDK vs pin; abort on mismatch |
| A7 | Medium | Stale `SWAP_VM` address (`0x8fdd…`) in constants from superseded docs | Removed |
| A8 | Medium | `FEE_BPS` / amounts not validated (NaN, negative, absurd) | Integer bounds + strict decimal regex |
| A9 | Low | Weak private-key format check | Require `0x` + 64 hex |
| A10 | Low | No `estimateGas` before send | estimateGas + 20% headroom; fail closed |
| A11 | Low | Unexpected `msg.value` not rejected | Abort if value ≠ 0 |
| A12 | Info | Key never logged (already true) | Re-confirmed; only addresses + truncated calldata printed |

## Residual risks (accepted / out of scope)

| Risk | Mitigation / note |
|------|-------------------|
| Maker is single EOA treasury | Use hardware wallet / multisig for treasury long-term; key never in repo |
| Unlimited ERC-20 allowance to Aqua registry | Standard for this protocol; revoke when idle; monitor Basescan |
| 1inch Aqua protocol risk (smart contract / resolver set) | External dependency; 8 audits claimed by 1inch; not under SINCOR control |
| Impermanent loss / adverse fills on shipped strategy | Market risk, not a code vuln |
| RPC integrity (public `mainnet.base.org`) | Prefer private BASE_RPC_URL in production |
| Compromised npm package within pinned range | Lockfile + `pnpm audit`; consider exact pins after first successful live ship |

## Operational checklist before every live ship

1. Maker = treasury `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (not Polyclaw / Polymarket key)
2. SINC + WETH approved to `0x1111113ccf1426a8e30e2bff5e005d929bf6a90a`
3. Amounts within caps and intentional
4. `CONFIRM=LIVE` set only for the intentional command
5. Watch Basescan for the returned tx hash
6. Verify strategy appears on https://1inch.com/aqua

## What this audit does NOT cover

- `SharedLiquidityVault` / `SharedLiquidityHook` / `SINCLending` (see `onchain/AUDIT.md`)
- Polyclaw / Polymarket CLOB key handling
- Railway runtime secrets configuration
- Frontend wallet-connect surface

## Commands

```bash
cd onchain/aqua && pnpm install

# Dry-run (no key, no broadcast)
MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac pnpm ship:dry-run

# Live (requires CONFIRM=LIVE + matching PRIVATE_KEY)
CONFIRM=LIVE \
MAKER_ADDRESS=0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac \
PRIVATE_KEY=0x... \
pnpm ship
```
