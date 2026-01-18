const { ethers } = require("ethers");
require("dotenv").config();

const RPC_URL = process.env.BASE_RPC_URL || "https://base.publicnode.com";
const SINC_ADDRESS = process.env.SINC_TOKEN_ADDRESS || "0xd10D86D09ee4316CdD3585fd6486537b7119A073";
const WETH_ADDRESS = "0x4200000000000000000000000000000000000006";
const AERO_FACTORY_ADDRESS = "0x420DD381b31aEf6683db6B902084cB0FFECe40Da";

// Minimal Pair ABI
const PAIR_ABI = [
  "function getReserves() view returns (uint112 reserve0, uint112 reserve1, uint32 blockTimestampLast)",
  "function token0() view returns (address)",
  "function token1() view returns (address)"
];

const FACTORY_ABI = [
  "function getPool(address tokenA, address tokenB, bool stable) view returns (address)"
];

const provider = new ethers.JsonRpcProvider(RPC_URL);

async function main() {
    console.log("Checking Aerodrome SINC/WETH Liquidity...");
    const factory = new ethers.Contract(AERO_FACTORY_ADDRESS, FACTORY_ABI, provider);
    
    // Check Volatile Pool
    const poolAddr = await factory.getPool(SINC_ADDRESS, WETH_ADDRESS, false); // stable = false
    console.log(`Pool Address (Volatile): ${poolAddr}`);

    if (poolAddr === ethers.ZeroAddress) {
        console.log("❌ No Pool Exists.");
        return;
    }

    const pool = new ethers.Contract(poolAddr, PAIR_ABI, provider);
    try {
        const [r0, r1] = await pool.getReserves();
        const t0 = await pool.token0();
        
        const sincReserves = (t0.toLowerCase() === SINC_ADDRESS.toLowerCase()) ? r0 : r1;
        const wethReserves = (t0.toLowerCase() === WETH_ADDRESS.toLowerCase()) ? r0 : r1;

        console.log(`Liquidity:`);
        console.log(`  SINC: ${ethers.formatEther(sincReserves)}`);
        console.log(`  WETH: ${ethers.formatEther(wethReserves)}`);
        
        if (sincReserves === 0n || wethReserves === 0n) {
            console.log("❌ Pool Exists but Empty (No Liquidity).");
        } else {
            console.log("✅ Pool has Liquidity.");
        }
    } catch (e) {
        console.error("Error reading reserves:", e.message);
    }
}
main();