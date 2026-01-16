#!/usr/bin/env node
/**
 * SINC Token Multi-DEX Listing & CEX Application Script
 * Lists SINC on all major Base DEXs and prepares CEX applications
 */

const { ethers } = require("ethers");
const https = require("https");
const http = require("http");
require("dotenv").config();

// Token Info
const TOKEN = {
  name: "SINCOR",
  symbol: "SINC",
  address: "0xd10D86D09ee4316CdD3585fd6486537b7119A073",
  decimals: 18,
  totalSupply: "100000000",
  chain: "Base",
  chainId: 8453,
  bondingCurve: "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa",
  ammRouter: "0x7949576312a33Adc76CAC2103506a6D54fADBaB7"
};

const WETH = "0x4200000000000000000000000000000000000006";

// DEX Routers on Base
const DEXES = {
  aerodrome: {
    name: "Aerodrome",
    router: "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43",
    factory: "0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
    type: "solidly"
  },
  uniswapV3: {
    name: "Uniswap V3",
    router: "0x2626664c2603336E57B271c5C0b26F421741e481",
    factory: "0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
    positionManager: "0x03a520b32C04BF3bEEf7BEb72E919cf822Ed34f1",
    type: "uniswapV3"
  },
  baseswap: {
    name: "BaseSwap",
    router: "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86",
    factory: "0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB",
    type: "uniswapV2"
  },
  swapBased: {
    name: "SwapBased",
    router: "0xaaa3b1F1bd7BCc97fD1917c18ADE665C5D31F066",
    factory: "0x04C9f118d21e8B767D2e50C946f0cC9F6C367300",
    type: "uniswapV2"
  }
};

// ABIs
const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
  "function allowance(address owner, address spender) view returns (uint256)",
  "function symbol() view returns (string)",
  "function name() view returns (string)",
  "function decimals() view returns (uint8)",
  "function totalSupply() view returns (uint256)"
];

const UNISWAP_V2_ROUTER_ABI = [
  "function addLiquidity(address tokenA, address tokenB, uint amountADesired, uint amountBDesired, uint amountAMin, uint amountBMin, address to, uint deadline) returns (uint amountA, uint amountB, uint liquidity)",
  "function factory() view returns (address)"
];

const UNISWAP_V2_FACTORY_ABI = [
  "function getPair(address tokenA, address tokenB) view returns (address)",
  "function createPair(address tokenA, address tokenB) returns (address)"
];

const AERODROME_ROUTER_ABI = [
  "function addLiquidity(address tokenA, address tokenB, bool stable, uint256 amountADesired, uint256 amountBDesired, uint256 amountAMin, uint256 amountBMin, address to, uint256 deadline) returns (uint256 amountA, uint256 amountB, uint256 liquidity)"
];

const WETH_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function approve(address spender, uint256 amount) returns (bool)",
  "function deposit() payable",
  "function withdraw(uint256 amount)"
];

