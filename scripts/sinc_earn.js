/**
 * SINC EARN SYSTEM
 * =================
 * Utility where SINC is earned through activities
 * 
 * Earn SINC by:
 *   - Running bots (passive earning)
 *   - Completing tasks
 *   - Referrals
 * 
 * This creates REAL utility for SINC tokens
 */

const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CONFIG =============
const SINC_ADDRESS = process.env.SINC_TOKEN_ADDRESS || '0xd10D86D09ee4316CdD3585fd6486537b7119A073';
const PRIVATE_KEY = process.env.PRIVATE_KEY;
const RPC = 'https://base.publicnode.com';

// Earn rates
const EARN_RATES = {
    botRunning: 10,      // 10 SINC per hour of bot running
    taskComplete: 50,    // 50 SINC per task
    referral: 100,       // 100 SINC per referral
    dailyCheckIn: 25,    // 25 SINC per daily check-in
};

const ERC20_ABI = [
    'function transfer(address to, uint256 amount) returns (bool)',
    'function balanceOf(address account) view returns (uint256)'
];

const DATA_FILE = path.join(__dirname, 'sinc_earn_data.json');
let earnData = { 
    users: {},  // wallet -> { earned: 0, pending: 0, lastCheckIn: 0, referrals: [] }
    totalEarned: 0,
    totalPaid: 0
};

function loadData() {
    try {
        if (fs.existsSync(DATA_FILE)) {
            earnData = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
        }
    } catch (e) {}
}

function saveData() {
    fs.writeFileSync(DATA_FILE, JSON.stringify(earnData, null, 2));
}

function getUser(wallet) {
    const w = wallet.toLowerCase();
    if (!earnData.users[w]) {
        earnData.users[w] = { 
            earned: 0, 
            pending: 0, 
            lastCheckIn: 0,
            lastBotPing: 0,
            botHours: 0,
            referrals: [],
            referredBy: null
        };
    }
    return earnData.users[w];
}

function log(msg, type = 'INFO') {
    const time = new Date().toISOString().replace('T', ' ').split('.')[0];
    const icons = { INFO: '📊', EARN: '💰', TX: '📤', SUCCESS: '✅', ERROR: '❌' };
    console.log(`[${time}] ${icons[type] || '📊'} ${msg}`);
}

// ============= EARN FUNCTIONS =============

function dailyCheckIn(wallet) {
    const user = getUser(wallet);
    const now = Date.now();
    const oneDayMs = 24 * 60 * 60 * 1000;
    
    if (user.lastCheckIn && (now - user.lastCheckIn) < oneDayMs) {
        const remaining = oneDayMs - (now - user.lastCheckIn);
        const hours = Math.floor(remaining / (60 * 60 * 1000));
        return { success: false, message: `Already checked in. Try again in ${hours} hours` };
    }
    
    user.pending += EARN_RATES.dailyCheckIn;
    user.lastCheckIn = now;
    earnData.totalEarned += EARN_RATES.dailyCheckIn;
    saveData();
    
    log(`${wallet.slice(0,10)}... checked in, earned ${EARN_RATES.dailyCheckIn} SINC`, 'EARN');
    return { success: true, earned: EARN_RATES.dailyCheckIn, pending: user.pending };
}

function botPing(wallet) {
    // Call this periodically when running a bot to earn SINC
    const user = getUser(wallet);
    const now = Date.now();
    
    if (user.lastBotPing) {
        const hoursSince = (now - user.lastBotPing) / (60 * 60 * 1000);
        if (hoursSince >= 1) {
            const hoursEarned = Math.floor(hoursSince);
            const sincEarned = hoursEarned * EARN_RATES.botRunning;
            user.pending += sincEarned;
            user.botHours += hoursEarned;
            earnData.totalEarned += sincEarned;
            log(`${wallet.slice(0,10)}... bot earned ${sincEarned} SINC (${hoursEarned}h)`, 'EARN');
        }
    }
    
    user.lastBotPing = now;
    saveData();
    return { success: true, pending: user.pending, botHours: user.botHours };
}

function addReferral(wallet, referrerWallet) {
    const user = getUser(wallet);
    const referrer = getUser(referrerWallet);
    
    if (user.referredBy) {
        return { success: false, message: 'Already has a referrer' };
    }
    
    if (wallet.toLowerCase() === referrerWallet.toLowerCase()) {
        return { success: false, message: 'Cannot refer yourself' };
    }
    
    user.referredBy = referrerWallet.toLowerCase();
    referrer.referrals.push(wallet.toLowerCase());
    referrer.pending += EARN_RATES.referral;
    earnData.totalEarned += EARN_RATES.referral;
    saveData();
    
    log(`${referrerWallet.slice(0,10)}... earned ${EARN_RATES.referral} SINC for referral`, 'EARN');
    return { success: true, earned: EARN_RATES.referral };
}

function completeTask(wallet, taskId) {
    const user = getUser(wallet);
    
    if (!user.completedTasks) user.completedTasks = [];
    if (user.completedTasks.includes(taskId)) {
        return { success: false, message: 'Task already completed' };
    }
    
    user.completedTasks.push(taskId);
    user.pending += EARN_RATES.taskComplete;
    earnData.totalEarned += EARN_RATES.taskComplete;
    saveData();
    
    log(`${wallet.slice(0,10)}... completed task ${taskId}, earned ${EARN_RATES.taskComplete} SINC`, 'EARN');
    return { success: true, earned: EARN_RATES.taskComplete, pending: user.pending };
}

