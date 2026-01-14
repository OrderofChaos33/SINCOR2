# SINC Token Deployment Guide

Complete step-by-step guide for deploying SINCOR (SINC) token on Base Mainnet.

## 📋 Pre-Deployment Checklist

- [ ] Fresh, secure private key generated (NOT the compromised one)
- [ ] Deployer wallet funded with 0.05+ ETH on Base
- [ ] Basescan API key obtained (for verification)
- [ ] `.env` file configured with all variables
- [ ] All dependencies installed (`npm install`)
- [ ] Contracts compiled successfully (`npm run compile`)

## 🔧 Step 1: Environment Setup

1. **Copy environment template:**
   ```bash
   cp .env.example .env
   ```

2. **Generate new secure wallet (if needed):**
   ```bash
   node -e "const ethers = require('ethers'); const wallet = ethers.Wallet.createRandom(); console.log('Address:', wallet.address); console.log('Private Key:', wallet.privateKey);"
   ```

3. **Edit `.env` file:**
   ```bash
   DEPLOYER_PRIVATE_KEY=<your_new_secure_private_key>
   SAFE_WALLET_ADDRESS=0xF915f3F4954c3da6A7D76B424b980A897c3909f1
   BASESCAN_API_KEY=<your_basescan_api_key>
   ```

4. **Fund deployer wallet:**
   - Send 0.05 ETH to deployer address on Base Mainnet
   - Verify: https://basescan.org/address/<your_deployer_address>

## 🚀 Step 2: Deploy Contracts

1. **Install dependencies:**
   ```bash
   cd external/sinc-token
   npm install
   ```

2. **Compile contracts:**
   ```bash
   npm run compile
   ```
   
   Expected output:
   ```
   Compiled 3 Solidity files successfully
   ```

3. **Deploy to Base Mainnet:**
   ```bash
   npm run deploy:base
   ```
   
   This will deploy:
   - SINC Token (ERC-20)
   - Bonding Curve
   - AMM Router
   
   **IMPORTANT**: Save the deployed contract addresses!

4. **Update `.env` with deployed addresses:**
   ```bash
   SINC_TOKEN_ADDRESS=<deployed_sinc_address>
   BONDING_CURVE_ADDRESS=<deployed_bonding_curve_address>
   AMM_ROUTER_ADDRESS=<deployed_amm_router_address>
   ```

## ✅ Step 3: Verify Deployment

1. **Run deployment check:**
   ```bash
   npm run check-deployment
   ```
   
   Verify all checks pass:
   - ✅ SINC Token deployed
   - ✅ Bonding Curve deployed
   - ✅ AMM Router deployed
   - ✅ All configurations correct

2. **Verify contracts on Basescan:**
   ```bash
   # SINC Token
   npx hardhat verify --network base <SINC_TOKEN_ADDRESS> "0xF915f3F4954c3da6A7D76B424b980A897c3909f1"
   
   # Bonding Curve
   npx hardhat verify --network base <BONDING_CURVE_ADDRESS> \
     "<SINC_TOKEN_ADDRESS>" \
     "0x4200000000000000000000000000000000000006" \
     "0xF915f3F4954c3da6A7D76B424b980A897c3909f1" \
     "0xF915f3F4954c3da6A7D76B424b980A897c3909f1"
   
   # AMM Router
   npx hardhat verify --network base <AMM_ROUTER_ADDRESS> \
     "<SINC_TOKEN_ADDRESS>" \
     "0x4200000000000000000000000000000000000006" \
     "0x420DD381b31aEf6683db6B902084cB0FFECe40Da" \
     "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43" \
     "0xF915f3F4954c3da6A7D76B424b980A897c3909f1"
   ```

3. **Verify on Basescan website:**
   - SINC Token: https://basescan.org/address/<SINC_TOKEN_ADDRESS>
   - Bonding Curve: https://basescan.org/address/<BONDING_CURVE_ADDRESS>
   - AMM Router: https://basescan.org/address/<AMM_ROUTER_ADDRESS>
   
   Check for green ✅ "Contract Source Code Verified" badge

## 💰 Step 4: Fund Bonding Curve

Transfer SINC tokens to bonding curve for trading:

```bash
# Transfer 50M SINC to bonding curve
node -e "
const ethers = require('ethers');
const provider = new ethers.JsonRpcProvider('https://mainnet.base.org');
const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
const sincABI = ['function transfer(address to, uint256 amount) returns (bool)'];
const sinc = new ethers.Contract(process.env.SINC_TOKEN_ADDRESS, sincABI, wallet);
(async () => {
  const tx = await sinc.transfer(
    process.env.BONDING_CURVE_ADDRESS,
    ethers.parseEther('50000000')
  );
  await tx.wait();
  console.log('✅ Transferred 50M SINC to bonding curve');
})();
"
```

