// SPDX-License-Identifier: MIT
pragma solidity ^0.8.27;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

interface IPoolManager {
    struct SwapParams {
        bool zeroForOne;
        int256 amountSpecified;
        uint160 sqrtPriceLimitX96;
    }
}

library Hooks {
    struct Permissions {
        bool beforeInitialize;
        bool afterInitialize;
        bool beforeAddLiquidity;
        bool afterAddLiquidity;
        bool beforeRemoveLiquidity;
        bool afterRemoveLiquidity;
        bool beforeSwap;
        bool afterSwap;
        bool beforeDonate;
        bool afterDonate;
        bool beforeSwapReturnDelta;
        bool afterSwapReturnDelta;
        bool afterAddLiquidityReturnDelta;
        bool afterRemoveLiquidityReturnDelta;
    }
}

struct PoolKey {
    address currency0;
    address currency1;
    uint24 fee;
    int24 tickSpacing;
    address hooks;
}

struct BalanceDelta {
    int128 amount0;
    int128 amount1;
}

/**
 * @title BribeLoopHook
 * @notice Uniswap v4 Hook that intercepts swap yield and routes continuous fee rewards directly into SincorTaskEscrow.
 */
contract BribeLoopHook {
    using SafeERC20 for IERC20;

    address public immutable poolManager;
    address public taskEscrow;
    address public owner;

    uint256 public bribeBps = 50; // 0.50% dynamic fee cut to task pool
    uint256 public constant MAX_BPS = 10000;

    event TaskPoolFunded(address indexed token, uint256 amount);
    event BribeBpsUpdated(uint256 newBps);
    event TaskEscrowUpdated(address newEscrow);

    modifier onlyPoolManager() {
        require(msg.sender == poolManager, "Caller not PoolManager");
        _;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Caller not Owner");
        _;
    }

    constructor(address _poolManager, address _taskEscrow) {
        require(_poolManager != address(0) && _taskEscrow != address(0), "Invalid address");
        poolManager = _poolManager;
        taskEscrow = _taskEscrow;
        owner = msg.sender;
    }

    function setBribeBps(uint256 _bribeBps) external onlyOwner {
        require(_bribeBps <= 500, "Bribe fee capped at 5%");
        bribeBps = _bribeBps;
        emit BribeBpsUpdated(_bribeBps);
    }

    function setTaskEscrow(address _taskEscrow) external onlyOwner {
        require(_taskEscrow != address(0), "Invalid escrow address");
        taskEscrow = _taskEscrow;
        emit TaskEscrowUpdated(_taskEscrow);
    }

    function getHookPermissions() public pure returns (Hooks.Permissions memory) {
        return Hooks.Permissions({
            beforeInitialize: false,
            afterInitialize: false,
            beforeAddLiquidity: false,
            afterAddLiquidity: false,
            beforeRemoveLiquidity: false,
            afterRemoveLiquidity: false,
            beforeSwap: false,
            afterSwap: true, // Route yield on swap completion
            beforeDonate: false,
            afterDonate: false,
            beforeSwapReturnDelta: false,
            afterSwapReturnDelta: false,
            afterAddLiquidityReturnDelta: false,
            afterRemoveLiquidityReturnDelta: false
        });
    }

    function afterSwap(
        address,
        PoolKey calldata key,
        IPoolManager.SwapParams calldata params,
        BalanceDelta delta,
        bytes calldata
    ) external onlyPoolManager returns (bytes4, int128) {
        // Identify output token based on swap direction
        address outputToken = params.zeroForOne ? key.currency1 : key.currency0;
        int128 amountOut = params.zeroForOne ? delta.amount1 : delta.amount0;

        if (amountOut > 0) {
            uint256 outputAmount = uint256(int256(amountOut));
            uint256 bribeAmount = (outputAmount * bribeBps) / MAX_BPS;

            if (bribeAmount > 0 && taskEscrow != address(0) && outputToken != address(0)) {
                IERC20(outputToken).safeTransfer(taskEscrow, bribeAmount);
                emit TaskPoolFunded(outputToken, bribeAmount);
            }
        }

        return (this.afterSwap.selector, 0);
    }
}
