# Deploying the SINC stack to Base mainnet — verified runbook

Status: treasury wallet `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` is funded on Base (ETH gas, $69.59 USDC, 3,193,471 SINC — verified onchain 2026-07-19). Contracts audited (`AUDIT.md`), **full clean-room build + 45/45 tests verified green 2026-07-19** (forge build + forge test, incl. 1000-run fuzz).

## 1. Install dependencies (EXACT pinned set — build is only reproducible with these)

The previous version of this runbook (OZ v4.9.6 + v4-core v4.0.0 + no periphery/hooks/permit2)
**does not compile**. The verified-good set, pinned to commits:

```bash
cd onchain
forge install foundry-rs/forge-std@v1.9.7
forge install OpenZeppelin/openzeppelin-contracts@v5.5.0
forge install Uniswap/v4-core@d153b048868a60c2403a3ef5b2301bb247884d46
forge install Uniswap/v4-periphery@7ebd04b161745b75ed0c24ba2df3bc7c25f65606
forge install Uniswap/permit2@cc56ad0f3439c502c246fc5cfcc3db92bb8b7219
forge install OpenZeppelin/uniswap-hooks@26dc8e53f812a1ca390d470342adb6cd8c3286ad
forge build
forge test    # expect 45/45 (Integration/LimitOrderHook fork tests need BASE_RPC_URL)
```

Notes: OZ **v5.5.0** is required (SincGenesisNFT `_update`, OZ uniswap-hooks). v4-core must be
the PoolOperation-era commit (v4.0.0 lacks `types/PoolOperation.sol`). v4-periphery moved
`BaseHook` to `src/utils/`. `permit2` is required by v4-periphery and has its own remapping.

## 2. Configure `onchain/.env`

| Var | Value |
|-----|-------|
| `PRIVATE_KEY` | deployer key (0x…) — use a dedicated deployer wallet, NOT the treasury key |
| `BASE_RPC` | default `https://base-rpc.publicnode.com` |
| `SINC_TOKEN` | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` (SINC v3, **8 decimals**) |
| `USDC_TOKEN` | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |
| `POOL_MANAGER` | `0x498581fF718922c3f8e6A244956aF099B2652b2b` (canonical V4, same on all chains) |
| `TREASURY` | `0x09E2891432827D8835d2E9b83B25e2a5ba9612Ac` (default) |
| `GUARDIAN` | defaults to deployer |
| `DEPLOY_HOOK` | `1` to deploy SharedLiquidityHook |
| `DEPLOY_LENDING` | `1` ONLY when `SINC_ORACLE` + `SINC_ROUTER` production contracts exist |

## 3. Run

```bash
cd onchain
chmod +x script/deploy-base.sh
./script/deploy-base.sh
```

The script pre-flights gas, deploys `SharedLiquidityVault` (+ staging `SharedLiquidityHook`,
+ `SINCLending` if enabled), writes receipts to `onchain/deployments/`, and prints the
post-deploy checklist (CREATE2 hook mining, strategy registration, shadow swaps — see
`script/Deploy.s.sol` header and `AUDIT.md` §4).
