#!/usr/bin/env node
/**
 * Create minimal SINC/WETH liquidity pool on Aerodrome
 * Uses all available ETH to create the pool
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Wallets
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  // OLD_PRIVATE_KEY redacted
  // const OLD_PRIVATE_KEY = "<REDACTED>";
  const oldWallet = new ethers.Wallet(OLD_PRIVATE_KEY, provider);
  
  // Contracts
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  const WETH_ADDRESS = "0x4200000000000000000000000000000000000006";
  const AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("     CREATE SINC/WETH LIQUIDITY POOL (CONCENTRATED)");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  // Check balances
  const uniEth = await provider.getBalance(wallet.address);
  const oldEth = await provider.getBalance(oldWallet.address);
  
  console.log("Uniswap Wallet:", wallet.address);
  console.log("  ETH:", ethers.formatEther(uniEth));
  console.log("Old Wallet:", oldWallet.address);
  console.log("  ETH:", ethers.formatEther(oldEth));
  console.log("");
  
  // Consolidate ETH to Uniswap wallet (leave gas for old wallet)
  const gasReserve = ethers.parseEther("0.0005");
  if (oldEth > gasReserve * 2n) {
    const sendAmount = oldEth - gasReserve;
    console.log("📤 Consolidating ETH...");
    const tx = await oldWallet.sendTransaction({
      to: wallet.address,
      value: sendAmount
    });
    await tx.wait();
    console.log("   Sent", ethers.formatEther(sendAmount), "ETH to Uniswap wallet");
  }
  
  // Check new balance
  const totalEth = await provider.getBalance(wallet.address);
  console.log("\nTotal ETH available:", ethers.formatEther(totalEth), "ETH");
  
  // Keep some for gas, use rest for liquidity
  const gasBuffer = ethers.parseEther("0.001");
  const ethForLiquidity = totalEth - gasBuffer;
  
  if (ethForLiquidity <= 0n) {
    console.log("❌ Not enough ETH for liquidity");
    process.exit(1);
  }
  
  console.log("ETH for liquidity:", ethers.formatEther(ethForLiquidity), "ETH");
  
  // Calculate SINC amount for ~$1.05 per SINC
  // If ETH = $3300, and SINC = $1.05, then 1 ETH = ~3142 SINC
  const ethPrice = 3300;
  const sincPrice = 1.05;
  const sincPerEth = ethPrice / sincPrice;
  const sincAmount = ethers.parseEther((parseFloat(ethers.formatEther(ethForLiquidity)) * sincPerEth).toString());
  
  console.log("SINC to pair:", ethers.formatEther(sincAmount), "SINC");
  console.log("Price: ~$" + sincPrice, "per SINC");
  console.log("");
  
  // ABIs
  const wethABI = [
    "function deposit() payable",
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)"
  ];
  
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)"
  ];
  
  const routerABI = [
    "function addLiquidity(address tokenA, address tokenB, bool stable, uint256 amountADesired, uint256 amountBDesired, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB, uint256 liquidity)"
  ];
  
  const weth = new ethers.Contract(WETH_ADDRESS, wethABI, wallet);
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  const router = new ethers.Contract(AERODROME_ROUTER, routerABI, wallet);
  
  // Step 1: Wrap ETH to WETH
  console.log("📤 Step 1: Wrapping ETH to WETH...");
  const wrapTx = await weth.deposit({ value: ethForLiquidity });
  await wrapTx.wait();
  console.log("   ✅ Wrapped", ethers.formatEther(ethForLiquidity), "ETH to WETH");
  
  // Step 2: Approve tokens
  console.log("\n📤 Step 2: Approving tokens...");
  const approveWethTx = await weth.approve(AERODROME_ROUTER, ethers.MaxUint256);
  await approveWethTx.wait();
  console.log("   ✅ WETH approved");
  
  const approveSincTx = await sinc.approve(AERODROME_ROUTER, ethers.MaxUint256);
  await approveSincTx.wait();
  console.log("   ✅ SINC approved");
  
  // Step 3: Add liquidity
  console.log("\n📤 Step 3: Adding liquidity to Aerodrome...");
  const deadline = Math.floor(Date.now() / 1000) + 600; // 10 minutes
  
  // Use 99% min amounts (1% slippage)
  const minSinc = sincAmount * 99n / 100n;
  const minWeth = ethForLiquidity * 99n / 100n;
  
  try {
    const addLiqTx = await router.addLiquidity(
      SINC_ADDRESS,      // tokenA
      WETH_ADDRESS,      // tokenB
      false,             // stable (false = volatile pair)
      sincAmount,        // amountADesired
      ethForLiquidity,   // amountBDesired
      minSinc,           // amountAMin
      minWeth,           // amountBMin
      wallet.address,    // to
      deadline           // deadline
    );
    
    console.log("   TX Hash:", addLiqTx.hash);
    const receipt = await addLiqTx.wait();
    
    console.log("\n✅ LIQUIDITY POOL CREATED!");
    console.log("");
    console.log("Pool Details:");
    console.log("  SINC:", ethers.formatEther(sincAmount));
    console.log("  WETH:", ethers.formatEther(ethForLiquidity));
    console.log("  Price: ~$" + sincPrice + " per SINC");
    console.log("");
    console.log("🔗 View TX: https://basescan.org/tx/" + addLiqTx.hash);
    console.log("");
    console.log("✨ You can now swap SINC ↔ WETH on Aerodrome!");
    console.log("   https://aerodrome.finance/swap");
    console.log("");
    
  } catch (error) {
    console.error("❌ Error:", error.message);
    
    // Check if pool needs to be created first
    if (error.message.includes("INSUFFICIENT_LIQUIDITY") || error.message.includes("PAIR_NOT_EXISTS")) {
      console.log("\n💡 Pool doesn't exist yet. Creating pool...");
    }
  }
}

main().catch(console.error);
