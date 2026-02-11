const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("SINC Ecosystem Upgrades", function () {
  let SINC, sinc, SINCAgentToken, agentToken;
  let owner, addr1, addr2;

  beforeEach(async function () {
    [owner, addr1, addr2] = await ethers.getSigners();

    // Deploy updated SINC
    SINC = await ethers.getContractFactory("SINC");
    sinc = await SINC.deploy();
    await sinc.waitForDeployment();

    // Deploy updated SINCAgentToken
    SINCAgentToken = await ethers.getContractFactory("SINCAgentToken");
    agentToken = await SINCAgentToken.deploy();
    await agentToken.waitForDeployment();
  });

  describe("SINC Token Upgrades", function () {
    it("Should enforce max supply of 1.2B", async function () {
      const maxSupply = await sinc.MAX_SUPPLY();
      expect(maxSupply).to.equal(ethers.parseEther("1200000000"));
    });

    it("Should apply 1% burn on transfers", async function () {
      const amount = ethers.parseEther("1000");
      // Transfer to addr1 first
      await sinc.transfer(addr1.address, amount);
      
      const addr1Balance = await sinc.balanceOf(addr1.address);
      // addr1 should receive 990 (1% of 1000 is 10)
      expect(addr1Balance).to.equal(ethers.parseEther("990"));
    });
  });

  describe("SINCAgentToken Upgrades", function () {
    it("Should initialize reputation at 500", async function () {
      await agentToken.mintAgent(
        addr1.address,
        "Test Agent",
        ["read"],
        1,
        0,
        3600
      );
      const tokenId = await agentToken.agentTokens(addr1.address);
      expect(await agentToken.agentReputation(tokenId)).to.equal(500);
    });

    it("Should allow reputation updates by owner", async function () {
      await agentToken.mintAgent(addr1.address, "Test", [], 1, 0, 3600);
      const tokenId = await agentToken.agentTokens(addr1.address);
      
      await agentToken.updateReputation(tokenId, 800);
      expect(await agentToken.agentReputation(tokenId)).to.equal(800);
    });

    it("Should decay reputation", async function () {
      await agentToken.mintAgent(addr1.address, "Test", [], 1, 0, 3600);
      const tokenId = await agentToken.agentTokens(addr1.address);
      
      await agentToken.decayReputation(tokenId);
      // 500 - 5 = 495
      expect(await agentToken.agentReputation(tokenId)).to.equal(495);
    });

    it("Should batch mint agents", async function () {
      await agentToken.mintAgentBatch(
        [addr1.address, addr2.address],
        ["Agent 1", "Agent 2"],
        [[], []],
        [1, 1],
        [0, 0],
        [3600, 3600]
      );
      
      expect(await agentToken.agentTokens(addr1.address)).to.not.equal(0);
      expect(await agentToken.agentTokens(addr2.address)).to.not.equal(0);
    });

    it("Should respect pause", async function () {
      await agentToken.pause();
      await expect(
        agentToken.mintAgent(addr1.address, "Test", [], 1, 0, 3600)
      ).to.be.revertedWithCustomError(agentToken, "EnforcedPause");
    });
  });
});
