#!/usr/bin/env node
/**
 * Quick script to check if deployer wallet has testnet ETH
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function checkBalance() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  if (!process.env.DEPLOYER_PRIVATE_KEY || process.env.DEPLOYER_PRIVATE_KEY === "YOUR_NEW_SECURE_PRIVATE_KEY_HERE") {
    console.error("❌ ERROR: DEPLOYER_PRIVATE_KEY not set in .env");
    process.exit(1);
  }
  
  const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
  const balance = await provider.getBalance(wallet.address);
  
  console.log("\n🔍 DEPLOYER WALLET STATUS");
  console.log("=========================");
  console.log("Address:", wallet.address);
  console.log("Balance:", ethers.formatEther(balance), "ETH");
  console.log("Network: Base Mainnet");
  console.log("");
  
  if (balance < ethers.parseEther("0.00001")) {
    console.log("⚠️  LOW BALANCE - Need at least 0.00001 ETH for deployment");
    console.log("");
    console.log("📌 Get testnet ETH from:");
    console.log("   https://www.coinbase.com/faucets/base-ethereum-sepolia-faucet");
    console.log("");
    console.log("Instructions:");
    console.log("1. Visit the faucet URL above");
    console.log("2. Connect your wallet OR paste address:", wallet.address);
    console.log("3. Request testnet ETH");
    console.log("4. Wait 1-2 minutes");
    console.log("5. Run this script again: node scripts/check-balance.js");
    console.log("");
    process.exit(1);
  } else {
    console.log("✅ Sufficient balance for deployment!");
    console.log("   Ready to deploy with: npm run deploy:base");
    console.log("");
  }
}

checkBalance().catch(console.error);
