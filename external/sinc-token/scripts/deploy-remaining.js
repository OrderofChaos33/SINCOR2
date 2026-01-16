const hre = require("hardhat");
const { ethers } = require("hardhat");
require("dotenv").config();

/**
 * Deploy remaining SINC contracts (Bonding Curve + AMM Router)
 * Uses existing SINC Token at 0xd10D86D09ee4316CdD3585fd6486537b7119A073
 */

async function main() {
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("        SINC REMAINING CONTRACTS DEPLOYMENT");
  console.log("═══════════════════════════════════════════════════════════\n");

  const [deployer] = await ethers.getSigners();
  console.log("Deploying with account:", deployer.address);
  
  const balance = await ethers.provider.getBalance(deployer.address);
  console.log("Account balance:", ethers.formatEther(balance), "ETH\n");

  // Existing SINC Token
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  
  // Configuration
  const SAFE_WALLET = process.env.SAFE_WALLET_ADDRESS || "0x9c87ac99da9AA1c6A7A20ac8214DCa846870b1c7";
  const QUOTE_TOKEN = process.env.QUOTE_TOKEN_ADDRESS || "0x4200000000000000000000000000000000000006"; // WETH
  const AERODROME_ROUTER = process.env.AERODROME_ROUTER || "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
  const AERODROME_FACTORY = process.env.AERODROME_FACTORY || "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";

  console.log("Configuration:");
  console.log("- SINC Token:", SINC_ADDRESS);
  console.log("- Safe Wallet:", SAFE_WALLET);
  console.log("- Quote Token (WETH):", QUOTE_TOKEN);
  console.log("");

  // ===== STEP 1: Deploy Bonding Curve =====
  console.log("📦 Step 1: Deploying SINC Bonding Curve...");
  const BondingCurve = await ethers.getContractFactory("SINCBondingCurve");
  const bondingCurve = await BondingCurve.deploy(
    SINC_ADDRESS,
    QUOTE_TOKEN,
    SAFE_WALLET, // Fee recipient
    SAFE_WALLET  // Owner
  );
  await bondingCurve.waitForDeployment();
  const bondingCurveAddress = await bondingCurve.getAddress();
  console.log("✅ Bonding Curve deployed to:", bondingCurveAddress);
  
  console.log("   Waiting for confirmation...");
  await new Promise(resolve => setTimeout(resolve, 5000));
  console.log("");

  // ===== STEP 2: Deploy AMM Router =====
  console.log("📦 Step 2: Deploying SINC AMM Router...");
  const AMMRouter = await ethers.getContractFactory("SINCAMMRouter");
  const ammRouter = await AMMRouter.deploy(
    SINC_ADDRESS,
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
  console.log("");

  // ===== STEP 3: Configure SINC Token =====
  console.log("⚙️  Step 3: Configuring SINC Token...");
  
  const sinc = await ethers.getContractAt("SINC", SINC_ADDRESS);
  
  console.log("   Setting bonding curve...");
  const setBondingCurveTx = await sinc.connect(deployer).setBondingCurve(bondingCurveAddress);
  await setBondingCurveTx.wait();
  console.log("   ✅ Bonding curve set");
  console.log("");

  // ===== DEPLOYMENT SUMMARY =====
  console.log("═══════════════════════════════════════════════════════════");
  console.log("              DEPLOYMENT COMPLETE!");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  console.log("Network: Base Mainnet (Chain ID: 8453)\n");
  
  console.log("Deployed Contracts:");
  console.log("- SINC Token:     ", SINC_ADDRESS);
  console.log("- Bonding Curve:  ", bondingCurveAddress);
  console.log("- AMM Router:     ", ammRouterAddress);
  console.log("");
  
  console.log("📋 Update your .env file with:");
  console.log(`SINC_TOKEN_ADDRESS=${SINC_ADDRESS}`);
  console.log(`BONDING_CURVE_ADDRESS=${bondingCurveAddress}`);
  console.log(`AMM_ROUTER_ADDRESS=${ammRouterAddress}`);
  console.log("");
  
  console.log("🔗 View on Basescan:");
  console.log(`   Token:  https://basescan.org/token/${SINC_ADDRESS}`);
  console.log(`   Curve:  https://basescan.org/address/${bondingCurveAddress}`);
  console.log(`   Router: https://basescan.org/address/${ammRouterAddress}`);
  console.log("");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("\n❌ DEPLOYMENT FAILED!");
    console.error(error);
    process.exit(1);
  });
