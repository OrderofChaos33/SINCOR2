#!/usr/bin/env node
/**
 * Buy SINC from the Bonding Curve
 * Usage: node scripts/buy-sinc.js <WETH_AMOUNT>
 * Example: node scripts/buy-sinc.js 0.001
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Your wallet
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  // Contracts
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  const BONDING_CURVE = "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa";
  const WETH_ADDRESS = "0x4200000000000000000000000000000000000006";
  
  // Amount to spend (from command line or default 0.0001 WETH)
  const wethAmount = process.argv[2] ? ethers.parseEther(process.argv[2]) : ethers.parseEther("0.0001");
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("              BUY SINC FROM BONDING CURVE");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  console.log("Wallet:", wallet.address);
  console.log("Spending:", ethers.formatEther(wethAmount), "WETH");
  console.log("");
  
  // ABIs
  const wethABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)",
    "function deposit() payable"
  ];
  
  const bondingCurveABI = [
    "function buy(uint256 quoteAmount, uint256 minSincAmount) returns (uint256)",
    "function calculateBuy(uint256 quoteAmount) view returns (uint256 sincAmount, uint256 fee)",
    "function getCurrentPrice(uint256 supply) view returns (uint256)",
    "function circulatingSupply() view returns (uint256)"
  ];
  
  const sincABI = ["function balanceOf(address) view returns (uint256)"];
  
  const weth = new ethers.Contract(WETH_ADDRESS, wethABI, wallet);
  const bondingCurve = new ethers.Contract(BONDING_CURVE, bondingCurveABI, wallet);
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  
  // Check WETH balance
  const wethBalance = await weth.balanceOf(wallet.address);
  console.log("Your WETH Balance:", ethers.formatEther(wethBalance), "WETH");
  
  // Check current price
  const supply = await bondingCurve.circulatingSupply();
  const currentPrice = await bondingCurve.getCurrentPrice(supply);
  console.log("Current SINC Price:", ethers.formatEther(currentPrice), "WETH");
  
  // Calculate how much SINC we'll get
  const [sincAmount, fee] = await bondingCurve.calculateBuy(wethAmount);
  console.log("\nYou will receive:", ethers.formatEther(sincAmount), "SINC");
  console.log("Fee:", ethers.formatEther(fee), "WETH");
  console.log("");
  
  if (wethBalance < wethAmount) {
    console.log("❌ Not enough WETH!");
    console.log("   You need to wrap some ETH first.");
    console.log("");
    
    // Check ETH balance
    const ethBalance = await provider.getBalance(wallet.address);
    console.log("Your ETH Balance:", ethers.formatEther(ethBalance), "ETH");
    
    if (ethBalance >= wethAmount) {
      console.log("\n📤 Wrapping ETH to WETH...");
      const wrapTx = await weth.deposit({ value: wethAmount });
      await wrapTx.wait();
      console.log("   ✅ Wrapped", ethers.formatEther(wethAmount), "ETH to WETH");
    } else {
      console.log("\n❌ Not enough ETH either. Send ETH to:", wallet.address);
      process.exit(1);
    }
  }
  
  // Approve WETH spending
  const allowance = await weth.allowance(wallet.address, BONDING_CURVE);
  if (allowance < wethAmount) {
    console.log("📤 Approving WETH spending...");
    const approveTx = await weth.approve(BONDING_CURVE, ethers.MaxUint256);
    await approveTx.wait();
    console.log("   ✅ Approved");
  }
  
  // Buy SINC
  console.log("\n📤 Buying SINC...");
  const minSinc = sincAmount * 95n / 100n; // 5% slippage tolerance
  const buyTx = await bondingCurve.buy(wethAmount, minSinc);
  console.log("   TX Hash:", buyTx.hash);
  await buyTx.wait();
  
  // Check new balance
  const newSincBalance = await sinc.balanceOf(wallet.address);
  
  console.log("\n✅ PURCHASE COMPLETE!");
  console.log("");
  console.log("Your SINC Balance:", ethers.formatEther(newSincBalance), "SINC");
  console.log("");
  console.log("🔗 View TX: https://basescan.org/tx/" + buyTx.hash);
  console.log("");
}

main().catch(console.error);
