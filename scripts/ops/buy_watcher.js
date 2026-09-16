/**
 * SINC Transfer Watcher — canonical live token on Base
 * Usage: node buy_watcher.js
 * Env: BASE_RPC_URL, NOTIFY_PHONE, TWILIO_SID, TWILIO_AUTH, TWILIO_FROM (all optional except RPC)
 */

const { ethers } = require('ethers');

const SINC_TOKEN = process.env.SINC_TOKEN || '0xe1D836087F6573b665d25CE088793E916D7892f8';
const RPC_HTTP = process.env.BASE_RPC_URL || 'https://mainnet.base.org';
const SINC_DECIMALS = 8;

const TWILIO_SID = process.env.TWILIO_SID || process.env.TWILO_ID || '';
const TWILIO_AUTH = process.env.TWILIO_AUTH || process.env.TWILO_AUTH || '';
const TWILIO_FROM = process.env.TWILIO_FROM || process.env.TWILO_NUMBER || '';
const NOTIFY_TO = process.env.NOTIFY_PHONE || process.env.ADMIN_SMS_NUMBER || '';

const TOKEN_ABI = [
    'event Transfer(address indexed from, address indexed to, uint256 value)',
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
    console.log('=== SINC Transfer Watcher (canonical live token) ===');
    console.log('Token:', SINC_TOKEN);
    console.log('RPC:', RPC_HTTP);
    console.log('Notify:', NOTIFY_TO || 'console only');
    console.log('');

    const provider = new ethers.JsonRpcProvider(RPC_HTTP);
    const block = await provider.getBlockNumber();
    console.log('Connected, block:', block);

    const token = new ethers.Contract(SINC_TOKEN, TOKEN_ABI, provider);
    let lastBlock = block;

    async function pollEvents() {
        try {
            const currentBlock = await provider.getBlockNumber();
            if (currentBlock <= lastBlock) return;

            const transferEvents = await token.queryFilter(token.filters.Transfer(), lastBlock + 1, currentBlock);

            for (const evt of transferEvents) {
                const sincAmount = fmtSinc(evt.args.value);
                const from = evt.args.from;
                const to = evt.args.to;
                const msg = `SINC TRANSFER: ${sincAmount} SINC | ${from.slice(0, 10)}… -> ${to.slice(0, 10)}…`;
                console.log(`[${new Date().toISOString()}]`, msg);
                sendSMS(msg);
            }

            lastBlock = currentBlock;
        } catch (e) {
            console.error('Poll error:', e.message);
        }
    }

    setInterval(pollEvents, 15000);
    console.log('Polling transfers every 15s…\n');

    setInterval(async () => {
        try {
            const blockNo = await provider.getBlockNumber();
            console.log(`[${new Date().toISOString()}] Heartbeat: canonical SINC watcher live at block ${blockNo}`);
        } catch (e) {
            console.error('Heartbeat error:', e.message);
        }
    }, 5 * 60 * 1000);
}

startWatcher().catch((err) => {
    console.error('FATAL:', err.message);
    process.exit(1);
});