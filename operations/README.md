# SINCOR Operations Scripts

This directory contains executable scripts for the operational bots of the SINCOR network.

## Available Bots

### 1. SINC Value Maintainer
**File:** `run_sinc_value_bot.bat`
**Purpose:** Monitors SINC token price on Curve vs DEX (Aerodrome), identifies arbitrage opportunities to maintain price alignment, and ensures deep liquidity.
**Target Assets:** SINC, WETH

### 2. Flash Loan Liquidator V3 (Multi-Protocol)
**File:** `run_flash_liquidator.bat`
**Purpose:** Scans Base network lending protocols for flash loan liquidation opportunities. Generates revenue by liquidating unhealthy positions using flash loans (ZERO capital requirement).

**Supported Protocols:**
- 🔵 **Aave V3** - Health factor < 1.0
- 🟢 **Compound V3** - Absorb + buyCollateral pattern
- 🟣 **Moonwell** - Compound V2 style liquidation

**Target Assets:** WETH, USDC, USDbC, cbETH, DAI, wstETH (on Base)

**Modes:**
1. **V3 Multi-Protocol** - Scans all protocols simultaneously (RECOMMENDED)
2. **V2 Legacy** - Aave-only scanner
3. **Sniper Mode** - Target a specific user address

## How Flash Loan Liquidation Works

```
1. Identify underwater position (health factor < 1.0)
2. Flash borrow the exact debt amount from Aave (0.05% fee)
3. Execute liquidation → receive collateral + 5-10% bonus
4. Swap collateral back to debt token via Aerodrome/Uniswap
5. Repay flash loan + fee
6. Keep the profit! 💰
```

**Zero capital required** - The protocol lends you the money!

## Setup

### Required Environment Variables
Create/update `.env` in `external/sin-bonding-curve`:

```env
# Required
PRIVATE_KEY=0x...                          # Wallet for signing transactions
BASE_RPC_URL=https://mainnet.base.org      # Base RPC endpoint

# Optional (for V2/V3 contract execution)
FLASH_LIQUIDATOR_BASE=0x...                # Deployed FlashLoanLiquidatorV2 address
FLASH_LIQUIDATOR_V3_BASE=0x...             # Deployed FlashLoanLiquidatorV3 address

# Bot Configuration
AUTO_EXECUTE=true                          # Set to true to auto-execute liquidations
MIN_PROFIT_USD=5                           # Minimum profit threshold
MAX_GAS_GWEI=50                            # Max gas price to execute
BOT_POLL_INTERVAL=5000                     # Scan interval in ms
```

### Deploy New Liquidator Contract
```bash
cd external/sin-bonding-curve
npx hardhat run scripts/deploy-flash-liquidator-v3.js --network base
```

## Quick Start
1. Configure `.env` with private key and RPC
2. Double-click `run_flash_liquidator.bat`
3. Select mode (1 = V3 recommended)
4. Monitor for opportunities!

