# Fluid Amplification — Integration Runbook (SINC × Fluid, Base 8453)

Implements the vault deposit/withdraw extension onto Fluid's unified liquidity layer.
All addresses and signatures below were verified against `Instadapp/fluid-contracts-public`
sources and Base deployment artifacts — not assumed.

---

## 0. Verified Fluid Base addresses

| Contract | Address |
|---|---|
| Liquidity (unified layer) | `0x52Aa899454998Be5b000Ad077a46Bbe360F4e497` |
| DexFactory | `0x91716C4EDA1Fb55e84Bf8b4c7085f84285c19085` |
| fUSDC (ERC-4626 lending) | `0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169` |
| fWETH | `0x9272D6153133175175Bc276512B2336BE3931CE9` |
| DexResolver | `0x11D80CfF056Cef4F9E6d23da8672fE9873e5cC07` |
| LendingResolver | `0x48D32f49aFeAEC7AE66ad7B9264f446fc11a1569` |
| SINC (8 dec) | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` |
| USDC (6 dec) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` |

Pool token order for SINC-USDC: **token0 = USDC, token1 = SINC** (Fluid orders by address).

## 1. Governance gates — facts, not assumptions

Verified in the Fluid factory/vault sources:

1. **`DexFactory.deployDex()` reverts unless `isDeployer(msg.sender)`.** Pool creation
   is NOT permissionless on DEX v1. Fluid governance must either list the SINC-USDC
   pair itself or grant deployer access to the treasury/multisig.
2. **Smart collateral/debt config is governance-controlled.** Fluid governance controls
   which token pairs get Liquidity-layer configs, oracles, LTVs. SINC has no oracle on
   Fluid today — a SINC-USDC pool with oracle-backed pricing is a prerequisite for
   borrowing against SINC collateral.
3. **`Liquidity.operate()` is protocol-auth gated.** Direct calls from unlisted
   contracts revert. The adapter correctly enters via fToken.deposit / DexT1.deposit /
   Vault.operate — the user-level surfaces.

**Action (blocking Stage 2-3):** open a Fluid governance/BD request to list SINC-USDC
on Fluid DEX with smart collateral enabled (or obtain deployer access for the treasury
multisig). Everything else below ships without waiting on it.

## 2. What shipped in this PR

| File | Purpose |
|---|---|
| `src/fluid/FluidInterfaces.sol` | Verified Fluid interfaces (Liquidity, DexFactory, DexT1, VaultT1, fToken) |
| `src/fluid/SincFluidAdapter.sol` | Vault extension: Stage 1 fUSDC deposits (live), Stage 2 smart collateral, Stage 3 smart debt / Vault operate |
| `script/DeploySincFluidAdapter.s.sol` | Foundry deploy script (Base, chain-guarded) |
| `test/SincFluidAdapter.fork.t.sol` | Base-fork test: full fUSDC loop + deployer-gate proof |
| `sdk/fluid-amplify.js` | getsincor.com drop-in: "Amplify SINC Liquidity" button + live reads |

## 3. Build + test

```bash
cd onchain
forge build
BASE_RPC_URL=https://mainnet.base.org \
  forge test --match-path test/SincFluidAdapter.fork.t.sol -vvv
```

Expected: 4 passing (infra live, deployer-gate blocked, fUSDC loop, DEX paths revert-until-set).

## 4. Deploy (after fork test green + slither clean)

```bash
slither src/fluid/SincFluidAdapter.sol --config-file slither.config.json

forge script script/DeploySincFluidAdapter.s.sol \
  --rpc-url $BASE_RPC_URL \
  --broadcast --verify \
  --etherscan-api-key $BASESCAN_API_KEY -vvvv
# set ADAPTER=<deployed address>
```

**Audit gate before mainnet scale:** external audit + cap initial deposits
(e.g. max $10k treasury seed). No user funds until audit closes.

## 5. Wire into SharedLiquidityVault

```bash
VAULT=0xeA90a257e5Dae20a0472C4812775F28614459bb6
# register adapter as the strategy backer (strategyId per vault layout)
cast send $VAULT "registerStrategy(uint256,string,address,address)" \
  <ID> "fluid-amplify" $ADAPTER $ADAPTER \
  --rpc-url $BASE_RPC_URL --private-key $GUARDIAN_KEY
```

## 6. Dashboard (getsincor.com)

1. Copy `onchain/sdk/fluid-amplify.js` into the site assets.
2. Set `ADAPTER` to the deployed address.
3. Add the button (snippet at bottom of the JS file). Flow: connect → switch to Base
   → approve USDC → `depositUSDC`. Live value comes from `userValueUSDC(user)`;
   once the pair is live, `amplifyPair(sinc, usdc)` supplies both as smart collateral.
4. Pool stats (fee APR, reserves, col/debt totals): `DexResolver.getDexEntireData(dex)`
   and `LendingResolver` for fUSDC rates. Full struct ABIs are in the Fluid deployment
   artifacts (Basescan-verified at the resolver addresses).

## 7. Seed the pair (post-governance)

Once Fluid lists SINC-USDC (or grants deployer access and the multisig runs
`deploySincUsdcDex`):

```bash
# 1. point adapter at the new pool
cast send $ADAPTER "setFluidDex(address)" $DEX --rpc-url $BASE_RPC_URL --private-key $GUARDIAN_KEY

# 2. treasury approves + seeds (sized to the $1.50 floor valuation — SINC is 8 decimals)
cast send $SINC "approve(address,uint256)" $ADAPTER <SINC_AMT> --rpc-url $BASE_RPC_URL --private-key $TREASURY_KEY
cast send $USDC "approve(address,uint256)" $ADAPTER <USDC_AMT> --rpc-url $BASE_RPC_URL --private-key $TREASURY_KEY
cast send $ADAPTER "supplyToDex(uint256,uint256,uint256)" <SINC_AMT> <USDC_AMT> 0 \
  --rpc-url $BASE_RPC_URL --private-key $TREASURY_KEY

# 3. optional smart-debt loop (conservative; liquidation-managed)
cast send $ADAPTER "borrowSmartDebt(uint256,uint256,uint256,address)" \
  <SHARES> <MIN_USDC> <MIN_SINC> $TREASURY \
  --rpc-url $BASE_RPC_URL --private-key $GUARDIAN_KEY
```

## 8. Announce + bootstrap

- Announce only after: adapter verified on Basescan, fork test green, treasury seed
  confirmed on-chain, dashboard button live.
- SINC incentives for vault LPs: route through the existing rewards path
  (SharedLiquidityVault settleUp) — do not add a second rewarder in the adapter.
- Publish the adapter address in CANONICAL_ADDRESSES.md post-deploy.

## 9. Honest scope notes

- "3-5x capital efficiency" is a property of Fluid smart collateral/debt once the pair
  is listed and configured by Fluid governance — it is not achievable today by code alone.
  Stage 1 (fUSDC yield on the USDC leg) is live and permissionless now.
- Per-user borrowing is intentionally not pooled through the adapter; treasury-level
  leverage is guardian-gated.
