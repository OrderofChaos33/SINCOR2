const { ethers } = require("ethers");
require("dotenv").config();

/**
 * Sets up initial liquidity for SINC/WETH on Aerodrome
 * This creates the pool and adds initial liquidity
 */

async function main() {
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("           SINC LIQUIDITY SETUP");
  console.log("═══════════════════════════════════════════════════════════\n");

  // Setup provider and wallet
  const provider = new ethers.JsonRpcProvider(process.env.BASE_RPC_URL || "https://mainnet.base.org");
  
  if (!process.env.DEPLOYER_PRIVATE_KEY) {
    console.error("❌ ERROR: DEPLOYER_PRIVATE_KEY not found in .env");
    process.exit(1);
  }

  const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
  console.log("Wallet:", wallet.address);

  const balance = await provider.getBalance(wallet.address);
  console.log("ETH Balance:", ethers.formatEther(balance), "ETH\n");

  // Get addresses
  const SINC_TOKEN = process.env.SINC_TOKEN_ADDRESS;
  const AERODROME_ROUTER = process.env.AERODROME_ROUTER || "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
  const WETH = process.env.QUOTE_TOKEN_ADDRESS || "0x4200000000000000000000000000000000000006";

  if (!SINC_TOKEN) {
    console.error("❌ ERROR: SINC_TOKEN_ADDRESS not found in .env");
    console.error("   Please deploy contracts first");
    process.exit(1);
  }

  console.log("Configuration:");
  console.log("   SINC Token:", SINC_TOKEN);
  console.log("   WETH:", WETH);
  console.log("   Aerodrome Router:", AERODROME_ROUTER);
  console.log("");

  // Liquidity amounts from .env
  const SINC_AMOUNT = ethers.parseEther(process.env.INITIAL_SINC_LIQUIDITY || "5000000"); // 5M SINC
  const WETH_AMOUNT = ethers.parseEther(process.env.INITIAL_WETH_LIQUIDITY || "1"); // 1 WETH

  console.log("Liquidity Amounts:");
  console.log("   SINC:", ethers.formatEther(SINC_AMOUNT), "SINC");
  console.log("   WETH:", ethers.formatEther(WETH_AMOUNT), "WETH");
  console.log("   Initial Price:", Number(ethers.formatEther(WETH_AMOUNT)) / Number(ethers.formatEther(SINC_AMOUNT)), "WETH per SINC");
  console.log("   Initial Price USD:", (Number(ethers.formatEther(WETH_AMOUNT)) / Number(ethers.formatEther(SINC_AMOUNT))) * 3000, "USD per SINC (assuming ETH = $3000)");
  console.log("");

  // ERC20 ABI
  const ERC20_ABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)",
  ];

  // Aerodrome Router ABI (simplified)
  const ROUTER_ABI = [
    "function addLiquidity(address tokenA, address tokenB, bool stable, uint256 amountADesired, uint256 amountBDesired, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB, uint256 liquidity)",
  ];

  const sincContract = new ethers.Contract(SINC_TOKEN, ERC20_ABI, wallet);
  const wethContract = new ethers.Contract(WETH, ERC20_ABI, wallet);
  const routerContract = new ethers.Contract(AERODROME_ROUTER, ROUTER_ABI, wallet);

  // ===== STEP 1: Check Balances =====
  console.log("📊 Step 1: Checking Balances...");
  const sincBalance = await sincContract.balanceOf(wallet.address);
  const wethBalance = await wethContract.balanceOf(wallet.address);

  console.log("   SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  console.log("   WETH Balance:", ethers.formatEther(wethBalance), "WETH");

  if (sincBalance < SINC_AMOUNT) {
    console.error("\n❌ ERROR: Insufficient SINC balance");
    console.error("   Required:", ethers.formatEther(SINC_AMOUNT), "SINC");
    console.error("   Available:", ethers.formatEther(sincBalance), "SINC");
    process.exit(1);
  }

  if (wethBalance < WETH_AMOUNT) {
    console.error("\n❌ ERROR: Insufficient WETH balance");
    console.error("   Required:", ethers.formatEther(WETH_AMOUNT), "WETH");
    console.error("   Available:", ethers.formatEther(wethBalance), "WETH");
    console.error("\n💡 Wrap ETH to WETH first using WETH contract");
    process.exit(1);
  }

  console.log("   ✅ Sufficient balances\n");

  // ===== STEP 2: Approve Tokens =====
  console.log("📝 Step 2: Approving Tokens...");

  const sincAllowance = await sincContract.allowance(wallet.address, AERODROME_ROUTER);
  if (sincAllowance < SINC_AMOUNT) {
    console.log("   Approving SINC...");
    const approveSincTx = await sincContract.approve(AERODROME_ROUTER, SINC_AMOUNT);
    await approveSincTx.wait();
    console.log("   ✅ SINC approved");
  } else {
    console.log("   ✅ SINC already approved");
  }

  const wethAllowance = await wethContract.allowance(wallet.address, AERODROME_ROUTER);
  if (wethAllowance < WETH_AMOUNT) {
    console.log("   Approving WETH...");
    const approveWethTx = await wethContract.approve(AERODROME_ROUTER, WETH_AMOUNT);
    await approveWethTx.wait();
    console.log("   ✅ WETH approved");
  } else {
    console.log("   ✅ WETH already approved");
  }

  console.log("");

  // ===== STEP 3: Add Liquidity =====
  console.log("💧 Step 3: Adding Liquidity to Aerodrome...");
  console.log("   This will create the SINC/WETH pool if it doesn't exist");
  console.log("   Pool Type: Volatile (not stable)");
  console.log("");

  const deadline = Math.floor(Date.now() / 1000) + 3600; // 1 hour from now
  const minSinc = (SINC_AMOUNT * 95n) / 100n; // 5% slippage
  const minWeth = (WETH_AMOUNT * 95n) / 100n; // 5% slippage

  try {
    console.log("   Sending transaction...");
    const addLiquidityTx = await routerContract.addLiquidity(
      SINC_TOKEN,
      WETH,
      false, // stable = false (volatile pool)
      SINC_AMOUNT,
      WETH_AMOUNT,
      minSinc,
      minWeth,
      wallet.address,
      deadline,
      {
        gasLimit: 500000, // Set reasonable gas limit
      }
    );

    console.log("   Transaction hash:", addLiquidityTx.hash);
    console.log("   Waiting for confirmation...");

    const receipt = await addLiquidityTx.wait();

    console.log("   ✅ Liquidity added successfully!");
    console.log("   Block:", receipt.blockNumber);
    console.log("   Gas used:", receipt.gasUsed.toString());
    console.log("");

    // Get final balances
    const finalSincBalance = await sincContract.balanceOf(wallet.address);
    const finalWethBalance = await wethContract.balanceOf(wallet.address);

    console.log("📊 Final Balances:");
    console.log("   SINC:", ethers.formatEther(finalSincBalance), "SINC");
    console.log("   WETH:", ethers.formatEther(finalWethBalance), "WETH");
    console.log("");

  } catch (error) {
    console.error("\n❌ ERROR: Failed to add liquidity");
    console.error("   Message:", error.message);
    
    if (error.message.includes("insufficient")) {
      console.error("\n💡 Possible solutions:");
      console.error("   - Ensure you have enough SINC and WETH");
      console.error("   - Check token approvals");
      console.error("   - Verify WETH is wrapped (not ETH)");
    }
    
    process.exit(1);
  }

  // ===== SUMMARY =====
  console.log("═══════════════════════════════════════════════════════════");
  console.log("           LIQUIDITY SETUP COMPLETE");
  console.log("═══════════════════════════════════════════════════════════\n");

  console.log("✅ SINC/WETH pool created and liquidity added!");
  console.log("");
  console.log("🔗 View on Aerodrome:");
  console.log("   https://aerodrome.finance/liquidity");
  console.log("");
  console.log("🔗 View on Basescan:");
  console.log("   https://basescan.org/address/" + SINC_TOKEN);
  console.log("");
  console.log("🎉 SINC is now tradeable on Aerodrome!");
  console.log("");
}

main()
  .then(() => process.exit(0))
  .catch((error) => {
    console.error("\n❌ LIQUIDITY SETUP FAILED!");
    console.error(error);
    process.exit(1);
  });
