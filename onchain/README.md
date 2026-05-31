# SINC + AXIOM — Onchain Contracts

Foundry project for all SINCOR ecosystem smart contracts, deployed on **Base** (chainId 8453).

---

## Token overview

| Token | Symbol | Contract (Base mainnet) | Supply | Decimals | Role |
|-------|--------|------------------------|--------|----------|------|
| SINC  | SINC   | `0x9C8cd8d3961F445D653713dE65C6578bE11668e7` | 100 M | 8  | Platform utility token; burned via agent-billing loop |
| AXIOM | AXM    | `0xfF7aF6ffca25A9DC0FC990d998AcF24Cc60b7822` | 1 B   | 18 | Autonomous intelligence token; A2A inter-agent settlement |

Both tokens: fixed supply, no mint, no owner, no tax, no proxy. Verified on Basescan.

---

## Contract directory

```
src/
├── SINC_v3.sol            # SINC ERC-20 (100 M, 8 dec) — canonical token
├── Axiom.sol              # AXIOM ERC-20 (1 B, 18 dec) — A2A settlement token
├── SincBondingCurve.sol   # Constant-product bonding curve for SINC Phase 1
├── SincGenesisNFT.sol     # Soulbound ERC-721 minted to Phase 1 buyers
└── SincLimitOrderHook.sol # Uniswap V4 hook: limit orders + anti-sandwich fee

script/
├── 00_DeployMockSinc.s.sol         # Test helper — mock SINC for fork tests
├── 01_DeployGenesisNFT.s.sol       # Deploy Genesis NFT (curve address needed)
├── 02_DeployBondingCurve.s.sol     # Deploy SincBondingCurve
├── 03_FundCurveWithSINC.s.sol      # Transfer 65 M SINC into curve
├── 04_MineHookAddress.s.sol        # CREATE2 salt mining for hook permissions
├── 05_DeployHook.s.sol             # Deploy SincLimitOrderHook at mined address
└── 06_DeployAxiom.s.sol            # Deploy AXIOM token (reference / Sepolia)

test/
├── Integration.t.sol
├── SincBondingCurve.Math.t.sol
├── SincBondingCurve.Graduation.t.sol
├── SincBondingCurve.Referral.t.sol
├── SincBondingCurve.NFTMint.t.sol
├── SincBondingCurve.NoRug.t.sol
├── SincGenesisNFT.t.sol
├── SincLimitOrderHook.t.sol
└── SincLimitOrderHook.AntiSandwich.t.sol
```

Root-level ABI files (used by the Flask backend and frontend):

| File | Description |
|------|-------------|
| `SINC_v3.sol` + `SINCBondingCurve_abi.json` | SINC token + legacy bonding curve ABI |
| `Axiom_abi.json` | AXIOM token ABI |

---

## Setup

```bash
forge install
forge build
forge test
```

## Environment

Copy `onchain/.env.example` to `onchain/.env` and fill in:

| Variable | Description |
|----------|-------------|
| `BASE_RPC_URL` | Base mainnet RPC (Alchemy / Infura) |
| `BASE_SEPOLIA_RPC_URL` | Base Sepolia RPC |
| `BASESCAN_API_KEY` | For contract verification |
| `DEPLOYER_PRIVATE_KEY` | Hot wallet for scripts (NEVER the treasury key) |
| `TREASURY_ADDRESS` | Treasury wallet — receives token supply at deploy |

## Test

```bash
forge test -vvv
forge test --match-contract SincBondingCurve -vvv
```

## Deploy to Sepolia

```bash
# SINC ecosystem
forge script script/01_DeployGenesisNFT.s.sol --rpc-url $BASE_SEPOLIA_RPC_URL --broadcast --verify
forge script script/02_DeployBondingCurve.s.sol --rpc-url $BASE_SEPOLIA_RPC_URL --broadcast --verify

# AXIOM (AXM)
forge script script/06_DeployAxiom.s.sol --rpc-url $BASE_SEPOLIA_RPC_URL --broadcast --verify
```

## Deploy to Base mainnet

```bash
# SINC ecosystem — run in order; each script writes to runbook_state.json
forge script script/01_DeployGenesisNFT.s.sol --rpc-url $BASE_RPC_URL --broadcast --verify
forge script script/02_DeployBondingCurve.s.sol --rpc-url $BASE_RPC_URL --broadcast --verify
forge script script/04_MineHookAddress.s.sol --rpc-url $BASE_RPC_URL
forge script script/05_DeployHook.s.sol --rpc-url $BASE_RPC_URL --broadcast --verify
forge script script/03_FundCurveWithSINC.s.sol --rpc-url $BASE_RPC_URL --broadcast
```

---

## Supply allocation (SINC — 100 M)

| Bucket | Amount | Notes |
|--------|--------|-------|
| Bonding curve (Phase 1 + LP seed) | 65 M | Consumed by buyers, remainder paired into V4 LP and burned |
| Concentrated $1.50 ceiling LP | 5 M | Single-tick V4 position |
| Sell-side limit-order ladder | 20 M | 7 rungs $0.01 → $1.40 via SincLimitOrderHook |
| Sablier 24-month linear vest | 10 M | Non-cancellable stream |

## Supply allocation (AXIOM — 1 B)

| Bucket | Amount | Notes |
|--------|--------|-------|
| Ecosystem / A2A treasury | 80 % | Funds agent-to-agent payment pool |
| Team / development | 10 % | 24-month vest recommended |
| Liquidity (Uniswap V4) | 10 % | Seeded at launch; LP burned |

> 80 % of AXIOM trading fees are routed via the team wallet back into the SINCOR treasury (off-chain commitment, publicly auditable on Basescan).

