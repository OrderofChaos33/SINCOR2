# SINC Onchain

Foundry project for the SINC token relaunch contracts.

## Setup

```bash
forge install
forge build
forge test
```

## Environment

Copy `.env.example` to `.env` and fill in:
- `BASE_RPC_URL` — Base mainnet RPC (Alchemy, Infura, etc.)
- `BASE_SEPOLIA_RPC_URL` — Base Sepolia RPC
- `BASESCAN_API_KEY` — for contract verification
- `DEPLOYER_PRIVATE_KEY` — local hot wallet (NEVER use treasury key here)

## Test

```bash
forge test -vvv
forge test --match-contract SincBondingCurve -vvv
```

## Deploy to Sepolia

```bash
forge script script/01_DeployGenesisNFT.s.sol --rpc-url sepolia --broadcast --verify
forge script script/02_DeployBondingCurve.s.sol --rpc-url sepolia --broadcast --verify
```
