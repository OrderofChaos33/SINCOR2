const { ethers } = require("hardhat");

const routerAbi = [
  "function getAmountsOut(uint amountIn, address[] calldata path) view returns (uint[] memory amounts)",
  "function swapExactTokensForETH(uint amountIn,uint amountOutMin,address[] calldata path,address to,uint deadline) returns (uint[] memory amounts)",
  "function WETH() view returns (address)"
];

const erc20Abi = [
  "function approve(address spender, uint256 amount) external returns (bool)",
  "function decimals() view returns (uint8)"
];

async function main() {
  const routerAddress = process.env.UNISWAP_V2_ROUTER;
  const tokenIn = process.argv[2]; // SINC address
  const desiredEthUSD = process.argv[3] || "5000"; // USD amount to convert to ETH approx (we'll approximate via price oracle path)
  const maxSincToSpend = process.argv[4] || "50000"; // max SINC to sell as fallback
  const recipient = process.argv[5]; // final ETH recipient

  if (!routerAddress || !tokenIn || !recipient) {
    console.error("Usage: node scripts/swap_sinc_for_eth.js <SINC_ADDR> <DESIRED_USD_EQUIV> <MAX_SINC> <RECIPIENT>");
    process.exit(1);
  }

  const [deployer] = await ethers.getSigners();
  const router = new ethers.Contract(routerAddress, routerAbi, deployer);
  const SINC = new ethers.Contract(tokenIn, erc20Abi, deployer);

  // Estimate ETH per USD via onchain or use a quick chainlink method; for now assume 1 ETH ~ $2000 for estimation (replace with oracle in production)
  const ETH_USD_EST = parseFloat(process.env.ETH_USD_EST || "2000");
  const targetEth = parseFloat(desiredEthUSD) / ETH_USD_EST;

  console.log(`Target ETH to obtain: ${targetEth}`);

  const weth = await router.WETH();

  const decimalsIn = 18;
  const maxSinc = ethers.parseUnits(maxSincToSpend, decimalsIn);

  // We'll attempt incremental swaps: start with a small fraction and increase until we get target ETH or hit maxSinc
  let amountIn = ethers.parseUnits("1000", decimalsIn); // start with 1000 SINC

  await (await SINC.approve(routerAddress, maxSinc)).wait();

  let totalEthObtained = ethers.parseEther("0");
  const deadline = Math.floor(Date.now() / 1000) + 60 * 20;
  while (amountIn.lte(maxSinc)) {
    try {
      const path = [tokenIn, weth];
      const amountsOut = await router.getAmountsOut(amountIn, path);
      const expectedEth = amountsOut[amountsOut.length - 1];
      console.log(`Trying swap amount ${amountIn.toString()} => expected ETH ${ethers.formatUnits(expectedEth, 18)}`);

      const tx = await router.swapExactTokensForETH(amountIn, 0, path, deployer.address, deadline);
      console.log("Swap tx sent:", tx.hash);
      const receipt = await tx.wait();
      // Calculate ETH received from logs by checking balance differences
      // For simplicity, check deployer balance after each swap
      const ethBal = await deployer.getBalance();
      console.log("Deployer ETH balance now:", ethers.formatEther(ethBal));

      // If we've reached approximate target balance or did initial target, break
      // In production, track exact amounts; here we stop when deployer has at least targetEth more than prior
      // For safety, run a single swap and then send target amount
      break;
    } catch (err) {
      console.error("Swap failed, increasing amount or aborting:", err);
      amountIn = amountIn.mul(2);
      if (amountIn.gt(maxSinc)) {
        console.error("Reached max SINC to spend, aborting");
        process.exit(1);
      }
    }
  }

  // After a successful swap, transfer target ETH amount to recipient
  const toSendEth = ethers.parseEther((process.env.FORWARD_ETH_AMOUNT || "2.5").toString()); // default 2.5 ETH (approx $5k at $2k/ETH)
  console.log(`Sending ${ethers.formatEther(toSendEth)} ETH to ${recipient}`);
  const sendTx = await deployer.sendTransaction({ to: recipient, value: toSendEth });
  console.log("Transfer tx:", sendTx.hash);
  await sendTx.wait();
  console.log("Transfer completed");
}

main().catch((err) => { console.error(err); process.exitCode = 1; });