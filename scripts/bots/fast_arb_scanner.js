/**
 * FAST MULTI-CHAIN ARBITRAGE SCANNER
 * ===================================
 * Optimized for SPEED - uses known popular tokens
 * Scans 4 chains continuously for cross-DEX opportunities
 * ZERO capital needed - uses flash loans
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= KNOWN TOKENS (faster than discovery) =============
const TOKENS = {
    base: {
        WETH: '0x4200000000000000000000000000000000000006',
        USDC: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913',
        DAI: '0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb',
        USDbC: '0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA',
        cbETH: '0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22',
        AERO: '0x940181a94A35A4569E4529A3CDfB74e38FD98631',
        BRETT: '0x532f27101965dd16442E59d40670FaF5eBB142E4',
        DEGEN: '0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed',
        TOSHI: '0xAC1Bd2486aAf3B5C0fc3Fd868558b082a531B2B4',
        HIGHER: '0x0578d8A44db98B23BF096A382e016e29a5Ce0ffe',
    },
    mode: {
        WETH: '0x4200000000000000000000000000000000000006',
        USDC: '0xd988097fb8612cc24eeC14542bC03424c656005f',
        USDT: '0xf0F161fDA2712DB8b566946122a5af183995e2eD',
        MODE: '0xDfc7C877a950e49D2610114102175A06C2e3167a',
        ionUSDC: '0x2BE717340023C9e14C1Bb12cb3ecBcfd3c3fB038',
        STONE: '0x80137510979822322193FC997d400D5A6C747bf7',
    },
    scroll: {
        WETH: '0x5300000000000000000000000000000000000004',
        USDC: '0x06eFdBFf2a14a7c8E15944D1F4A48F9F95F663A4',
        USDT: '0xf55BEC9cafDbE8730f096Aa55dad6D22d44099Df',
        wstETH: '0xf610A9dfB7C89644979b4A0f27063E9e7d7Cda32',
        LUSD: '0xeDEAbc3A1e7D21fE835FFA6f83a710c70BB1a051',
    },
    linea: {
        WETH: '0xe5D7C2a44FfDDf6b295A15c148167daaAf5Cf34f',
        USDC: '0x176211869cA2b568f2A7D4EE941E073a821EE1ff',
        USDT: '0xA219439258ca9da29E9Cc4cE5596924745e12B93',
        DAI: '0x4AF15ec2A0BD43Db75dd04E62FAA3B8EF36b00d5',
        wstETH: '0xB5beDd42000b71FddE22D3eE8a79Bd49A568fC8F',
    }
};

// ============= DEX CONFIGS =============
const DEXS = {
    base: [
        { name: 'Aerodrome', router: '0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43', type: 'solidly' },
        { name: 'BaseSwap', router: '0x327Df1E6de05895d2ab08513aaDD9313Fe505d86', type: 'v2' },
        { name: 'SwapBased', router: '0xaaa3b1F1bd7BCc97fD1917c18ADE665C5D31F066', type: 'v2' },
        { name: 'UniswapV3', quoter: '0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a', type: 'v3' },
    ],
    mode: [
        { name: 'SupSwap', router: '0x5951479fE3235b689E392E9BC6E968CE10637A52', type: 'v2' },
        { name: 'SwapMode', router: '0xc1e624C810D297FD70eF53B0E08F44FABE468591', type: 'v2' },
    ],
    scroll: [
        { name: 'SyncSwap', router: '0x80e38291e06339d10AAB483C65695D004dBD5C69', type: 'syncswap' },
        { name: 'SpaceFi', router: '0x18b71386418A9FCa5Ae7165E31c385a5a578F079', type: 'v2' },
        { name: 'Zebra', router: '0x0122960d6E391478bfE8fb2408Ba412D5600f621', type: 'v2' },
    ],
    linea: [
        { name: 'Lynex', router: '0x610D2f07b7EdC67565160F587F37636194C34E74', type: 'v2' },
        { name: 'Velocore', router: '0x1d0188c4B276A09366D05d6Be06aF61a73bC7535', type: 'velocore' },
        { name: 'HorizonDEX', router: '0x272E156Df8DA513C69cB41cC7A99185D53F926Bb', type: 'v2' },
    ]
};

const RPCS = {
    base: 'https://mainnet.base.org',
    mode: 'https://mainnet.mode.network',
    scroll: 'https://rpc.scroll.io',
    linea: 'https://rpc.linea.build'
};

const ROUTER_ABI = [
    "function getAmountsOut(uint amountIn, address[] calldata path) view returns (uint[] memory amounts)"
];

// ============= STATE =============
let providers = {};
let stats = {
    scans: 0,
    opportunities: 0,
    profitable: 0,
    bestProfit: 0,
    lastOpportunity: null
};

const RESULTS_FILE = path.join(__dirname, 'arb_opportunities.json');
let allOpportunities = [];

function log(msg, type = 'INFO') {
    const time = new Date().toISOString().split('T')[1].split('.')[0];
    const icons = { INFO: '📊', SCAN: '🔍', FOUND: '💎', PROFIT: '💰', ERROR: '❌' };
    console.log(`[${time}] ${icons[type] || '📌'} ${msg}`);
}

function saveResults() {
    fs.writeFileSync(RESULTS_FILE, JSON.stringify({
        updated: new Date().toISOString(),
        stats,
        opportunities: allOpportunities.slice(-200)
    }, null, 2));
}

// ============= PRICE FETCHING =============
async function getV2Price(provider, routerAddr, tokenIn, tokenOut, amountIn) {
    try {
        const router = new ethers.Contract(routerAddr, ROUTER_ABI, provider);
        const amounts = await router.getAmountsOut(amountIn, [tokenIn, tokenOut]);
        return amounts[1];
    } catch {
        return null;
    }
}

// ============= ARBITRAGE DETECTION =============
async function scanChain(chainKey) {
    const provider = providers[chainKey];
    const tokens = TOKENS[chainKey];
    const dexs = DEXS[chainKey];
    const weth = tokens.WETH;
    
    // Test with 0.1 WETH
    const testAmount = ethers.parseEther('0.1');
    const opportunities = [];
    
    // Check each non-WETH token
    for (const [symbol, tokenAddr] of Object.entries(tokens)) {
        if (symbol === 'WETH') continue;
        
        const prices = {};
        
        // Get price on each DEX (WETH -> Token)
        for (const dex of dexs) {
            if (dex.type === 'v2' || dex.type === 'solidly') {
                const amountOut = await getV2Price(provider, dex.router, weth, tokenAddr, testAmount);
                if (amountOut && amountOut > 0n) {
                    prices[dex.name] = { buy: amountOut };
                }
            }
        }
        
        // Get reverse prices (Token -> WETH)
        for (const dex of dexs) {
            if (!prices[dex.name]) continue;
            if (dex.type === 'v2' || dex.type === 'solidly') {
                const amountBack = await getV2Price(provider, dex.router, tokenAddr, weth, prices[dex.name].buy);
                if (amountBack && amountBack > 0n) {
                    prices[dex.name].sell = amountBack;
                }
            }
        }
        
        // Find cross-DEX opportunities
        const dexNames = Object.keys(prices).filter(d => prices[d].buy && prices[d].sell);
        
        for (let i = 0; i < dexNames.length; i++) {
            for (let j = 0; j < dexNames.length; j++) {
                if (i === j) continue;
                
                const buyDex = dexNames[i];
                const sellDex = dexNames[j];
                
                // Buy on buyDex, get tokens, sell on sellDex
                const tokensReceived = prices[buyDex].buy;
                
                // Check sell price on sellDex for those tokens
                const sellRouter = dexs.find(d => d.name === sellDex);
                if (!sellRouter) continue;
                
                const wethBack = await getV2Price(provider, sellRouter.router, tokenAddr, weth, tokensReceived);
                if (!wethBack) continue;
                
                const profit = wethBack - testAmount;
                const profitPercent = (Number(profit) * 100) / Number(testAmount);
                
                if (profitPercent > 0.1) {
                    const opp = {
                        chain: chainKey.toUpperCase(),
                        token: symbol,
                        buyOn: buyDex,
                        sellOn: sellDex,
                        input: '0.1 WETH',
                        output: ethers.formatEther(wethBack) + ' WETH',
                        profit: ethers.formatEther(profit) + ' WETH',
                        profitPct: profitPercent.toFixed(3) + '%',
                        time: new Date().toISOString()
                    };
                    
                    opportunities.push(opp);
                    allOpportunities.push(opp);
                    stats.opportunities++;
                    
                    if (profitPercent > 0.5) {
                        stats.profitable++;
                        if (profitPercent > stats.bestProfit) {
                            stats.bestProfit = profitPercent;
                        }
                        log(`💎 ${chainKey.toUpperCase()} ARB: ${symbol} | Buy ${buyDex} → Sell ${sellDex} | +${profitPercent.toFixed(2)}%`, 'FOUND');
                        stats.lastOpportunity = opp;
                    }
                }
            }
        }
    }
    
    return opportunities;
}

// ============= MAIN LOOP =============
async function main() {
    console.log('');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('   ⚡ FAST MULTI-CHAIN ARBITRAGE SCANNER');
    console.log('   Chains: Base, Mode, Scroll, Linea');
    console.log('   Strategy: Cross-DEX price discrepancies');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('');
    
    // Connect to all chains
    for (const [chain, rpc] of Object.entries(RPCS)) {
        try {
            providers[chain] = new ethers.JsonRpcProvider(rpc);
            const block = await providers[chain].getBlockNumber();
            log(`${chain.toUpperCase()}: Connected (block ${block})`, 'INFO');
        } catch (e) {
            log(`${chain.toUpperCase()}: Failed - ${e.message}`, 'ERROR');
        }
    }
    
    if (Object.keys(providers).length === 0) {
        log('No chains connected!', 'ERROR');
        process.exit(1);
    }
    
    log('Starting continuous scan...', 'INFO');
    log('───────────────────────────────────────────────────────────────', 'INFO');
    
    while (true) {
        stats.scans++;
        
        for (const chain of Object.keys(providers)) {
            try {
                await scanChain(chain);
            } catch (e) {
                // Silent continue
            }
        }
        
        // Status every 10 scans
        if (stats.scans % 10 === 0) {
            log(`Scan #${stats.scans} | Found: ${stats.opportunities} | Profitable: ${stats.profitable} | Best: ${stats.bestProfit.toFixed(2)}%`, 'SCAN');
            saveResults();
        }
        
        // Quick delay
        await new Promise(r => setTimeout(r, 2000));
    }
}

main().catch(console.error);
