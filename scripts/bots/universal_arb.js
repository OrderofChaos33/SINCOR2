const { ethers } = require("ethers");
require("dotenv").config();

// --- CONFIGURATION ---
const RPC_URL = process.env.BASE_RPC_URL || "https://base.publicnode.com";
const PRIVATE_KEY = process.env.PRIVATE_KEY;

// CONTRACTS
const UNIVERSAL_ARB_CONTRACT = "0xEa9161EfE748cDe9c0d9B9542EDAaB7C1b4Ac442";

// TOKENS (Base)
const WETH = "0x4200000000000000000000000000000000000006";
const USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"; // Circle USDC

// ROUTERS
const AERO_ROUTER = "0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43";
const BASESWAP_ROUTER = "0x327Df1E6de05895d2ab08513aaDD9313Fe505d86"; 

// AERO Factory
const AERO_FACTORY = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";

const ABIS = {
    ROUTER_V2: [
        "function getAmountsOut(uint amountIn, address[] memory path) public view returns (uint[] memory amounts)"
    ],
    ROUTER_AERO: [
        "function getAmountsOut(uint amountIn, tuple(address from, address to, bool stable, address factory)[] routes) view returns (uint[] amounts)"
    ],
    UNIVERSAL_ARB: [
        "function requestFlashLoan(address asset, uint256 amount, bytes calldata params) external"
    ]
};

const provider = new ethers.JsonRpcProvider(RPC_URL);
let wallet;
let arbBot;

async function main() {
    console.log("\n=== UNIVERSAL FLASH LOAN ARB BOT (WETH/USDC) ===");
    
    if (!PRIVATE_KEY) {
        console.error("❌ PRIVATE_KEY missing");
        return;
    }
    wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    console.log(`Wallet: ${wallet.address}`);
    
    // Check Gas Balance
    const balance = await provider.getBalance(wallet.address);
    console.log(`ETH Balance: ${ethers.formatEther(balance)} ETH`);

    arbBot = new ethers.Contract(UNIVERSAL_ARB_CONTRACT, ABIS.UNIVERSAL_ARB, wallet);
    const aero = new ethers.Contract(AERO_ROUTER, ABIS.ROUTER_AERO, wallet);
    const baseSwap = new ethers.Contract(BASESWAP_ROUTER, ABIS.ROUTER_V2, wallet);

    console.log("\n🚀 Scanning for Flash Loan Opportunities...");
    
    while(true) {
        try {
            await scanArb(aero, baseSwap);
        } catch(e) {
            console.error("Loop Error:", e.message);
        }
        await new Promise(r => setTimeout(r, 6000)); // 6s scan
    }
}

async function scanArb(aero, baseSwap) {
    const borrowAmount = ethers.parseEther("0.1"); // Flash Loan 0.1 ETH
    
    // We strictly look for WETH Price Diff
    // 1. Get BaseSwap Price (Sell WETH -> USDC)
    let baseSwapOut = 0n;
    try {
        const amts = await baseSwap.getAmountsOut(borrowAmount, [WETH, USDC]);
        baseSwapOut = amts[1]; 
    } catch(e) {}

    // 2. Get Aerodrome Price (Sell WETH -> USDC)
    let aeroOut = 0n;
    const routeAero = [{ from: WETH, to: USDC, stable: false, factory: AERO_FACTORY }];
    try {
        const amts = await aero.getAmountsOut(borrowAmount, routeAero);
        aeroOut = amts[amts.length - 1];
    } catch(e) {}

    if (baseSwapOut > 0n && aeroOut > 0n) {
        const bsUSDC = parseFloat(ethers.formatUnits(baseSwapOut, 6));
        const aeUSDC = parseFloat(ethers.formatUnits(aeroOut, 6));
        
        console.log(`[${new Date().toLocaleTimeString()}] Aero: $${aeUSDC.toFixed(2)} | BaseSwap: $${bsUSDC.toFixed(2)} | Diff: $${Math.abs(bsUSDC - aeUSDC).toFixed(2)}`);

        // ESTIMATE FLASH LOAN FEE (0.05%)
        // 0.1 ETH * 0.05% = 0.00005 ETH (~$0.15)
        // GAS FEE estimate (~$0.10)
        // Threshold: $1.00 profit minimum
        
        if (aeUSDC > bsUSDC + 1.0) { 
             // Aero is HIGHER. Sell there.
             // Strategy: Borrow WETH -> Sell on Aero (WETH->USDC) -> Buy on BaseSwap (USDC->WETH) -> Repay
             console.log("👉 Opportunity: Sell Aero, Buy BaseSwap. Executing via Flash Loan...");
             await executeFlashLoan(true); 
        } else if (bsUSDC > aeUSDC + 1.0) {
             // BaseSwap is HIGHER. Sell there.
             // Strategy: Borrow WETH -> Sell on BaseSwap (WETH->USDC) -> Buy on Aero (USDC->WETH) -> Repay
             console.log("👉 Opportunity: Sell BaseSwap, Buy Aero. Executing via Flash Loan...");
             await executeFlashLoan(false);
        }
    }
}

async function executeFlashLoan(sellOnAeroFirst) {
    const asset = WETH;
    const amount = ethers.parseEther("0.1");

    let routerA, routerB;
    let isAerodromeA, isAerodromeB;
    let payloadA, payloadB;

    const coder = ethers.AbiCoder.defaultAbiCoder(); 

    if (sellOnAeroFirst) {
        // A: Aero (WETH -> USDC)
        routerA = AERO_ROUTER;
        isAerodromeA = true;
        
        const routeAStruct = [{
            from: WETH,
            to: USDC,
            stable: false,
            factory: AERO_FACTORY
        }];
        const routeType = "tuple(address from, address to, bool stable, address factory)[]";
        payloadA = coder.encode([routeType], [routeAStruct]);

        // B: BaseSwap (USDC -> WETH)
        routerB = BASESWAP_ROUTER;
        isAerodromeB = false;
        payloadB = coder.encode(["address[]"], [[USDC, WETH]]);
    } else {
        // A: BaseSwap (WETH -> USDC)
        routerA = BASESWAP_ROUTER;
        isAerodromeA = false;
        payloadA = coder.encode(["address[]"], [[WETH, USDC]]);

        // B: Aero (USDC -> WETH)
        routerB = AERO_ROUTER;
        isAerodromeB = true;
        const routeBStruct = [{
            from: USDC,
            to: WETH,
            stable: false,
            factory: AERO_FACTORY
        }];
        const routeType = "tuple(address from, address to, bool stable, address factory)[]";
        payloadB = coder.encode([routeType], [routeBStruct]);
    }

    const params = coder.encode(
        ["address", "address", "bool", "bool", "bytes", "bytes"],
        [routerA, routerB, isAerodromeA, isAerodromeB, payloadA, payloadB]
    );

    try {
        console.log("Sending Transaction...");
        // Use manual gas limit to avoid estimation errors if profitable scan fails on-chain unexpectedly
        const tx = await arbBot.requestFlashLoan(asset, amount, params, { gasLimit: 500000 });
        console.log(`Tx Sent: ${tx.hash}`);
        const receipt = await tx.wait();
        console.log(`✅ Success! Block used: ${receipt.gasUsed}`);
    } catch(e) {
        console.error("❌ Execution Failed:", e.message);
    }
}

main();
