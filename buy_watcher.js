/**
 * SINC Buy Event Watcher
 * Monitors the bonding curve for Buy events and sends SMS via Twilio
 * 
 * Usage: node buy_watcher.js
 * Run as background process or on Railway as a worker
 */

const { ethers } = require('ethers');
const https = require('https');
const fs = require('fs');

// Config
const CURVE = '0xb627F53E08AD7d455e787d052C18D6877020E2BF';
const RPC_WS = 'wss://base-mainnet.public.blastapi.io'; // Free WebSocket RPC
const RPC_HTTP = 'https://mainnet.base.org';

// Twilio (from .env)
const TWILIO_SID = process.env.TWILO_ID || 'ACbfe3a0df26ca1bb5e2be2f17a42b1807';
const TWILIO_AUTH = process.env.TWILO_AUTH || 'debe3e1104335ea04bc45ecb2eb4cc55';
const TWILIO_FROM = process.env.TWILO_NUMBER || '+18555088949';
const NOTIFY_TO = process.env.NOTIFY_PHONE || '+18157188936';

const CURVE_ABI = [
    'event Buy(address indexed buyer, uint256 amount, uint256 cost)',
    'event Sell(address indexed seller, uint256 amount, uint256 refund)',
    'function tokensSold() view returns (uint256)',
    'function currentPrice() view returns (uint256)',
    'function reserveBalance() view returns (uint256)'
];

function sendSMS(message) {
    if (NOTIFY_TO === 'YOUR_PHONE_HERE') {
        console.log('[SMS SKIPPED - no phone set]', message);
        return;
    }

    const data = new URLSearchParams({
        To: NOTIFY_TO,
        From: TWILIO_FROM,
        Body: message
    }).toString();

    const options = {
        hostname: 'api.twilio.com',
        path: `/2010-04-01/Accounts/${TWILIO_SID}/Messages.json`,
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': data.length,
            'Authorization': 'Basic ' + Buffer.from(`${TWILIO_SID}:${TWILIO_AUTH}`).toString('base64')
        }
    };

    const req = https.request(options, (res) => {
        let body = '';
        res.on('data', chunk => body += chunk);
        res.on('end', () => {
            if (res.statusCode === 201) {
                console.log('[SMS SENT]', message);
            } else {
                console.error('[SMS FAILED]', res.statusCode, body.substring(0, 200));
            }
        });
    });
    req.on('error', e => console.error('[SMS ERROR]', e.message));
    req.write(data);
    req.end();
}

async function startWatcher() {
    console.log('=== SINC Buy Watcher ===');
    console.log('Curve:', CURVE);
    console.log('Notify:', NOTIFY_TO === 'YOUR_PHONE_HERE' ? 'NOT SET (console only)' : NOTIFY_TO);
    console.log('');

    // Use HTTP polling (WebSocket public endpoints are unreliable)
    let provider = new ethers.JsonRpcProvider(RPC_HTTP);
    const block = await provider.getBlockNumber();
    console.log('Connected via HTTP polling, block:', block);

    const curve = new ethers.Contract(CURVE, CURVE_ABI, provider);

    let lastBlock = block;

    // Poll for Buy/Sell events every 15 seconds
    async function pollEvents() {
        try {
            const currentBlock = await provider.getBlockNumber();
            if (currentBlock <= lastBlock) return;

            const buyFilter = curve.filters.Buy();
            const sellFilter = curve.filters.Sell();

            const [buyEvents, sellEvents] = await Promise.all([
                curve.queryFilter(buyFilter, lastBlock + 1, currentBlock),
                curve.queryFilter(sellFilter, lastBlock + 1, currentBlock)
            ]);

            for (const evt of buyEvents) {
                const ethCost = ethers.formatEther(evt.args[2]);
                const msg = `SINC BUY: ${evt.args[1]} SINC for ${ethCost} ETH by ${evt.args[0].substring(0, 8)}...`;
                console.log(`[${new Date().toISOString()}]`, msg);
                sendSMS(msg);
            }

            for (const evt of sellEvents) {
                const ethRefund = ethers.formatEther(evt.args[2]);
                const msg = `SINC SELL: ${evt.args[1]} SINC for ${ethRefund} ETH by ${evt.args[0].substring(0, 8)}...`;
                console.log(`[${new Date().toISOString()}]`, msg);
                sendSMS(msg);
            }

            lastBlock = currentBlock;
        } catch (e) {
            console.error('Poll error:', e.message);
        }
    }

    setInterval(pollEvents, 15000);
    console.log('Polling for Buy/Sell events every 15s...\n');

    // Heartbeat every 5 minutes
    setInterval(async () => {
        try {
            const sold = await curve.tokensSold();
            const price = ethers.formatEther(await curve.currentPrice());
            const reserve = ethers.formatEther(await curve.reserveBalance());
            console.log(`[${new Date().toISOString()}] Heartbeat: ${sold} sold, price ${price} ETH, reserve ${reserve} ETH`);
        } catch (e) {
            console.error('Heartbeat error:', e.message);
        }
    }, 5 * 60 * 1000);
}

startWatcher().catch(err => {
    console.error('FATAL:', err.message);
    process.exit(1);
});
