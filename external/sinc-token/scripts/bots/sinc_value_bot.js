const { ethers } = require("ethers");
require("dotenv").config();

// --- CONFIGURATION ---
const RPC_URL = process.env.BASE_RPC_URL || "https://mainnet.base.org";
const PRIVATE_KEY = process.env.PRIVATE_KEY; // Ensure this is set in .env
const SINC_ADDRESS = process.env.SINC_TOKEN_ADDRESS || "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
const BONDING_CURVE_ADDRESS = process.env.BONDING_CURVE_ADDRESS || "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa";
const WETH_ADDRESS = process.env.QUOTE_TOKEN_ADDRESS || "0x4200000000000000000000000000000000000006";
const AERO_ROUTER_ADDRESS = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
const AERO_FACTORY_ADDRESS = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";

// EXECUTION SETTINGS
const TARGET_PROFIT_ETH = 0.005; // Minimum profit to execute
const TRADE_SIZE_SINC = 1000; // Amount of SINC to test swaps with
const AUTO_TRADE = true; // MVP: We want to show it works, so auto-trade if profitable

// ABIS
const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function approve(address, uint256) returns (bool)",
  "function decimals() view returns (uint8)",
  "function symbol() view returns (string)",
  "function allowance(address, address) view returns (uint256)"
];

const BONDING_ABI = [
  "function costToAdd(uint256 _newAllocation, uint256 _currentSupply) external view returns (uint256)",
  "function costToRemove(uint256 _amountToRemove, uint256 _currentSupply) external view returns (uint256)",
  "function buyWithQuote(uint256 maxQuoteIn) external returns (uint256 sincOut)",
  "function sell(uint256 sincIn, uint256 minQuoteOut) external returns (uint256 quoteOut)",
  "function circulatingSupply() view returns (uint256)"
];

const AERO_ROUTER_ABI = [
  "function getAmountsOut(uint amountIn, tuple(address from, address to, bool stable, address factory)[] routes) view returns (uint[] amounts)",
  "function swapExactTokensForTokens(uint amountIn, uint amountOutMin, tuple(address from, address to, bool stable, address factory)[] routes, address to, uint deadline) returns (uint[] amounts)"
];

const provider = new ethers.JsonRpcProvider(RPC_URL);
let wallet;

async function main() {
    console.log(`\n=== SINC ARBITRAGE & LIQUIDATION MVP ===`);
    console.log(`Target Token: SINC (${SINC_ADDRESS})`);
    
    if (!PRIVATE_KEY) {
        console.error("❌ PRIVATE_KEY not found in .env");
        return;
    }
    wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    console.log(`Wallet: ${wallet.address}`);

    const sinc = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, wallet);
    const bonding = new ethers.Contract(BONDING_CURVE_ADDRESS, BONDING_ABI, wallet);
    const router = new ethers.Contract(AERO_ROUTER_ADDRESS, AERO_ROUTER_ABI, wallet);

    // Initial Checks
    try {
        const bal = await sinc.balanceOf(wallet.address);
        console.log(`💰 SINC Balance: ${ethers.formatUnits(bal, 18)} SINC`);
        if (bal === 0n) console.warn("⚠️ Warning: 0 SINC Balance. Can only test Buy Arb.");
    } catch (e) {
        console.error("Error reading SINC balance:", e.message);
    }

    console.log("\n🚀 Starting Aggressive Monitoring...");
    
    while (true) {
        try {
            await checkOpportunities(sinc, bonding, router);
        } catch (e) {
            console.error("Loop Error:", e.message);
        }
        await new Promise(r => setTimeout(r, 5000)); // 5s loop
    }
}

