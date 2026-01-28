const { ethers } = require("hardhat");

const routerAbi = [
  "function swapExactTokensForTokens(uint amountIn,uint amountOutMin,address[] calldata path,address to,uint deadline) returns (uint[] memory amounts)"
];

const erc20Abi = [
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function decimals() view returns (uint8)"
];

async function main() {
  const routerAddress = process.env.UNISWAP_V2_ROUTER;
  const tokenIn = process.argv[2];
  const tokenOut = process.argv[3];
  const amountInStr = process.argv[4] || "1"; // amount in human units

  if (!routerAddress || !tokenIn || !tokenOut) {
    console.error("Usage: node scripts/swap_sinc.js <TOKEN_IN> <TOKEN_OUT> <AMOUNT_IN> (set UNISWAP_V2_ROUTER in env)");
    process.exit(1);
  }

  const [deployer] = await ethers.getSigners();
  const router = new ethers.Contract(routerAddress, routerAbi, deployer);
  const tokenInC = new ethers.Contract(tokenIn, erc20Abi, deployer);

  const decimalsIn = await tokenInC.decimals();
  const amountIn = ethers.parseUnits(amountInStr, decimalsIn);

  await (await tokenInC.approve(routerAddress, amountIn)).wait();

  const path = [tokenIn, tokenOut];
  const deadline = Math.floor(Date.now() / 1000) + 60 * 20;
  const amountOutMin = 0; // TODO: set slippage

  const tx = await router.swapExactTokensForTokens(amountIn, amountOutMin, path, deployer.address, deadline);
  console.log("Swap tx sent:", tx.hash);
  await tx.wait();
  console.log("Swap complete");
}

main().catch((err) => { console.error(err); process.exitCode = 1; });