## 💧 Step 5: Add Initial Liquidity

1. **Wrap ETH to WETH:**
   ```bash
   node -e "
   const ethers = require('ethers');
   const provider = new ethers.JsonRpcProvider('https://mainnet.base.org');
   const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
   const wethABI = ['function deposit() payable'];
   const weth = new ethers.Contract('0x4200000000000000000000000000000000000006', wethABI, wallet);
   (async () => {
     const tx = await weth.deposit({ value: ethers.parseEther('1') });
     await tx.wait();
     console.log('✅ Wrapped 1 ETH to WETH');
   })();
   "
   ```

2. **Transfer SINC to your safe wallet (for liquidity):**
   ```bash
   # Transfer 5M SINC to safe wallet for liquidity
   node -e "
   const ethers = require('ethers');
   const provider = new ethers.JsonRpcProvider('https://mainnet.base.org');
   const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
   const sincABI = ['function transfer(address to, uint256 amount) returns (bool)'];
   const sinc = new ethers.Contract(process.env.SINC_TOKEN_ADDRESS, sincABI, wallet);
   (async () => {
     const tx = await sinc.transfer(
       '0xF915f3F4954c3da6A7D76B424b980A897c3909f1',
       ethers.parseEther('5000000')
     );
     await tx.wait();
     console.log('✅ Transferred 5M SINC to safe wallet');
   })();
   "
   ```

3. **Set up liquidity pool:**
   ```bash
   npm run setup-liquidity
   ```
   
   This creates SINC/WETH pool on Aerodrome with:
   - 5,000,000 SINC
   - 1 WETH
   - Initial price: ~$1.05 per SINC (assuming ETH = $3000)

## 🎉 Step 6: Verify Everything Works

1. **Check token on Basescan:**
   - Total Supply: 100,000,000 SINC
   - Safe Wallet Balance: Check balance
   - Bonding Curve Balance: ~50,000,000 SINC

2. **Test buy on bonding curve:**
   ```bash
   # Small test buy with 0.001 WETH
   node scripts/test-bonding-curve-buy.js
   ```

3. **Check Aerodrome pool:**
   - Visit: https://aerodrome.finance/liquidity
   - Search for SINC/WETH pool
   - Verify liquidity is visible

4. **Test swap on Aerodrome:**
   - Go to: https://aerodrome.finance/swap
   - Paste SINC token address
   - Try small test swap (0.001 WETH → SINC)

## 📊 Post-Deployment Checklist

- [ ] All contracts deployed and verified on Basescan
- [ ] SINC tokens distributed:
  - [ ] 50M SINC in bonding curve
  - [ ] 5M SINC in Aerodrome liquidity
  - [ ] Remaining in safe wallet
- [ ] Liquidity pool created on Aerodrome
- [ ] Test trades executed successfully
- [ ] Contract ownership transferred to safe wallet
- [ ] Documentation updated with contract addresses
- [ ] Community announcement prepared

## 🔐 Security Best Practices

1. **Transfer ownership to multisig:**
   ```solidity
   sincToken.transferOwnership(multisigAddress);
   bondingCurve.transferOwnership(multisigAddress);
   ```

2. **Verify all transactions:**
   - Always check transaction details before signing
   - Use hardware wallet for safe wallet operations
   - Never share private keys

3. **Monitor contracts:**
   - Set up alerts on Basescan
   - Monitor trading volume
   - Watch for unusual activity

## 🆘 Troubleshooting

### Deployment Fails

**Issue**: "Insufficient funds for gas"
- **Solution**: Add more ETH to deployer wallet

**Issue**: "nonce too low"
- **Solution**: Wait for pending transactions to confirm

**Issue**: "Contract already deployed at address"
- **Solution**: Clean artifacts: `rm -rf artifacts cache`

### Verification Fails

**Issue**: "Already Verified"
- **Solution**: Contract is already verified, skip step

**Issue**: "Constructor arguments mismatch"
- **Solution**: Double-check constructor arguments match deployment

### Liquidity Setup Fails

**Issue**: "Insufficient WETH balance"
- **Solution**: Wrap ETH to WETH first (see Step 5.1)

**Issue**: "Insufficient SINC balance"
- **Solution**: Transfer SINC to wallet first (see Step 5.2)

## 📞 Support Resources

- **Base Documentation**: https://docs.base.org
- **Aerodrome Docs**: https://aerodrome.finance/docs
- **Hardhat Docs**: https://hardhat.org/docs
- **OpenZeppelin**: https://docs.openzeppelin.com

---

**Deployment Completed Successfully! 🎉**

Your SINC token is now live on Base Mainnet with full bonding curve and AMM functionality.
