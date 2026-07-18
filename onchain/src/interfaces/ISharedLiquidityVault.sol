// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title ISharedLiquidityVault — surface used by strategy hooks
interface ISharedLiquidityVault {
    function SINC() external view returns (IERC20);
    function USDC() external view returns (IERC20);

    function drawDown(uint256 strategyId, address lp, address token, uint256 amount) external;
    function settleUp(
        uint256 strategyId,
        address lp,
        address token,
        uint256 principal,
        uint256 fee,
        uint256 protocolFeeBps
    ) external;

    function availableDraw(uint256 strategyId, address lp, address token) external view returns (uint256);
    function strategyHook(uint256 strategyId) external view returns (address);
    function strategyDefaultBacker(uint256 strategyId) external view returns (address);
}
