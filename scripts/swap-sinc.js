#!/usr/bin/env node
/**
 * Swap SINC for WETH on Aerodrome
 * Swaps as much as possible given pool liquidity
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
  console.log("              SWAP SINC → WETH ON AERODROME");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  const wethABI = [
    "function balanceOf(address) view returns (uint256)",
    "function withdraw(uint256 amount)"
  ];
  
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)"
  ];
  
  const routerABI = [
    "function getAmountsOut(uint256 amountIn, (address from, address to, bool stable, address factory)[] routes) view returns (uint256[] amounts)",
    "function swapExactTokensForTokens(uint256 amountIn, uint256 amountOutMin, (address from, address to, bool stable, address factory)[] routes, address to, uint256 deadline) returns (uint256[] amounts)"
  ];
  
  const weth = new ethers.Contract(WETH_ADDRESS, wethABI, wallet);
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  const router = new ethers.Contract(AERODROME_ROUTER, routerABI, wallet);
  
  // Check balances
  const sincBalance = await sinc.balanceOf(wallet.address);
  const wethBefore = await weth.balanceOf(wallet.address);
  const ethBefore = await provider.getBalance(wallet.address);
  
  console.log("Wallet:", wallet.address);
  console.log("SINC Balance:", ethers.formatEther(sincBalance));
  console.log("WETH Balance:", ethers.formatEther(wethBefore));
  console.log("ETH Balance:", ethers.formatEther(ethBefore));
  console.log("");
  
  // Try swapping 10 SINC first to see what we get
  const testAmount = ethers.parseEther("10");
  const AERODROME_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";
  
  const route = [{
    from: SINC_ADDRESS,
    to: WETH_ADDRESS,
    stable: false,
    factory: AERODROME_FACTORY
  }];
  
  try {
    const amounts = await router.getAmountsOut(testAmount, route);
    console.log("Quote: 10 SINC →", ethers.formatEther(amounts[1]), "WETH");
    console.log("       (~$" + (parseFloat(ethers.formatEther(amounts[1])) * 3300).toFixed(2) + ")");
    console.log("");
    
    // Swap 10 SINC
    const swapAmount = testAmount;
    
    // Approve
    const allowance = await sinc.allowance(wallet.address, AERODROME_ROUTER);
    if (allowance < swapAmount) {
      console.log("📤 Approving SINC...");
      const approveTx = await sinc.approve(AERODROME_ROUTER, ethers.MaxUint256);
      await approveTx.wait();
      console.log("   ✅ Approved");
    }
    
    // Swap
    console.log("📤 Swapping", ethers.formatEther(swapAmount), "SINC for WETH...");
    const deadline = Math.floor(Date.now() / 1000) + 600;
    const minOut = amounts[1] * 95n / 100n;
    
    const swapTx = await router.swapExactTokensForTokens(
      swapAmount,
      minOut,
      route,
      wallet.address,
      deadline
    );
    console.log("   TX Hash:", swapTx.hash);
    await swapTx.wait();
    
    // Check results
    const wethAfter = await weth.balanceOf(wallet.address);
    const sincAfter = await sinc.balanceOf(wallet.address);
    
    console.log("\n✅ SWAP COMPLETE!");
    console.log("");
    console.log("WETH Received:", ethers.formatEther(wethAfter - wethBefore), "WETH");
    console.log("SINC Remaining:", ethers.formatEther(sincAfter), "SINC");
    console.log("");
    console.log("🔗 View TX: https://basescan.org/tx/" + swapTx.hash);
    console.log("");
    
    // Unwrap to ETH
    if (wethAfter > 0n) {
      console.log("📤 Unwrapping WETH to ETH...");
      const unwrapTx = await weth.withdraw(wethAfter);
      await unwrapTx.wait();
      
      const ethAfter = await provider.getBalance(wallet.address);
      console.log("   ✅ ETH Balance:", ethers.formatEther(ethAfter), "ETH");
      console.log("   (~$" + (parseFloat(ethers.formatEther(ethAfter)) * 3300).toFixed(2) + ")");
    }
    
  } catch (error) {
    console.error("❌ Error:", error.message);
  }
}

main().catch(console.error);
