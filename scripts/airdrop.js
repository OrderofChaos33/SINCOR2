#!/usr/bin/env node
/**
 * SINC Airdrop Distribution Script
 * Distributes SINC tokens to a list of wallet addresses
 * 
 * Usage:
 *   1. Add addresses to the AIRDROP_LIST below
 *   2. Run: node scripts/airdrop.js
 */

const { ethers } = require("ethers");
require("dotenv").config();

// ═══════════════════════════════════════════════════════════════════════════════
// CONFIGURATION - Edit this section
// ═══════════════════════════════════════════════════════════════════════════════

// Amount of SINC per airdrop recipient
const SINC_PER_ADDRESS = "100"; // 100 SINC each

// List of addresses to receive airdrop
// Add wallet addresses here (one per line)
const AIRDROP_LIST = [
  // Example addresses - replace with real ones:
  // "0x1234567890123456789012345678901234567890",
  // "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",
];

// ═══════════════════════════════════════════════════════════════════════════════
// SCRIPT - Don't edit below unless you know what you're doing
// ═══════════════════════════════════════════════════════════════════════════════

const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";

async function main() {
  console.log(`
═══════════════════════════════════════════════════════════════════════════════
                        SINC AIRDROP DISTRIBUTION
═══════════════════════════════════════════════════════════════════════════════
`);

  if (AIRDROP_LIST.length === 0) {
    console.log("⚠️  No addresses in airdrop list!");
    console.log("");
    console.log("To use this script:");
    console.log("1. Edit scripts/airdrop.js");
    console.log("2. Add wallet addresses to AIRDROP_LIST array");
    console.log("3. Set SINC_PER_ADDRESS amount");
    console.log("4. Run: node scripts/airdrop.js");
    console.log("");
    return;
  }

  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function transfer(address to, uint256 amount) returns (bool)"
  ];
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  
  // Check balances
  const sincBalance = await sinc.balanceOf(wallet.address);
  const ethBalance = await provider.getBalance(wallet.address);
  const amountPerAddress = ethers.parseEther(SINC_PER_ADDRESS);
  const totalNeeded = amountPerAddress * BigInt(AIRDROP_LIST.length);
  
  console.log("Sender Wallet:", wallet.address);
  console.log("ETH Balance:", ethers.formatEther(ethBalance), "ETH");
  console.log("SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  console.log("");
  console.log("Airdrop Config:");
  console.log("  Recipients:", AIRDROP_LIST.length);
  console.log("  Per Address:", SINC_PER_ADDRESS, "SINC");
  console.log("  Total:", ethers.formatEther(totalNeeded), "SINC");
  console.log("");
  
  if (sincBalance < totalNeeded) {
    console.log("❌ Not enough SINC for airdrop!");
    console.log("   Need:", ethers.formatEther(totalNeeded), "SINC");
    console.log("   Have:", ethers.formatEther(sincBalance), "SINC");
    return;
  }
  
  // Estimate gas needed
  const estimatedGas = BigInt(AIRDROP_LIST.length) * 50000n; // ~50k gas per transfer
  const gasPrice = await provider.getFeeData();
  const estimatedCost = estimatedGas * gasPrice.gasPrice;
  
  console.log("Estimated gas cost:", ethers.formatEther(estimatedCost), "ETH");
  
  if (ethBalance < estimatedCost) {
    console.log("⚠️  Warning: May not have enough ETH for all transfers");
  }
  
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("STARTING AIRDROP...");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  let successCount = 0;
  let failCount = 0;
  
  for (let i = 0; i < AIRDROP_LIST.length; i++) {
    const address = AIRDROP_LIST[i];
    
    // Validate address
    if (!ethers.isAddress(address)) {
      console.log(`[${i + 1}/${AIRDROP_LIST.length}] ❌ Invalid address: ${address}`);
      failCount++;
      continue;
    }
    
    try {
      console.log(`[${i + 1}/${AIRDROP_LIST.length}] Sending ${SINC_PER_ADDRESS} SINC to ${address.slice(0, 10)}...`);
      const tx = await sinc.transfer(address, amountPerAddress);
      await tx.wait();
      console.log(`   ✅ TX: ${tx.hash.slice(0, 20)}...`);
      successCount++;
      
      // Small delay to avoid nonce issues
      await new Promise(r => setTimeout(r, 500));
      
    } catch (e) {
      console.log(`   ❌ Failed: ${e.message.slice(0, 50)}`);
      failCount++;
    }
  }
  
  console.log("");
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("AIRDROP COMPLETE!");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  console.log(`Success: ${successCount}/${AIRDROP_LIST.length}`);
  console.log(`Failed: ${failCount}/${AIRDROP_LIST.length}`);
  console.log(`Total Distributed: ${successCount * parseFloat(SINC_PER_ADDRESS)} SINC`);
  console.log("");
}

main().catch(console.error);
