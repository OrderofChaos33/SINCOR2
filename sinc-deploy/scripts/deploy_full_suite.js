const { ethers } = require("hardhat");

async function main() {
  console.log("Starting full SINC ecosystem deployment...");

  // Resolve deployer
  let deployer;
  const signers = await ethers.getSigners().catch(() => []);
  if (signers && signers.length > 0) {
    deployer = signers[0];
  } else if (process.env.PRIVATE_KEY && process.env.BASE_SEPOLIA_RPC_URL) {
    const provider = new ethers.JsonRpcProvider(process.env.BASE_SEPOLIA_RPC_URL);
    const wallet = new ethers.Wallet(process.env.PRIVATE_KEY, provider);
    deployer = wallet;
  } else {
    console.error('❌ No deployer signer found. Ensure PRIVATE_KEY and RPC URL are set in .env');
    process.exit(1);
  }

  console.log("Deploying with account:", deployer.address);
  const balance = await ethers.provider.getBalance(deployer.address);
  console.log("Account balance:", ethers.formatEther(balance));

  // 1. Deploy SINC Token (Core)
  console.log("\n1. Deploying SINC Token...");
  const SINC = await ethers.getContractFactory("SINC", deployer);
  const sinc = await SINC.deploy();
  await sinc.waitForDeployment();
  const sincAddress = await sinc.getAddress();
  console.log("✅ SINC deployed to:", sincAddress);

  // 2. Deploy SINCAgentToken (Soulbound Identity)
  console.log("\n2. Deploying SINCAgentToken (Agents)...");
  const AgentToken = await ethers.getContractFactory("SINCAgentToken", deployer);
  const agentToken = await AgentToken.deploy();
  await agentToken.waitForDeployment();
  console.log("✅ SINCAgentToken deployed to:", await agentToken.getAddress());

  // 3. Deploy SINCCarbonCredit (RWA)
  console.log("\n3. Deploying SINCCarbonCredit (RWA)...");
  const CarbonCredit = await ethers.getContractFactory("SINCCarbonCredit", deployer);
  const carbonCredit = await CarbonCredit.deploy();
  await carbonCredit.waitForDeployment();
  console.log("✅ SINCCarbonCredit deployed to:", await carbonCredit.getAddress());

  // 4. Deploy SINCPredictionMarket (Utility)
  console.log("\n4. Deploying SINCPredictionMarket...");
  const PredictionMarket = await ethers.getContractFactory("SINCPredictionMarket", deployer);
  const predictionMarket = await PredictionMarket.deploy(sincAddress);
  await predictionMarket.waitForDeployment();
  console.log("✅ SINCPredictionMarket deployed to:", await predictionMarket.getAddress());

  // 5. Deploy SINCairdrop (Marketing)
  console.log("\n5. Deploying SINCairdrop...");
  const Airdrop = await ethers.getContractFactory("SINCairdrop", deployer);
  const airdrop = await Airdrop.deploy(sincAddress);
  await airdrop.waitForDeployment();
  console.log("✅ SINCairdrop deployed to:", await airdrop.getAddress());

  console.log("\n Deployment Complete! 🚀");
  console.log("----------------------------------------------------");
  console.log(`SINC:               ${sincAddress}`);
  console.log(`SINCAgentToken:     ${await agentToken.getAddress()}`);
  console.log(`SINCCarbonCredit:   ${await carbonCredit.getAddress()}`);
  console.log(`SINCPredictionMarket:${await predictionMarket.getAddress()}`);
  console.log(`SINCairdrop:        ${await airdrop.getAddress()}`);
  console.log("----------------------------------------------------");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
