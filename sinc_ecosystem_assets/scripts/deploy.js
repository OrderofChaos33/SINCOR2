import { ethers } from "hardhat";

async function main() {
  const SINC = await ethers.getContractFactory("SINC");
  const sinc = await SINC.deploy();

  await sinc.deployed();

  console.log("SINC deployed to:", sinc.address);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});