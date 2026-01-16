const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("SinBondingCurve - 9 decimals handling", function () {
  let token, curve, owner;

  beforeEach(async function () {
    [owner] = await ethers.getSigners();

    const MockToken = await ethers.getContractFactory("MockToken9");
    token = await MockToken.deploy("Mock9", "M9");
    await token.deployed();

    const SinCurve = await ethers.getContractFactory("SinBondingCurve");
    const basePrice = ethers.utils.parseEther("2"); // 2 ETH per whole token
    curve = await SinCurve.deploy(token.address, basePrice);
    await curve.deployed();
  });

  it("calculates price correctly for whole token (1 * 10^9)", async function () {
    const oneToken = ethers.BigNumber.from("1000000000"); // 1e9 smallest units
    const price = await curve.priceForTokenAmount(oneToken);
    expect(price.toString()).to.equal(ethers.utils.parseEther("2").toString());
  });

  it("calculates price correctly for 1 smallest unit", async function () {
    const oneSmallest = ethers.BigNumber.from("1");
    const price = await curve.priceForTokenAmount(oneSmallest);
    // 2 ETH / 1e9 = 2e9 wei
    expect(price.toString()).to.equal(ethers.BigNumber.from("2000000000").toString());
  });

  it("works with arithmetic without overflow for large amounts", async function () {
    const millionTokens = ethers.BigNumber.from("1000000000000000"); // 1e6 whole tokens in smallest units
    const price = await curve.priceForTokenAmount(millionTokens);
    // price should equal 2 ETH * 1e6 = 2e6 ETH in wei
    const expected = ethers.utils.parseEther("2000000");
    expect(price.toString()).to.equal(expected.toString());
  });
});
