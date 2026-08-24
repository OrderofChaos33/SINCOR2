import axios from 'axios';
import * as fs from 'fs';

// --- CONFIGURATION ---
const BASESCAN_API_KEY = process.env.BASESCAN_API_KEY || '';
const PING_INTERVAL_MS = 300000; // 5 minutes

// Canonical Base addresses (CANONICAL_ADDRESSES.md). Env overrides always win.
const TOKENS = [
  {
    symbol: 'SINC',
    address: process.env.SINC_TOKEN_ADDRESS || '0xe1D836087F6573b665d25CE088793E916D7892f8',
  },
  {
    symbol: 'AXM',
    address: process.env.AXM_TOKEN_ADDRESS || '0x4c3fb66f14fbaa2088c9ae91017ba770da53715a',
  },
];

interface IndexerHealthReport {
  timestamp: string;
  symbol: string;
  address: string;
  basescanVerified: boolean;
  dexScreenerIndexed: boolean;
  dexScreenerPairsCount: number;
  dexScreenerTotalTvlUsd: number;
  geckoTerminalIndexed: boolean;
  geckoTerminalPoolsCount: number;
  status: 'READY' | 'INDEXING' | 'ACTION_REQUIRED';
}

/**
 * Checks contract verification status on Basescan (Base L2)
 */
async function checkBasescan(address: string): Promise<boolean> {
  try {
    const url = `https://api.basescan.org/api?module=contract&action=getsourcecode&address=${address}&apikey=${BASESCAN_API_KEY}`;
    const response = await axios.get(url, { timeout: 10000 });
    const result = response.data?.result?.[0];
    return result && result.ABI !== 'Contract source code not verified';
  } catch (err) {
    console.warn(`[!] Basescan ping warning for ${address}:`, (err as Error).message);
    return false;
  }
}

/**
 * Pings DexScreener API to trigger indexer cache refresh and fetch active market pairs
 */
async function checkDexScreener(address: string) {
  try {
    const url = `https://api.dexscreener.com/latest/dex/tokens/${address}`;
    const response = await axios.get(url, { timeout: 10000 });
    const pairs = response.data?.pairs || [];

    const totalTvl = pairs.reduce((acc: number, p: any) => acc + (parseFloat(p.liquidity?.usd) || 0), 0);
    return {
      indexed: pairs.length > 0,
      pairsCount: pairs.length,
      totalTvlUsd: totalTvl,
    };
  } catch (err) {
    console.warn(`[!] DexScreener ping warning for ${address}:`, (err as Error).message);
    return { indexed: false, pairsCount: 0, totalTvlUsd: 0 };
  }
}

/**
 * Pings GeckoTerminal V2 API to check on-chain pool discovery and token profile
 */
async function checkGeckoTerminal(address: string) {
  try {
    const url = `https://api.geckoterminal.com/api/v2/networks/base/tokens/${address}`;
    const response = await axios.get(url, {
      headers: { Accept: 'application/json;version=20230203' },
      timeout: 10000,
    });

    const tokenData = response.data?.data;
    const poolsUrl = `https://api.geckoterminal.com/api/v2/networks/base/tokens/${address}/pools`;
    const poolsResponse = await axios.get(poolsUrl, {
      headers: { Accept: 'application/json;version=20230203' },
      timeout: 10000,
    });

    const poolsCount = poolsResponse.data?.data?.length || 0;
    return {
      indexed: !!tokenData,
      poolsCount,
    };
  } catch (err) {
    console.warn(`[!] GeckoTerminal ping warning for ${address}:`, (err as Error).message);
    return { indexed: false, poolsCount: 0 };
  }
}

/**
 * Agent loop function to ping indexers and compile health state
 */
async function pingIndexers() {
  console.log(`\n==================================================`);
  console.log(`[Indexer Agent] Running Indexer Sync Sweep: ${new Date().toISOString()}`);
  console.log(`==================================================`);

  const reports: IndexerHealthReport[] = [];

  for (const token of TOKENS) {
    if (token.address === '0x0000000000000000000000000000000000000000') {
      console.log(`[!] Skipping ${token.symbol}: Contract address not set in env variables.`);
      continue;
    }

    console.log(`\n[+] Auditing Indexer Sync for ${token.symbol} (${token.address})...`);

    const basescanVerified = await checkBasescan(token.address);
    const dexScreener = await checkDexScreener(token.address);
    const geckoTerminal = await checkGeckoTerminal(token.address);

    let status: 'READY' | 'INDEXING' | 'ACTION_REQUIRED' = 'ACTION_REQUIRED';

    if (basescanVerified && dexScreener.indexed && geckoTerminal.indexed) {
      status = 'READY';
    } else if (dexScreener.indexed || geckoTerminal.indexed) {
      status = 'INDEXING';
    }

    const report: IndexerHealthReport = {
      timestamp: new Date().toISOString(),
      symbol: token.symbol,
      address: token.address,
      basescanVerified,
      dexScreenerIndexed: dexScreener.indexed,
      dexScreenerPairsCount: dexScreener.pairsCount,
      dexScreenerTotalTvlUsd: dexScreener.totalTvlUsd,
      geckoTerminalIndexed: geckoTerminal.indexed,
      geckoTerminalPoolsCount: geckoTerminal.poolsCount,
      status,
    };

    reports.push(report);

    console.log(`    ├── Basescan Verified:    ${basescanVerified ? 'YES' : 'NO'}`);
    console.log(
      `    ├── DexScreener Indexed:  ${
        dexScreener.indexed
          ? `YES (${dexScreener.pairsCount} pairs, $${dexScreener.totalTvlUsd.toFixed(2)} TVL)`
          : 'NO'
      }`
    );
    console.log(
      `    └── GeckoTerminal Status: ${
        geckoTerminal.indexed ? `YES (${geckoTerminal.poolsCount} pools found)` : 'NO'
      }`
    );
    console.log(`    └─ STATUS OVERALL:       [ ${status} ]`);
  }

  fs.writeFileSync('indexer-health.json', JSON.stringify(reports, null, 2));
  console.log(`\n[+] Health snapshot stored in indexer-health.json`);
}

async function main() {
  await pingIndexers();

  if (process.env.RUN_ONCE === 'true') {
    process.exit(0);
  }

  setInterval(pingIndexers, PING_INTERVAL_MS);
}

main().catch((err) => {
  console.error('[!] Agent Crash:', err);
  process.exit(1);
});
