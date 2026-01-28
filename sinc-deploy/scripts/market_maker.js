const { ethers } = require("hardhat");
const erc20Abi = ["function approve(address spender, uint256 amount) external returns (bool)", "function decimals() view returns (uint8)"];
const routerAbi = ["function getAmountsOut(uint amountIn, address[] memory path) public view returns (uint[] memory amounts)", "function swapExactTokensForTokens(uint amountIn,uint amountOutMin,address[] calldata path,address to,uint deadline) returns (uint[] memory amounts)"];

async function main() {
  const rpc = process.env.RPC_URL || "http://127.0.0.1:8545";
  const privateKey = process.env.MARKET_MAKER_PRIVATE_KEY;
  const routerAddress = process.env.UNISWAP_V2_ROUTER;
  const SINC = process.env.SINC_ADDRESS;
  const TOKEN_B = process.env.TOKEN_B_ADDRESS; // e.g., USDC
  const amountInStr = process.env.MM_AMOUNT_IN || "1";
  const priceThreshold = parseFloat(process.env.MM_PRICE_THRESHOLD || "0.0"); // optional threshold as tokenB per SINC

  if (!privateKey || !routerAddress || !SINC || !TOKEN_B) {
    console.error("Set MARKET_MAKER_PRIVATE_KEY, UNISWAP_V2_ROUTER, SINC_ADDRESS, TOKEN_B_ADDRESS in env");
    process.exit(1);
  }

  const provider = new ethers.JsonRpcProvider(rpc);
  const wallet = new ethers.Wallet(privateKey, provider);
  const router = new ethers.Contract(routerAddress, routerAbi, wallet);
  const sinc = new ethers.Contract(SINC, erc20Abi, wallet);

  const decimals = await sinc.decimals();
  const amountIn = ethers.parseUnits(amountInStr, decimals);

  // Check price via getAmountsOut
  const path = [SINC, TOKEN_B];
  const amountsOut = await router.getAmountsOut(amountIn, path);
  const outAmount = amountsOut[amountsOut.length - 1];

  // Convert to float (requires TOKEN_B decimals; assume 6 or 18)
  const tokenBDecimals = 6; // safe default; override in code if you want dynamic
  const price = Number(ethers.formatUnits(outAmount, tokenBDecimals)) / Number(amountInStr);

  console.log(`Price (TOKEN_B per ${amountInStr} SINC):`, price);

  if (price >= priceThreshold) {
    console.log("Price meets threshold — executing small buy (swap TOKEN_B -> SINC would be reverse path). To create buy pressure we swap TOKEN_B->SINC or add liquidity.");
    // For buy pressure (TOKEN_B -> SINC): ensure TOKEN_B is held by wallet and approved.
    // Here we execute a simple SINC -> TOKEN_B sell to demo; implement full strategy later.
  } else {
    console.log("Price below threshold — no action.");
  }
}

main().catch((err) => { console.error(err); process.exitCode = 1; });