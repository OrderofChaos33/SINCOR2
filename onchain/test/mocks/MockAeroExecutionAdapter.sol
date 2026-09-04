// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

interface IMintableERC20 {
    function mint(address to, uint256 amount) external;
}

contract MockAeroExecutionAdapter {
    using SafeERC20 for IERC20;

    address public immutable aeroToken;
    uint256 public rateNumerator = 1;
    uint256 public rateDenominator = 1;

    uint256 public totalLocked;
    uint256 public lastTokenId;
    address public lastGauge;
    uint256 public lastWeight;

    constructor(address _aeroToken) {
        aeroToken = _aeroToken;
    }

    function setRate(uint256 numerator, uint256 denominator) external {
        rateNumerator = numerator;
        rateDenominator = denominator;
    }

    function swapToAero(address tokenIn, uint256 amountIn, uint256 minAeroOut) external returns (uint256 aeroOut) {
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);
        aeroOut = (amountIn * rateNumerator) / rateDenominator;
        require(aeroOut >= minAeroOut, "MIN_OUT");
        IMintableERC20(aeroToken).mint(msg.sender, aeroOut);
    }

    function lockVeAero(uint256 aeroAmount, uint256) external returns (uint256 tokenId) {
        IERC20(aeroToken).safeTransferFrom(msg.sender, address(this), aeroAmount);
        totalLocked += aeroAmount;
        lastTokenId += 1;
        tokenId = lastTokenId;
    }

    function voteGauge(address gauge, uint256 weight) external {
        lastGauge = gauge;
        lastWeight = weight;
    }
}
