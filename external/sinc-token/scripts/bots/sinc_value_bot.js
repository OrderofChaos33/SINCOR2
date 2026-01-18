const { ethers } = require("ethers");
require("dotenv").config();

// --- CONFIGURATION ---
const RPC_URL = process.env.BASE_RPC_URL || "https://base.publicnode.com";
const PRIVATE_KEY = process.env.PRIVATE_KEY; 
const SINC_ADDRESS = process.env.SINC_TOKEN_ADDRESS || "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
const BONDING_CURVE_ADDRESS = process.env.BONDING_CURVE_ADDRESS || "0x25cA41Dac29f892c72A53500853eC45a5FfF90aa";
// WETH is the Quote Token
const WETH_ADDRESS = process.env.QUOTE_TOKEN_ADDRESS || "0x4200000000000000000000000000000000000006";

const AERO_ROUTER_ADDRESS = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
const AERO_FACTORY_ADDRESS = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";

// EXECUTION SETTINGS
const TARGET_PROFIT_ETH = 0.0005; // Lower threshold; usually expect >0.005 for gas
const TRADE_SIZE_WETH = 0.001; // Amount of WETH to test Buy with
const AUTO_TRADE = true; 

// ABIS
const ERC20_ABI = [
  "function balanceOf(address) view returns (uint256)",
  "function approve(address, uint256) returns (bool)",
  "function allowance(address, address) view returns (uint256)",
  "function decimals() view returns (uint8)"
];

const BONDING_ABI = [
  "function buy(uint256 quoteAmount, uint256 minSincAmount) returns (uint256)",
  "function sell(uint256 sincAmount, uint256 minQuoteAmount) returns (uint256)",
  "function calculateBuy(uint256 quoteAmount) view returns (uint256 sincAmount, uint256 fee)",
  "function calculateSell(uint256 sincAmount) view returns (uint256 quoteAmount, uint256 fee)",
  "function circulatingSupply() view returns (uint256)"
];

const AERO_ROUTER_ABI = [
  "function getAmountsOut(uint amountIn, tuple(address from, address to, bool stable, address factory)[] routes) view returns (uint[] amounts)",
  "function swapExactTokensForTokens(uint amountIn, uint amountOutMin, tuple(address from, address to, bool stable, address factory)[] routes, address to, uint deadline) returns (uint[] amounts)"
];

const provider = new ethers.JsonRpcProvider(RPC_URL);
let wallet;

async function main() {
    console.log(`\n=== SINC ARBITRAGE BOT v2 (Corrected ABI) ===`);
    console.log(`Target Token: SINC (${SINC_ADDRESS})`);
    console.log(`Bonding Curve: ${BONDING_CURVE_ADDRESS}`);
    
    if (!PRIVATE_KEY) {
        console.error("❌ PRIVATE_KEY not found in .env");
        return;
    }
    wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    console.log(`Wallet: ${wallet.address}`);

    const sinc = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, wallet);
    const weth = new ethers.Contract(WETH_ADDRESS, ERC20_ABI, wallet);
    const bonding = new ethers.Contract(BONDING_CURVE_ADDRESS, BONDING_ABI, wallet);
    const router = new ethers.Contract(AERO_ROUTER_ADDRESS, AERO_ROUTER_ABI, wallet);

    console.log("\n🚀 Starting Monitoring Loop...");
    
    while (true) {
        try {
            await checkOpportunities(sinc, weth, bonding, router);
        } catch (e) {
            // Log only relevant errors
            if (e.code === "CALL_EXCEPTION") console.log(".");
            else console.error("Loop Error:", e.message);
        }
        await new Promise(r => setTimeout(r, 6000)); // 6s loop
    }
}

async function checkOpportunities(sinc, weth, bonding, router) {
    const ts = new Date().toLocaleTimeString();
    
    // Test Amount: Buy with 0.001 ETH
    const wethIn = ethers.parseEther(TRADE_SIZE_WETH.toString());

    // 1. Get Curve Buy Price (Buy SINC with WETH)
    let curveSincOut = 0n;
    try {
        // calculateBuy returns (sincAmount, fee)
        const res = await bonding.calculateBuy(wethIn);
        curveSincOut = res[0];
    } catch (e) {
        console.log(`[${ts}] Curve Buy Calc Failed (Paused?): ${e.message}`);
        return;
    }

    // 2. Get DEX Sell Price (Sell the SINC we just bought back to WETH)
    // Route: SINC -> WETH
    const routeSell = [{ from: SINC_ADDRESS, to: WETH_ADDRESS, stable: false, factory: AERO_FACTORY_ADDRESS }];
    let dexWethOut = 0n;
    try {
        const amts = await router.getAmountsOut(curveSincOut, routeSell);
        dexWethOut = amts[amts.length - 1];
    } catch(e) {
        console.log(`[${ts}] DEX Sell Calc Failed (No liquidity?): ${e.message}`);
        // If DEX has no liq, we can't arb
        return;
    }

    // ARB CALCULATION
    // Start: 0.001 ETH -> Curve -> SINC -> DEX -> ETH
    const profitEth = BigInt(dexWethOut) - BigInt(wethIn);
    
    const wethInFloat = parseFloat(ethers.formatEther(wethIn));
    const dexOutFloat = parseFloat(ethers.formatEther(dexWethOut));
    const curveSincFloat = parseFloat(ethers.formatEther(curveSincOut));
    
    // console.log(`[${ts}] 1 ETH buys ${curveSincFloat/wethInFloat} SINC on Curve`);
    
    if (profitEth > 0n) {
        const pFloat = parseFloat(ethers.formatEther(profitEth));
        console.log(`[${ts}] 💰 ARB FOUND: ${pFloat.toFixed(6)} ETH Profit! (Buy Curve -> Sell DEX)`);
        
        if (pFloat > TARGET_PROFIT_ETH && AUTO_TRADE) {
             console.log("⚡ EXECUTING ARB...");
             await executeArb(wethIn, curveSincOut, bonding, sinc, router);
        }
    } else {
        // Log status occasionally
        console.log(`[${ts}] No Arb. Loss/Diff: ${(dexOutFloat - wethInFloat).toFixed(6)} ETH (Curve buys ${curveSincFloat.toFixed(2)} SINC)`);
    }
}

async function executeArb(wethIn, minSincFromCurve, bonding, sinc, router) {
    try {
        // 1. Buy on Curve
       
        console.log("   1. Buying on Curve...");
         
        // Slippage 1%
        const minSinc = minSincFromCurve * 99n / 100n;
        const txBuy = await bonding.buy(wethIn, minSinc);
        console.log(`      TX: ${txBuy.hash}`);
        await txBuy.wait();

        // 2. Sell on DEX
        console.log("   2. Selling on DEX...");
        const routeSell = [{ from: SINC_ADDRESS, to: WETH_ADDRESS, stable: false, factory: AERO_FACTORY_ADDRESS }];
        // approve router
        // await (await sinc.approve(AERO_ROUTER_ADDRESS, minSincFromCurve)).wait();
        
        const txSell = await router.swapExactTokensForTokens(
            minSincFromCurve, // Try to sell what we got (or balance)
            0, // Accept any ETH (risky but MVP) or set limit
            routeSell,
            wallet.address,
            Math.floor(Date.now()/1000) + 60
        );
        await txSell.wait();
        console.log("   ✅ Arbitrage Success!");
        
    } catch (e) {
        console.error("   ❌ Execution Error:", e.message);
    }
}

main().catch(console.error);
