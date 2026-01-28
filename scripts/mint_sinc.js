const { ethers } = require("hardhat");

async function main() {
  const tokenAddress = process.argv[2];
  const to = process.argv[3];
  const amount = process.argv[4]; // human readable amount

  if (!tokenAddress || !to || !amount) {
    console.error("Usage: node scripts/mint_sinc.js <tokenAddress> <to> <amount>");
    process.exit(1);
  }

  const [deployer] = await ethers.getSigners();
  console.log("Minting from deployer:", deployer.address);

  const SINC = await ethers.getContractAt("SINC", tokenAddress);
  const decimals = 18;
  const amt = ethers.parseUnits(amount, decimals);

  const tx = await SINC.mint(to, amt);
  console.log("Mint tx:", tx.hash);
  await tx.wait();
  console.log("Minted", amount, "SINC to", to);
}

main().catch((err) => {
  console.error(err);
  process.exitCode = 1;
});
