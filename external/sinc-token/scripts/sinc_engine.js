/**
 * SINC TOKEN VALUE ENGINE
 * ========================
 * You have 50M SINC tokens. Here's how to create value:
 * 
 * Strategy: DISTRIBUTION + UTILITY
 * 
 * 1. Airdrop to active Base users (creates holders)
 * 2. Create a simple utility (bot access, etc.)
 * 3. Track everything for proof of distribution
 * 
 * The goal: Get SINC into many wallets = potential demand
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CONFIG =============
const SINC_ADDRESS = process.env.SINC_TOKEN_ADDRESS || '0xd10D86D09ee4316CdD3585fd6486537b7119A073';
const PRIVATE_KEY = process.env.PRIVATE_KEY;
const RPC = 'https://base.publicnode.com';

// Distribution amounts
const AIRDROP_AMOUNT = ethers.parseEther('100'); // 100 SINC per airdrop
const REFERRAL_AMOUNT = ethers.parseEther('50'); // 50 SINC for referrals

const ERC20_ABI = [
    'function transfer(address to, uint256 amount) returns (bool)',
    'function balanceOf(address account) view returns (uint256)',
    'function name() view returns (string)',
    'function symbol() view returns (string)',
    'function decimals() view returns (uint8)',
    'function totalSupply() view returns (uint256)'
];

const DISTRIBUTION_LOG = path.join(__dirname, 'sinc_distributions.json');
let distributions = { total: 0, recipients: [], history: [] };

function loadDistributions() {
    try {
        if (fs.existsSync(DISTRIBUTION_LOG)) {
            distributions = JSON.parse(fs.readFileSync(DISTRIBUTION_LOG, 'utf8'));
        }
    } catch (e) {
        console.log('Starting fresh distribution log');
    }
}

function saveDistributions() {
    fs.writeFileSync(DISTRIBUTION_LOG, JSON.stringify(distributions, null, 2));
}

function log(msg, type = 'INFO') {
    const time = new Date().toISOString().replace('T', ' ').split('.')[0];
    const icons = { INFO: '📊', TX: '📤', SUCCESS: '✅', ERROR: '❌', SINC: '💎' };
    console.log(`[${time}] ${icons[type] || '📊'} ${msg}`);
}

async function getTokenInfo() {
    const provider = new ethers.JsonRpcProvider(RPC);
    const token = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, provider);
    
    const wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    
    const [name, symbol, balance, supply] = await Promise.all([
        token.name(),
        token.symbol(),
        token.balanceOf(wallet.address),
        token.totalSupply()
    ]);
    
    return {
        name,
        symbol,
        balance: ethers.formatEther(balance),
        supply: ethers.formatEther(supply),
        walletAddress: wallet.address
    };
}

async function sendSINC(toAddress, amount, reason = 'airdrop') {
    const provider = new ethers.JsonRpcProvider(RPC);
    const wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    const token = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, wallet);
    
    try {
        // Check balance first
        const balance = await token.balanceOf(wallet.address);
        if (balance < amount) {
            log(`Insufficient SINC balance`, 'ERROR');
            return null;
        }
        
        // Check if already sent to this address
        if (distributions.recipients.includes(toAddress.toLowerCase())) {
            log(`Already sent to ${toAddress}`, 'INFO');
            return 'duplicate';
        }
        
        log(`Sending ${ethers.formatEther(amount)} SINC to ${toAddress}...`, 'TX');
        
        const tx = await token.transfer(toAddress, amount);
        const receipt = await tx.wait();
        
        // Log distribution
        const entry = {
            to: toAddress,
            amount: ethers.formatEther(amount),
            reason,
            txHash: tx.hash,
            timestamp: new Date().toISOString()
        };
        
        distributions.recipients.push(toAddress.toLowerCase());
        distributions.history.push(entry);
        distributions.total += parseFloat(ethers.formatEther(amount));
        saveDistributions();
        
        log(`✅ Sent! TX: ${tx.hash}`, 'SUCCESS');
        return tx.hash;
        
    } catch (e) {
        log(`Failed: ${e.message}`, 'ERROR');
        return null;
    }
}

// ============= AIRDROP STRATEGIES =============

async function airdropToActiveWallets(walletList) {
    log(`Starting airdrop to ${walletList.length} wallets...`, 'SINC');
    
    let sent = 0;
    for (const wallet of walletList) {
        const result = await sendSINC(wallet, AIRDROP_AMOUNT, 'airdrop');
        if (result && result !== 'duplicate') {
            sent++;
            // Small delay between txs
            await new Promise(r => setTimeout(r, 2000));
        }
    }
    
    log(`Airdrop complete: ${sent}/${walletList.length} sent`, 'SUCCESS');
}

// Discover active wallets from recent Base transactions
async function findActiveWallets(count = 10) {
    const provider = new ethers.JsonRpcProvider(RPC);
    
    log(`Finding ${count} active Base wallets...`, 'INFO');
    
    const activeWallets = new Set();
    const latestBlock = await provider.getBlockNumber();
    
    // Check last few blocks for unique addresses
    for (let i = 0; i < 5 && activeWallets.size < count; i++) {
        try {
            const block = await provider.getBlock(latestBlock - i, true);
            if (block && block.prefetchedTransactions) {
                for (const tx of block.prefetchedTransactions) {
                    if (tx.from) activeWallets.add(tx.from);
                    if (tx.to) activeWallets.add(tx.to);
                    if (activeWallets.size >= count) break;
                }
            }
        } catch (e) {
            // continue
        }
    }
    
    // Filter out contracts and our own wallet
    const wallet = new ethers.Wallet(PRIVATE_KEY);
    const wallets = Array.from(activeWallets)
        .filter(w => w && w !== wallet.address)
        .slice(0, count);
    
    log(`Found ${wallets.length} active wallets`, 'SUCCESS');
    return wallets;
}

// ============= STATS =============

async function printStats() {
    loadDistributions();
    
    console.log('\n' + '═'.repeat(60));
    console.log('   💎 SINC TOKEN DISTRIBUTION STATS');
    console.log('═'.repeat(60));
    
    try {
        const info = await getTokenInfo();
        console.log(`\n   Token: ${info.name} (${info.symbol})`);
        console.log(`   Your Balance: ${parseFloat(info.balance).toLocaleString()} SINC`);
        console.log(`   Total Supply: ${parseFloat(info.supply).toLocaleString()} SINC`);
        console.log(`   Your Share: ${(parseFloat(info.balance) / parseFloat(info.supply) * 100).toFixed(2)}%`);
    } catch (e) {
        console.log(`   Error getting token info: ${e.message}`);
    }
    
    console.log(`\n   📊 Distribution Stats:`);
    console.log(`   Total Distributed: ${distributions.total.toLocaleString()} SINC`);
    console.log(`   Unique Recipients: ${distributions.recipients.length}`);
    console.log(`   Transactions: ${distributions.history.length}`);
    
    if (distributions.history.length > 0) {
        console.log(`\n   📜 Recent Distributions:`);
        const recent = distributions.history.slice(-5);
        recent.forEach(d => {
            console.log(`     ${d.amount} SINC → ${d.to.slice(0,10)}... (${d.reason})`);
        });
    }
    
    console.log('\n' + '═'.repeat(60));
}

// ============= HELP =============

function printHelp() {
    console.log(`
💎 SINC Token Value Engine
==========================

Commands:
  node sinc_engine.js stats         - Show distribution stats
  node sinc_engine.js airdrop <N>   - Airdrop to N active wallets
  node sinc_engine.js send <addr>   - Send 100 SINC to address
  node sinc_engine.js find          - Find active wallets to airdrop

Value Creation Strategy:
  1. Distribute SINC to many wallets (creates potential buyers)
  2. Each holder might add liquidity or tell others
  3. More holders = more potential demand
  4. Eventually: liquidity + demand = real price

Token Details:
  Contract: ${SINC_ADDRESS}
  Network: Base (Chain ID 8453)
  Explorer: https://basescan.org/token/${SINC_ADDRESS}
`);
}

// ============= MAIN =============

async function main() {
    const args = process.argv.slice(2);
    const command = args[0];
    
    loadDistributions();
    
    if (!command || command === 'help') {
        printHelp();
        return;
    }
    
    if (!PRIVATE_KEY) {
        log('ERROR: PRIVATE_KEY not found in .env', 'ERROR');
        return;
    }
    
    switch (command) {
        case 'stats':
            await printStats();
            break;
            
        case 'find':
            const wallets = await findActiveWallets(20);
            console.log('\nActive wallets found:');
            wallets.forEach((w, i) => console.log(`  ${i+1}. ${w}`));
            break;
            
        case 'airdrop':
            const count = parseInt(args[1]) || 5;
            const targets = await findActiveWallets(count);
            await airdropToActiveWallets(targets);
            await printStats();
            break;
            
        case 'send':
            const address = args[1];
            if (!address || !ethers.isAddress(address)) {
                log('Invalid address', 'ERROR');
                return;
            }
            await sendSINC(address, AIRDROP_AMOUNT, 'manual');
            break;
            
        default:
            printHelp();
    }
}

main().catch(console.error);
