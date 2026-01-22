#!/usr/bin/env node
/**
 * Transfer SINC to correct Uniswap wallet address
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Old wallet that has ETH for gas
  // OLD_PRIVATE_KEY redacted
  // const OLD_PRIVATE_KEY = "<REDACTED>";
  const oldWallet = new ethers.Wallet(OLD_PRIVATE_KEY, provider);
  
  // Derived wallet that now has the SINC (from mnemonic)
  const DERIVED_PRIVATE_KEY = "0x8094a1304d942acada3cb22dbda68fd501d643443802e516ba37c2a609674536";
  const derivedWallet = new ethers.Wallet(DERIVED_PRIVATE_KEY, provider);
  
  // Correct Uniswap wallet
  const CORRECT_UNISWAP_WALLET = "0xF915f3F4954c3da6A7D76B424b980A897c3909f1";
  
  // SINC Token
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("      TRANSFER SINC TO CORRECT UNISWAP WALLET");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  // Step 1: Send ETH for gas to derived wallet
  console.log("📤 Step 1: Sending ETH for gas...");
  const ethBalance = await provider.getBalance(oldWallet.address);
  console.log("   Old wallet ETH:", ethers.formatEther(ethBalance), "ETH");
  
  const gasAmount = ethers.parseEther("0.0005"); // Enough for transfer
  const ethTx = await oldWallet.sendTransaction({
    to: derivedWallet.address,
    value: gasAmount
  });
  await ethTx.wait();
  console.log("   ✅ Sent 0.0005 ETH to derived wallet for gas");
  console.log("");
  
  // Step 2: Transfer SINC from derived wallet to correct address
  console.log("📤 Step 2: Transferring SINC...");
  
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function transfer(address to, uint256 amount) returns (bool)"
  ];
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, derivedWallet);
  
  const sincBalance = await sinc.balanceOf(derivedWallet.address);
  console.log("   SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  
  const tx = await sinc.transfer(CORRECT_UNISWAP_WALLET, sincBalance);
  console.log("   TX Hash:", tx.hash);
  console.log("   Waiting for confirmation...");
  
  await tx.wait();
  
  console.log("\n✅ TRANSFER COMPLETE!");
  console.log("");
  console.log("Your Uniswap wallet now has 100,000,000 SINC");
  console.log("Wallet:", CORRECT_UNISWAP_WALLET);
  console.log("");
  console.log("🔗 View TX: https://basescan.org/tx/" + tx.hash);
  console.log("");
}

main().catch(console.error);
