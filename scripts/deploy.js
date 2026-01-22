const hre = require("hardhat");
const { ethers } = require("hardhat");
require("dotenv").config();

/**
 * SINC Token Deployment Script
 * Deploys: SINC Token, Bonding Curve, AMM Router
 * Network: Base Mainnet
 */

async function main() {
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("              SINC TOKEN DEPLOYMENT");
  console.log("═══════════════════════════════════════════════════════════\n");

  const [deployer] = await ethers.getSigners();
  console.log("Deploying contracts with account:", deployer.address);
  
  const balance = await ethers.provider.getBalance(deployer.address);
  console.log("Account balance:", ethers.formatEther(balance), "ETH\n");

  if (balance < ethers.parseEther("0.00001")) {
    console.error("❌ ERROR: Insufficient balance for deployment");
    console.error("   Need at least 0.00001 ETH for gas");
    process.exit(1);
  }

  // Configuration
  const SAFE_WALLET = process.env.SAFE_WALLET_ADDRESS || "0xF915f3F4954c3da6A7D76B424b980A897c3909f1";
  const QUOTE_TOKEN = process.env.QUOTE_TOKEN_ADDRESS || "0x4200000000000000000000000000000000000006"; // WETH
  const AERODROME_ROUTER = process.env.AERODROME_ROUTER || "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
  const AERODROME_FACTORY = process.env.AERODROME_FACTORY || "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";

  console.log("Configuration:");
  console.log("- Safe Wallet:", SAFE_WALLET);
  console.log("- Quote Token (WETH):", QUOTE_TOKEN);
  console.log("- Aerodrome Router:", AERODROME_ROUTER);
  console.log("- Aerodrome Factory:", AERODROME_FACTORY);
  console.log("");

  // ===== STEP 1: Deploy SINC Token =====
  console.log("📦 Step 1: Deploying SINC Token...");
  const SINC = await ethers.getContractFactory("SINC");
  const sinc = await SINC.deploy(SAFE_WALLET);
  await sinc.waitForDeployment();
  const sincAddress = await sinc.getAddress();
  console.log("✅ SINC Token deployed to:", sincAddress);
  
  // Wait for a few blocks to ensure deployment is confirmed
  console.log("   Waiting for confirmation...");
  await new Promise(resolve => setTimeout(resolve, 5000));
  
  try {
    console.log("   Owner:", await sinc.owner());
    console.log("   Total Supply:", ethers.formatEther(await sinc.totalSupply()), "SINC");
  } catch (e) {
    console.log("   (Contract deployed, verifying on-chain...)");
  }
  console.log("");

  // ===== STEP 2: Deploy Bonding Curve =====
  console.log("📦 Step 2: Deploying SINC Bonding Curve...");
  const BondingCurve = await ethers.getContractFactory("SINCBondingCurve");
  const bondingCurve = await BondingCurve.deploy(
    sincAddress,
    QUOTE_TOKEN,
    SAFE_WALLET, // Fee recipient
    SAFE_WALLET  // Owner
  );
  await bondingCurve.waitForDeployment();
  const bondingCurveAddress = await bondingCurve.getAddress();
  console.log("✅ Bonding Curve deployed to:", bondingCurveAddress);
  
  console.log("   Waiting for confirmation...");
  await new Promise(resolve => setTimeout(resolve, 5000));
  
  try {
    console.log("   Quote Token:", await bondingCurve.quoteToken());
    console.log("   Fee Recipient:", await bondingCurve.feeRecipient());
    console.log("   Fee:", await bondingCurve.feeBasisPoints(), "basis points");
  } catch (e) {
    console.log("   (Contract deployed, verifying on-chain...)");
  }
  console.log("");

  // ===== STEP 3: Deploy AMM Router =====
  console.log("📦 Step 3: Deploying SINC AMM Router...");
  const AMMRouter = await ethers.getContractFactory("SINCAMMRouter");
  const ammRouter = await AMMRouter.deploy(
    sincAddress,
    QUOTE_TOKEN,
    AERODROME_FACTORY,
    AERODROME_ROUTER,
    SAFE_WALLET // Owner
  );
  await ammRouter.waitForDeployment();
  const ammRouterAddress = await ammRouter.getAddress();
  console.log("✅ AMM Router deployed to:", ammRouterAddress);
  
  console.log("   Waiting for confirmation...");
  await new Promise(resolve => setTimeout(resolve, 5000));
  
  try {
    console.log("   SINC Token:", await ammRouter.sincToken());
    console.log("   WETH:", await ammRouter.WETH());
    console.log("   Factory:", await ammRouter.factory());
  } catch (e) {
    console.log("   (Contract deployed, verifying on-chain...)");
  }
  console.log("");

  // ===== STEP 4: Configure SINC Token =====
  console.log("⚙️  Step 4: Configuring SINC Token...");
  
  // Set bonding curve address
  console.log("   Setting bonding curve...");
  const setBondingCurveTx = await sinc.connect(deployer).setBondingCurve(bondingCurveAddress);
  await setBondingCurveTx.wait();
  console.log("   ✅ Bonding curve set");

  // Transfer ownership to safe wallet if deployer is not safe wallet
  if (deployer.address.toLowerCase() !== SAFE_WALLET.toLowerCase()) {
    console.log("   Transferring ownership to safe wallet...");
    const transferOwnershipTx = await sinc.connect(deployer).transferOwnership(SAFE_WALLET);
    await transferOwnershipTx.wait();
    console.log("   ✅ Ownership transferred");
  }

  console.log("");

  // ===== DEPLOYMENT SUMMARY =====
  console.log("═══════════════════════════════════════════════════════════");
  console.log("              DEPLOYMENT SUMMARY");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  console.log("Network: Base Mainnet (Chain ID: 8453)");
  console.log("Deployer:", deployer.address);
  console.log("");
  
  console.log("📋 CONTRACT ADDRESSES:");
  console.log("   SINC Token:        ", sincAddress);
  console.log("   Bonding Curve:     ", bondingCurveAddress);
  console.log("   AMM Router:        ", ammRouterAddress);
  console.log("");
  
  console.log("🔗 BASESCAN LINKS:");
  console.log("   SINC Token:        https://basescan.org/address/" + sincAddress);
  console.log("   Bonding Curve:     https://basescan.org/address/" + bondingCurveAddress);
  console.log("   AMM Router:        https://basescan.org/address/" + ammRouterAddress);
  console.log("");
  
  console.log("📝 VERIFICATION COMMANDS:");
  console.log(`   npx hardhat verify --network base ${sincAddress} "${SAFE_WALLET}"`);
  console.log(`   npx hardhat verify --network base ${bondingCurveAddress} "${sincAddress}" "${QUOTE_TOKEN}" "${SAFE_WALLET}" "${SAFE_WALLET}"`);
  console.log(`   npx hardhat verify --network base ${ammRouterAddress} "${sincAddress}" "${QUOTE_TOKEN}" "${AERODROME_FACTORY}" "${AERODROME_ROUTER}" "${SAFE_WALLET}"`);
  console.log("");
  
  console.log("📄 UPDATE .env FILE:");
  console.log(`   SINC_TOKEN_ADDRESS=${sincAddress}`);
  console.log(`   BONDING_CURVE_ADDRESS=${bondingCurveAddress}`);
  console.log(`   AMM_ROUTER_ADDRESS=${ammRouterAddress}`);
  console.log("");
  
  console.log("✅ Deployment complete!");
  console.log("");
  
  console.log("🚀 NEXT STEPS:");
  console.log("   1. Update .env file with deployed addresses");
  console.log("   2. Verify contracts on Basescan");
  console.log("   3. Transfer SINC to bonding curve for trading");
  console.log("   4. Set up initial liquidity with setup-liquidity.js");
  console.log("   5. Test buy/sell on bonding curve");
  console.log("   6. Create Aerodrome pool and add liquidity");
  console.log("");
  
  console.log("⚠️  SECURITY REMINDERS:");
  console.log("   - Ownership transferred to:", SAFE_WALLET);
  console.log("   - Initial supply in safe wallet:", SAFE_WALLET);
  console.log("   - NEVER share your private keys");
  console.log("   - Verify all transactions before signing");
  console.log("");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("\n❌ DEPLOYMENT FAILED!");
    console.error(error);
    process.exit(1);
  });
