const { expect } = require("chai");
const { ethers } = require("hardhat");

// Small JS-side helper that mirrors contract math
function priceForTokenAmountJS(basePricePerWholeTokenWei, tokenAmountSmallest, tokenDecimals) {
  const decimalsFactor = ethers.BigNumber.from(10).pow(tokenDecimals);
  return ethers.BigNumber.from(basePricePerWholeTokenWei).mul(tokenAmountSmallest).div(decimalsFactor);
}

describe("JS formatting helpers", function () {
  it("matches contract math for 9-decimal token", function () {
    const basePrice = ethers.utils.parseEther("2");
    const oneToken = ethers.BigNumber.from("1000000000");
    const res = priceForTokenAmountJS(basePrice, oneToken, 9);
    expect(res.toString()).to.equal(ethers.utils.parseEther("2").toString());
  });

  it("uses formatUnits for display", function () {
    const basePrice = ethers.utils.parseEther("2");
    const oneSmallest = ethers.BigNumber.from("1");
    const res = priceForTokenAmountJS(basePrice, oneSmallest, 9);
    // convert wei to readable using formatUnits with 9 decimals on token amount
    const human = ethers.utils.formatUnits(res, 18); // price in ETH -> 1e18
    expect(human.startsWith("0.")).to.be.true;
  });
});
