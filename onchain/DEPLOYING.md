# Deploying the SINC stack on Base — runbook

**Status (verified onchain 2026-07-19):** treasury `0x09E2…12Ac` holds ETH for gas, **$69.59 USDC**, and **3,193,471 SINC** on Base. SINC v3 `0x9C8c…68e7` confirmed: 8 decimals, 100M supply. V4 PoolManager live on Base. Contracts: 41/41 tests green, Slither 0 medium+ (see `AUDIT.md`).

## 3 steps

```bash
# 1. clone & set up (once)
git clone https://github.com/OrderofChaos33/SINCOR2.git && cd SINCOR2/onchain
curl -L https://foundry.paradigm.xyz | bash && foundryup
forge install foundry-rs/forge-std@v1.9.7 OpenZeppelin/openzeppelin-contracts@v4.9.6 Uniswap/v4-core@v4.0.0 --no-commit

# 2. your key (never committed — .env is gitignored)
echo "PRIVATE_KEY=0x..." > .env

# 3. deploy
chmod +x script/deploy-base.sh && ./script/deploy-base.sh
```

The script prints each deployed address, then the post-deploy checklist.

## What gets deployed

| Contract | Default | Notes |
|---|---|---|
| `SharedLiquidityVault` | always | core Aqua/Fluid accounting layer |
| `SharedLiquidityHook` | `DEPLOY_HOOK=1` | staging instance; production pool attachment needs CREATE2 address mining (0xC0 flags) first |
| `SINCLending` | `DEPLOY_LENDING=0` | enable once `SINC_ORACLE` + `SINC_ROUTER` production contracts exist |

## Safety gates before real value

Per `AUDIT.md` §4: hook address mining → register strategy/pool → **shadow swap with caps** → `checkInvariant()` after every swap for 48h → external audit before TVL > $50k.
