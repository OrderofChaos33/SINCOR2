SINC Token - Quick Deploy & Liquidity Guide

Overview
--------
This document explains how to compile, deploy and seed liquidity for the SINC ERC-20 token using Hardhat.

Requirements
------------
- Node 18+ and npm
- A funded testnet account (Base Sepolia recommended) with RPC URL in env
- `UNISWAP_V2_ROUTER` env variable pointing to a UniswapV2-compatible router on the chosen network (testnet or mainnet)

Env variables (example)
-----------------------
- BASE_SEPOLIA_RPC_URL - RPC endpoint for Base Sepolia
- DEPLOYER_PRIVATE_KEY - private key for deployer account (keep secret; use environment secrets)
- UNISWAP_V2_ROUTER - router contract address (UniswapV2-style)

Install & Compile
-----------------
1. npm install
2. npm run compile

Deploy SINC (testnet)
---------------------
npx hardhat run scripts/deploy_sinc.js --network baseSepolia

Mint SINC
---------
npx hardhat run scripts/mint_sinc.js --network baseSepolia -- <TOKEN_ADDR> <TO_ADDRESS> <AMOUNT>

Add Liquidity (seed SINC/USDC)
-----------------------------
Set `UNISWAP_V2_ROUTER` env var and run:

node scripts/add_liquidity.js <SINC_ADDR> <USDC_ADDR> <SINC_AMOUNT> <USDC_AMOUNT>

Notes
-----
- This is a minimal deployment flow intended for testnet and proof-of-concept. For mainnet use, ensure audits, multisig treasury, KYC and regulatory compliance are in place before selling tokens.
- To automate market making and swaps, see later scripts or request a market-maker bot implementation.
