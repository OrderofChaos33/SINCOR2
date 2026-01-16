const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("SinBondingCurve - 18 decimals handling", function () {
  let token, curve, owner;

  beforeEach(async function () {
    [owner] = await ethers.getSigners();

    const MockToken18 = await ethers.getContractFactory("MockToken18");
    token = await MockToken18.deploy("Mock18", "M18");
    await token.deployed();

    const SinCurve = await ethers.getContractFactory("SinBondingCurve");
    const basePrice = ethers.utils.parseEther("1"); // 1 ETH per whole token
    curve = await SinCurve.deploy(token.address, basePrice);
    await curve.deployed();
  });

  it("calculates price correctly for whole token (1 * 10^18)", async function () {
    const oneToken = ethers.utils.parseUnits("1", 18);
    const price = await curve.priceForTokenAmount(oneToken);
    expect(price.toString()).to.equal(ethers.utils.parseEther("1").toString());
  });

  it("calculates price correctly for 1 smallest unit", async function () {
    const oneSmallest = ethers.BigNumber.from("1");
    const price = await curve.priceForTokenAmount(oneSmallest);
    // 1 ETH / 1e18 = 1 wei
    expect(price.toString()).to.equal("1");
  });
});
