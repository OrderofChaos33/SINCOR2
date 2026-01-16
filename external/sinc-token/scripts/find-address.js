#!/usr/bin/env node
/**
 * Find wallet address from mnemonic using different derivation paths
 */

const { ethers } = require("ethers");

const mnemonic = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
const targetAddress = "0xF915f3F4954c3da6A7D76B424b980A897c3909f1".toLowerCase();

console.log("\n🔍 Searching for address:", targetAddress);
console.log("");

// Try standard Ethereum path
for (let i = 0; i < 10; i++) {
  const path = `m/44'/60'/0'/0/${i}`;
  const wallet = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, path);
  const match = wallet.address.toLowerCase() === targetAddress ? "✅ MATCH!" : "";
  console.log(`${path}: ${wallet.address} ${match}`);
  if (match) {
    console.log("\nPrivate Key:", wallet.privateKey);
  }
}

// Try Ledger Live path
console.log("\n--- Ledger Live Path ---");
for (let i = 0; i < 5; i++) {
  const path = `m/44'/60'/${i}'/0/0`;
  const wallet = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, path);
  const match = wallet.address.toLowerCase() === targetAddress ? "✅ MATCH!" : "";
  console.log(`${path}: ${wallet.address} ${match}`);
  if (match) {
    console.log("\nPrivate Key:", wallet.privateKey);
  }
}
