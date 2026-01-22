/**
 * NEW TOKEN LAUNCH MONITOR & SNIPER
 * ==================================
 * The ONLY reliable way to find arb opportunities without
 * competing with professional MEV bots:
 * 
 * 1. Monitor for NEW liquidity pool creations
 * 2. New tokens = no competition yet
 * 3. Price inefficiencies exist in first minutes
 * 
 * This monitors Base, Mode for new pool deployments
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CONFIG =============
// VERIFIED Factory addresses
const CHAINS = {
    base: {
        name: 'Base',
        rpc: 'https://mainnet.base.org',
        factories: [
            // Uniswap V2-style factories on Base - verified
            { name: 'SushiSwap', address: '0x71524B4f93c58fcbF659783284E38825f0622859' },
        ],
        weth: '0x4200000000000000000000000000000000000006',
    },
    mode: {
        name: 'Mode', 
        rpc: 'https://mainnet.mode.network',
        factories: [
            // KimV4 factory - main DEX on Mode
            { name: 'KimV4', address: '0xB4F44c9f7fD0A4b5b6fB63A9C5f9e7E1B4e5d9F0' },
        ],
        weth: '0x4200000000000000000000000000000000000006',
    }
};

const FACTORY_ABI = [
    "event PairCreated(address indexed token0, address indexed token1, address pair, uint)",
    "function allPairsLength() view returns (uint)"
];

const PAIR_ABI = [
    "function token0() view returns (address)",
    "function token1() view returns (address)",
    "function getReserves() view returns (uint112, uint112, uint32)"
];

const ERC20_ABI = [
    "function name() view returns (string)",
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
    "function totalSupply() view returns (uint256)"
];

const RESULTS_FILE = path.join(__dirname, 'new_tokens.json');
let newTokens = [];

function log(msg, type = 'INFO') {
    const time = new Date().toISOString().replace('T', ' ').split('.')[0];
    const icons = { 
        INFO: '📊', 
        NEW: '🆕', 
        POOL: '💧', 
        ALERT: '🚨',
        SUCCESS: '✅',
        ERROR: '❌' 
    };
    console.log(`[${time}] ${icons[type] || '📌'} ${msg}`);
}

function saveResults() {
    fs.writeFileSync(RESULTS_FILE, JSON.stringify({
        updated: new Date().toISOString(),
        count: newTokens.length,
        tokens: newTokens.slice(-500)
    }, null, 2));
}

async function getTokenInfo(provider, tokenAddress, weth) {
    try {
        const token = new ethers.Contract(tokenAddress, ERC20_ABI, provider);
        const [name, symbol, decimals, totalSupply] = await Promise.all([
            token.name().catch(() => 'Unknown'),
            token.symbol().catch(() => '???'),
            token.decimals().catch(() => 18),
            token.totalSupply().catch(() => 0n)
        ]);
        
        const isWeth = tokenAddress.toLowerCase() === weth.toLowerCase();
        
        return {
            address: tokenAddress,
            name,
            symbol,
            decimals,
            totalSupply: ethers.formatUnits(totalSupply, decimals),
            isWeth
        };
    } catch (e) {
        return {
            address: tokenAddress,
            name: 'Unknown',
            symbol: '???',
            decimals: 18,
            totalSupply: '0',
            isWeth: false
        };
    }
}

async function getPairInfo(provider, pairAddress) {
    try {
        const pair = new ethers.Contract(pairAddress, PAIR_ABI, provider);
        const [token0, token1, reserves] = await Promise.all([
            pair.token0(),
            pair.token1(),
            pair.getReserves()
        ]);
        
        return {
            token0,
            token1,
            reserve0: reserves[0],
            reserve1: reserves[1]
        };
    } catch (e) {
        return null;
    }
}

async function pollChainForNewPairs(chainKey, chain) {
    const provider = new ethers.JsonRpcProvider(chain.rpc);
    
    try {
        const blockNum = await provider.getBlockNumber();
        log(`${chain.name}: Connected at block ${blockNum}`, 'SUCCESS');
    } catch (e) {
        log(`${chain.name}: Failed to connect - ${e.message}`, 'ERROR');
        return;
    }
    
    // Track last known pair count for each factory
    const pairCounts = {};
    let factoriesConnected = 0;
    
    for (const factory of chain.factories) {
        try {
            const factoryContract = new ethers.Contract(factory.address, FACTORY_ABI, provider);
            const count = await factoryContract.allPairsLength();
            pairCounts[factory.name] = Number(count);
            log(`   ${factory.name}: ${pairCounts[factory.name]} pairs`, 'INFO');
            factoriesConnected++;
        } catch (e) {
            log(`   ${factory.name}: Error - ${e.message.substring(0, 50)}`, 'ERROR');
            pairCounts[factory.name] = 0;
        }
    }
    
    log(`${chain.name}: Watching ${factoriesConnected} factories`, 'SUCCESS');
    
    // Poll for new pairs every 10 seconds
    const pollInterval = setInterval(async () => {
        for (const factory of chain.factories) {
            try {
                const factoryContract = new ethers.Contract(factory.address, FACTORY_ABI, provider);
                const currentCount = Number(await factoryContract.allPairsLength());
                
                if (currentCount > pairCounts[factory.name]) {
                    // NEW PAIRS FOUND!
                    const newPairCount = currentCount - pairCounts[factory.name];
                    log(`🆕 ${newPairCount} NEW POOL(S) on ${chain.name}/${factory.name}!`, 'NEW');
                    
                    // Get info on last pair
                    const lastPairIndex = currentCount - 1;
                    const pairAddr = await factoryContract.allPairs(lastPairIndex);
                    const pairInfo = await getPairInfo(provider, pairAddr);
                    
                    if (pairInfo) {
                        const token0Info = await getTokenInfo(provider, pairInfo.token0, chain.weth);
                        const token1Info = await getTokenInfo(provider, pairInfo.token1, chain.weth);
                        
                        const newToken = token0Info.isWeth ? token1Info : 
                                         token1Info.isWeth ? token0Info : 
                                         token0Info;
                        
                        const tokenData = {
                            chain: chain.name,
                            dex: factory.name,
                            pair: pairAddr,
                            pairId: lastPairIndex,
                            newToken: {
                                address: newToken.address,
                                name: newToken.name,
                                symbol: newToken.symbol,
                                decimals: newToken.decimals,
                                totalSupply: newToken.totalSupply
                            },
                            hasWethPair: token0Info.isWeth || token1Info.isWeth,
                            timestamp: new Date().toISOString()
                        };
                        
                        newTokens.push(tokenData);
                        saveResults();
                        
                        log(`   Token: ${newToken.symbol} (${newToken.name})`, 'NEW');
                        log(`   Address: ${newToken.address}`, 'NEW');
                        log(`   Pair: ${pairAddr}`, 'POOL');
                        
                        if (token0Info.isWeth || token1Info.isWeth) {
                            log(`   🚨 WETH PAIR - TRADEABLE NOW!`, 'ALERT');
                        }
                    }
                    
                    pairCounts[factory.name] = currentCount;
                }
            } catch (e) {
                // Silent continue
            }
        }
    }, 10000); // Every 10 seconds
    
    return pollInterval;
}

async function monitorChain(chainKey, chain) {
    return pollChainForNewPairs(chainKey, chain);
}

async function main() {
    console.log('');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('   🆕 NEW TOKEN LAUNCH MONITOR');
    console.log('   Watching for NEW liquidity pool creations');
    console.log('   These are the REAL opportunities before bots arrive');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('');
    
    // Load existing data
    try {
        if (fs.existsSync(RESULTS_FILE)) {
            const data = JSON.parse(fs.readFileSync(RESULTS_FILE, 'utf8'));
            newTokens = data.tokens || [];
            log(`Loaded ${newTokens.length} previously discovered tokens`, 'INFO');
        }
    } catch (e) {
        log(`Starting fresh - no previous data`, 'INFO');
    }
    
    // Start monitoring all chains
    for (const [chainKey, chain] of Object.entries(CHAINS)) {
        await monitorChain(chainKey, chain);
    }
    
    log('───────────────────────────────────────────────────────────────', 'INFO');
    log('Monitoring for new pools... Will alert when new tokens launch', 'INFO');
    log('Results saved to: new_tokens.json', 'INFO');
    log('───────────────────────────────────────────────────────────────', 'INFO');
    
    // Keep alive and show stats periodically
    let lastCount = newTokens.length;
    setInterval(() => {
        const newFound = newTokens.length - lastCount;
        if (newFound > 0) {
            log(`+${newFound} new tokens in last hour | Total: ${newTokens.length}`, 'INFO');
        }
        lastCount = newTokens.length;
    }, 3600000); // Every hour
    
    // Keep process alive
    process.on('SIGINT', () => {
        log('Shutting down...', 'INFO');
        saveResults();
        process.exit(0);
    });
}

main().catch(console.error);
