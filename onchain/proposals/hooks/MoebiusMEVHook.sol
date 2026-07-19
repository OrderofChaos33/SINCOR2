// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import {BaseHook} from "v4-periphery/src/base/hooks/BaseHook.sol";
import {Hooks} from "v4-core/src/libraries/Hooks.sol";
import {IPoolManager} from "v4-core/src/interfaces/IPoolManager.sol";
import {PoolKey} from "v4-core/src/types/PoolKey.sol";
import {BalanceDelta} from "v4-core/src/types/BalanceDelta.sol";
import {ReentrancyGuardTransient} from "@openzeppelin/contracts/utils/ReentrancyGuardTransient.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title MoebiusMEVHook - Phantom Credit & Atomic Recapture Engine
contract MoebiusMEVHook is BaseHook, ReentrancyGuardTransient {
    using SafeERC20 for IERC20;

    address public immutable phantomCreditToken;
    address public immutable treasury;

    event PhantomLiquidityDeployed(bytes32 indexed poolId, uint256 amount);
    event MEVCaptured(bytes32 indexed poolId, uint256 amount, address to);

    constructor(IPoolManager _manager, address _pct, address _treasury) BaseHook(_manager) {
        phantomCreditToken = _pct;
        treasury = _treasury;
    }

    function getHookPermissions() public pure override returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: false,
            beforeSwap: true,
            afterSwap: true,
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: true,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    function _beforeSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata params, bytes calldata)
        internal override returns (bytes4, int128, uint24)
    {
        bytes32 poolId = keccak256(abi.encode(key));
        emit PhantomLiquidityDeployed(poolId, uint256(params.amountSpecified));
        return (this.beforeSwap.selector, 0, 0);
    }

    function _afterSwap(address, PoolKey calldata key, IPoolManager.SwapParams calldata, BalanceDelta delta, bytes calldata)
        internal override returns (bytes4, int128)
    {
        bytes32 poolId = keccak256(abi.encode(key));
        int128 feeAmount = delta.amount1();
        if (feeAmount > 0) {
            emit MEVCaptured(poolId, uint256(uint128(feeAmount)), treasury);
        }
        return (this.afterSwap.selector, 0);
    }
}
