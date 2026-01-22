/**
 * BLOCK WATCHER - Realtime Base Chain Monitor
 * =============================================
 * Watches EVERY block on Base chain and logs:
 * - New transactions
 * - Contract creations
 * - Large transfers
 * 
 * This is the foundation for any trading bot.
 * Run this to PROVE the connection works, then build on it.
 */

const { ethers } = require('ethers');

const RPC = 'https://mainnet.base.org';
const provider = new ethers.JsonRpcProvider(RPC);

let blocksProcessed = 0;
let txCount = 0;
let contractsCreated = 0;

async function processBlock(blockNumber) {
    try {
        const block = await provider.getBlock(blockNumber, true);
        if (!block || !block.prefetchedTransactions) return;
        
        blocksProcessed++;
        const txs = block.prefetchedTransactions;
        txCount += txs.length;
        
        console.log(`\n📦 Block ${blockNumber} | ${txs.length} txs | ${block.gasUsed.toString()} gas`);
        
        // Find interesting transactions
        for (const tx of txs) {
            // Contract creation (to = null)
            if (tx.to === null) {
                contractsCreated++;
                console.log(`   🆕 CONTRACT DEPLOY: from ${tx.from.slice(0,10)}... | gas ${tx.gasLimit.toString()}`);
            }
            
            // Large ETH transfers (> 0.1 ETH)
            if (tx.value > ethers.parseEther('0.1')) {
                console.log(`   💰 BIG TRANSFER: ${ethers.formatEther(tx.value)} ETH | ${tx.from.slice(0,10)}... → ${tx.to?.slice(0,10) || 'new'}...`);
            }
        }
        
    } catch (e) {
        // Skip failed blocks
    }
}

async function main() {
    console.log('═══════════════════════════════════════════════════════════════');
    console.log('   🔍 BASE CHAIN BLOCK WATCHER');
    console.log('   Monitoring every block in realtime');
    console.log('═══════════════════════════════════════════════════════════════');
    
    // Get current block
    const startBlock = await provider.getBlockNumber();
    console.log(`\n📊 Starting at block ${startBlock}`);
    console.log('   Watching for: Contract deployments, Large transfers');
    console.log('   Press Ctrl+C to stop\n');
    
    let lastBlock = startBlock;
    
    // Poll every 2 seconds
    setInterval(async () => {
        try {
            const currentBlock = await provider.getBlockNumber();
            
            if (currentBlock > lastBlock) {
                // Process any new blocks
                for (let b = lastBlock + 1; b <= currentBlock; b++) {
                    await processBlock(b);
                }
                lastBlock = currentBlock;
            }
        } catch (e) {
            console.log('⚠️ RPC hiccup, continuing...');
        }
    }, 2000);
    
    // Stats every 60 seconds
    setInterval(() => {
        console.log(`\n📊 STATS: ${blocksProcessed} blocks | ${txCount} txs | ${contractsCreated} contracts deployed\n`);
    }, 60000);
}

main().catch(console.error);