async function claimPending(wallet) {
    const user = getUser(wallet);
    
    if (user.pending < 10) {
        return { success: false, message: 'Minimum claim is 10 SINC' };
    }
    
    const amount = user.pending;
    
    try {
        const provider = new ethers.JsonRpcProvider(RPC);
        const signer = new ethers.Wallet(PRIVATE_KEY, provider);
        const token = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, signer);
        
        log(`Sending ${amount} SINC to ${wallet}...`, 'TX');
        const tx = await token.transfer(wallet, ethers.parseEther(amount.toString()));
        await tx.wait();
        
        user.earned += amount;
        user.pending = 0;
        earnData.totalPaid += amount;
        saveData();
        
        log(`✅ Paid ${amount} SINC to ${wallet} - TX: ${tx.hash}`, 'SUCCESS');
        return { success: true, amount, txHash: tx.hash };
        
    } catch (e) {
        log(`Claim failed: ${e.message}`, 'ERROR');
        return { success: false, message: e.message };
    }
}

function getUserStats(wallet) {
    const user = getUser(wallet);
    return {
        wallet,
        pending: user.pending,
        earned: user.earned,
        botHours: user.botHours || 0,
        referralCount: user.referrals?.length || 0,
        tasksCompleted: user.completedTasks?.length || 0
    };
}

function getGlobalStats() {
    return {
        totalUsers: Object.keys(earnData.users).length,
        totalEarned: earnData.totalEarned,
        totalPaid: earnData.totalPaid,
        earnRates: EARN_RATES
    };
}

// ============= CLI =============

async function main() {
    loadData();
    
    const args = process.argv.slice(2);
    const command = args[0];
    const wallet = args[1];
    
    console.log('');
    console.log('═══════════════════════════════════════════════════════════');
    console.log('   💰 SINC EARN SYSTEM');
    console.log('═══════════════════════════════════════════════════════════');
    
    if (!command) {
        console.log(`
Commands:
  node sinc_earn.js stats                    - Global stats
  node sinc_earn.js user <wallet>            - User stats
  node sinc_earn.js checkin <wallet>         - Daily check-in
  node sinc_earn.js ping <wallet>            - Bot running ping
  node sinc_earn.js task <wallet> <taskId>   - Complete a task
  node sinc_earn.js refer <wallet> <by>      - Add referral
  node sinc_earn.js claim <wallet>           - Claim pending SINC

Earn Rates:
  Daily Check-in: ${EARN_RATES.dailyCheckIn} SINC
  Bot Running: ${EARN_RATES.botRunning} SINC/hour
  Task Complete: ${EARN_RATES.taskComplete} SINC
  Referral: ${EARN_RATES.referral} SINC
`);
        return;
    }
    
    switch (command) {
        case 'stats':
            const stats = getGlobalStats();
            console.log(`\n   Total Users: ${stats.totalUsers}`);
            console.log(`   Total Earned: ${stats.totalEarned} SINC`);
            console.log(`   Total Paid: ${stats.totalPaid} SINC`);
            break;
            
        case 'user':
            if (!wallet || !ethers.isAddress(wallet)) {
                console.log('   Invalid wallet address');
                return;
            }
            const userStats = getUserStats(wallet);
            console.log(`\n   Wallet: ${wallet}`);
            console.log(`   Pending: ${userStats.pending} SINC`);
            console.log(`   Total Earned: ${userStats.earned} SINC`);
            console.log(`   Bot Hours: ${userStats.botHours}`);
            console.log(`   Referrals: ${userStats.referralCount}`);
            console.log(`   Tasks Done: ${userStats.tasksCompleted}`);
            break;
            
        case 'checkin':
            if (!wallet || !ethers.isAddress(wallet)) {
                console.log('   Invalid wallet address');
                return;
            }
            const checkIn = dailyCheckIn(wallet);
            console.log(`\n   ${checkIn.success ? '✅' : '❌'} ${checkIn.message || `Earned ${checkIn.earned} SINC!`}`);
            if (checkIn.success) console.log(`   Pending: ${checkIn.pending} SINC`);
            break;
            
        case 'ping':
            if (!wallet || !ethers.isAddress(wallet)) {
                console.log('   Invalid wallet address');
                return;
            }
            const ping = botPing(wallet);
            console.log(`\n   ✅ Bot ping recorded`);
            console.log(`   Pending: ${ping.pending} SINC`);
            console.log(`   Total Bot Hours: ${ping.botHours}`);
            break;
            
        case 'task':
            const taskId = args[2];
            if (!wallet || !ethers.isAddress(wallet) || !taskId) {
                console.log('   Usage: task <wallet> <taskId>');
                return;
            }
            const task = completeTask(wallet, taskId);
            console.log(`\n   ${task.success ? '✅' : '❌'} ${task.message || `Task complete! Earned ${task.earned} SINC`}`);
            break;
            
        case 'refer':
            const referrer = args[2];
            if (!wallet || !ethers.isAddress(wallet) || !referrer || !ethers.isAddress(referrer)) {
                console.log('   Usage: refer <newUser> <referrer>');
                return;
            }
            const ref = addReferral(wallet, referrer);
            console.log(`\n   ${ref.success ? '✅' : '❌'} ${ref.message || `Referral added! ${referrer.slice(0,10)}... earned ${ref.earned} SINC`}`);
            break;
            
        case 'claim':
            if (!wallet || !ethers.isAddress(wallet)) {
                console.log('   Invalid wallet address');
                return;
            }
            const claim = await claimPending(wallet);
            console.log(`\n   ${claim.success ? '✅' : '❌'} ${claim.message || `Claimed ${claim.amount} SINC!`}`);
            if (claim.txHash) console.log(`   TX: ${claim.txHash}`);
            break;
            
        default:
            console.log('   Unknown command. Run without args for help.');
    }
    
    console.log('\n═══════════════════════════════════════════════════════════\n');
}

// Export for use in other scripts
module.exports = { dailyCheckIn, botPing, addReferral, completeTask, claimPending, getUserStats };

main().catch(console.error);
