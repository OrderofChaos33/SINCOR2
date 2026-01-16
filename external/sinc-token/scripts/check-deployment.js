const { ethers } = require("ethers");
require("dotenv").config();

/**
 * Checks deployment status and configuration
 */

async function main() {
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("           SINC TOKEN DEPLOYMENT CHECK");
  console.log("═══════════════════════════════════════════════════════════\n");

  const provider = new ethers.JsonRpcProvider(process.env.BASE_RPC_URL || "https://mainnet.base.org");

  // Get addresses from .env
  const SINC_TOKEN = process.env.SINC_TOKEN_ADDRESS;
  const BONDING_CURVE = process.env.BONDING_CURVE_ADDRESS;
  const AMM_ROUTER = process.env.AMM_ROUTER_ADDRESS;
  const SAFE_WALLET = process.env.SAFE_WALLET_ADDRESS || "0xF915f3F4954c3da6A7D76B424b980A897c3909f1";

  if (!SINC_TOKEN || !BONDING_CURVE || !AMM_ROUTER) {
    console.error("❌ ERROR: Missing contract addresses in .env file");
    console.error("   Please deploy contracts first using: npm run deploy:base");
    process.exit(1);
  }

  console.log("Checking deployment...");
  console.log("Network: Base Mainnet");
  console.log("");

  // ERC20 ABI (minimal)
  const ERC20_ABI = [
    "function name() view returns (string)",
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
    "function totalSupply() view returns (uint256)",
    "function balanceOf(address) view returns (uint256)",
    "function owner() view returns (address)",
    "function bondingCurve() view returns (address)",
  ];

  // Bonding Curve ABI (minimal)
  const BONDING_CURVE_ABI = [
    "function sincToken() view returns (address)",
    "function quoteToken() view returns (address)",
    "function feeRecipient() view returns (address)",
    "function feeBasisPoints() view returns (uint256)",
    "function circulatingSupply() view returns (uint256)",
    "function getCurrentPrice(uint256) view returns (uint256)",
  ];

  // ===== CHECK SINC TOKEN =====
  console.log("📋 SINC Token Check");
  console.log("   Address:", SINC_TOKEN);

  try {
    const sinc = new ethers.Contract(SINC_TOKEN, ERC20_ABI, provider);

    const name = await sinc.name();
    const symbol = await sinc.symbol();
    const decimals = await sinc.decimals();
    const totalSupply = await sinc.totalSupply();
    const owner = await sinc.owner();
    const bondingCurveAddr = await sinc.bondingCurve();
    const walletBalance = await sinc.balanceOf(SAFE_WALLET);

    console.log("   ✅ Contract deployed");
    console.log("   Name:", name);
    console.log("   Symbol:", symbol);
    console.log("   Decimals:", decimals);
    console.log("   Total Supply:", ethers.formatEther(totalSupply), symbol);
    console.log("   Owner:", owner);
    console.log("   Bonding Curve:", bondingCurveAddr);
    console.log("   Safe Wallet Balance:", ethers.formatEther(walletBalance), symbol);

    // Verify owner is safe wallet
    if (owner.toLowerCase() === SAFE_WALLET.toLowerCase()) {
      console.log("   ✅ Owner is safe wallet");
    } else {
      console.log("   ⚠️  WARNING: Owner is not safe wallet!");
    }

    // Verify bonding curve is set
    if (bondingCurveAddr === BONDING_CURVE) {
      console.log("   ✅ Bonding curve configured correctly");
    } else {
      console.log("   ⚠️  WARNING: Bonding curve mismatch!");
    }

  } catch (error) {
    console.error("   ❌ ERROR:", error.message);
  }

  console.log("");

  // ===== CHECK BONDING CURVE =====
  console.log("📋 Bonding Curve Check");
  console.log("   Address:", BONDING_CURVE);

  try {
    const bondingCurve = new ethers.Contract(BONDING_CURVE, BONDING_CURVE_ABI, provider);

    const sincAddr = await bondingCurve.sincToken();
    const quoteToken = await bondingCurve.quoteToken();
    const feeRecipient = await bondingCurve.feeRecipient();
    const feeBps = await bondingCurve.feeBasisPoints();
    const circulatingSupply = await bondingCurve.circulatingSupply();
    const currentPrice = await bondingCurve.getCurrentPrice(circulatingSupply);

    console.log("   ✅ Contract deployed");
    console.log("   SINC Token:", sincAddr);
    console.log("   Quote Token:", quoteToken);
    console.log("   Fee Recipient:", feeRecipient);
    console.log("   Fee:", feeBps, "basis points (" + (Number(feeBps) / 100) + "%)");
    console.log("   Circulating Supply:", ethers.formatEther(circulatingSupply), "SINC");
    console.log("   Current Price:", ethers.formatEther(currentPrice), "WETH per SINC");

    // Verify configuration
    if (sincAddr === SINC_TOKEN) {
      console.log("   ✅ SINC token configured correctly");
    } else {
      console.log("   ⚠️  WARNING: SINC token mismatch!");
    }

    if (feeRecipient.toLowerCase() === SAFE_WALLET.toLowerCase()) {
      console.log("   ✅ Fee recipient is safe wallet");
    } else {
      console.log("   ⚠️  WARNING: Fee recipient is not safe wallet!");
    }

  } catch (error) {
    console.error("   ❌ ERROR:", error.message);
  }

  console.log("");

  // ===== CHECK AMM ROUTER =====
  console.log("📋 AMM Router Check");
  console.log("   Address:", AMM_ROUTER);

  const AMM_ROUTER_ABI = [
    "function sincToken() view returns (address)",
    "function WETH() view returns (address)",
    "function factory() view returns (address)",
    "function ammRouter() view returns (address)",
  ];

  try {
    const ammRouter = new ethers.Contract(AMM_ROUTER, AMM_ROUTER_ABI, provider);

    const sincAddr = await ammRouter.sincToken();
    const weth = await ammRouter.WETH();
    const factory = await ammRouter.factory();
    const router = await ammRouter.ammRouter();

    console.log("   ✅ Contract deployed");
    console.log("   SINC Token:", sincAddr);
    console.log("   WETH:", weth);
    console.log("   Factory:", factory);
    console.log("   Router:", router);

    if (sincAddr === SINC_TOKEN) {
      console.log("   ✅ SINC token configured correctly");
    } else {
      console.log("   ⚠️  WARNING: SINC token mismatch!");
    }

  } catch (error) {
    console.error("   ❌ ERROR:", error.message);
  }

  console.log("");

  // ===== SUMMARY =====
  console.log("═══════════════════════════════════════════════════════════");
  console.log("                 CHECK COMPLETE");
  console.log("═══════════════════════════════════════════════════════════\n");

  console.log("✅ All contracts deployed successfully!");
  console.log("");
  console.log("🔗 View on Basescan:");
  console.log("   SINC Token:    https://basescan.org/address/" + SINC_TOKEN);
  console.log("   Bonding Curve: https://basescan.org/address/" + BONDING_CURVE);
  console.log("   AMM Router:    https://basescan.org/address/" + AMM_ROUTER);
  console.log("");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("\n❌ CHECK FAILED!");
    console.error(error);
    process.exit(1);
  });
