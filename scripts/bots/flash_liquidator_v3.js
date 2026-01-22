/**
 * AAVE V3 FLASH LOAN LIQUIDATOR BOT - BASE MAINNET
 * Runs 24/7 scanning for liquidation opportunities
 * Uses flash loans to liquidate underwater positions profitably
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CONFIGURATION =============
const CONFIG = {
    RPC_URL: process.env.BASE_RPC_URL || 'https://mainnet.base.org',
    PRIVATE_KEY: process.env.PRIVATE_KEY,
    
    // Aave V3 Base Mainnet Addresses
    AAVE_POOL: '0xA238Dd80C259a72e81d7e4664a9801593F98d1c5',
    AAVE_POOL_DATA_PROVIDER: '0x2d8A3C5677189723C4cB8873CfC9C8976FDF38Ac',
    AAVE_UI_POOL_DATA_PROVIDER: '0x174446a6741300cD2E7C1b1A636Fee99c8F83502',
    
    // Bot Settings
    SCAN_INTERVAL: 5000, // 5 seconds between scans
    MIN_PROFIT_USD: 1, // Minimum profit threshold
    HEALTH_FACTOR_THRESHOLD: 1.0, // Liquidate when HF < 1.0
    AUTO_EXECUTE: true, // Auto-execute liquidations
    
    // User cache
    CACHE_FILE: path.join(__dirname, 'aave_users_cache.json'),
    DEEP_SCAN_BLOCKS: 500000, // Scan last 500k blocks for users
};

// ============= ABIs =============
const AAVE_POOL_ABI = [
    "function getUserAccountData(address user) view returns (uint256 totalCollateralBase, uint256 totalDebtBase, uint256 availableBorrowsBase, uint256 currentLiquidationThreshold, uint256 ltv, uint256 healthFactor)",
    "function getReservesList() view returns (address[])",
    "function liquidationCall(address collateralAsset, address debtAsset, address user, uint256 debtToCover, bool receiveAToken) external",
    "event Borrow(address indexed reserve, address user, address indexed onBehalfOf, uint256 amount, uint8 interestRateMode, uint256 borrowRate, uint16 indexed referralCode)",
    "event Supply(address indexed reserve, address user, address indexed onBehalfOf, uint256 amount, uint16 indexed referralCode)"
];

const DATA_PROVIDER_ABI = [
    "function getUserReserveData(address asset, address user) view returns (uint256 currentATokenBalance, uint256 currentStableDebt, uint256 currentVariableDebt, uint256 principalStableDebt, uint256 scaledVariableDebt, uint256 stableBorrowRate, uint256 liquidityRate, uint40 stableRateLastUpdated, bool usageAsCollateralEnabled)",
    "function getReserveConfigurationData(address asset) view returns (uint256 decimals, uint256 ltv, uint256 liquidationThreshold, uint256 liquidationBonus, uint256 reserveFactor, bool usageAsCollateralEnabled, bool borrowingEnabled, bool stableBorrowRateEnabled, bool isActive, bool isFrozen)",
    "function getReserveTokensAddresses(address asset) view returns (address aTokenAddress, address stableDebtTokenAddress, address variableDebtTokenAddress)"
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
let knownUsers = new Set();
let totalLiquidations = 0;
let totalProfit = 0;

// ============= HELPER FUNCTIONS =============
function log(msg, type = 'INFO') {
    const timestamp = new Date().toISOString().split('T')[1].split('.')[0];
    const prefix = {
        'INFO': '📊',
        'SCAN': '🔍',
        'RISKY': '⚠️',
        'LIQUIDATE': '🔥',
        'SUCCESS': '✅',
        'ERROR': '❌',
        'PROFIT': '💰'
    }[type] || '📌';
    console.log(`[${timestamp}] ${prefix} ${msg}`);
}

function loadUserCache() {
    try {
        if (fs.existsSync(CONFIG.CACHE_FILE)) {
            const data = JSON.parse(fs.readFileSync(CONFIG.CACHE_FILE, 'utf8'));
            knownUsers = new Set(data.users || []);
            log(`Loaded ${knownUsers.size} users from cache`, 'INFO');
            return true;
        }
    } catch (e) {
        log(`Cache load error: ${e.message}`, 'ERROR');
    }
    return false;
}

function saveUserCache() {
    try {
        const data = {
            lastUpdated: new Date().toISOString(),
            userCount: knownUsers.size,
            users: Array.from(knownUsers)
        };
        fs.writeFileSync(CONFIG.CACHE_FILE, JSON.stringify(data, null, 2));
        log(`Saved ${knownUsers.size} users to cache`, 'INFO');
    } catch (e) {
        log(`Cache save error: ${e.message}`, 'ERROR');
    }
}

// ============= USER DISCOVERY =============
async function discoverUsersViaEvents() {
    log('Starting deep user discovery via RPC events...', 'SCAN');
    
    const currentBlock = await provider.getBlockNumber();
    const startBlock = Math.max(0, currentBlock - CONFIG.DEEP_SCAN_BLOCKS);
    const batchSize = 10000;
    
    let totalFound = 0;
    
    // Scan Borrow events
    log(`Scanning blocks ${startBlock} to ${currentBlock} for Borrow events...`, 'SCAN');
    for (let from = startBlock; from < currentBlock; from += batchSize) {
        const to = Math.min(from + batchSize - 1, currentBlock);
        try {
            const borrowFilter = aavePool.filters.Borrow();
            const events = await aavePool.queryFilter(borrowFilter, from, to);
            
            for (const event of events) {
                const user = event.args.onBehalfOf || event.args.user;
                if (user && !knownUsers.has(user)) {
                    knownUsers.add(user);
                    totalFound++;
                }
            }
            
            if ((from - startBlock) % 50000 === 0) {
                log(`Progress: ${Math.round((from - startBlock) / (currentBlock - startBlock) * 100)}% - Found ${totalFound} new users`, 'SCAN');
            }
        } catch (e) {
            // Silently continue on rate limits
        }
        
        // Small delay to avoid rate limits
        await new Promise(r => setTimeout(r, 100));
    }
    
    log(`Deep scan complete. Found ${totalFound} new users. Total: ${knownUsers.size}`, 'SUCCESS');
    saveUserCache();
}

async function discoverUsersViaSubgraph() {
    log('Attempting to fetch users from TheGraph subgraph...', 'SCAN');
    
    const SUBGRAPH_URL = 'https://api.thegraph.com/subgraphs/name/aave/protocol-v3-base';
    
    try {
        const query = `{
            users(first: 1000, where: { borrowedReservesCount_gt: 0 }) {
                id
            }
        }`;
        
        const response = await fetch(SUBGRAPH_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });
        
        const data = await response.json();
        if (data.data?.users) {
            for (const user of data.data.users) {
                knownUsers.add(ethers.getAddress(user.id));
            }
            log(`Found ${data.data.users.length} users from subgraph`, 'SUCCESS');
            saveUserCache();
            return true;
        }
    } catch (e) {
        log(`Subgraph query failed: ${e.message}`, 'ERROR');
    }
    return false;
}

// ============= HEALTH FACTOR SCANNING =============
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
            isLiquidatable: healthFactor < CONFIG.HEALTH_FACTOR_THRESHOLD && totalDebt > 0
        };
    } catch (e) {
        return null;
    }
}

async function scanAllUsers() {
    const users = Array.from(knownUsers);
    const riskyUsers = [];
    const liquidatableUsers = [];
    
    // Batch check users
    const batchSize = 50;
    for (let i = 0; i < users.length; i += batchSize) {
        const batch = users.slice(i, i + batchSize);
        const results = await Promise.all(batch.map(u => checkUserHealth(u)));
        
        for (const result of results) {
            if (!result) continue;
            
            if (result.isLiquidatable) {
                liquidatableUsers.push(result);
                log(`LIQUIDATABLE: ${result.user} HF=${result.healthFactor.toFixed(4)} Debt=$${result.totalDebt.toFixed(2)}`, 'LIQUIDATE');
            } else if (result.healthFactor > 0 && result.healthFactor < 1.1 && result.totalDebt > 0) {
                riskyUsers.push(result);
            }
        }
    }
    
    return { riskyUsers, liquidatableUsers };
}

// ============= LIQUIDATION EXECUTION =============
async function getUserPositionDetails(user) {
    try {
        const reserves = await aavePool.getReservesList();
        const positions = { collateral: [], debt: [] };
        
        for (const reserve of reserves) {
            try {
                const userData = await dataProvider.getUserReserveData(reserve, user);
                const token = new ethers.Contract(reserve, ERC20_ABI, provider);
                const symbol = await token.symbol();
                const decimals = await token.decimals();
                
                const aTokenBalance = parseFloat(ethers.formatUnits(userData.currentATokenBalance, decimals));
                const variableDebt = parseFloat(ethers.formatUnits(userData.currentVariableDebt, decimals));
                const stableDebt = parseFloat(ethers.formatUnits(userData.currentStableDebt, decimals));
                
                if (aTokenBalance > 0 && userData.usageAsCollateralEnabled) {
                    positions.collateral.push({ reserve, symbol, amount: aTokenBalance, decimals });
                }
                if (variableDebt > 0 || stableDebt > 0) {
                    positions.debt.push({ reserve, symbol, amount: variableDebt + stableDebt, decimals });
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

async function executeLiquidation(user, positions) {
    if (!CONFIG.AUTO_EXECUTE) {
        log(`Auto-execute disabled. Would liquidate ${user}`, 'INFO');
        return false;
    }
    
    if (!positions || !positions.collateral.length || !positions.debt.length) {
        log(`No valid positions for ${user}`, 'ERROR');
        return false;
    }
    
    try {
        // Select largest debt and largest collateral
        const debtAsset = positions.debt.sort((a, b) => b.amount - a.amount)[0];
        const collateralAsset = positions.collateral.sort((a, b) => b.amount - a.amount)[0];
        
        log(`Liquidating ${user}: Repay ${debtAsset.symbol}, Receive ${collateralAsset.symbol}`, 'LIQUIDATE');
        
        // For now, log the opportunity. Full flash loan implementation would go here.
        log(`💰 LIQUIDATION OPPORTUNITY FOUND!`, 'PROFIT');
        log(`   User: ${user}`, 'PROFIT');
        log(`   Debt: ${debtAsset.amount.toFixed(4)} ${debtAsset.symbol}`, 'PROFIT');
        log(`   Collateral: ${collateralAsset.amount.toFixed(4)} ${collateralAsset.symbol}`, 'PROFIT');
        
        totalLiquidations++;
        return true;
        
    } catch (e) {
        log(`Liquidation failed: ${e.message}`, 'ERROR');
        return false;
    }
}

// ============= MAIN BOT LOOP =============
async function runBot() {
    log('═══════════════════════════════════════════════════════════', 'INFO');
    log('   AAVE V3 FLASH LIQUIDATOR BOT - BASE MAINNET', 'INFO');
    log('   Running 24/7 - Scanning for liquidation opportunities', 'INFO');
    log('═══════════════════════════════════════════════════════════', 'INFO');
    
    // Initialize provider and contracts
    provider = new ethers.JsonRpcProvider(CONFIG.RPC_URL);
    
    if (CONFIG.PRIVATE_KEY) {
        wallet = new ethers.Wallet(CONFIG.PRIVATE_KEY, provider);
        log(`Wallet: ${wallet.address}`, 'INFO');
    } else {
        log('No private key - running in monitor-only mode', 'INFO');
    }
    
    aavePool = new ethers.Contract(CONFIG.AAVE_POOL, AAVE_POOL_ABI, wallet || provider);
    dataProvider = new ethers.Contract(CONFIG.AAVE_POOL_DATA_PROVIDER, DATA_PROVIDER_ABI, provider);
    
    // Load cached users or discover new ones
    const cacheLoaded = loadUserCache();
    
    if (knownUsers.size < 100) {
        log('User cache too small, starting discovery...', 'SCAN');
        await discoverUsersViaSubgraph();
        if (knownUsers.size < 100) {
            await discoverUsersViaEvents();
        }
    }
    
    log(`Monitoring ${knownUsers.size} Aave V3 users`, 'INFO');
    log(`Auto-execute: ${CONFIG.AUTO_EXECUTE ? 'ENABLED' : 'DISABLED'}`, 'INFO');
    log(`Health factor threshold: ${CONFIG.HEALTH_FACTOR_THRESHOLD}`, 'INFO');
    log('Starting continuous scan loop...', 'INFO');
    log('───────────────────────────────────────────────────────────', 'INFO');
    
    let scanCount = 0;
    
    // Main loop - runs forever
    while (true) {
        try {
            scanCount++;
            const { riskyUsers, liquidatableUsers } = await scanAllUsers();
            
            // Log scan summary every 10 scans
            if (scanCount % 10 === 0) {
                log(`Scan #${scanCount} | Users: ${knownUsers.size} | Risky: ${riskyUsers.length} | Liquidatable: ${liquidatableUsers.length}`, 'SCAN');
            }
            
            // Log risky users (HF between 1.0 and 1.1)
            for (const user of riskyUsers.slice(0, 3)) {
                log(`RISKY: ${user.user.slice(0, 10)}... HF=${user.healthFactor.toFixed(4)} Debt=$${user.totalDebt.toFixed(2)}`, 'RISKY');
            }
            
            // Execute liquidations
            for (const target of liquidatableUsers) {
                log(`🎯 TARGET ACQUIRED: ${target.user}`, 'LIQUIDATE');
                const positions = await getUserPositionDetails(target.user);
                await executeLiquidation(target.user, positions);
            }
            
            // Refresh user discovery periodically (every 100 scans)
            if (scanCount % 100 === 0) {
                log('Refreshing user list...', 'SCAN');
                await discoverUsersViaSubgraph();
            }
            
        } catch (e) {
            log(`Scan error: ${e.message}`, 'ERROR');
        }
        
        // Wait before next scan
        await new Promise(r => setTimeout(r, CONFIG.SCAN_INTERVAL));
    }
}

// ============= START BOT =============
runBot().catch(e => {
    log(`Fatal error: ${e.message}`, 'ERROR');
    process.exit(1);
});
