# SINCOR Operations Scripts

This directory contains executable scripts for the operational bots of the SINCOR network.

## Available Bots

### 1. SINC Value Maintainer
**File:** `run_sinc_value_bot.bat`
**Purpose:** Monitors SINC token price on Curve vs DEX (Aerodrome), identifies arbitrage opportunities to maintain price alignment, and ensures deep liquidity.
**Target Assets:** SINC, WETH

### 2. Base Flash Loan Liquidator
**File:** `run_flash_liquidator.bat`
**Purpose:** Scans Base network lending protocols (like Aave V3) for flash loan liquidation opportunities. Generates revenue by liquidating unhealthy positions using flash loans (0 capital requirement).
**Target Assets:** WETH, USDC, cbETH, DAI (on Base)

## Setup
Ensure `.env` files in `external/sinc-token` and `external/sin-bonding-curve` are configured with a valid `PRIVATE_KEY` and RPC URL before running.
