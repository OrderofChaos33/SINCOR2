const { ethers } = require("hardhat");

async function main() {
  // Resolve deployer account: prefer Hardhat getSigners(), fallback to DEPLOYER_PRIVATE_KEY
  let deployer;
  const signers = await ethers.getSigners().catch(() => []);
  if (signers && signers.length > 0 && signers[0] && signers[0].address) {
    deployer = signers[0];
  } else if (process.env.DEPLOYER_PRIVATE_KEY && process.env.BASE_RPC_URL) {
    const provider = new ethers.JsonRpcProvider(process.env.BASE_RPC_URL);
    const wallet = new ethers.Wallet(process.env.DEPLOYER_PRIVATE_KEY, provider);
    deployer = wallet;
  } else {
    console.error('❌ No deployer signer found. Set DEPLOYER_PRIVATE_KEY and BASE_RPC_URL, or run with Hardhat network and unlocked accounts.');
    process.exit(1);
  }

  console.log("Deploying contracts with account:", deployer.address);

  const SINC = await ethers.getContractFactory("SINC", deployer);
  const sinc = await SINC.deploy();
  await sinc.waitForDeployment();

  console.log("SINC deployed to:", await sinc.getAddress());
}
main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});