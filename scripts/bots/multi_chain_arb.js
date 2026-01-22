/**
 * MULTI-CHAIN LONG-TAIL ARBITRAGE BOT
 * ====================================
 * Targets REAL opportunities that big bots IGNORE:
 * 
 * 1. New/small DEXs with low bot saturation
 * 2. Long-tail tokens (low volume = less competition)
 * 3. Cross-DEX price discrepancies
 * 4. Uses flash loans = ZERO capital needed
 * 
 * Chains: Base, Mode, Scroll, Linea (less competitive than mainnet)
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CHAIN CONFIGS =============
const CHAINS = {
    base: {
        name: 'Base',
        chainId: 8453,
        rpc: 'https://mainnet.base.org',
        explorer: 'https://basescan.org',
        dexs: [
            { name: 'Uniswap V3', factory: '0x33128a8fC17869897dcE68Ed026d694621f6FDfD', router: '0x2626664c2603336E57B271c5C0b26F421741e481', type: 'v3' },
            { name: 'Aerodrome', factory: '0x420DD381b31aEf6683db6B902084cB0FFECe40Da', router: '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43', type: 'v2' },
            { name: 'BaseSwap', factory: '0xFDa619b6d20975be80A10332cD39b9a4b0FAa8BB', router: '0x327Df1E6de05895d2ab08513aaDD9313Fe505d86', type: 'v2' },
            { name: 'SwapBased', factory: '0x04C9f118d21e8B767D2e50C946f0cC9F6C367300', router: '0xaaa3b1F1bd7BCc97fD1917c18ADE665C5D31F066', type: 'v2' },
        ],
        weth: '0x4200000000000000000000000000000000000006',
        usdc: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        flashLoanProvider: '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5', // Aave V3
    },
    mode: {
        name: 'Mode',
        chainId: 34443,
        rpc: 'https://mainnet.mode.network',
        explorer: 'https://explorer.mode.network',
        dexs: [
            { name: 'SupSwap', factory: '0x9A26f7A3a37D51fD5C3C89d87557E974D2a52b4c', router: '0x5951479fE3235b689E392E9BC6E968CE10637A52', type: 'v2' },
            { name: 'Kim Exchange', factory: '0xB396D59C6Ca4e626DC5d1e5C4ef23886ed617cba', router: '0xAc48FcF1049668B285f3dC72483DF5Ae2162f7e8', type: 'v3' },
            { name: 'SwapMode', factory: '0xfB3323E517A4C6C9Fab2141849858595eb4bcDa1', router: '0xc1e624C810D297FD70eF53B0E08F44FABE468591', type: 'v2' },
        ],
        weth: '0x4200000000000000000000000000000000000006',
        usdc: '0xd988097fb8612cc24eeC14542bC03424c656005f',
        flashLoanProvider: null, // Use DEX flash swaps
    },
    scroll: {
        name: 'Scroll',
        chainId: 534352,
        rpc: 'https://rpc.scroll.io',
        explorer: 'https://scrollscan.com',
        dexs: [
            { name: 'SyncSwap', factory: '0x37BAc764494c8db4e54BDE72f6965beA9fa0AC2d', router: '0x80e38291e06339d10AAB483C65695D004dBD5C69', type: 'v2' },
            { name: 'Zebra', factory: '0xf6a35Cc4A3a57bB1B4871C0D98684DaBE0a4C228', router: '0x0122960d6E391478bfE8fb2408Ba412D5600f621', type: 'v2' },
            { name: 'Ambient', factory: '0xaaaaAAAACB71BF2C8CaE522EA5fa455571A74106', router: '0xaaaaAAAACB71BF2C8CaE522EA5fa455571A74106', type: 'ambient' },
        ],
        weth: '0x5300000000000000000000000000000000000004',
        usdc: '0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4',
        flashLoanProvider: null,
    },
    linea: {
        name: 'Linea',
        chainId: 59144,
        rpc: 'https://rpc.linea.build',
        explorer: 'https://lineascan.build',
        dexs: [
            { name: 'Lynex', factory: '0xBc7695Fd00E3b32D08124b7a4287493aEE99f9ee', router: '0x610D2f07b7EdC67565160F587F37636194C34E74', type: 'v2' },
            { name: 'Velocore', factory: '0x7A1CC4e70C9F98F287d9EF39ec4ADd0a8b5d4c65', router: '0x1d0188c4B276A09366D05d6Be06aF61a73bC7535', type: 'v2' },
            { name: 'HorizonDEX', factory: '0x54369A3D8a96f73A339d3F0D5B8F5F5F4B6F6c31', router: '0x272E156Df8DA513C69cB41cC7A99185D53F926Bb', type: 'v2' },
        ],
        weth: '0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f',
        usdc: '0x176211869cA2b568f2A7D4EE941E073a821EE1ff',
        flashLoanProvider: null,
    }
};

// ============= ABIs =============
const FACTORY_V2_ABI = [
    "function getPair(address tokenA, address tokenB) view returns (address)",
    "function allPairsLength() view returns (uint256)",
    "function allPairs(uint256) view returns (address)"
];

const PAIR_V2_ABI = [
    "function token0() view returns (address)",
    "function token1() view returns (address)",
    "function getReserves() view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)",
    "function swap(uint amount0Out, uint amount1Out, address to, bytes calldata data)"
];

const ROUTER_V2_ABI = [
    "function getAmountsOut(uint amountIn, address[] calldata path) view returns (uint[] memory amounts)",
    "function swapExactTokensForTokens(uint amountIn, uint amountOutMin, address[] calldata path, address to, uint deadline) returns (uint[] memory amounts)"
];

const ERC20_ABI = [
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
    "function balanceOf(address) view returns (uint256)",
    "function approve(address spender, uint256 amount) returns (bool)"
];

// ============= STATE =============
let providers = {};
let wallets = {};
let opportunities = [];
let stats = {
    scansCompleted: 0,
    opportunitiesFound: 0,
    profitableOpportunities: 0,
    executedTrades: 0,
    totalProfit: 0,
    lastOpportunity: null
};

const RESULTS_FILE = path.join(__dirname, 'arb_results.json');
const TOKENS_CACHE = path.join(__dirname, 'known_tokens.json');

// ============= LOGGING =============
function log(msg, type = 'INFO') {
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    const icons = {
        'INFO': '📊',
        'SCAN': '🔍',
        'OPPORTUNITY': '💎',
        'PROFIT': '💰',
        'EXECUTE': '⚡',
        'SUCCESS': '✅',
        'ERROR': '❌',
        'CHAIN': '🔗'
    };
    console.log(`[${timestamp}] ${icons[type] || '📌'} ${msg}`);
}

function saveResults() {
    fs.writeFileSync(RESULTS_FILE, JSON.stringify({
        lastUpdated: new Date().toISOString(),
        stats,
        recentOpportunities: opportunities.slice(-100)
    }, null, 2));
}

// ============= INITIALIZATION =============
async function initializeChains() {
    log('Initializing multi-chain connections...', 'INFO');
    
    for (const [chainKey, chain] of Object.entries(CHAINS)) {
        try {
            providers[chainKey] = new ethers.JsonRpcProvider(chain.rpc);
            
            // Verify connection
            const blockNum = await providers[chainKey].getBlockNumber();
            log(`${chain.name}: Connected (block ${blockNum})`, 'CHAIN');
            
            if (process.env.PRIVATE_KEY) {
                wallets[chainKey] = new ethers.Wallet(process.env.PRIVATE_KEY, providers[chainKey]);
            }
        } catch (e) {
            log(`${chain.name}: Connection failed - ${e.message}`, 'ERROR');
        }
    }
    
    log(`Connected to ${Object.keys(providers).length} chains`, 'SUCCESS');
}

// ============= TOKEN DISCOVERY =============
async function discoverTokens(chainKey) {
    const chain = CHAINS[chainKey];
    const provider = providers[chainKey];
    const tokens = new Set([chain.weth, chain.usdc]);
    
    for (const dex of chain.dexs) {
        if (dex.type !== 'v2') continue;
        
        try {
            const factory = new ethers.Contract(dex.factory, FACTORY_V2_ABI, provider);
            const pairCount = await factory.allPairsLength();
            
            // Sample up to 100 pairs
            const sampleSize = Math.min(100, Number(pairCount));
            for (let i = 0; i < sampleSize; i++) {
                try {
                    const pairAddr = await factory.allPairs(i);
                    const pair = new ethers.Contract(pairAddr, PAIR_V2_ABI, provider);
                    const [token0, token1] = await Promise.all([pair.token0(), pair.token1()]);
                    tokens.add(token0);
                    tokens.add(token1);
                } catch (e) {
                    continue;
                }
            }
            
            log(`${chain.name}/${dex.name}: Found ${tokens.size} tokens from ${sampleSize}/${pairCount} pairs`, 'SCAN');
        } catch (e) {
            continue;
        }
    }
    
    return Array.from(tokens);
}

// ============= PRICE CHECKING =============
async function getPrice(chainKey, dex, tokenIn, tokenOut, amountIn) {
    const provider = providers[chainKey];
    
    try {
        if (dex.type === 'v2') {
            const router = new ethers.Contract(dex.router, ROUTER_V2_ABI, provider);
            const amounts = await router.getAmountsOut(amountIn, [tokenIn, tokenOut]);
            return {
                dex: dex.name,
                amountOut: amounts[1],
                price: Number(amounts[1]) / Number(amountIn)
            };
        }
        // Add V3 support later
        return null;
    } catch (e) {
        return null;
    }
}

// ============= ARBITRAGE SCANNING =============
async function findArbitrageOpportunities(chainKey) {
    const chain = CHAINS[chainKey];
    const provider = providers[chainKey];
    const opps = [];
    
    // Test amount: 0.1 WETH equivalent
    const testAmount = ethers.parseEther('0.1');
    
    // Get tokens to scan
    const tokens = await discoverTokens(chainKey);
    
    // For each token, compare prices across DEXs
    for (const token of tokens.slice(0, 50)) { // Limit for speed
        if (token === chain.weth) continue;
        
        const prices = [];
        
        for (const dex of chain.dexs) {
            const price = await getPrice(chainKey, dex, chain.weth, token, testAmount);
            if (price && price.amountOut > 0n) {
                prices.push(price);
            }
        }
        
        if (prices.length < 2) continue;
        
        // Find best buy and sell
        prices.sort((a, b) => Number(b.amountOut - a.amountOut));
        const bestBuy = prices[0]; // Highest output = best price to buy
        const worstBuy = prices[prices.length - 1];
        
        // Now check reverse: token -> WETH
        const reversePrices = [];
        for (const dex of chain.dexs) {
            const price = await getPrice(chainKey, dex, token, chain.weth, bestBuy.amountOut);
            if (price && price.amountOut > 0n) {
                reversePrices.push(price);
            }
        }
        
        if (reversePrices.length === 0) continue;
        
        reversePrices.sort((a, b) => Number(b.amountOut - a.amountOut));
        const bestSell = reversePrices[0];
        
        // Calculate profit
        const profit = bestSell.amountOut - testAmount;
        const profitPercent = (Number(profit) / Number(testAmount)) * 100;
        
        if (profitPercent > 0.1) { // At least 0.1% profit potential
            const opp = {
                chain: chain.name,
                token,
                buyDex: bestBuy.dex,
                sellDex: bestSell.dex,
                inputAmount: ethers.formatEther(testAmount),
                outputAmount: ethers.formatEther(bestSell.amountOut),
                profitWETH: ethers.formatEther(profit),
                profitPercent: profitPercent.toFixed(4),
                timestamp: new Date().toISOString()
            };
            
            opps.push(opp);
            opportunities.push(opp);
            stats.opportunitiesFound++;
            
            if (profitPercent > 0.5) {
                stats.profitableOpportunities++;
                log(`💎 ARBITRAGE FOUND on ${chain.name}!`, 'OPPORTUNITY');
                log(`   Buy on ${bestBuy.dex}, Sell on ${bestSell.dex}`, 'OPPORTUNITY');
                log(`   Profit: ${profitPercent.toFixed(2)}% (${ethers.formatEther(profit)} WETH)`, 'PROFIT');
                stats.lastOpportunity = opp;
            }
        }
    }
    
    return opps;
}

// ============= CROSS-DEX SAME-CHAIN EXECUTION =============
async function executeArbitrage(chainKey, opportunity) {
    // For now, log what we would do
    // Real execution needs flash loan contract deployment
    log(`Would execute: Buy on ${opportunity.buyDex}, Sell on ${opportunity.sellDex}`, 'EXECUTE');
    log(`Expected profit: ${opportunity.profitPercent}%`, 'EXECUTE');
    
    // TODO: Deploy and use flash loan arbitrage contract
    return false;
}

// ============= MAIN LOOP =============
async function runBot() {
    console.log('');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('   MULTI-CHAIN LONG-TAIL ARBITRAGE BOT');
    console.log('   Targeting: Base, Mode, Scroll, Linea');
    console.log('   Strategy: Cross-DEX arbitrage on low-competition tokens');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('');
    
    await initializeChains();
    
    if (Object.keys(providers).length === 0) {
        log('No chains connected. Check RPC endpoints.', 'ERROR');
        process.exit(1);
    }
    
    log('Starting arbitrage scan loop...', 'INFO');
    log('───────────────────────────────────────────────────────────────', 'INFO');
    
    while (true) {
        for (const chainKey of Object.keys(providers)) {
            try {
                stats.scansCompleted++;
                const opps = await findArbitrageOpportunities(chainKey);
                
                if (opps.length > 0) {
                    log(`${CHAINS[chainKey].name}: Found ${opps.length} opportunities`, 'SCAN');
                }
                
                // Save results periodically
                if (stats.scansCompleted % 10 === 0) {
                    saveResults();
                    log(`Scan #${stats.scansCompleted} | Opportunities: ${stats.opportunitiesFound} | Profitable: ${stats.profitableOpportunities}`, 'INFO');
                }
                
            } catch (e) {
                log(`${CHAINS[chainKey].name} error: ${e.message}`, 'ERROR');
            }
            
            // Small delay between chains
            await new Promise(r => setTimeout(r, 1000));
        }
        
        // Delay between full cycles
        await new Promise(r => setTimeout(r, 5000));
    }
}

// ============= START =============
runBot().catch(e => {
    log(`Fatal error: ${e.message}`, 'ERROR');
    process.exit(1);
});
