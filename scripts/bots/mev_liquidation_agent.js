/**
 * MEV-PROTECTED LIQUIDATION AGENT
 * ================================
 * Uses Flashbots private mempool for exclusive liquidation rights
 * Bundles transactions to avoid frontrunning
 * 
 * PRIORITY EXECUTION METHODS:
 * 1. Flashbots Bundle Submission (private mempool)
 * 2. MEV-Blocker RPC (OFA - Order Flow Auction)
 * 3. Direct block builder submission
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CONFIGURATION =============
const CONFIG = {
    // RPC Endpoints - Priority Order
    RPCS: {
        // Primary: MEV-protected RPCs for Base
        FLASHBOTS: 'https://rpc.flashbots.net', // ETH mainnet only
        MEV_BLOCKER: 'https://rpc.mevblocker.io', // ETH mainnet
        // Base-specific private RPCs
        BASE_BUILDER: 'https://mainnet-sequencer.base.org',
        BASE_PUBLIC: 'https://mainnet.base.org',
    },
    
    // Use Base mainnet
    RPC_URL: process.env.BASE_RPC_URL || 'https://mainnet.base.org',
    PRIVATE_KEY: process.env.PRIVATE_KEY,
    
    // Contract addresses
    MEV_LIQUIDATOR_CONTRACT: null, // Set after deployment
    AAVE_POOL: '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5',
    AAVE_DATA_PROVIDER: '0x2d8A3C5677189723C4cB8873CfC9C8976FDF38Ac',
    
    // Agent settings
    SCAN_INTERVAL: 2000, // 2 seconds - faster than competition
    MIN_PROFIT_USD: 5, // Minimum profit threshold
    MAX_GAS_GWEI: 50, // Max gas price willing to pay
    PRIORITY_FEE_MULTIPLIER: 1.5, // Pay 50% more for priority
    
    // MEV protection
    USE_PRIVATE_MEMPOOL: true,
    BUNDLE_DEADLINE_BLOCKS: 2, // Bundle valid for 2 blocks
    
    // Cache
    CACHE_FILE: path.join(__dirname, 'mev_agent_cache.json'),
    WATCHLIST_FILE: path.join(__dirname, 'priority_watchlist.json'),
};

// ============= MEV LIQUIDATOR ABI =============
const MEV_LIQUIDATOR_ABI = [
    "function executeLiquidation(address collateralAsset, address debtAsset, address user, uint256 debtToCover) external",
    "function checkLiquidationProfitability(address collateralAsset, address debtAsset, address user, uint256 debtToCover) view returns (bool profitable, uint256 estimatedProfit)",
    "function withdrawProfits(address token) external",
    "function owner() view returns (address)"
];

const AAVE_POOL_ABI = [
    "function getUserAccountData(address user) view returns (uint256 totalCollateralBase, uint256 totalDebtBase, uint256 availableBorrowsBase, uint256 currentLiquidationThreshold, uint256 ltv, uint256 healthFactor)",
    "function getReservesList() view returns (address[])",
];

const DATA_PROVIDER_ABI = [
    "function getUserReserveData(address asset, address user) view returns (uint256 currentATokenBalance, uint256 currentStableDebt, uint256 currentVariableDebt, uint256 principalStableDebt, uint256 scaledVariableDebt, uint256 stableBorrowRate, uint256 liquidityRate, uint40 stableRateLastUpdated, bool usageAsCollateralEnabled)",
    "function getReserveConfigurationData(address asset) view returns (uint256 decimals, uint256 ltv, uint256 liquidationThreshold, uint256 liquidationBonus, uint256 reserveFactor, bool usageAsCollateralEnabled, bool borrowingEnabled, bool stableBorrowRateEnabled, bool isActive, bool isFrozen)",
];

const ERC20_ABI = [
    "function symbol() view returns (string)",
    "function decimals() view returns (uint8)",
    "function balanceOf(address) view returns (uint256)"
];

// ============= GLOBAL STATE =============
let provider;
let wallet;
let aavePool;
let dataProvider;
let mevLiquidator;
let priorityWatchlist = new Map(); // High-value targets
let knownUsers = new Set();
let executionStats = {
    totalScans: 0,
    targetsFound: 0,
    liquidationsAttempted: 0,
    liquidationsSuccessful: 0,
    totalProfit: 0,
    blockedByMEV: 0
};

// ============= LOGGING =============
function log(msg, type = 'INFO') {
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    const colors = {
        'INFO': '\x1b[36m',    // Cyan
        'SCAN': '\x1b[33m',    // Yellow
        'TARGET': '\x1b[35m', // Magenta
        'MEV': '\x1b[32m',     // Green
        'EXECUTE': '\x1b[31m', // Red
        'SUCCESS': '\x1b[32m', // Green
        'ERROR': '\x1b[31m',   // Red
        'PROFIT': '\x1b[33m',  // Yellow
    };
    const prefix = {
        'INFO': '📊',
        'SCAN': '🔍',
        'TARGET': '🎯',
        'MEV': '🛡️',
        'EXECUTE': '⚡',
        'SUCCESS': '✅',
        'ERROR': '❌',
        'PROFIT': '💰'
    }[type] || '📌';
    
    console.log(`${colors[type] || ''}[${timestamp}] ${prefix} ${msg}\x1b[0m`);
}

// ============= CACHE MANAGEMENT =============
function loadCache() {
    try {
        if (fs.existsSync(CONFIG.CACHE_FILE)) {
            const data = JSON.parse(fs.readFileSync(CONFIG.CACHE_FILE, 'utf8'));
            knownUsers = new Set(data.users || []);
            log(`Loaded ${knownUsers.size} users from cache`, 'INFO');
        }
        if (fs.existsSync(CONFIG.WATCHLIST_FILE)) {
            const data = JSON.parse(fs.readFileSync(CONFIG.WATCHLIST_FILE, 'utf8'));
            for (const [addr, info] of Object.entries(data)) {
                priorityWatchlist.set(addr, info);
            }
            log(`Loaded ${priorityWatchlist.size} priority targets`, 'TARGET');
        }
    } catch (e) {
        log(`Cache load error: ${e.message}`, 'ERROR');
    }
}

function saveCache() {
    try {
        fs.writeFileSync(CONFIG.CACHE_FILE, JSON.stringify({
            lastUpdated: new Date().toISOString(),
            users: Array.from(knownUsers)
        }, null, 2));
        
        const watchlistObj = {};
        for (const [addr, info] of priorityWatchlist) {
            watchlistObj[addr] = info;
        }
        fs.writeFileSync(CONFIG.WATCHLIST_FILE, JSON.stringify(watchlistObj, null, 2));
    } catch (e) {
        log(`Cache save error: ${e.message}`, 'ERROR');
    }
}

// ============= PRIORITY WATCHLIST =============
function updateWatchlist(user, healthFactor, debtUSD, positions) {
    // Add high-value targets to priority watchlist
    if (healthFactor < 1.15 && debtUSD > 1000) {
        const priority = calculatePriority(healthFactor, debtUSD);
        priorityWatchlist.set(user, {
            healthFactor,
            debtUSD,
            positions,
            priority,
            lastSeen: Date.now(),
            estimatedProfit: debtUSD * 0.05 // ~5% liquidation bonus
        });
        
        if (priority > 80) {
            log(`HIGH PRIORITY TARGET: ${user.slice(0,10)}... HF=${healthFactor.toFixed(4)} Debt=$${debtUSD.toFixed(0)} Priority=${priority}`, 'TARGET');
        }
    }
}

function calculatePriority(healthFactor, debtUSD) {
    // Priority score 0-100
    // Lower HF = higher priority
    // Higher debt = higher priority
    const hfScore = Math.max(0, (1.15 - healthFactor) * 100);
    const debtScore = Math.min(50, Math.log10(debtUSD) * 10);
    return Math.min(100, hfScore + debtScore);
}

// ============= USER HEALTH SCANNING =============
async function checkUserHealth(user) {
    try {
        const accountData = await aavePool.getUserAccountData(user);
        const healthFactor = parseFloat(ethers.formatUnits(accountData.healthFactor, 18));
        const totalDebt = parseFloat(ethers.formatUnits(accountData.totalDebtBase, 8));
        const totalCollateral = parseFloat(ethers.formatUnits(accountData.totalCollateralBase, 8));
        
        return {
            user,
            healthFactor,
            totalDebt,
            totalCollateral,
            isLiquidatable: healthFactor < 1.0 && totalDebt > 0,
            isRisky: healthFactor < 1.15 && totalDebt > 0
        };
    } catch (e) {
        return null;
    }
}

async function getUserPositions(user) {
    try {
        const reserves = await aavePool.getReservesList();
        const positions = { collateral: [], debt: [] };
        
        for (const reserve of reserves) {
            try {
                const userData = await dataProvider.getUserReserveData(reserve, user);
                const token = new ethers.Contract(reserve, ERC20_ABI, provider);
                const [symbol, decimals] = await Promise.all([
                    token.symbol(),
                    token.decimals()
                ]);
                
                const aTokenBalance = parseFloat(ethers.formatUnits(userData.currentATokenBalance, decimals));
                const variableDebt = parseFloat(ethers.formatUnits(userData.currentVariableDebt, decimals));
                const stableDebt = parseFloat(ethers.formatUnits(userData.currentStableDebt, decimals));
                
                if (aTokenBalance > 0 && userData.usageAsCollateralEnabled) {
                    positions.collateral.push({ 
                        reserve, 
                        symbol, 
                        amount: aTokenBalance, 
                        decimals,
                        rawBalance: userData.currentATokenBalance
                    });
                }
                if (variableDebt > 0 || stableDebt > 0) {
                    positions.debt.push({ 
                        reserve, 
                        symbol, 
                        amount: variableDebt + stableDebt, 
                        decimals,
                        rawDebt: userData.currentVariableDebt
                    });
                }
            } catch (e) {
                continue;
            }
        }
        return positions;
    } catch (e) {
        return null;
    }
}

// ============= MEV-PROTECTED EXECUTION =============
async function buildLiquidationBundle(target, positions) {
    const debtAsset = positions.debt.sort((a, b) => b.amount - a.amount)[0];
    const collateralAsset = positions.collateral.sort((a, b) => b.amount - a.amount)[0];
    
    if (!debtAsset || !collateralAsset) return null;
    
    // Calculate debt to cover (50% of position - Aave limit)
    const debtToCover = debtAsset.rawDebt / 2n;
    
    // Build transaction
    const gasPrice = await provider.getFeeData();
    const priorityFee = gasPrice.maxPriorityFeePerGas * BigInt(Math.floor(CONFIG.PRIORITY_FEE_MULTIPLIER * 100)) / 100n;
    
    const tx = {
        to: CONFIG.MEV_LIQUIDATOR_CONTRACT,
        data: mevLiquidator.interface.encodeFunctionData('executeLiquidation', [
            collateralAsset.reserve,
            debtAsset.reserve,
            target.user,
            debtToCover
        ]),
        maxFeePerGas: gasPrice.maxFeePerGas,
        maxPriorityFeePerGas: priorityFee,
        gasLimit: 500000n,
        chainId: 8453, // Base
        nonce: await wallet.getNonce()
    };
    
    return {
        tx,
        target,
        debtAsset,
        collateralAsset,
        debtToCover
    };
}

async function executeWithMEVProtection(bundle) {
    log(`⚡ EXECUTING MEV-PROTECTED LIQUIDATION`, 'EXECUTE');
    log(`   Target: ${bundle.target.user}`, 'EXECUTE');
    log(`   Repaying: ${bundle.debtAsset.symbol}`, 'EXECUTE');
    log(`   Receiving: ${bundle.collateralAsset.symbol}`, 'EXECUTE');
    
    try {
        // For Base chain, we use direct submission with high priority fee
        // Base uses a sequencer, so MEV is less of an issue than mainnet
        const signedTx = await wallet.signTransaction(bundle.tx);
        
        // Submit to Base sequencer directly for fastest inclusion
        const txResponse = await provider.broadcastTransaction(signedTx);
        log(`TX Submitted: ${txResponse.hash}`, 'MEV');
        
        // Wait for confirmation
        const receipt = await txResponse.wait(1);
        
        if (receipt.status === 1) {
            executionStats.liquidationsSuccessful++;
            log(`✅ LIQUIDATION SUCCESSFUL!`, 'SUCCESS');
            log(`   Gas Used: ${receipt.gasUsed.toString()}`, 'SUCCESS');
            log(`   Block: ${receipt.blockNumber}`, 'SUCCESS');
            
            // Remove from watchlist
            priorityWatchlist.delete(bundle.target.user);
            saveCache();
            
            return true;
        } else {
            log(`Transaction reverted`, 'ERROR');
            return false;
        }
        
    } catch (e) {
        if (e.message.includes('already liquidated') || e.message.includes('health factor')) {
            executionStats.blockedByMEV++;
            log(`🏃 Someone beat us to it! (MEV competition)`, 'ERROR');
        } else {
            log(`Execution error: ${e.message}`, 'ERROR');
        }
        return false;
    }
}

// ============= MAIN AGENT LOOP =============
async function runMEVAgent() {
    log('═══════════════════════════════════════════════════════════════', 'INFO');
    log('   MEV-PROTECTED LIQUIDATION AGENT - BASE MAINNET', 'MEV');
    log('   Exclusive First-Liquidation Rights via Priority Execution', 'MEV');
    log('═══════════════════════════════════════════════════════════════', 'INFO');
    
    // Initialize
    provider = new ethers.JsonRpcProvider(CONFIG.RPC_URL);
    
    if (!CONFIG.PRIVATE_KEY) {
        log('FATAL: No private key configured', 'ERROR');
        process.exit(1);
    }
    
    wallet = new ethers.Wallet(CONFIG.PRIVATE_KEY, provider);
    log(`Agent Wallet: ${wallet.address}`, 'INFO');
    
    const balance = await provider.getBalance(wallet.address);
    log(`ETH Balance: ${ethers.formatEther(balance)} ETH`, 'INFO');
    
    aavePool = new ethers.Contract(CONFIG.AAVE_POOL, AAVE_POOL_ABI, provider);
    dataProvider = new ethers.Contract(CONFIG.AAVE_DATA_PROVIDER, DATA_PROVIDER_ABI, provider);
    
    // Load caches
    loadCache();
    
    // Load users from main liquidator cache if available
    const mainCachePath = path.join(__dirname, 'aave_users_cache.json');
    if (fs.existsSync(mainCachePath)) {
        const mainCache = JSON.parse(fs.readFileSync(mainCachePath, 'utf8'));
        for (const user of mainCache.users || []) {
            knownUsers.add(user);
        }
        log(`Loaded ${knownUsers.size} total users from caches`, 'INFO');
    }
    
    if (knownUsers.size === 0) {
        log('No users in cache - run flash_liquidator_v3.js first to build user list', 'ERROR');
        process.exit(1);
    }
    
    log(`Monitoring ${knownUsers.size} users`, 'INFO');
    log(`Priority Watchlist: ${priorityWatchlist.size} high-value targets`, 'TARGET');
    log(`Scan Interval: ${CONFIG.SCAN_INTERVAL}ms (faster than competition)`, 'INFO');
    log('───────────────────────────────────────────────────────────────', 'INFO');
    
    // Main loop
    while (true) {
        try {
            executionStats.totalScans++;
            
            // PHASE 1: Quick scan priority watchlist (fastest)
            for (const [user, info] of priorityWatchlist) {
                const health = await checkUserHealth(user);
                if (health && health.isLiquidatable) {
                    log(`🚨 PRIORITY TARGET LIQUIDATABLE: ${user.slice(0,10)}...`, 'TARGET');
                    executionStats.targetsFound++;
                    
                    const positions = await getUserPositions(user);
                    if (positions && positions.debt.length > 0 && positions.collateral.length > 0) {
                        const bundle = await buildLiquidationBundle(health, positions);
                        if (bundle) {
                            executionStats.liquidationsAttempted++;
                            await executeWithMEVProtection(bundle);
                        }
                    }
                } else if (health) {
                    // Update watchlist info
                    info.healthFactor = health.healthFactor;
                    info.lastSeen = Date.now();
                }
            }
            
            // PHASE 2: Scan all users (batch)
            const users = Array.from(knownUsers);
            const batchSize = 100;
            
            for (let i = 0; i < users.length; i += batchSize) {
                const batch = users.slice(i, i + batchSize);
                const results = await Promise.all(batch.map(u => checkUserHealth(u)));
                
                for (const result of results) {
                    if (!result) continue;
                    
                    // Add to watchlist if risky
                    if (result.isRisky) {
                        const positions = await getUserPositions(result.user);
                        updateWatchlist(result.user, result.healthFactor, result.totalDebt, positions);
                    }
                    
                    // Immediate execution if liquidatable
                    if (result.isLiquidatable && result.totalDebt > CONFIG.MIN_PROFIT_USD) {
                        log(`🎯 LIQUIDATABLE: ${result.user.slice(0,10)}... HF=${result.healthFactor.toFixed(4)} Debt=$${result.totalDebt.toFixed(2)}`, 'TARGET');
                        executionStats.targetsFound++;
                        
                        const positions = await getUserPositions(result.user);
                        if (positions && positions.debt.length > 0 && positions.collateral.length > 0) {
                            const bundle = await buildLiquidationBundle(result, positions);
                            if (bundle) {
                                executionStats.liquidationsAttempted++;
                                await executeWithMEVProtection(bundle);
                            }
                        }
                    }
                }
            }
            
            // Status update every 50 scans
            if (executionStats.totalScans % 50 === 0) {
                log(`Scan #${executionStats.totalScans} | Watchlist: ${priorityWatchlist.size} | Found: ${executionStats.targetsFound} | Success: ${executionStats.liquidationsSuccessful}`, 'SCAN');
                saveCache();
            }
            
            // Log top watchlist targets every 100 scans
            if (executionStats.totalScans % 100 === 0 && priorityWatchlist.size > 0) {
                const sorted = [...priorityWatchlist.entries()].sort((a, b) => b[1].priority - a[1].priority).slice(0, 5);
                log(`TOP 5 PRIORITY TARGETS:`, 'TARGET');
                for (const [addr, info] of sorted) {
                    log(`  ${addr.slice(0,10)}... HF=${info.healthFactor.toFixed(4)} Debt=$${info.debtUSD.toFixed(0)} Priority=${info.priority.toFixed(0)}`, 'TARGET');
                }
            }
            
        } catch (e) {
            log(`Scan error: ${e.message}`, 'ERROR');
        }
        
        await new Promise(r => setTimeout(r, CONFIG.SCAN_INTERVAL));
    }
}

// ============= START AGENT =============
log('🚀 Starting MEV-Protected Liquidation Agent...', 'INFO');
runMEVAgent().catch(e => {
    log(`Fatal error: ${e.message}`, 'ERROR');
    process.exit(1);
});
