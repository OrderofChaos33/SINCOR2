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
| `DEPLOY_VAULT` | `0` to skip vault redeploy (vault is already live at `0xeA90…59bb6`) |
| `DEPLOY_LOOP_INFRA` | `1` to deploy the production `SincPriceOracle` + `SincSwapRouter` |
| `DEPLOY_LENDING` | `1` to deploy `SINCLending` (uses loop infra deployed in the same run, or `SINC_ORACLE`/`SINC_ROUTER` env overrides) |

### Production loop infra (oracle + router) — deployed design

`SincPriceOracle` (upgrade of `sinc-liquidity-pipeline/contracts/OracleRouter.sol`) prices SINC as
bonding-curve spot (`currentPriceWei`, wei ETH per whole SINC) × Chainlink ETH/USD, returned as
USDC 6 dp per whole SINC scaled 1e6 (`ISincPriceOracle`). Hardened with staleness heartbeat,
positive-answer check, and admin-tunable price bounds; MANUAL mode (admin-pushed price with its
own 24 h heartbeat) covers post-graduation or Chainlink outage.

`SincSwapRouter` (upgrade of `contracts/SINCAMMRouter.sol` @ `feature/sincor-consolidation`) routes
USDC→WETH (Aerodrome volatile pool) → ETH → `curve.buy`, and SINC → `curve.sell` → ETH → WETH →
USDC. The AMM SINC pools are dust — the bonding curve IS the live market (verified onchain
2026-07-20). Slippage: each AMM leg bounded vs Aerodrome quote (1%), aggregate bounded vs oracle
(5% default; must exceed the curve's 3% referral cut).

Loop-infra dependencies (Base, exported by `deploy-base.sh`):

| Env | Address |
|---|---|
| `SINC_CURVE` | `0x75dE341a2BC81806198364F125d4Cde36527619C` |
| `CHAINLINK_ETH_USD` | `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70` |
| `WETH` | `0x4200000000000000000000000000000000000006` |
| `AERO_ROUTER` | `0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43` |
| `AERO_FACTORY` | `0x420DD381b31aEf6683db6B902084cB0FFECe40Da` |

Verified by `test/SincLoopFork.t.sol` against a live Base fork (runs in CI when `BASE_RPC_URL`
is set): oracle price sanity + staleness, real 1-USDC buy through both legs, sell round-trip,
slippage reversion, access control.

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
