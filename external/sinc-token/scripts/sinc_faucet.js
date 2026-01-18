/**
 * SINC FAUCET SERVER
 * ==================
 * Simple HTTP server that distributes SINC tokens
 * 
 * Endpoints:
 *   GET /claim/:wallet - Claim 100 SINC (1 per wallet per 24h)
 *   GET /stats - View faucet stats
 *   GET /balance/:wallet - Check SINC balance
 * 
 * Run: node sinc_faucet.js
 * Access: http://localhost:3456
 */

const http = require('http');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });
const { ethers } = require('ethers');
const fs = require('fs');

// ============= CONFIG =============
const PORT = process.env.FAUCET_PORT || 3456;
const SINC_ADDRESS = process.env.SINC_TOKEN_ADDRESS || '0xd10D86D09ee4316CdD3585fd6486537b7119A073';
const PRIVATE_KEY = process.env.PRIVATE_KEY;
const RPC = 'https://base.publicnode.com';

const CLAIM_AMOUNT = ethers.parseEther('100'); // 100 SINC per claim
const COOLDOWN_MS = 24 * 60 * 60 * 1000; // 24 hours

const ERC20_ABI = [
    'function transfer(address to, uint256 amount) returns (bool)',
    'function balanceOf(address account) view returns (uint256)',
    'function name() view returns (string)',
    'function symbol() view returns (string)'
];

const DATA_FILE = path.join(__dirname, 'faucet_data.json');
let faucetData = { claims: {}, totalClaimed: 0, claimCount: 0 };

function loadData() {
    try {
        if (fs.existsSync(DATA_FILE)) {
            faucetData = JSON.parse(fs.readFileSync(DATA_FILE, 'utf8'));
        }
    } catch (e) {}
}

function saveData() {
    fs.writeFileSync(DATA_FILE, JSON.stringify(faucetData, null, 2));
}

function log(msg) {
    console.log(`[${new Date().toISOString().split('T')[1].split('.')[0]}] ${msg}`);
}

async function sendSINC(toAddress) {
    const provider = new ethers.JsonRpcProvider(RPC);
    const wallet = new ethers.Wallet(PRIVATE_KEY, provider);
    const token = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, wallet);
    
    const tx = await token.transfer(toAddress, CLAIM_AMOUNT);
    await tx.wait();
    return tx.hash;
}

async function getBalance(address) {
    const provider = new ethers.JsonRpcProvider(RPC);
    const token = new ethers.Contract(SINC_ADDRESS, ERC20_ABI, provider);
    const balance = await token.balanceOf(address);
    return ethers.formatEther(balance);
}

function canClaim(wallet) {
    const lastClaim = faucetData.claims[wallet.toLowerCase()];
    if (!lastClaim) return { canClaim: true };
    
    const timeSince = Date.now() - lastClaim;
    if (timeSince >= COOLDOWN_MS) return { canClaim: true };
    
    const remaining = COOLDOWN_MS - timeSince;
    const hours = Math.floor(remaining / (60 * 60 * 1000));
    const mins = Math.floor((remaining % (60 * 60 * 1000)) / (60 * 1000));
    
    return { canClaim: false, remaining: `${hours}h ${mins}m` };
}

