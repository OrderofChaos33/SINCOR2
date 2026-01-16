#!/usr/bin/env node
/**
 * Derive private key from mnemonic phrase
 */

const { ethers } = require("ethers");
require("dotenv").config();

const mnemonic = process.env.DEPLOYER_PRIVATE_KEY;

// Check if it's a mnemonic (has spaces) or already a private key
if (mnemonic.includes(" ")) {
  console.log("\n🔑 Deriving private key from mnemonic...\n");
  
  try {
    const wallet = ethers.Wallet.fromPhrase(mnemonic);
    
    console.log("═══════════════════════════════════════════════════════════");
    console.log("             WALLET DERIVED FROM MNEMONIC");
    console.log("═══════════════════════════════════════════════════════════\n");
    
    console.log("Address:     ", wallet.address);
    console.log("Private Key: ", wallet.privateKey);
    console.log("");
    console.log("📋 Update your .env DEPLOYER_PRIVATE_KEY to:");
    console.log(wallet.privateKey);
    console.log("");
    console.log("⚠️  KEEP THIS PRIVATE KEY SECRET! Never share it.");
    console.log("");
  } catch (e) {
    console.error("❌ Invalid mnemonic phrase:", e.message);
  }
} else {
  console.log("DEPLOYER_PRIVATE_KEY is already a private key (not a mnemonic)");
}
