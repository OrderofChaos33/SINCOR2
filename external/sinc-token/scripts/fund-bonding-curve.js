#!/usr/bin/env node
/**
 * Fund the Bonding Curve with SINC tokens
 * Transfers 50M SINC to the bonding curve contract
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Uniswap wallet (has the SINC tokens) - derivation path index 1
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  // Old wallet with ETH for gas
  // OLD_PRIVATE_KEY redacted
  // const OLD_PRIVATE_KEY = "<REDACTED>";
  const oldWallet = new ethers.Wallet(OLD_PRIVATE_KEY, provider);
  
  // Contract addresses
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  const BONDING_CURVE_ADDRESS = "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa";
  
  // Amount to transfer to bonding curve (50M SINC)
  const BONDING_CURVE_AMOUNT = ethers.parseEther("50000000");
  
  console.log("\n═══════════════════════════════════════════════════════════");
  console.log("         FUND BONDING CURVE WITH SINC");
  console.log("═══════════════════════════════════════════════════════════\n");
  
  console.log("Wallet:", wallet.address);
  console.log("Bonding Curve:", BONDING_CURVE_ADDRESS);
  console.log("Amount: 50,000,000 SINC");
  console.log("");
  
  // Check ETH for gas
  const ethBalance = await provider.getBalance(wallet.address);
  const oldWalletEth = await provider.getBalance(oldWallet.address);
  console.log("ETH Balance:", ethers.formatEther(ethBalance), "ETH");
  
  // Send ETH for gas if needed
  if (ethBalance < ethers.parseEther("0.0003")) {
    console.log("\n📤 Sending ETH for gas from old wallet...");
    const ethTx = await oldWallet.sendTransaction({
      to: wallet.address,
      value: ethers.parseEther("0.0005")
    });
    await ethTx.wait();
    console.log("   ✅ Sent 0.0005 ETH for gas");
  }
  
  // SINC contract
  const sincABI = [
    "function balanceOf(address) view returns (uint256)",
    "function transfer(address to, uint256 amount) returns (bool)",
    "function approve(address spender, uint256 amount) returns (bool)"
  ];
  const sinc = new ethers.Contract(SINC_ADDRESS, sincABI, wallet);
  
  // Check SINC balance
  const sincBalance = await sinc.balanceOf(wallet.address);
  console.log("SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  console.log("");
  
  if (sincBalance < BONDING_CURVE_AMOUNT) {
    console.error("❌ Not enough SINC! Need 50M, have", ethers.formatEther(sincBalance));
    process.exit(1);
  }
  
  // Transfer SINC to bonding curve
  console.log("📤 Transferring 50,000,000 SINC to Bonding Curve...");
  const tx = await sinc.transfer(BONDING_CURVE_ADDRESS, BONDING_CURVE_AMOUNT);
  console.log("   TX Hash:", tx.hash);
  console.log("   Waiting for confirmation...");
  
  await tx.wait();
  
  // Check bonding curve balance
  const curveBalance = await sinc.balanceOf(BONDING_CURVE_ADDRESS);
  
  console.log("\n✅ BONDING CURVE FUNDED!");
  console.log("");
  console.log("Bonding Curve SINC Balance:", ethers.formatEther(curveBalance), "SINC");
  console.log("Your Remaining SINC:", ethers.formatEther(sincBalance - BONDING_CURVE_AMOUNT), "SINC");
  console.log("");
  console.log("🔗 View TX: https://basescan.org/tx/" + tx.hash);
  console.log("");
  console.log("The bonding curve is now active! Users can buy SINC with WETH.");
  console.log("");
}

main().catch(console.error);
