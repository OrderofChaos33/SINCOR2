#!/usr/bin/env node
/**
 * Create pools on BaseSwap and SwapBased DEXs
 */

const { ethers } = require("ethers");
require("dotenv").config();

async function main() {
  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  const SINC_ADDRESS = "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
  const WETH_ADDRESS = "0x4200000000000000000000000000000000000006";
  
  const DEXES = [
    { name: "BaseSwap", router: "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86" },
    { name: "SwapBased", router: "0xaaa3b1F1bd7BCc97fD1917c18ADE665C5D31F066" }
  ];
  
  console.log("\n═══════════════════════════════════════════════════════════════════════════════");
  console.log("              CREATE POOLS ON ADDITIONAL DEXs");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  // ABIs
  const erc20ABI = [
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)",
    "function allowance(address owner, address spender) view returns (uint256)"
  ];
  
  const routerABI = [
    "function addLiquidity(address tokenA, address tokenB, uint amountADesired, uint amountBDesired, uint amountAMin, uint amountBMin, address to, uint deadline) returns (uint amountA, uint amountB, uint liquidity)"
  ];
  
  const sinc = new ethers.Contract(SINC_ADDRESS, erc20ABI, wallet);
  const weth = new ethers.Contract(WETH_ADDRESS, erc20ABI, wallet);
  
  // Check balances
  const wethBalance = await weth.balanceOf(wallet.address);
  const sincBalance = await sinc.balanceOf(wallet.address);
  const ethBalance = await provider.getBalance(wallet.address);
  
  console.log("Wallet:", wallet.address);
  console.log("ETH:", ethers.formatEther(ethBalance));
  console.log("WETH:", ethers.formatEther(wethBalance));
  console.log("SINC:", ethers.formatEther(sincBalance));
  console.log("");
  
  if (wethBalance < ethers.parseEther("0.0001")) {
    console.log("⚠️  Not enough WETH. Skipping pool creation.");
    return;
  }
  
  // Split WETH between DEXs
  const wethPerDex = wethBalance / 2n;
  const sincPerDex = wethPerDex * 3142n; // ~$1.05 per SINC at $3300 ETH
  
  console.log(`Per DEX: ${ethers.formatEther(wethPerDex)} WETH + ${ethers.formatEther(sincPerDex)} SINC\n`);
  
  for (const dex of DEXES) {
    console.log(`📤 Creating pool on ${dex.name}...`);
    
    try {
      const router = new ethers.Contract(dex.router, routerABI, wallet);
      
      // Approve WETH
      const wethAllowance = await weth.allowance(wallet.address, dex.router);
      if (wethAllowance < wethPerDex) {
        console.log("   Approving WETH...");
        const tx = await weth.approve(dex.router, ethers.MaxUint256);
        await tx.wait();
      }
      
      // Approve SINC
      const sincAllowance = await sinc.allowance(wallet.address, dex.router);
      if (sincAllowance < sincPerDex) {
        console.log("   Approving SINC...");
        const tx = await sinc.approve(dex.router, ethers.MaxUint256);
        await tx.wait();
      }
      
      // Add liquidity
      const deadline = Math.floor(Date.now() / 1000) + 600;
      const minWeth = wethPerDex * 90n / 100n;
      const minSinc = sincPerDex * 90n / 100n;
      
      const tx = await router.addLiquidity(
        SINC_ADDRESS,
        WETH_ADDRESS,
        sincPerDex,
        wethPerDex,
        minSinc,
        minWeth,
        wallet.address,
        deadline
      );
      
      console.log("   TX:", tx.hash);
      await tx.wait();
      console.log(`   ✅ ${dex.name} pool created!\n`);
      
    } catch (e) {
      console.log(`   ❌ Failed: ${e.message.slice(0, 80)}\n`);
    }
  }
  
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("DONE! SINC is now listed on:");
  console.log("  ✅ Aerodrome");
  console.log("  ✅ BaseSwap (if successful)");
  console.log("  ✅ SwapBased (if successful)");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
}

main().catch(console.error);
