const { ethers } = require("hardhat");

const routerAbi = [
  "function addLiquidityETH(address token,uint amountTokenDesired,uint amountTokenMin,uint amountETHMin,address to,uint deadline) payable returns (uint amountToken, uint amountETH, uint liquidity)",
  "function WETH() view returns (address)"
];

const erc20Abi = [
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function decimals() view returns (uint8)"
];

async function main() {
  const routerAddress = process.env.UNISWAP_V2_ROUTER;
  const tokenA = process.argv[2]; // SINC address
  const amountAStr = process.argv[3] || "150000"; // SINC amount
  const amountEthStr = process.argv[4] || "10"; // ETH amount in ETH

  if (!routerAddress || !tokenA) {
    console.error("Usage: node scripts/add_liquidity_eth.js <SINC_ADDR> <AMOUNT_SINC> <AMOUNT_ETH>");
    process.exit(1);
  }

  const [deployer] = await ethers.getSigners();
  console.log("Adding liquidity with account:", deployer.address);

  const router = new ethers.Contract(routerAddress, routerAbi, deployer);
  const SINC = new ethers.Contract(tokenA, erc20Abi, deployer);

  const decimalsA = 18; // SINC decimals
  const amountA = ethers.parseUnits(amountAStr, decimalsA);
  const amountETH = ethers.parseEther(amountEthStr);

  // Approve router to pull SINC tokens
  const approveA = await SINC.approve(routerAddress, amountA);
  console.log("Approve tx:", approveA.hash);
  await approveA.wait();

  const deadline = Math.floor(Date.now() / 1000) + 60 * 20;

  const tx = await router.addLiquidityETH(
    tokenA,
    amountA,
    0,
    0,
    deployer.address,
    deadline,
    { value: amountETH }
  );
  console.log("addLiquidityETH tx sent:", tx.hash);
  await tx.wait();
  console.log("Liquidity added for pair (SINC/ETH)");
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});