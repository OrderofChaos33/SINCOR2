const { ethers } = require("hardhat");

const routerAbi = [
  "function addLiquidity(address tokenA,address tokenB,uint amountADesired,uint amountBDesired,uint amountAMin,uint amountBMin,address to,uint deadline) returns (uint amountA, uint amountB, uint liquidity)",
  "function WETH() view returns (address)"
];

const erc20Abi = [
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function decimals() view returns (uint8)"
];

async function main() {
  const routerAddress = process.env.UNISWAP_V2_ROUTER;
  const tokenA = process.argv[2];
  const tokenB = process.argv[3];
  const amountAStr = process.argv[4] || "1000";
  const amountBStr = process.argv[5] || "100";

  if (!routerAddress || !tokenA || !tokenB) {
    console.error("Usage: node scripts/add_liquidity.js <SINC_ADDR> <TOKEN_B_ADDR> <AMOUNT_SINC> <AMOUNT_B> (set UNISWAP_V2_ROUTER in env)");
    process.exit(1);
  }

  const [deployer] = await ethers.getSigners();
  console.log("Adding liquidity with account:", deployer.address);

  const router = new ethers.Contract(routerAddress, routerAbi, deployer);
  const SINC = new ethers.Contract(tokenA, erc20Abi, deployer);
  const TOKENB = new ethers.Contract(tokenB, erc20Abi, deployer);

  const decimalsA = 18;
  const decimalsB = await TOKENB.decimals();

  const amountA = ethers.parseUnits(amountAStr, decimalsA);
  const amountB = ethers.parseUnits(amountBStr, decimalsB);

  await (await SINC.approve(routerAddress, amountA)).wait();
  await (await TOKENB.approve(routerAddress, amountB)).wait();

  const deadline = Math.floor(Date.now() / 1000) + 60 * 20;
  const tx = await router.addLiquidity(
    tokenA,
    tokenB,
    amountA,
    amountB,
    0,
    0,
    deployer.address,
    deadline
  );
  console.log("addLiquidity tx sent:", tx.hash);
  await tx.wait();
  console.log("Liquidity added for pair:", tokenA, tokenB);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});