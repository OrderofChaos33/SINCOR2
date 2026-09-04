// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

contract MockStrategy {
    using SafeERC20 for IERC20;

    IERC20 public immutable assetToken;
    uint256 public managed;
    uint256 public nextGain;
    uint256 public nextLoss;

    constructor(address asset_) {
        assetToken = IERC20(asset_);
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function totalAssets() external view returns (uint256) {
        return managed;
    }

    function deposit(uint256 amount) external returns (uint256 deployed) {
        assetToken.safeTransferFrom(msg.sender, address(this), amount);
        managed += amount;
        return amount;
    }

    function withdraw(uint256 amount, address to) external returns (uint256 returnedAmount) {
        returnedAmount = amount > managed ? managed : amount;
        managed -= returnedAmount;
        assetToken.safeTransfer(to, returnedAmount);
    }

    function setHarvest(uint256 gain, uint256 loss) external {
        nextGain = gain;
        nextLoss = loss;
    }

    function harvest() external returns (uint256 gain, uint256 loss) {
        gain = nextGain;
        loss = nextLoss;
        nextGain = 0;
        nextLoss = 0;

        if (gain > 0) {
            managed += gain;
        }
        if (loss > 0) {
            managed = loss > managed ? 0 : managed - loss;
        }
    }
}
