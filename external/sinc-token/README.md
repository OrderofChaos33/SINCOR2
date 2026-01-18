# SINC Token - Complete ERC-20 with Bonding Curve & AMM

**SINC (SINC)** - Premium ERC-20 token with full interoperability, logarithmic bonding curve, and AMM integration on Base.

## 📋 Token Specifications

- **Name**: SINC
- **Symbol**: SINC
- **Total Supply**: 100,000,000 SINC
- **Decimals**: 18
- **Network**: Base Mainnet (Chain ID: 8453)
- **Initial Price Range**: $1.05 - $1.33 USD

## 🎯 Features

### Token Features
- ✅ Full ERC-20 compliance
- ✅ ERC-2612 (Permit) for gasless approvals
- ✅ Burnable tokens
- ✅ Ownable with safe ownership transfer
- ✅ Cross-chain compatible design
- ✅ Interoperable with all major DeFi protocols

### Bonding Curve Features
- 📈 Logarithmic price curve: P(x) = a * ln(x + b) + c
- 💰 Initial price: $1.05 USD
- 🎯 Target price: $1.33 USD at 10M supply
- 💸 Trading fee: 0.3% (30 basis points)
- 🔒 Reentrancy protection
- 🛡️ Slippage protection on buy/sell

### AMM Features
- 🌊 Aerodrome integration (Base's leading DEX)
- 💧 Liquidity provision support
- 🔄 Token swap functionality
- 📊 Price discovery via liquidity pools

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SINC Ecosystem                        │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─────────────┐      ┌──────────────────┐             │
│  │ SINC Token  │◄─────┤ Bonding Curve    │             │
│  │  (ERC-20)   │      │ - Buy/Sell       │             │
│  └──────┬──────┘      │ - Dynamic Price  │             │
│         │             └──────────────────┘             │
│         │                                               │
│         ▼                                               │
│  ┌─────────────┐      ┌──────────────────┐             │
│  │ AMM Router  │◄─────┤ Aerodrome DEX    │             │
│  │  (SINC/ETH) │      │ - Liquidity Pool │             │
│  └─────────────┘      │ - Trading        │             │
│                       └──────────────────┘             │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

```bash
# Install dependencies
npm install

# Copy environment template
cp .env.example .env

# Edit .env with your configuration
# IMPORTANT: Use a NEW secure private key (not compromised one)
```

### Configuration (.env)

```bash
# Your new deployer wallet (needs ~0.05 ETH for deployment)
DEPLOYER_PRIVATE_KEY=YOUR_NEW_SECURE_PRIVATE_KEY

# Your safe wallet (receives initial supply)
SAFE_WALLET_ADDRESS=0xF915f3F4954c3da6A7D76B424b980A897c3909f1

# Basescan API key for verification
BASESCAN_API_KEY=YOUR_BASESCAN_API_KEY
```

### Deployment Steps

```bash
# 1. Compile contracts
npm run compile

# 2. Deploy to Base Mainnet
npm run deploy:base

# 3. Verify deployment
npm run check-deployment

# 4. Verify contracts on Basescan
npx hardhat verify --network base <SINC_TOKEN_ADDRESS> "0xF915f3F4954c3da6A7D76B424b980A897c3909f1"

# 5. Set up initial liquidity
npm run setup-liquidity
```

## 📦 Contract Addresses

After deployment, update these addresses in your `.env`:

```bash
SINC_TOKEN_ADDRESS=<deployed_address>
BONDING_CURVE_ADDRESS=<deployed_address>
AMM_ROUTER_ADDRESS=<deployed_address>
LIQUIDITY_POOL_ADDRESS=<pool_address>
```

## 💡 Usage Examples

### Buying SINC via Bonding Curve

```javascript
const { ethers } = require("ethers");

// Setup
const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
const wallet = new ethers.Wallet(privateKey, provider);

// Contract instances
const bondingCurve = new ethers.Contract(BONDING_CURVE_ADDRESS, BONDING_CURVE_ABI, wallet);
const weth = new ethers.Contract(WETH_ADDRESS, ERC20_ABI, wallet);

// Approve WETH
await weth.approve(BONDING_CURVE_ADDRESS, ethers.parseEther("0.1"));

// Buy SINC
const quoteAmount = ethers.parseEther("0.1"); // 0.1 WETH
const minSincAmount = ethers.parseEther("90"); // Minimum 90 SINC (slippage protection)

const tx = await bondingCurve.buy(quoteAmount, minSincAmount);
await tx.wait();

console.log("Purchased SINC successfully!");
```

### Adding Liquidity on Aerodrome

```javascript
// Approve tokens
await sincToken.approve(AERODROME_ROUTER, sincAmount);
await weth.approve(AERODROME_ROUTER, wethAmount);

// Add liquidity
const router = new ethers.Contract(AERODROME_ROUTER, ROUTER_ABI, wallet);
await router.addLiquidity(
  SINC_TOKEN_ADDRESS,
  WETH_ADDRESS,
  false, // volatile pool
  sincAmount,
  wethAmount,
  minSinc,
  minWeth,
  wallet.address,
  deadline
);
```

### Trading on Aerodrome

```javascript
// Swap WETH for SINC
const path = [WETH_ADDRESS, SINC_TOKEN_ADDRESS];
const amountIn = ethers.parseEther("0.1"); // 0.1 WETH
const minAmountOut = ethers.parseEther("90"); // Min 90 SINC

await weth.approve(AERODROME_ROUTER, amountIn);
await router.swapExactTokensForTokens(
  amountIn,
  minAmountOut,
  path,
  wallet.address,
  deadline
);
```

## 🔐 Security Features

- ✅ **OpenZeppelin Contracts**: Industry-standard secure implementations
- ✅ **Reentrancy Guard**: Protection against reentrancy attacks
- ✅ **Slippage Protection**: Min/max amount checks on all trades
- ✅ **Ownership Controls**: Safe ownership transfer pattern
- ✅ **Fee Limits**: Maximum fee cap to prevent abuse
- ✅ **Emergency Withdrawals**: Owner can rescue stuck tokens

## 📊 Bonding Curve Mathematics

The bonding curve uses a logarithmic formula for price discovery:

```
P(x) = a * ln(x + b) + c

Where:
- P(x) = Price at supply x
- a = 0.000000028 (curve steepness)
- b = 1,000,000 (horizontal shift)
- c = 1.05 (base price in WETH)
- x = Current circulating supply
```

**Price Examples:**
- At 0 supply: ~$1.05 USD
- At 1M supply: ~$1.15 USD
- At 5M supply: ~$1.25 USD
- At 10M supply: ~$1.33 USD

## 🛠️ Development

### Run Tests

```bash
npx hardhat test
```

### Local Development

```bash
# Start local Hardhat node
npx hardhat node

# Deploy to local node
npx hardhat run scripts/deploy.js --network localhost
```

### Contract Verification

```bash
# Verify SINC Token
npx hardhat verify --network base <SINC_ADDRESS> "<SAFE_WALLET>"

# Verify Bonding Curve
npx hardhat verify --network base <BONDING_CURVE_ADDRESS> \
  "<SINC_ADDRESS>" "<WETH_ADDRESS>" "<FEE_RECIPIENT>" "<OWNER>"

# Verify AMM Router
npx hardhat verify --network base <AMM_ROUTER_ADDRESS> \
  "<SINC_ADDRESS>" "<WETH_ADDRESS>" "<FACTORY>" "<ROUTER>" "<OWNER>"
```

## 📈 Initial Distribution

- **Total Supply**: 100,000,000 SINC
- **Safe Wallet**: 100,000,000 SINC (initial mint)
- **Bonding Curve**: Transfer amount for trading
- **Liquidity Pool**: 5,000,000 SINC + 1 WETH (configurable)

## 🌐 Useful Links

- **Base Mainnet RPC**: https://mainnet.base.org
- **Basescan**: https://basescan.org
- **Aerodrome Finance**: https://aerodrome.finance
- **OpenZeppelin Docs**: https://docs.openzeppelin.com

## ⚠️ Important Notes

1. **Never share your private keys**
2. **Use a NEW wallet for deployment** (not the compromised one)
3. **Test on Base Sepolia testnet first** if unsure
4. **Verify all contracts on Basescan** after deployment
5. **Transfer ownership to multisig** for production
6. **Keep at least 0.05 ETH** in deployer wallet for gas

### Security Incident: Leaked deployer key
If a deployer private key has been committed or otherwise exposed, take these actions immediately:

- **Rotate keys**: generate a new deployer key and store it locally (do NOT commit). Example (local):
  ```bash
  node -e "const { Wallet } = require('ethers'); const w = Wallet.createRandom(); console.log('PRIVATE_KEY=', w.privateKey);"
  # Then paste into your local .env (do NOT commit)
  ```

- **Transfer ownership to your multisig/safe wallet** (must be executed by the current contract owner):
  ```bash
  # Example using hardhat (replace OWNER_ADDRESS and CONTRACT_ADDRESS)
  npx hardhat run scripts/transfer-ownership.js --network base --contract <CONTRACT_ADDRESS> --owner <MULTISIG_ADDRESS>
  ```
  Or, manually in a node REPL:
  ```js
  const { ethers } = require('ethers');
  const provider = new ethers.JsonRpcProvider(process.env.BASE_RPC_URL);
  const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
  const abi = ['function transferOwnership(address)'];
  const c = new ethers.Contract('<CONTRACT_ADDRESS>', abi, wallet);
  await c.transferOwnership('<MULTISIG_ADDRESS>');
  ```

- **Remove secrets from git and purge history**: remove the file and rewrite history using BFG or git filter-repo. Example:
  ```bash
  git rm --cached external/sinc-token/.env
  git commit -m "chore(secrets): remove leaked .env"
  # Then use BFG or git filter-repo to purge the key from history (this rewrites history).
  ```

- **Audit usages**: check CI, deploy scripts, and other places for the leaked key and rotate those credentials as well.

If you want, we can perform the repository cleanup (remove `.env` from repo and add to `.gitignore`), generate a new local key, and add these instructions to the docs.

## 📞 Support

For issues or questions:
1. Check contract addresses in `.env`
2. Verify deployment with `npm run check-deployment`
3. Review transaction on Basescan
4. Check contract verification status

## 📄 License

MIT License - See LICENSE file for details

---

**Built with ❤️ for SINC**  
*Clean, Perfect, Production-Ready*
