/**
 * SINC Buy Event Watcher — live bonding curve on Base
 * Usage: node buy_watcher.js
 * Env: BASE_RPC_URL, NOTIFY_PHONE, TWILIO_SID, TWILIO_AUTH, TWILIO_FROM (all optional except RPC)
 */

const { ethers } = require('ethers');

const CURVE = process.env.SINC_CURVE || '0x75dE341a2BC81806198364F125d4Cde36527619C';
const RPC_HTTP = process.env.BASE_RPC_URL || 'https://mainnet.base.org';
const SINC_DECIMALS = 8;

const TWILIO_SID = process.env.TWILIO_SID || process.env.TWILO_ID || '';
const TWILIO_AUTH = process.env.TWILIO_AUTH || process.env.TWILO_AUTH || '';
const TWILIO_FROM = process.env.TWILIO_FROM || process.env.TWILO_NUMBER || '';
const NOTIFY_TO = process.env.NOTIFY_PHONE || process.env.ADMIN_SMS_NUMBER || '';

const CURVE_ABI = [
    'event Buy(address indexed buyer, uint256 ethIn, uint256 sincOut, address indexed referrer)',
    'event Sell(address indexed seller, uint256 sincIn, uint256 ethOut)',
    'function sincSold() view returns (uint256)',
    'function ethAccumulated() view returns (uint256)',
];

function sendSMS(message) {
    if (!NOTIFY_TO || !TWILIO_SID || !TWILIO_AUTH || !TWILIO_FROM) {
        console.log('[SMS skipped — set NOTIFY_PHONE + Twilio env vars]', message);
        return;
    }

    const https = require('https');
    const data = new URLSearchParams({ To: NOTIFY_TO, From: TWILIO_FROM, Body: message }).toString();
    const options = {
        hostname: 'api.twilio.com',
        path: `/2010-04-01/Accounts/${TWILIO_SID}/Messages.json`,
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': data.length,
            Authorization: 'Basic ' + Buffer.from(`${TWILIO_SID}:${TWILIO_AUTH}`).toString('base64'),
        },
    };
    const req = https.request(options, (res) => {
        let body = '';
        res.on('data', (c) => (body += c));
        res.on('end', () => {
            if (res.statusCode === 201) console.log('[SMS sent]', message);
            else console.error('[SMS failed]', res.statusCode, body.slice(0, 200));
        });
    });
    req.on('error', (e) => console.error('[SMS error]', e.message));
    req.write(data);
    req.end();
}

function fmtSinc(raw) {
    return Number(ethers.formatUnits(raw, SINC_DECIMALS)).toLocaleString();
}

async function startWatcher() {
    console.log('=== SINC Buy Watcher (live curve) ===');
    console.log('Curve:', CURVE);
    console.log('RPC:', RPC_HTTP);
    console.log('Notify:', NOTIFY_TO || 'console only');
    console.log('');

    const provider = new ethers.JsonRpcProvider(RPC_HTTP);
    const block = await provider.getBlockNumber();
    console.log('Connected, block:', block);

    const curve = new ethers.Contract(CURVE, CURVE_ABI, provider);
    let lastBlock = block;

    async function pollEvents() {
        try {
            const currentBlock = await provider.getBlockNumber();
            if (currentBlock <= lastBlock) return;

            const [buyEvents, sellEvents] = await Promise.all([
                curve.queryFilter(curve.filters.Buy(), lastBlock + 1, currentBlock),
                curve.queryFilter(curve.filters.Sell(), lastBlock + 1, currentBlock),
            ]);

            for (const evt of buyEvents) {
                const ethIn = ethers.formatEther(evt.args.ethIn);
                const sincOut = fmtSinc(evt.args.sincOut);
                const ref = evt.args.referrer;
                const msg = `SINC BUY: ${sincOut} SINC for ${ethIn} ETH | buyer ${evt.args.buyer.slice(0, 10)}… | ref ${ref.slice(0, 10)}…`;
                console.log(`[${new Date().toISOString()}]`, msg);
                sendSMS(msg);
            }

            for (const evt of sellEvents) {
                const ethOut = ethers.formatEther(evt.args.ethOut);
                const sincIn = fmtSinc(evt.args.sincIn);
                const msg = `SINC SELL: ${sincIn} SINC for ${ethOut} ETH | ${evt.args.seller.slice(0, 10)}…`;
                console.log(`[${new Date().toISOString()}]`, msg);
                sendSMS(msg);
            }

            lastBlock = currentBlock;
        } catch (e) {
            console.error('Poll error:', e.message);
        }
    }

    setInterval(pollEvents, 15000);
    console.log('Polling Buy/Sell every 15s…\n');

    setInterval(async () => {
        try {
            const sold = await curve.sincSold();
            const eth = ethers.formatEther(await curve.ethAccumulated());
            console.log(
                `[${new Date().toISOString()}] Heartbeat: ${fmtSinc(sold)} SINC sold | ${eth} ETH accumulated`
            );
        } catch (e) {
            console.error('Heartbeat error:', e.message);
        }
    }, 5 * 60 * 1000);
}

startWatcher().catch((err) => {
    console.error('FATAL:', err.message);
    process.exit(1);
});