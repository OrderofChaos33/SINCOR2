#!/usr/bin/env node
/**
 * Transfer SINC tokens to Uniswap wallet
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Old wallet that has the SINC tokens
  // OLD_PRIVATE_KEY redacted
  // const OLD_PRIVATE_KEY = "<REDACTED>";
  const oldWallet = new ethers.Wallet(OLD_PRIVATE_KEY, provider);
  
  // New Uniswap wallet
  const UNISWAP_WALLET = "0x37E20A67C120d4729a479f558BCBe7197aeCc714";
  
  // SINC Token
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("         TRANSFER SINC TO UNISWAP WALLET");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  console.log("From:", oldWallet.address);
  console.log("To:  ", UNISWAP_WALLET);
  console.log("");
  
  // Check ETH balance for gas
  const ethBalance = await provider.getBalance(oldWallet.address);
  console.log("ETH Balance:", ethers.formatEther(ethBalance), "ETH");
  
  if (ethBalance < ethers.parseEther("0.00001")) {
    console.error("\n❌ Need ETH for gas! Send some ETH to:", oldWallet.address);
    process.exit(1);
  }
  
  // SINC contract
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function transfer(address to, uint256 amount) returns (bool)",
    "function symbol() view returns (string)"
  ];
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, oldWallet);
  
  // Check SINC balance
  const sincBalance = await sinc.balanceOf(oldWallet.address);
  console.log("SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  console.log("");
  
  if (sincBalance === 0n) {
    console.log("❌ No SINC tokens to transfer!");
    process.exit(1);
  }
  
  // Transfer all SINC
  console.log("📤 Transferring", ethers.formatEther(sincBalance), "SINC...");
  const tx = await sinc.transfer(UNISWAP_WALLET, sincBalance);
  console.log("   TX Hash:", tx.hash);
  console.log("   Waiting for confirmation...");
  
  await tx.wait();
  
  console.log("\n✅ TRANSFER COMPLETE!");
  console.log("");
  console.log("Your Uniswap wallet now has 100,000,000 SINC");
  console.log("Wallet:", UNISWAP_WALLET);
  console.log("");
  console.log("🔗 View TX: https://basescan.org/tx/" + tx.hash);
  console.log("");
}

main().catch(console.error);
