const { expect } = require("chai");
const { ethers } = require("hardhat");

describe("Rounding behavior", function () {
  it("rounded-up price avoids zero when base price is tiny (9 decimals)", async function () {
    const MockToken = await ethers.getContractFactory("MockToken9");
    const token = await MockToken.deploy("Mock9Small", "M9S");
    await token.deployed();

    const SinCurve = await ethers.getContractFactory("SinBondingCurve");
    // tiny base price: 1 wei per whole token
    const curve = await SinCurve.deploy(token.address, ethers.BigNumber.from("1"));
    await curve.deployed();

    const oneSmallest = ethers.BigNumber.from("1");
    const floorPrice = await curve.priceForTokenAmount(oneSmallest);
    const ceilPrice = await curve.priceForTokenAmountRoundedUp(oneSmallest);

    expect(floorPrice.toNumber()).to.equal(0);
    expect(ceilPrice.toNumber()).to.equal(1); // ceil should be 1 wei
  });
});
