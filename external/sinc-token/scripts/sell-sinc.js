#!/usr/bin/env node
/**
 * Sell SINC for WETH via Bonding Curve
 * Sells approximately $20,000 worth of SINC
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Your wallet (derivation path index 1)
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  // Contracts
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  const BONDING_CURVE = "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa";
  const WETH_ADDRESS = "0x4200000000000000000000000000000000000006";
  
  // ETH price assumption ~$3,300, so $20k = ~6 ETH worth
  // At SINC price ~$1.05, $20k = ~19,047 SINC
  // Let's sell 20,000 SINC to get approximately $20k worth of WETH
  const SINC_TO_SELL = ethers.parseEther("20000");
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("         SELL SINC FOR WETH VIA BONDING CURVE");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  console.log("Wallet:", wallet.address);
  console.log("Selling:", ethers.formatEther(SINC_TO_SELL), "SINC");
  console.log("");
  
  // ABIs
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)"
  ];
  
  const bondingCurveABI = [
    "function sell(uint256 sincAmount, uint256 minQuoteAmount) returns (uint256)",
    "function calculateSell(uint256 sincAmount) view returns (uint256 quoteAmount, uint256 fee)",
    "function getCurrentPrice(uint256 supply) view returns (uint256)",
    "function circulatingSupply() view returns (uint256)"
  ];
  
  const wethABI = [
    "function balanceOf(address) view returns (uint256)",
    "function withdraw(uint256 amount)"
  ];
  
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  const bondingCurve = new ethers.Contract(BONDING_CURVE, bondingCurveABI, wallet);
  const weth = new ethers.Contract(WETH_ADDRESS, wethABI, wallet);
  
  // Check balances
  const sincBalance = await sinc.balanceOf(wallet.address);
  const wethBalanceBefore = await weth.balanceOf(wallet.address);
  const ethBalanceBefore = await provider.getBalance(wallet.address);
  
  console.log("Your SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  console.log("Your WETH Balance:", ethers.formatEther(wethBalanceBefore), "WETH");
  console.log("Your ETH Balance:", ethers.formatEther(ethBalanceBefore), "ETH");
  
  // Check current price and calculate sale
  const supply = await bondingCurve.circulatingSupply();
  const currentPrice = await bondingCurve.getCurrentPrice(supply);
  console.log("\nCurrent SINC Price:", ethers.formatEther(currentPrice), "WETH per SINC");
  
  // Calculate how much WETH we'll get
  try {
    const [wethAmount, fee] = await bondingCurve.calculateSell(SINC_TO_SELL);
    console.log("\nYou will receive:", ethers.formatEther(wethAmount), "WETH");
    console.log("Fee:", ethers.formatEther(fee), "WETH");
    console.log("Approximate USD value: $" + (parseFloat(ethers.formatEther(wethAmount)) * 3300).toFixed(2));
    console.log("");
    
    // Approve SINC spending
    const allowance = await sinc.allowance(wallet.address, BONDING_CURVE);
    if (allowance < SINC_TO_SELL) {
      console.log("📤 Approving SINC spending...");
      const approveTx = await sinc.approve(BONDING_CURVE, ethers.MaxUint256);
      await approveTx.wait();
      console.log("   ✅ Approved");
    }
    
    // Sell SINC
    console.log("\n📤 Selling SINC...");
    const minWeth = wethAmount * 95n / 100n; // 5% slippage
    const sellTx = await bondingCurve.sell(SINC_TO_SELL, minWeth);
    console.log("   TX Hash:", sellTx.hash);
    await sellTx.wait();
    
    // Check new balances
    const wethBalanceAfter = await weth.balanceOf(wallet.address);
    const sincBalanceAfter = await sinc.balanceOf(wallet.address);
    
    console.log("\n✅ SALE COMPLETE!");
    console.log("");
    console.log("WETH Received:", ethers.formatEther(wethBalanceAfter - wethBalanceBefore), "WETH");
    console.log("Remaining SINC:", ethers.formatEther(sincBalanceAfter), "SINC");
    console.log("");
    console.log("🔗 View TX: https://basescan.org/tx/" + sellTx.hash);
    
    // Ask if user wants to unwrap to ETH
    console.log("\n💡 To convert WETH to ETH, run: node scripts/unwrap-weth.js");
    console.log("");
    
  } catch (error) {
    console.error("\n❌ Error:", error.message);
    if (error.message.includes("Exceeds circulating supply")) {
      console.log("\nThe bonding curve has no circulating supply yet.");
      console.log("You can only sell SINC that was bought through the bonding curve.");
      console.log("Your SINC was minted directly, not purchased through the curve.");
      console.log("\n💡 To swap SINC for WETH, you need to set up a liquidity pool on Aerodrome/Uniswap.");
    }
  }
}

main().catch(console.error);
