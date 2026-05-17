// SPDX-License-Identifier: MIT
pragma solidity 0.8.26;

import {LimitOrderHook} from "@openzeppelin/uniswap-hooks/general/LimitOrderHook.sol";
import {BaseHook} from "@openzeppelin/uniswap-hooks/base/BaseHook.sol";
import {Hooks} from "@uniswap/v4-core/src/libraries/Hooks.sol";
import {IPoolManager} from "@uniswap/v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "@uniswap/v4-core/src/types/PoolKey.sol";
import {SwapParams} from "@uniswap/v4-core/src/types/PoolOperation.sol";
import {BeforeSwapDelta, toBeforeSwapDelta} from "@uniswap/v4-core/src/types/BeforeSwapDelta.sol";

/// @title SincLimitOrderHook
/// @notice Extends OZ LimitOrderHook with anti-sandwich dynamic fee scaling.
///         First swap in a block on a given pool pays BASE_FEE; any subsequent
///         swap in the same block pays SANDWICH_FEE, disincentivising sandwich attacks.
contract SincLimitOrderHook is LimitOrderHook {
    /// @notice Per-pool swap counter, keyed by poolId hash and block number.
    mapping(bytes32 => mapping(uint256 => uint256)) public swapsInBlock;

    /// @notice Normal fee: 0.30%
    uint24 public constant BASE_FEE = 3000;
    /// @notice Elevated fee applied to second+ swap in same block: 3.00%
    uint24 public constant SANDWICH_FEE = 30000;

    constructor(IPoolManager m) BaseHook(m) {}

    /// @inheritdoc BaseHook
    function getHookPermissions() public pure override returns (Hooks.Permissions memory perms) {
        perms = super.getHookPermissions();
        // Enable beforeSwap on top of what LimitOrderHook already enables
        // (afterInitialize + afterSwap).
        perms.beforeSwap = true;
    }

    /// @dev Counts swaps per pool per block and scales the fee accordingly.
    function _beforeSwap(
        address,
        PoolKey calldata key,
        SwapParams calldata,
        bytes calldata
    ) internal override returns (bytes4, BeforeSwapDelta, uint24) {
        bytes32 pid = keccak256(abi.encode(key));
        uint256 count = swapsInBlock[pid][block.number];
        uint24 fee = count >= 1 ? SANDWICH_FEE : BASE_FEE;
        swapsInBlock[pid][block.number] = count + 1;
        return (this.beforeSwap.selector, toBeforeSwapDelta(0, 0), fee);
    }
}