async function main() {
  console.log(`
═══════════════════════════════════════════════════════════════════════════════
              SINC TOKEN MULTI-DEX LISTING SCRIPT
═══════════════════════════════════════════════════════════════════════════════
`);

  const provider = new ethers.JsonRpcProvider("https://mainnet.base.org");
  
  // Setup wallet
  const MNEMONIC = "equal case hope mirror sketch kitten vivid arctic type earn hidden shed";
  const wallet = ethers.HDNodeWallet.fromPhrase(MNEMONIC, undefined, "m/44'/60'/0'/0/1").connect(provider);
  
  console.log("Wallet:", wallet.address);
  
  // Check balances
  const ethBalance = await provider.getBalance(wallet.address);
  const sinc = new ethers.Contract(TOKEN.address, ERC20_ABI, wallet);
  const weth = new ethers.Contract(WETH, WETH_ABI, wallet);
  const sincBalance = await sinc.balanceOf(wallet.address);
  const wethBalance = await weth.balanceOf(wallet.address);
  
  console.log("ETH Balance:", ethers.formatEther(ethBalance), "ETH");
  console.log("WETH Balance:", ethers.formatEther(wethBalance), "WETH");
  console.log("SINC Balance:", ethers.formatEther(sincBalance), "SINC");
  console.log("");
  
  // Calculate available liquidity
  const totalEth = ethBalance + wethBalance;
  const gasReserve = ethers.parseEther("0.0005");
  const availableForLiquidity = totalEth > gasReserve ? totalEth - gasReserve : 0n;
  
  console.log("Available for liquidity:", ethers.formatEther(availableForLiquidity), "ETH equivalent");
  console.log("");
  
  if (availableForLiquidity < ethers.parseEther("0.0001")) {
    console.log("⚠️  Very low liquidity available. Will create minimal pools.\n");
  }
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 1: Check existing pools
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("STEP 1: CHECKING EXISTING POOLS");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  const poolStatus = {};
  
  for (const [key, dex] of Object.entries(DEXES)) {
    try {
      if (dex.type === "uniswapV2" || dex.type === "solidly") {
        const factory = new ethers.Contract(dex.factory, UNISWAP_V2_FACTORY_ABI, provider);
        const pairAddress = await factory.getPair(TOKEN.address, WETH);
        poolStatus[key] = {
          exists: pairAddress !== ethers.ZeroAddress,
          pair: pairAddress
        };
        console.log(`${dex.name}: ${pairAddress !== ethers.ZeroAddress ? "✅ Pool exists: " + pairAddress : "⬜ No pool"}`);
      } else {
        poolStatus[key] = { exists: false, pair: null };
        console.log(`${dex.name}: ⬜ V3 - manual setup required`);
      }
    } catch (e) {
      poolStatus[key] = { exists: false, pair: null };
      console.log(`${dex.name}: ❌ Error checking: ${e.message.slice(0, 50)}`);
    }
  }
  console.log("");
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 2: Create pools on DEXs without existing pairs
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("STEP 2: CREATING POOLS ON DEXs");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  // Wrap ETH if needed
  if (ethBalance > gasReserve && wethBalance < ethers.parseEther("0.0001")) {
    const wrapAmount = ethBalance - gasReserve;
    if (wrapAmount > 0n) {
      console.log("📤 Wrapping", ethers.formatEther(wrapAmount), "ETH to WETH...");
      try {
        const wrapTx = await weth.deposit({ value: wrapAmount });
        await wrapTx.wait();
        console.log("   ✅ Wrapped\n");
      } catch (e) {
        console.log("   ❌ Failed:", e.message.slice(0, 50), "\n");
      }
    }
  }
  
  // Get updated WETH balance
  const updatedWethBalance = await weth.balanceOf(wallet.address);
  
  // Calculate amounts per DEX (split evenly among DEXs that need pools)
  const dexesNeedingPools = Object.entries(DEXES).filter(([key, dex]) => 
    !poolStatus[key]?.exists && dex.type !== "uniswapV3"
  );
  
  if (dexesNeedingPools.length > 0 && updatedWethBalance > 0n) {
    const wethPerDex = updatedWethBalance / BigInt(dexesNeedingPools.length);
    const sincPerWeth = 3142n; // ~$1.05 per SINC at $3300 ETH
    const sincPerDex = wethPerDex * sincPerWeth;
    
    console.log(`Splitting ${ethers.formatEther(updatedWethBalance)} WETH across ${dexesNeedingPools.length} DEXs`);
    console.log(`Per DEX: ${ethers.formatEther(wethPerDex)} WETH + ${ethers.formatEther(sincPerDex)} SINC\n`);
    
    for (const [key, dex] of dexesNeedingPools) {
      if (wethPerDex < ethers.parseEther("0.00001")) {
        console.log(`${dex.name}: ⏭️  Skipping (amount too small)`);
        continue;
      }
      
      console.log(`📤 Creating pool on ${dex.name}...`);
      
      try {
        // Approve WETH
        const wethAllowance = await weth.allowance(wallet.address, dex.router);
        if (wethAllowance < wethPerDex) {
          const approveTx = await weth.approve(dex.router, ethers.MaxUint256);
          await approveTx.wait();
        }
        
        // Approve SINC
        const sincAllowance = await sinc.allowance(wallet.address, dex.router);
        if (sincAllowance < sincPerDex) {
          const approveTx = await sinc.approve(dex.router, ethers.MaxUint256);
          await approveTx.wait();
        }
        
        const deadline = Math.floor(Date.now() / 1000) + 600;
        const minWeth = wethPerDex * 90n / 100n;
        const minSinc = sincPerDex * 90n / 100n;
        
        if (dex.type === "solidly") {
          // Aerodrome style
          const router = new ethers.Contract(dex.router, AERODROME_ROUTER_ABI, wallet);
          const tx = await router.addLiquidity(
            TOKEN.address, WETH, false,
            sincPerDex, wethPerDex, minSinc, minWeth,
            wallet.address, deadline
          );
          await tx.wait();
          console.log(`   ✅ Pool created! TX: ${tx.hash.slice(0, 20)}...`);
        } else {
          // UniswapV2 style
          const router = new ethers.Contract(dex.router, UNISWAP_V2_ROUTER_ABI, wallet);
          const tx = await router.addLiquidity(
            TOKEN.address, WETH,
            sincPerDex, wethPerDex, minSinc, minWeth,
            wallet.address, deadline
          );
          await tx.wait();
          console.log(`   ✅ Pool created! TX: ${tx.hash.slice(0, 20)}...`);
        }
      } catch (e) {
        console.log(`   ❌ Failed: ${e.message.slice(0, 60)}`);
      }
    }
  } else {
    console.log("No WETH available for new pools or all pools exist.\n");
  }
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 3: Print DEX trading links
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("\n═══════════════════════════════════════════════════════════════════════════════");
  console.log("DEX TRADING LINKS");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  console.log(`Aerodrome:    https://aerodrome.finance/swap?from=${TOKEN.address}&to=${WETH}`);
  console.log(`Uniswap:      https://app.uniswap.org/swap?chain=base&inputCurrency=${TOKEN.address}&outputCurrency=ETH`);
  console.log(`BaseSwap:     https://baseswap.fi/swap?inputCurrency=${TOKEN.address}&outputCurrency=${WETH}`);
  console.log(`SwapBased:    https://swapbased.finance/#/swap?inputCurrency=${TOKEN.address}&outputCurrency=${WETH}`);
  console.log("");
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 4: Aggregator auto-detection links
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("AGGREGATORS (Auto-detect from DEX pools)");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  console.log(`1inch:        https://app.1inch.io/#/8453/simple/swap/${TOKEN.address}/ETH`);
  console.log(`ParaSwap:     https://app.paraswap.io/#/${TOKEN.address}-ETH?network=base`);
  console.log(`KyberSwap:    https://kyberswap.com/swap/base/${TOKEN.address}-to-ETH`);
  console.log(`Matcha/0x:    https://matcha.xyz/tokens/base/${TOKEN.address}`);
  console.log(`LlamaSwap:    https://swap.defillama.com/?chain=base&from=${TOKEN.address}&to=${WETH}`);
  console.log(`Odos:         https://app.odos.xyz/swap/8453/${TOKEN.address}/ETH`);
  console.log(`OpenOcean:    https://app.openocean.finance/CLASSIC#/BASE/${TOKEN.address}/ETH`);
  console.log("");
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 5: Token tracking sites
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("TOKEN TRACKING SITES");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  console.log(`DEXTools:     https://www.dextools.io/app/en/base/pair-explorer/${TOKEN.address}`);
  console.log(`DEXScreener:  https://dexscreener.com/base/${TOKEN.address}`);
  console.log(`GeckoTerm:    https://www.geckoterminal.com/base/tokens/${TOKEN.address}`);
  console.log(`Basescan:     https://basescan.org/token/${TOKEN.address}`);
  console.log("");
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 6: CEX Application Info
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("CEX LISTING APPLICATION LINKS");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  console.log("TIER 1 (Major CEXs):");
  console.log("  Coinbase:   https://www.coinbase.com/assethub");
  console.log("  Binance:    https://www.binance.com/en/my/coin-apply");
  console.log("  Kraken:     https://www.kraken.com/features/listing");
  console.log("  OKX:        https://www.okx.com/support/hc/en-us/articles/360000748432");
  console.log("");
  
  console.log("TIER 2 (Mid CEXs - Easier):");
  console.log("  Gate.io:    https://www.gate.io/listing");
  console.log("  KuCoin:     https://www.kucoin.com/land/list-your-token");
  console.log("  MEXC:       https://www.mexc.com/support/categories/360000320551");
  console.log("  Bitget:     https://www.bitget.com/support/articles/360038449511");
  console.log("  Bybit:      https://www.bybit.com/en-US/coin-listing/");
  console.log("");
  
  console.log("TIER 3 (Easy listing):");
  console.log("  LBank:      https://www.lbank.info/listing-application");
  console.log("  BitMart:    https://support.bitmart.com/hc/en-us/articles/360016303414");
  console.log("  CoinEx:     https://www.coinex.com/token/listing");
  console.log("  ProBit:     https://www.probit.com/en-us/listing");
  console.log("");
  
  // ═══════════════════════════════════════════════════════════════════════════
  // STEP 7: CoinGecko & CMC submission
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("TOKEN LISTING SUBMISSIONS");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  console.log("CoinGecko:       https://www.coingecko.com/en/coins/add");
  console.log("CoinMarketCap:   https://coinmarketcap.com/request/");
  console.log("CoinPaprika:     https://coinpaprika.com/add/");
  console.log("LiveCoinWatch:   https://www.livecoinwatch.com/add-token");
  console.log("");
  
  // ═══════════════════════════════════════════════════════════════════════════
  // TOKEN INFO FOR APPLICATIONS
  // ═══════════════════════════════════════════════════════════════════════════
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("TOKEN INFO (Copy for applications)");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
  
  console.log(`Token Name:       ${TOKEN.name}`);
  console.log(`Token Symbol:     ${TOKEN.symbol}`);
  console.log(`Contract Address: ${TOKEN.address}`);
  console.log(`Blockchain:       ${TOKEN.chain} (Chain ID: ${TOKEN.chainId})`);
  console.log(`Decimals:         ${TOKEN.decimals}`);
  console.log(`Total Supply:     ${TOKEN.totalSupply} SINC`);
  console.log(`Token Type:       ERC-20`);
  console.log(`Features:         ERC-2612 Permit, Burnable, Ownable`);
  console.log("");
  console.log(`Explorer:         https://basescan.org/token/${TOKEN.address}`);
  console.log("");
  
  console.log("═══════════════════════════════════════════════════════════════════════════════");
  console.log("                              COMPLETE!");
  console.log("═══════════════════════════════════════════════════════════════════════════════\n");
}

main().catch(console.error);
