const { ethers } = require("hardhat");

async function main() {
  const [deployer] = await ethers.getSigners();
  console.log("Deploying contracts with account:", deployer.address);

  const SINC = await ethers.getContractFactory("SINC");
  const sinc = await SINC.deploy();
  await sinc.deployed();

  console.log("SINC deployed to:", sinc.address);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