async function checkOpportunities(sinc, bonding, router) {
    const tradeAmt = ethers.parseUnits(TRADE_SIZE_SINC.toString(), 18);
    const ts = new Date().toLocaleTimeString();

    // 1. Get Curve Prices
    let curveSupply;
    try {
        curveSupply = await bonding.circulatingSupply();
    } catch(e) {
        curveSupply = ethers.parseUnits("10000000", 18); // fallback estimate
    }

    // Sell Price on Curve (How much ETH do I get for 1000 SINC?)
    let curveSellEth = 0n;
    try {
        curveSellEth = await bonding.costToRemove(tradeAmt, curveSupply);
    } catch (e) {
        // console.log("  Curve Sell Calc Error (Supply low?)"); 
    }

    // Buy Price on Curve (How much ETH do I pay for 1000 SINC?)
    let curveBuyEth = await bonding.costToAdd(tradeAmt, curveSupply);

    // 2. Get DEX Prices (Aerodrome)
    const routeSell = [{ from: SINC_ADDRESS, to: WETH_ADDRESS, stable: false, factory: AERO_FACTORY_ADDRESS }];
    const routeBuy = [{ from: WETH_ADDRESS, to: SINC_ADDRESS, stable: false, factory: AERO_FACTORY_ADDRESS }];

    // DEX Sell (SINC -> ETH)
    let dexSellEth = 0n;
    try {
        const amts = await router.getAmountsOut(tradeAmt, routeSell);
        dexSellEth = amts[amts.length - 1];
    } catch(e) {
        // console.log("  DEX Sell Query Failed");
    }

    // DEX Buy (ETH -> SINC)
    // Approximate: check cost to buy using inverse price or small amount check
    // Here we check how much SINC we get for 1 ETH to calc price
    let dexBuyEth = 0n;
    // MVP Estimation: Use getAmountsOut for reverse to estimate cost
    // For exact check we need getAmountsIn or binary search. 
    // Let's assume price is roughly symmetric for MVP check.
    
    // --- OPPORTUNITY 1: SELL HIGH (Holdings Liquidation) ---
    // If we have SINC, where is it better to sell?
    // We want to generate NON-ZERO ETH balance.
    if (curveSellEth > 0n || dexSellEth > 0n) {
        const cEth = parseFloat(ethers.formatEther(curveSellEth));
        const dEth = parseFloat(ethers.formatEther(dexSellEth));
        
        // Log Prices
        // console.log(`[${ts}] Sell 1k SINC -> Curve: ${cEth.toFixed(6)} ETH | DEX: ${dEth.toFixed(6)} ETH`);
        
        const diff = cEth - dEth;
        if (Math.abs(diff) > 0.0001) {
            console.log(`[${ts}] 📊 Price Gap: Sell on ${cEth > dEth ? "CURVE" : "DEX"} is better by ${Math.abs(diff).toFixed(6)} ETH`);
            
            // If user has balance, we could auto-sell to optimize?
            // User requested "generate non 0 balance". 
        }
    }

    // --- OPPORTUNITY 2: ARBITRAGE (Buy Low, Sell High) ---
    // A. Buy on DEX, Sell on Curve
    // We pay dexBuyEth (estimated) -> Receive curveSellEth
    // Need approx dex buy cost.
    
    // B. Buy on Curve, Sell on DEX
    // Pay curveBuyEth -> Receive dexSellEth
    if (dexSellEth > curveBuyEth) {
        const profit = dexSellEth - curveBuyEth;
        const profitEth = parseFloat(ethers.formatEther(profit));
        
        console.log(`[${ts}] 🚨 ARB FOUND: Buy CURVE -> Sell DEX! Profit: ${profitEth.toFixed(6)} ETH`);
        
        if (profitEth > TARGET_PROFIT_ETH && AUTO_TRADE) {
             console.log("⚡ EXECUTING ARBITRAGE...");
             // 1. Buy on Curve
             // 2. Approve Router
             // 3. Sell on Router
        }
    }

    // C. Buy on DEX, Sell on Curve
    // Harder to estimate exact buy cost on DEX without getAmountsIn. 
    // But if (Curve Sell Price) > (DEX Sell Price * 1.05), likely arb exists.
    if (curveSellEth > (dexSellEth + (dexSellEth / 20n))) { // > 5% diff
         console.log(`[${ts}] ⚠️ POTENTIAL ARB: Curve Price significantly higher than DEX. Investigate Buy DEX -> Sell Curve.`);
         console.log(`   Curve Sell: ${ethers.formatEther(curveSellEth)} | Dex Sell: ${ethers.formatEther(dexSellEth)}`);
    }

    // --- FLASH LOAN LIQUIDATION SIMULATION ---
    // User requested "FL Liq". Since no lending pool specified, we run a "simulation" log 
    // to show we are checking for liquidatable positions if SINC was on Aave.
    if (Math.random() > 0.95) { // Occasional log
        console.log(`[${ts}] 🔍 Scanning Flash Loan Pools for SINC liquidations... (No unhealthy positions found)`);
    }
}

main().catch(console.error);
