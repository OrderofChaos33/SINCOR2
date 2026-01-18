const { ethers } = require("ethers");
require("dotenv").config();
const RPC_URL = process.env.BASE_RPC_URL || "https://base.publicnode.com";
const PRIVATE_KEY = process.env.PRIVATE_KEY;

const provider = new ethers.JsonRpcProvider(RPC_URL);
const wallet = new ethers.Wallet(PRIVATE_KEY, provider);

async function main() {
    const bal = await provider.getBalance(wallet.address);
    console.log(`ETH Balance: ${ethers.formatEther(bal)} ETH`);
}
main();