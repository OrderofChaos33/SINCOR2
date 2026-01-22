/**
 * TESTNET AIRDROP FARMER
 * =======================
 * Automated farming across multiple testnets
 * 
 * EDGE STRATEGY:
 * 1. Focus on OBSCURE testnets (less competition)
 * 2. Daily automated activity (consistency wins)
 * 3. Varied actions (swaps, LPs, bridges, mints)
 * 4. Track everything for proof of activity
 * 
 * Run this daily - cron job or Task Scheduler
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= YOUR WALLET =============
const PRIVATE_KEY = process.env.PRIVATE_KEY;
const WALLET_ADDRESS = process.env.WALLET_ADDRESS || '0xF915f3F4954c3da6A7D76B424b980A897c3909f1';

// ============= TESTNET CONFIGS =============
// Focus on OBSCURE chains - less farmed = higher airdrop potential
const TESTNETS = {
    // TIER 1 - HIGH PRIORITY (Likely big airdrops, less mainstream)
    monad: {
        name: 'Monad Testnet',
        rpc: 'https://testnet-rpc.monad.xyz',
        chainId: 10143,
        faucet: 'https://faucet.monad.xyz',
        explorer: 'https://testnet.monadexplorer.com',
        contracts: {
            // Will populate when we connect
        },
        priority: 1,
        status: 'active'
    },
    
    megaeth: {
        name: 'MegaETH Testnet', 
        rpc: 'https://carrot.megaeth.com',
        chainId: 6342,
        faucet: 'https://faucet.megaeth.com',
        explorer: 'https://megaeth.xyz',
        priority: 1,
        status: 'active'
    },
    
    abstract: {
        name: 'Abstract Testnet',
        rpc: 'https://api.testnet.abs.xyz',
        chainId: 11124,
        faucet: 'https://faucet.abs.xyz',
        explorer: 'https://explorer.testnet.abs.xyz',
        priority: 1,
        status: 'active'
    },
    
    // TIER 2 - OBSCURE (Very few people farming these)
    rise: {
        name: 'Rise Chain Testnet',
        rpc: 'https://testnet.riselabs.xyz',
        chainId: 11155420,
        faucet: null,
        explorer: 'https://explorer.testnet.riselabs.xyz',
        priority: 2,
        status: 'check'
    },
    
    somnia: {
        name: 'Somnia Testnet',
        rpc: 'https://dream-rpc.somnia.network',
        chainId: 50311,
        faucet: 'https://faucet.somnia.network',
        explorer: 'https://somnia.network',
        priority: 2,
        status: 'active'
    },
    
    pharos: {
        name: 'Pharos Testnet',
        rpc: 'https://testnet.rpc.pharosnetwork.xyz',
        chainId: 688688,
        faucet: null,
        explorer: null,
        priority: 2,
        status: 'check'
    },
    
    // TIER 3 - ESTABLISHED BUT STILL VALUABLE
    berachain: {
        name: 'Berachain bArtio',
        rpc: 'https://bartio.rpc.berachain.com',
        chainId: 80084,
        faucet: 'https://bartio.faucet.berachain.com',
        explorer: 'https://bartio.beratrail.io',
        priority: 3,
        status: 'active'
    }
};

// Activity log
const LOG_FILE = path.join(__dirname, 'farming_activity.json');
let activityLog = { wallets: {}, sessions: [], stats: { totalTxs: 0, chainsActive: 0, daysActive: 0 } };

function loadActivityLog() {
    try {
        if (fs.existsSync(LOG_FILE)) {
            activityLog = JSON.parse(fs.readFileSync(LOG_FILE, 'utf8'));
        }
    } catch (e) {
        console.log('Starting fresh activity log');
    }
}

function saveActivityLog() {
    fs.writeFileSync(LOG_FILE, JSON.stringify(activityLog, null, 2));
}

function log(msg, type = 'INFO') {
    const time = new Date().toISOString().replace('T', ' ').split('.')[0];
    const icons = { INFO: '📊', SUCCESS: '✅', ERROR: '❌', TX: '📤', FARM: '🌾', SKIP: '⏭️' };
    console.log(`[${time}] ${icons[type] || '📊'} ${msg}`);
}

async function checkConnection(testnet) {
    try {
        const provider = new ethers.JsonRpcProvider(testnet.rpc, testnet.chainId);
        const blockNum = await Promise.race([
            provider.getBlockNumber(),
            new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000))
        ]);
        return { connected: true, block: blockNum, provider };
    } catch (e) {
        return { connected: false, error: e.message };
    }
}

async function getBalance(provider, address) {
    try {
        const balance = await provider.getBalance(address);
        return ethers.formatEther(balance);
    } catch (e) {
        return '0';
    }
}

// ============= FARMING ACTIONS =============

async function sendSelfTransfer(wallet, provider, chainName) {
    // Simple self-transfer - counts as activity
    try {
        const balance = await provider.getBalance(wallet.address);
        if (balance < ethers.parseEther('0.0001')) {
            log(`${chainName}: Insufficient balance for tx`, 'SKIP');
            return null;
        }
        
        const tx = await wallet.sendTransaction({
            to: wallet.address,
            value: ethers.parseEther('0.00001'),
            gasLimit: 21000
        });
        
        log(`${chainName}: Self-transfer sent: ${tx.hash}`, 'TX');
        return tx.hash;
    } catch (e) {
        log(`${chainName}: TX failed - ${e.message.substring(0, 50)}`, 'ERROR');
        return null;
    }
}

async function deployDummyContract(wallet, chainName) {
    // Deploying a contract = high-value activity for airdrops
    try {
        const balance = await wallet.provider.getBalance(wallet.address);
        if (balance < ethers.parseEther('0.001')) {
            log(`${chainName}: Need more ETH for contract deploy`, 'SKIP');
            return null;
        }
        
        // Minimal contract bytecode (just returns nothing)
        const bytecode = '0x6080604052348015600f57600080fd5b50603f80601d6000396000f3fe6080604052600080fdfea265627a7a72315820';
        
        const tx = await wallet.sendTransaction({
            data: bytecode,
            gasLimit: 100000
        });
        
        log(`${chainName}: Contract deploy: ${tx.hash}`, 'TX');
        return tx.hash;
    } catch (e) {
        log(`${chainName}: Deploy failed - ${e.message.substring(0, 50)}`, 'ERROR');
        return null;
    }
}

async function interactWithContract(wallet, contractAddress, chainName) {
    // Generic contract interaction
    try {
        // Simple transfer call (won't actually transfer anything but creates tx)
        const iface = new ethers.Interface(['function transfer(address to, uint256 amount) returns (bool)']);
        const data = iface.encodeFunctionData('transfer', [wallet.address, 0]);
        
        const tx = await wallet.sendTransaction({
            to: contractAddress,
            data: data,
            gasLimit: 50000
        });
        
        log(`${chainName}: Contract interaction: ${tx.hash}`, 'TX');
        return tx.hash;
    } catch (e) {
        return null;
    }
}

// ============= MAIN FARMING LOOP =============

async function farmTestnet(testnetKey, testnet, wallet) {
    log(`\n${'─'.repeat(50)}`, 'INFO');
    log(`Farming: ${testnet.name} (Priority ${testnet.priority})`, 'FARM');
    
    const connection = await checkConnection(testnet);
    if (!connection.connected) {
        log(`${testnet.name}: Connection failed - ${connection.error}`, 'ERROR');
        return { success: false, txs: 0 };
    }
    
    log(`${testnet.name}: Connected at block ${connection.block}`, 'SUCCESS');
    
    const connectedWallet = wallet.connect(connection.provider);
    const balance = await getBalance(connection.provider, wallet.address);
    log(`${testnet.name}: Balance = ${balance} ETH`, 'INFO');
    
    let txCount = 0;
    const txHashes = [];
    
    // Action 1: Self transfer (if we have balance)
    if (parseFloat(balance) > 0.0001) {
        const hash = await sendSelfTransfer(connectedWallet, connection.provider, testnet.name);
        if (hash) {
            txHashes.push(hash);
            txCount++;
        }
    }
    
    // Action 2: Contract deploy (if enough balance - this is high value)
    if (parseFloat(balance) > 0.005) {
        const hash = await deployDummyContract(connectedWallet, testnet.name);
        if (hash) {
            txHashes.push(hash);
            txCount++;
        }
    }
    
    // Log activity
    const session = {
        chain: testnetKey,
        chainName: testnet.name,
        timestamp: new Date().toISOString(),
        txCount,
        txHashes,
        balance
    };
    
    if (!activityLog.wallets[wallet.address]) {
        activityLog.wallets[wallet.address] = { chains: {}, totalTxs: 0 };
    }
    if (!activityLog.wallets[wallet.address].chains[testnetKey]) {
        activityLog.wallets[wallet.address].chains[testnetKey] = { txs: [], totalTxs: 0 };
    }
    
    activityLog.wallets[wallet.address].chains[testnetKey].txs.push(session);
    activityLog.wallets[wallet.address].chains[testnetKey].totalTxs += txCount;
    activityLog.wallets[wallet.address].totalTxs += txCount;
    activityLog.stats.totalTxs += txCount;
    activityLog.sessions.push(session);
    
    saveActivityLog();
    
    log(`${testnet.name}: Session complete - ${txCount} transactions`, 'SUCCESS');
    
    return { success: true, txs: txCount, balance };
}

async function discoverNewTestnets() {
    // This would scrape for new testnets - placeholder for now
    log('\n📡 Checking for new testnet opportunities...', 'INFO');
    
    // Sources to monitor:
    // - https://chainlist.org (filter by testnet)
    // - Twitter/X crypto accounts
    // - Discord servers
    // - DefiLlama new chains
    
    const potentialNewChains = [
        { name: 'Lens Network', status: 'Announced, testnet soon' },
        { name: 'Unichain', status: 'Testnet active' },
        { name: 'Soneium', status: 'Sony blockchain - early' },
        { name: 'Aztec', status: 'Privacy L2 - devnet' }
    ];
    
    log('New opportunities to research:', 'INFO');
    potentialNewChains.forEach(c => log(`  - ${c.name}: ${c.status}`, 'INFO'));
}

async function printStats() {
    console.log('\n' + '═'.repeat(60));
    console.log('   📊 FARMING STATISTICS');
    console.log('═'.repeat(60));
    
    const walletStats = activityLog.wallets[WALLET_ADDRESS];
    if (walletStats) {
        console.log(`\n   Wallet: ${WALLET_ADDRESS.slice(0, 10)}...`);
        console.log(`   Total Transactions: ${walletStats.totalTxs}`);
        console.log(`   Chains Farmed: ${Object.keys(walletStats.chains).length}`);
        console.log('\n   Per-Chain Activity:');
        
        for (const [chain, data] of Object.entries(walletStats.chains)) {
            const chainName = TESTNETS[chain]?.name || chain;
            console.log(`     ${chainName}: ${data.totalTxs} txs`);
        }
    }
    
    console.log('\n' + '═'.repeat(60));
}

async function main() {
    console.log('');
    console.log('═'.repeat(60));
    console.log('   🌾 TESTNET AIRDROP FARMER');
    console.log('   Automated multi-chain farming for maximum airdrop exposure');
    console.log('═'.repeat(60));
    console.log('');
    
    if (!PRIVATE_KEY) {
        log('ERROR: PRIVATE_KEY not found in .env', 'ERROR');
        log('Add PRIVATE_KEY=0x... to your .env file', 'INFO');
        process.exit(1);
    }
    
    loadActivityLog();
    
    const wallet = new ethers.Wallet(PRIVATE_KEY);
    log(`Wallet: ${wallet.address}`, 'INFO');
    
    const today = new Date().toISOString().split('T')[0];
    log(`Farming session: ${today}`, 'INFO');
    
    // Check which testnets are active
    log('\n🔍 Checking testnet connections...', 'INFO');
    
    const activeTestnets = [];
    for (const [key, testnet] of Object.entries(TESTNETS)) {
        const status = await checkConnection(testnet);
        if (status.connected) {
            activeTestnets.push({ key, testnet, block: status.block });
            log(`  ✅ ${testnet.name} - Block ${status.block}`, 'SUCCESS');
        } else {
            log(`  ❌ ${testnet.name} - Offline`, 'ERROR');
        }
    }
    
    log(`\n${activeTestnets.length}/${Object.keys(TESTNETS).length} testnets online`, 'INFO');
    
    // Farm each active testnet
    let totalTxs = 0;
    const results = [];
    
    for (const { key, testnet } of activeTestnets) {
        const result = await farmTestnet(key, testnet, wallet);
        results.push({ chain: testnet.name, ...result });
        totalTxs += result.txs;
        
        // Small delay between chains
        await new Promise(r => setTimeout(r, 2000));
    }
    
    // Look for new opportunities
    await discoverNewTestnets();
    
    // Print summary
    console.log('\n' + '═'.repeat(60));
    console.log('   SESSION SUMMARY');
    console.log('═'.repeat(60));
    console.log(`\n   Chains farmed: ${activeTestnets.length}`);
    console.log(`   Transactions: ${totalTxs}`);
    console.log('\n   Results:');
    results.forEach(r => {
        const status = r.success ? '✅' : '❌';
        console.log(`     ${status} ${r.chain}: ${r.txs} txs ${r.balance ? `(${r.balance} ETH)` : ''}`);
    });
    
    // Show overall stats
    await printStats();
    
    console.log('\n   💡 TIP: Run this daily for maximum airdrop eligibility');
    console.log('   💡 TIP: Get testnet ETH from faucets listed above');
    console.log('   💡 TIP: Research new chains in activity log');
    console.log('═'.repeat(60) + '\n');
}

// Run modes
const args = process.argv.slice(2);

if (args.includes('--loop')) {
    // Continuous mode - run every 6 hours
    log('Starting continuous farming mode (every 6 hours)...', 'INFO');
    main().catch(console.error);
    setInterval(() => {
        main().catch(console.error);
    }, 6 * 60 * 60 * 1000);
} else if (args.includes('--stats')) {
    // Just show stats
    loadActivityLog();
    printStats();
} else {
    // Single run
    main().catch(console.error);
}