// HTML Pages
const homePage = `
<!DOCTYPE html>
<html>
<head>
    <title>SINC Faucet</title>
    <style>
        body { font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px; background: #1a1a2e; color: #eee; }
        h1 { color: #00d4ff; }
        input { padding: 10px; width: 100%; margin: 10px 0; border-radius: 5px; border: none; }
        button { padding: 15px 30px; background: #00d4ff; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }
        button:hover { background: #00b4df; }
        .stats { background: #2a2a4e; padding: 20px; border-radius: 10px; margin: 20px 0; }
        .result { margin-top: 20px; padding: 15px; border-radius: 5px; }
        .success { background: #1e4620; }
        .error { background: #4a1e1e; }
        a { color: #00d4ff; }
    </style>
</head>
<body>
    <h1>💎 SINC Token Faucet</h1>
    <p>Get free SINC tokens on Base network!</p>
    
    <div class="stats">
        <h3>📊 Faucet Stats</h3>
        <p>Total Claimed: <span id="total">...</span> SINC</p>
        <p>Total Claims: <span id="count">...</span></p>
        <p>Amount per claim: 100 SINC</p>
        <p>Cooldown: 24 hours</p>
    </div>
    
    <h3>Claim SINC</h3>
    <input type="text" id="wallet" placeholder="Enter your wallet address (0x...)" />
    <button onclick="claim()">Claim 100 SINC</button>
    
    <div id="result"></div>
    
    <p style="margin-top: 30px; font-size: 12px; color: #888;">
        Token: <a href="https://basescan.org/token/${SINC_ADDRESS}" target="_blank">${SINC_ADDRESS}</a><br>
        Network: Base (Chain ID 8453)
    </p>
    
    <script>
        fetch('/stats').then(r => r.json()).then(d => {
            document.getElementById('total').textContent = d.totalClaimed.toLocaleString();
            document.getElementById('count').textContent = d.claimCount;
        });
        
        async function claim() {
            const wallet = document.getElementById('wallet').value;
            if (!wallet.startsWith('0x') || wallet.length !== 42) {
                document.getElementById('result').innerHTML = '<div class="result error">Invalid wallet address</div>';
                return;
            }
            document.getElementById('result').innerHTML = '<div class="result">Processing... please wait</div>';
            const res = await fetch('/claim/' + wallet);
            const data = await res.json();
            if (data.success) {
                document.getElementById('result').innerHTML = '<div class="result success">✅ ' + data.message + '<br><a href="https://basescan.org/tx/' + data.txHash + '" target="_blank">View Transaction</a></div>';
            } else {
                document.getElementById('result').innerHTML = '<div class="result error">❌ ' + data.message + '</div>';
            }
        }
    </script>
</body>
</html>
`;

// Request handler
async function handleRequest(req, res) {
    const url = new URL(req.url, `http://localhost:${PORT}`);
    const path = url.pathname;
    
    // CORS headers
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Content-Type', 'application/json');
    
    try {
        // Home page
        if (path === '/' || path === '/index.html') {
            res.setHeader('Content-Type', 'text/html');
            res.end(homePage);
            return;
        }
        
        // Stats
        if (path === '/stats') {
            res.end(JSON.stringify({
                totalClaimed: faucetData.totalClaimed,
                claimCount: faucetData.claimCount,
                sincAddress: SINC_ADDRESS
            }));
            return;
        }
        
        // Balance check
        if (path.startsWith('/balance/')) {
            const wallet = path.split('/')[2];
            if (!ethers.isAddress(wallet)) {
                res.statusCode = 400;
                res.end(JSON.stringify({ error: 'Invalid address' }));
                return;
            }
            const balance = await getBalance(wallet);
            res.end(JSON.stringify({ wallet, balance, symbol: 'SINC' }));
            return;
        }
        
        // Claim
        if (path.startsWith('/claim/')) {
            const wallet = path.split('/')[2];
            
            if (!ethers.isAddress(wallet)) {
                res.statusCode = 400;
                res.end(JSON.stringify({ success: false, message: 'Invalid wallet address' }));
                return;
            }
            
            const claimStatus = canClaim(wallet);
            if (!claimStatus.canClaim) {
                res.end(JSON.stringify({ 
                    success: false, 
                    message: `Already claimed. Try again in ${claimStatus.remaining}` 
                }));
                return;
            }
            
            log(`Processing claim for ${wallet}`);
            
            const txHash = await sendSINC(wallet);
            
            // Update data
            faucetData.claims[wallet.toLowerCase()] = Date.now();
            faucetData.totalClaimed += 100;
            faucetData.claimCount++;
            saveData();
            
            log(`✅ Sent 100 SINC to ${wallet} - TX: ${txHash}`);
            
            res.end(JSON.stringify({ 
                success: true, 
                message: 'Claimed 100 SINC!',
                txHash 
            }));
            return;
        }
        
        // 404
        res.statusCode = 404;
        res.end(JSON.stringify({ error: 'Not found' }));
        
    } catch (e) {
        log(`Error: ${e.message}`);
        res.statusCode = 500;
        res.end(JSON.stringify({ success: false, message: e.message }));
    }
}

// Start server
loadData();

const server = http.createServer(handleRequest);
server.listen(PORT, () => {
    console.log('');
    console.log('═══════════════════════════════════════════════════════════');
    console.log('   💎 SINC FAUCET SERVER');
    console.log('═══════════════════════════════════════════════════════════');
    console.log(`   URL: http://localhost:${PORT}`);
    console.log(`   Token: ${SINC_ADDRESS}`);
    console.log(`   Claim Amount: 100 SINC`);
    console.log(`   Cooldown: 24 hours`);
    console.log('═══════════════════════════════════════════════════════════');
    console.log('');
});
