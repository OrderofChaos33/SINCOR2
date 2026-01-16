#!/usr/bin/env node
/**
 * Add liquidity using existing WETH balance
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  const WETH_ADDRESS = "0x4200000000000000000000000000000000000006";
  const AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("         ADD LIQUIDITY TO AERODROME");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  const wethABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)"
  ];
  
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)"
  ];
  
  const routerABI = [
    "function addLiquidity(address tokenA, address tokenB, bool stable, uint256 amountADesired, uint256 amountBDesired, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB, uint256 liquidity)"
  ];
  
  const weth = new ethers.Contract(WETH_ADDRESS, wethABI, wallet);
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  const router = new ethers.Contract(AERODROME_ROUTER, routerABI, wallet);
  
  // Check balances
  const wethBalance = await weth.balanceOf(wallet.address);
  const sincBalance = await sinc.balanceOf(wallet.address);
  const ethBalance = await provider.getBalance(wallet.address);
  
  console.log("Wallet:", wallet.address);
  console.log("ETH:", ethers.formatEther(ethBalance));
  console.log("WETH:", ethers.formatEther(wethBalance));
  console.log("SINC:", ethers.formatEther(sincBalance));
  console.log("");
  
  // Use all WETH, calculate matching SINC at $1.05
  const ethPrice = 3300;
  const sincPrice = 1.05;
  const sincPerEth = ethPrice / sincPrice;
  const sincAmount = ethers.parseEther((parseFloat(ethers.formatEther(wethBalance)) * sincPerEth).toString());
  
  console.log("Adding Liquidity:");
  console.log("  WETH:", ethers.formatEther(wethBalance));
  console.log("  SINC:", ethers.formatEther(sincAmount));
  console.log("  Price: $" + sincPrice + " per SINC");
  console.log("");
  
  // Check and approve WETH
  const wethAllowance = await weth.allowance(wallet.address, AERODROME_ROUTER);
  if (wethAllowance < wethBalance) {
    console.log("📤 Approving WETH...");
    const tx = await weth.approve(AERODROME_ROUTER, ethers.MaxUint256);
    await tx.wait();
    console.log("   ✅ WETH approved");
  } else {
    console.log("✅ WETH already approved");
  }
  
  // Check and approve SINC
  const sincAllowance = await sinc.allowance(wallet.address, AERODROME_ROUTER);
  if (sincAllowance < sincAmount) {
    console.log("📤 Approving SINC...");
    const tx = await sinc.approve(AERODROME_ROUTER, ethers.MaxUint256);
    await tx.wait();
    console.log("   ✅ SINC approved");
  } else {
    console.log("✅ SINC already approved");
  }
  
  // Add liquidity
  console.log("\n📤 Adding liquidity...");
  const deadline = Math.floor(Date.now() / 1000) + 600;
  
  const minSinc = sincAmount * 90n / 100n;
  const minWeth = wethBalance * 90n / 100n;
  
  const addLiqTx = await router.addLiquidity(
    SINC_ADDRESS,
    WETH_ADDRESS,
    false,  // volatile pair
    sincAmount,
    wethBalance,
    minSinc,
    minWeth,
    wallet.address,
    deadline
  );
  
  console.log("   TX Hash:", addLiqTx.hash);
  await addLiqTx.wait();
  
  console.log("\n✅ LIQUIDITY POOL CREATED!");
  console.log("");
  console.log("🔗 View TX: https://basescan.org/tx/" + addLiqTx.hash);
  console.log("");
  console.log("✨ You can now swap SINC ↔ WETH on Aerodrome!");
  console.log("   https://aerodrome.finance/swap");
  console.log("");
}

main().catch(console.error);
